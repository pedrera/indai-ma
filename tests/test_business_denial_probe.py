"""Safe observability and offline negative probes; never send a live POST."""
import json
from unittest.mock import Mock

import httpx
import pytest

import business_auth as auth
import business_denial_probe as probe
from api_client import AnalysisApiResponseError
from tests.test_streamlit_auth import ui, metadata
from tests.test_security_http import environment, signing_key, signed, assert_no_execution


@pytest.mark.parametrize("tenant,kind,stage", [
    ("tenant-a", "PermissionDenied", "OPERATION_AUTHORIZATION"),
    ("tenant-b", "TenantAccessDenied", "CONTEXT_FACTORY"),
])
def test_audit_proves_dependency_denial_before_service(environment, signing_key, capsys, tenant, kind, stage):
    token = signed(signing_key, "viewer")
    supervisor = Mock(side_effect=AssertionError("Supervisor constructed"))
    environment[1]._supervisor_factory = supervisor
    response = environment[0].post("/api/v1/analysis", json={"text": "fixed test input"}, headers={
        "Authorization": "Bearer " + token, "X-Tenant-ID": tenant, "X-Validation-ID": "a" * 32})
    assert response.status_code == 403 and response.json()["code"] == "forbidden"
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["exception_class"] == kind and event["security_stage"] == stage
    assert event["service_dependency_entered"] is False and event["analysis_handler_entered"] is False
    assert token not in output and "Bearer" not in output
    assert_no_execution(environment)
    supervisor.assert_not_called()


def test_untrusted_validation_header_is_not_logged(environment, signing_key, capsys):
    response = environment[0].post("/api/v1/analysis", json={"text": "fixed test input"}, headers={
        "Authorization": "Bearer " + signed(signing_key, "viewer"), "X-Tenant-ID": "tenant-a",
        "X-Validation-ID": "PRIVATE_SENTINEL"})
    assert response.status_code == 403 and capsys.readouterr().out == ""
    assert_no_execution(environment)


@pytest.fixture
def prepared(ui, monkeypatch):
    st, session_client = ui
    session_client.session.side_effect = lambda **kwargs: metadata(st.user, [
        {"tenant_id": "tenant-alpha", "permissions": ["documents:read", "portfolio:read"]}])
    st.session_state["business_readonly_tenant_selection"] = "tenant-alpha"
    auth.refresh_business_session(st)
    st.session_state["business_readonly_tenant_selection"] = "tenant-alpha"
    config = {"case": "viewer-a", "issuer": st.user["iss"], "subject": st.user["sub"],
              "tenant_id": "tenant-alpha", "approved_post": True}
    monkeypatch.setattr(probe, "_used", set())
    monkeypatch.setattr(probe, "_blocked", False)
    return st, config


def test_probe_is_explicit_fixed_local_single_request(prepared, monkeypatch, capsys):
    st, config = prepared
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) == "http://127.0.0.1:8000/api/v1/analysis"
        assert request.headers["authorization"] == "Bearer private-access-token"
        assert request.headers["x-tenant-id"] == "tenant-alpha"
        return httpx.Response(403, json={"code": "forbidden"})
    original = httpx.Client
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(probe.httpx, "Client", client)
    result = probe.perform_probe(st, config)
    assert result["http_status"] == 403 and result["server_evidence"] == "PENDING_REVIEW"
    with pytest.raises(auth.AnalysisApiCredentialsError):
        probe.perform_probe(st, config)
    assert len(requests) == 1
    assert "private-access-token" not in capsys.readouterr().out


def test_unapproved_probe_never_constructs_http_client(prepared, monkeypatch):
    st, config = prepared
    config["approved_post"] = False
    client = Mock()
    monkeypatch.setattr(probe.httpx, "Client", client)
    with pytest.raises(auth.AnalysisApiCredentialsError):
        probe.perform_probe(st, config)
    client.assert_not_called()


@pytest.mark.parametrize("fault", [200, 401, "timeout"])
def test_unexpected_response_stops_all_further_probes(prepared, monkeypatch, capsys, fault):
    st, config = prepared
    requests = []
    def handler(request):
        requests.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("PRIVATE_TIMEOUT_SENTINEL", request=request)
        return httpx.Response(fault, json={"private": "PRIVATE_RESPONSE_SENTINEL"})
    original = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(AnalysisApiResponseError):
        probe.perform_probe(st, config)
    config["tenant_id"] = "tenant-beta"
    with pytest.raises(auth.AnalysisApiCredentialsError):
        probe.perform_probe(st, config)
    assert len(requests) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_" not in output and "private-access-token" not in output
