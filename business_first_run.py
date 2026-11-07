"""Narrow first-run UI: real native/backend auth, fixed input, one attempt.

No identities/grants are synthesized. The trusted private configuration only
narrows which already authenticated principal/tenant may perform the pilot.
Approval defaults to false. This module has no automatic execution path.
"""
from threading import Lock
import json

from api_client import AnalysisApiClient, AnalysisApiClientError, AnalysisApiCredentialsError
from business_api_adapter import ApiJobAdapter
from business_auth import (BusinessSession, capture_business_request,
                           refresh_business_session, business_result_is_current)
from security.models import PrincipalId

PILOT_QUERY = ("La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados "
               "y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento.")
_attempt_lock = Lock()
_attempt_used = False


def expected_scope(config):
    if not all(isinstance(config.get(k), str) and config[k].strip()
               for k in ("issuer", "subject", "tenant_id")):
        raise AnalysisApiCredentialsError("La configuración de la prueba no está completa.")
    return PrincipalId(config["issuer"], config["subject"]), config["tenant_id"]


def readiness(session, config):
    principal, tenant = expected_scope(config)
    valid = isinstance(session, BusinessSession)
    return {
        "session_verified": valid,
        "expected_identity_matches": valid and session.principal == principal,
        "explicit_tenant_matches": valid and session.tenant_id == tenant,
        "analysis_permission": valid and session.can_analyze,
        "review_approved": config.get("approved") is True,
    }


def perform_first_run(st, config):
    """Called ONLY by an explicit enabled UI click; uses the existing HTTP adapter."""
    global _attempt_used
    session = refresh_business_session(st)
    if not all(readiness(session, config).values()):
        raise AnalysisApiCredentialsError("La prueba requiere identidad, tenant, permiso y revisión válidos.")
    request, binding = capture_business_request(st, PILOT_QUERY)
    expected, tenant = expected_scope(config)
    if binding[0] != expected or request.tenant_id != tenant:
        raise AnalysisApiCredentialsError("El ámbito de la operación ha cambiado.")
    with _attempt_lock:
        if _attempt_used:
            raise AnalysisApiCredentialsError("El único intento de esta instancia ya se ha consumido.")
        # Consume before networking. An error/timeout never authorizes a retry.
        _attempt_used = True
    client = AnalysisApiClient(analysis_timeout=30)
    try:
        result = ApiJobAdapter(client).run(request, 30)
    finally:
        client.close()
        request = None
    refresh_business_session(st)
    if not business_result_is_current(st.session_state, binding):
        raise AnalysisApiCredentialsError("La sesión cambió; se descarta el resultado.")
    st.session_state["business_first_run_result"] = (binding, result)
    return result


def render_first_run(st, config):
    st.subheader("Primera prueba Business")
    st.code(PILOT_QUERY, language=None)
    try:
        checks = readiness(st.session_state.get("business_session"), config)
    except (KeyError, TypeError, ValueError, AnalysisApiCredentialsError):
        st.error("La prueba permanece bloqueada por configuración incompleta.")
        return
    marker = tuple(checks.values())
    if st.session_state.get("business_first_run_readiness") != marker:
        # Fixed booleans only: no subject, session ID, claims or credentials.
        print(json.dumps({"event": "BUSINESS_FIRST_RUN_READINESS", **checks}), flush=True)
        st.session_state["business_first_run_readiness"] = marker
    if checks["expected_identity_matches"]:
        st.success("Identidad autenticada coincidente con la identidad esperada de la prueba.")
    else:
        st.warning("La identidad esperada de la prueba todavía no está confirmada.")
    if not checks["review_approved"]:
        st.info("Ejecución pendiente de revisión. No se enviará ninguna solicitud de análisis.")
    with _attempt_lock:
        used = _attempt_used
    if st.button("Ejecutar una única prueba Business", key="business_first_run_submit",
                 disabled=used or not all(checks.values())):
        try:
            perform_first_run(st, config)
        except AnalysisApiClientError:
            st.error("La prueba no pudo completarse. Revisa la sesión y el backend antes de otro intento.")
    saved = st.session_state.get("business_first_run_result")
    if saved and business_result_is_current(st.session_state, saved[0]):
        result = saved[1]
        st.write("Estado del backend: " + result.status)
        st.write(result.summary)
        st.table([{"Métrica": m.label, "Valor": m.value, "Origen": m.origin} for m in result.metrics])
        st.json({"estado": result.status, "agentes": result.routing.selected_agents,
                 "especialistas": [{"agente": s.agent_name, "estado": s.status} for s in result.specialists],
                 "llm_calls": result.diagnostics.llm_calls, "rag_calls": result.diagnostics.rag_calls,
                 "tool_calls": result.diagnostics.tool_calls})
