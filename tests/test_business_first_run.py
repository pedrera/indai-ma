"""Controlled pilot, with HTTP mocked; never sends a live analysis request."""
from dataclasses import replace
from unittest.mock import Mock
import json

import httpx
import pytest

import business_auth as auth
import business_first_run as pilot
from api_client import AnalysisApiClient, AnalysisApiCredentialsError, AnalysisApiResponseError
from tests.test_streamlit_auth import ui, activate, native_user
from tests.test_api_client import PAYLOAD


@pytest.fixture
def prepared(ui, monkeypatch):
    st, session_client = ui
    activate(st, "a")
    config = {"issuer": st.user["iss"], "subject": st.user["sub"], "tenant_id": "a", "approved": True}
    monkeypatch.setattr(pilot, "_attempt_used", False)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=PAYLOAD)
    real_client = AnalysisApiClient(transport=httpx.MockTransport(handler))
    factory = Mock(return_value=real_client)
    monkeypatch.setattr(pilot, "AnalysisApiClient", factory)
    return st, session_client, config, requests, factory, real_client


def test_fixed_route_is_unambiguous_and_document_free():
    from supervisor_routing import route_deterministically
    route = route_deterministically(pilot.PILOT_QUERY)
    assert route.selected_agents == ["ProcurementAgent"]
    assert not route.ambiguity_detected


def test_bearer_tenant_propagation_and_single_attempt(prepared):
    st, _, config, requests, _, client = prepared
    result = pilot.perform_first_run(st, config)
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and request.url.path == "/api/v1/analysis"
    assert request.headers["authorization"] == "Bearer private-access-token"
    assert request.headers["x-tenant-id"] == "a"
    assert json.loads(request.content) == {"text": pilot.PILOT_QUERY}
    assert client._client.is_closed
    assert "private-access-token" not in repr(st.session_state)
    assert st.session_state["business_first_run_result"][1] is result
    with pytest.raises(AnalysisApiCredentialsError):
        pilot.perform_first_run(st, config)
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["anonymous", "no_tenant", "no_analysis", "invalid", "expired", "wrong_principal", "wrong_tenant", "not_reviewed"])
def test_no_request_or_quota_consumption_without_all_guards(prepared, failure):
    st, session_client, config, requests, factory, _ = prepared
    if failure == "anonymous":
        st.user = native_user(logged=False)
    elif failure == "no_tenant":
        st.session_state["business_session"] = replace(st.session_state["business_session"], tenant_id=None)
    elif failure == "no_analysis":
        session_client.session.return_value = None
        session_client.session.side_effect = lambda **kwargs: {"issuer": st.user["iss"], "subject": st.user["sub"],
            "expires_at": st.user["exp"], "tenants": [{"tenant_id": "a", "permissions": ["documents:read"]}]}
    elif failure == "invalid":
        session_client.session.side_effect = AnalysisApiCredentialsError("private backend error")
    elif failure == "expired":
        st.user["exp"] = 1
    elif failure == "wrong_principal":
        config["subject"] = "another-expected-subject"
    elif failure == "wrong_tenant":
        config["tenant_id"] = "b"
    elif failure == "not_reviewed":
        config["approved"] = False
    with pytest.raises(AnalysisApiCredentialsError):
        pilot.perform_first_run(st, config)
    assert requests == []
    factory.assert_not_called()
    assert pilot._attempt_used is False


def test_http_failure_has_no_local_fallback_and_no_retry(prepared, monkeypatch):
    st, _, config, requests, factory, _ = prepared
    client = Mock()
    client.analyze.side_effect = AnalysisApiResponseError("backend unavailable")
    factory.return_value = client
    supervisor = Mock(side_effect=AssertionError("Local fallback invoked"))
    monkeypatch.setattr("application_service.Supervisor", supervisor)
    with pytest.raises(AnalysisApiResponseError):
        pilot.perform_first_run(st, config)
    client.close.assert_called_once()
    supervisor.assert_not_called()
    assert "business_first_run_result" not in st.session_state
    assert pilot._attempt_used is True


def test_result_is_discarded_when_session_changes(prepared, monkeypatch):
    st, _, config, _, factory, _ = prepared
    from api_client_models import ApiAnalysisResult
    client = Mock()
    def change_session(*args, **kwargs):
        st.user["sub"] = "another-real-user"
        return ApiAnalysisResult.model_validate(PAYLOAD)
    client.analyze.side_effect = change_session
    factory.return_value = client
    with pytest.raises(AnalysisApiCredentialsError):
        pilot.perform_first_run(st, config)
    assert "business_first_run_result" not in st.session_state
    assert st.session_state["business_session"].tenant_id is None
    client.close.assert_called_once()


def test_denial_precedes_supervisor_provider_rag_and_recorder():
    from application_service import AnalysisService
    from application_models import AnalysisRequest
    from tests.security_fixtures import security_fixture
    from security.errors import AuthenticationRequired
    from runtime_config import LLMRuntimeConfig
    supervisor, provider, rag, recorder = (Mock() for _ in range(4))
    service = AnalysisService(authorizer=security_fixture().authorizer, supervisor_factory=supervisor,
                              provider_factory=provider, rag_factory=rag, recorder_factory=recorder)
    with pytest.raises(AuthenticationRequired):
        service.analyze(AnalysisRequest(text=pilot.PILOT_QUERY, runtime=LLMRuntimeConfig(384,384,30,False)), None)
    for dependency in (supervisor, provider, rag, recorder):
        dependency.assert_not_called()


def test_native_preparation_ui_is_locked_and_confirms_identity(monkeypatch):
    from pathlib import Path
    import streamlit
    from streamlit.testing.v1 import AppTest
    from tests.test_streamlit_auth import metadata
    monkeypatch.setenv("INDAI_STREAMLIT_SESSION_ONLY", "1")
    monkeypatch.setattr(pilot, "_attempt_used", False)
    user = native_user()
    monkeypatch.setattr(streamlit, "user", user)
    client = Mock()
    client.session.side_effect = lambda **kwargs: metadata(user)
    monkeypatch.setattr(auth, "AnalysisApiClient", Mock(return_value=client))
    app = AppTest.from_file(str(Path("app.py").resolve()), default_timeout=20)
    app.secrets["business_first_run"] = {"issuer": user["iss"], "subject": user["sub"], "tenant_id": "a", "approved": False}
    app.run()
    assert not app.exception and app.button(key="business_first_run_submit").disabled
    assert app.selectbox(key="business_tenant_selection").value is None
    app.selectbox(key="business_tenant_selection").select("a").run()
    assert app.button(key="business_first_run_submit").disabled
    assert any("identidad esperada" in x.value for x in app.success)
    assert len(app.radio) == 0
    client.analyze.assert_not_called()
