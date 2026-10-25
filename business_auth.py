"""Business-only native Streamlit OIDC session and job isolation.

Uses public st.user.tokens.access only. Tokens are read for one operation and
never retained in session metadata, logs, prompts or diagnostics.
"""
from dataclasses import dataclass, replace
import math
from time import time
from uuid import uuid4

from api_client import AnalysisApiClient, AnalysisApiClientError, AnalysisApiCredentialsError
from business_api_adapter import BusinessApiRequest
from security.models import PrincipalId
from security.permissions import Permission


@dataclass(frozen=True)
class BusinessSession:
    principal: PrincipalId
    login_marker: tuple
    expires_at: float
    tenants: tuple[tuple[str, frozenset[Permission]], ...]
    epoch: str
    tenant_id: str | None = None

    @property
    def binding(self):
        return (self.principal, self.tenant_id, self.epoch)

    @property
    def can_analyze(self):
        return self.expires_at > time() and any(
            tenant == self.tenant_id and Permission.ANALYSIS_RUN in permissions
            for tenant, permissions in self.tenants)


def clear_business_state(state):
    """Detach old jobs; cancellation is local, never distributed authorization."""
    if state.get("generation_kind") == "business_api":
        job = state.get("generation_job")
        if job is not None:
            job.cancel()
        for key in ("generation_job", "generation_kind", "generation_notice", "operation_id", "operation_started_at"):
            state[key] = None
    ids = set(state.get("business_operation_ids", ()))
    views = state.get("execution_views", {})
    ids.update(operation for operation, view in views.items()
               if getattr(view, "mode", None) in {"Business", "Multi-Agent Supervisor"})
    for operation in ids:
        views.pop(operation, None)
    for mode, operation in list(state.get("execution_mode_ids", {}).items()):
        if operation in ids:
            state["execution_mode_ids"].pop(mode, None)
    recorder = state.get("pipeline_recorder")
    if recorder is not None and recorder.operation_id in ids:
        state["pipeline_recorder"] = None
    for key in list(state):
        if key.startswith("business_") and key not in {"business_tenant_selection", "business_session", "business_auth_error"}:
            del state[key]
    state["business_api_result"] = None
    state["business_operation_ids"] = ()


def _deny(state, message):
    clear_business_state(state)
    state["business_session"] = None
    state["business_auth_error"] = message


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Invalid session time")
    return float(value)


def refresh_business_session(st):
    """Validate native identity against the API on every rerun, including polling."""
    state = st.session_state
    old = state.get("business_session")
    if not getattr(st.user, "is_logged_in", False):
        if old is not None:
            _deny(state, None)
            state.pop("business_tenant_selection", None)
        return None
    try:
        principal = PrincipalId(st.user["iss"], st.user["sub"])
        native_exp = _timestamp(st.user["exp"])
        marker = (principal, _timestamp(st.user["iat"]), native_exp)
        access = st.user.tokens.get("access")
        if not isinstance(access, str) or not access or native_exp <= time():
            raise AnalysisApiCredentialsError()
        client = AnalysisApiClient(analysis_timeout=10)
        try:
            metadata = client.session(access_token=access)
        finally:
            client.close()
        if PrincipalId(metadata["issuer"], metadata["subject"]) != principal:
            raise AnalysisApiCredentialsError()
        expiry = min(native_exp, _timestamp(metadata["expires_at"]))
        if expiry <= time() or not isinstance(metadata["tenants"], list):
            raise AnalysisApiCredentialsError()
        tenants = []
        for item in metadata["tenants"]:
            tenant = item["tenant_id"]
            if not isinstance(tenant, str) or not tenant or not isinstance(item["permissions"], list):
                raise ValueError()
            if any(tenant == existing for existing, _ in tenants):
                raise ValueError()
            tenants.append((tenant, frozenset(Permission(value) for value in item["permissions"])))
        tenants = tuple(tenants)
        same_login = isinstance(old, BusinessSession) and old.login_marker == marker
        selected = old.tenant_id if same_login else None
        selected_allowed = selected is None or any(
            tenant == selected and Permission.ANALYSIS_RUN in permissions for tenant, permissions in tenants)
        if not same_login or not selected_allowed:
            clear_business_state(state)
            selected = None
            state.pop("business_tenant_selection", None)
        session = BusinessSession(principal, marker, expiry, tenants,
                                  old.epoch if same_login and selected_allowed else uuid4().hex, selected)
        state["business_session"] = session
        state["business_auth_error"] = None
        return session
    except AnalysisApiCredentialsError:
        _deny(state, "La sesión no dispone de un access token válido para esta API. Cierra sesión e inicia de nuevo.")
    except (AnalysisApiClientError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
        _deny(state, "No se puede verificar la sesión y sus permisos. Business permanece bloqueado.")
    return None


def select_business_tenant(st):
    state = st.session_state
    session = state.get("business_session")
    selected = state.get("business_tenant_selection")
    if not isinstance(session, BusinessSession):
        raise AnalysisApiCredentialsError("Se requiere una sesión verificada.")
    if selected is not None and not any(
            tenant == selected and Permission.ANALYSIS_RUN in permissions for tenant, permissions in session.tenants):
        _deny(state, "El tenant seleccionado no está autorizado.")
        return
    if selected != session.tenant_id:
        clear_business_state(state)
        state["business_session"] = replace(session, tenant_id=selected, epoch=uuid4().hex)


def logout_business(st):
    _deny(st.session_state, None)
    st.logout()


def render_business_auth(st):
    session = st.session_state.get("business_session")
    if getattr(st.user, "is_logged_in", False):
        if st.button("Cerrar sesión / renovar acceso", key="business_logout"):
            logout_business(st)
            return False
    else:
        try:
            auth = st.secrets.get("auth", {})
            configured = bool(auth.get("client_id") and auth.get("client_secret")
                              and auth.get("server_metadata_url") and auth.get("cookie_secret")
                              and "access" in auth.get("expose_tokens", []))
        except Exception:
            configured = False
        if st.button("Iniciar sesión", key="business_login", disabled=not configured):
            st.login()
        st.warning("Business pendiente de autenticación. Inicia sesión con el proveedor configurado.")
    message = st.session_state.get("business_auth_error")
    if message:
        st.warning(message)
    if not isinstance(session, BusinessSession):
        return False
    options = [tenant for tenant, permissions in session.tenants if Permission.ANALYSIS_RUN in permissions]
    if not options:
        st.warning("Tu usuario no tiene tenants con permiso de análisis.")
        return False
    st.selectbox("Tenant activo", options, index=None, key="business_tenant_selection",
                 on_change=lambda: select_business_tenant(st))
    if not session.can_analyze:
        st.info("Selecciona un tenant autorizado para ejecutar Business.")
    return session.can_analyze


def capture_business_request(st, text, alternative_evaluation=None):
    session = refresh_business_session(st)
    if not isinstance(session, BusinessSession) or not session.can_analyze:
        raise AnalysisApiCredentialsError("Se requiere sesión y tenant autorizados.")
    return (BusinessApiRequest(text, alternative_evaluation, st.user.tokens.get("access"), session.tenant_id),
            session.binding)


def business_result_is_current(state, binding):
    session = state.get("business_session")
    return (isinstance(session, BusinessSession) and session.can_analyze
            and session.binding == binding)
