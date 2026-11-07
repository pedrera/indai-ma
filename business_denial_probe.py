"""Explicitly approved, fixed loopback negative test; no OAuth or analysis bypass."""
import json
from threading import Lock
from uuid import uuid4

import httpx
from api_client import AnalysisApiResponseError

import business_auth as auth
from business_first_run import PILOT_QUERY
from security.models import PrincipalId
from security.permissions import Permission

_lock = Lock()
_used = set()
_blocked = False


def expected_identity(config):
    if config.get("case") not in {"viewer-a", "outsider"} or config.get("tenant_id") not in {"tenant-alpha", "tenant-beta"}:
        raise ValueError("Invalid denial scope")
    return PrincipalId(config["issuer"], config["subject"])


def readiness(st, config):
    session = auth.refresh_business_session(st)
    expected = expected_identity(config)
    tenant_permissions = dict(session.tenants) if isinstance(session, auth.BusinessSession) else {}
    expected_tenants = {"tenant-alpha"} if config["case"] == "viewer-a" else set()
    return {"session_verified": isinstance(session, auth.BusinessSession),
            "identity_matches": isinstance(session, auth.BusinessSession) and session.principal == expected,
            "tenant_listing_matches": set(tenant_permissions) == expected_tenants,
            "analysis_absent": not any(Permission.ANALYSIS_RUN in p for p in tenant_permissions.values()),
            "no_pending_job": st.session_state.get("generation_job") is None,
            "readonly_selection_matches": config["case"] != "viewer-a" or st.session_state.get("business_readonly_tenant_selection") == "tenant-alpha",
            "approved_post": config.get("approved_post") is True}


def perform_probe(st, config):
    global _blocked
    checks = readiness(st, config)
    if not all(checks.values()):
        raise auth.AnalysisApiCredentialsError("Negative test preconditions failed")
    key = (config["case"], config["tenant_id"])
    with _lock:
        if _blocked or key in _used:
            raise auth.AnalysisApiCredentialsError("Attempt already used or stopped")
        _used.add(key)
    validation_id = uuid4().hex
    # Only public native access-token API. Nothing is saved, printed or retried.
    token = st.user.tokens.get("access")
    status = None
    try:
        with httpx.Client(trust_env=False, timeout=10, follow_redirects=False) as client:
            response = client.post("http://127.0.0.1:8000/api/v1/analysis", json={"text": PILOT_QUERY},
                headers={"Authorization": "Bearer " + token, "X-Tenant-ID": config["tenant_id"],
                         "X-Validation-ID": validation_id})
        status = response.status_code
        if status != 403 or response.json().get("code") != "forbidden":
            raise ValueError("Expected rejection not observed")
        result = {"event": "BUSINESS_NEGATIVE_HTTP_RESULT", "validation_id": validation_id,
                  "case": config["case"], "tenant": config["tenant_id"], "http_status": 403,
                  "server_evidence": "PENDING_REVIEW"}
        print(json.dumps(result), flush=True)
        return result
    except Exception:
        with _lock:
            _blocked = True
        print(json.dumps({"event": "BUSINESS_NEGATIVE_TEST_STOPPED", "validation_id": validation_id,
                          "http_status": status}), flush=True)
        raise AnalysisApiResponseError("Unexpected result or transport error; no further tests permitted") from None
    finally:
        token = None


def render_preparation(st, config):
    st.subheader("Comprobación negativa de Business")
    try:
        checks = readiness(st, config)
    except Exception:
        st.warning("La comprobación permanece bloqueada por configuración o sesión no válida.")
        return
    marker = tuple(checks.values())
    if st.session_state.get("business_denial_readiness") != marker:
        print(json.dumps({"event": "BUSINESS_DENIAL_READINESS", "case": config["case"], **checks}), flush=True)
        st.session_state["business_denial_readiness"] = marker
    if checks["identity_matches"]:
        st.success("Identidad de la prueba confirmada por la sesión autenticada del backend: " + config["case"])
    st.info("Analizar sigue deshabilitado. La petición negativa requiere confirmación independiente.")
    with _lock:
        disabled = _blocked or (config["case"], config["tenant_id"]) in _used or not all(checks.values())
    if st.button("Enviar una única petición negativa", key="business_denial_submit", disabled=disabled):
        try:
            result = perform_probe(st, config)
            st.write(result)
        except auth.AnalysisApiClientError:
            st.error("Prueba detenida. No reintentes; se debe revisar el diagnóstico del servidor.")
