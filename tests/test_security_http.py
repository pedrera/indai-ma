"""C1.4: real JWT validation, real policy and service; all JWKS traffic offline."""
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from time import time
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest

from api.app import create_app
from api.composition import build_security
from api.security import APISecurity
from api_client import AnalysisApiClient, AnalysisApiCredentialsError
from application_models import AnalysisRequest
from application_service import AnalysisService
from business_api_adapter import BusinessApiRequest, ApiJobAdapter, create_business_api_job
from runtime_config import LLMRuntimeConfig
from security.config import OIDCConfig
from security.errors import (
    AuthenticationRequired, IdentityInfrastructureError, InvalidCredentials,
    PermissionDenied, SecurityError, TenantAccessDenied,
)
from security.memberships import ConfiguredMembershipDirectory, TenantMembership
from security.models import PrincipalId, TenantId
from security.oidc import OIDCAuthenticator
from security.policy import TenantAuthorizer
from security.roles import Role
from tests.security_fixtures import context_fixture, security_fixture
from tests.test_api import PROCUREMENT, RUNTIME
from tests.test_api_client import PAYLOAD


ISSUER = "https://http-security.example"
A = TenantId("tenant-a")
B = TenantId("tenant-b")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed(key, subject="analyst", **changes):
    return jwt.encode({
        "iss": ISSUER, "sub": subject, "aud": "indai-api", "iat": int(time()) - 10,
        "exp": int(time()) + 300, "client_id": "test-client", "jti": "test-token",
        **changes,
    }, key, algorithm="RS256", headers={"kid": "test-key", "typ": "at+jwt"})


@pytest.fixture
def environment(signing_key):
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    state = {"fail": False, "requests": []}
    def jwks(request):
        state["requests"].append(str(request.url))
        if state["fail"]:
            raise httpx.ConnectError("private JWKS detail", request=request)
        return httpx.Response(200, json={"keys": [{**jwk, "kid": "test-key", "alg": "RS256"}]})
    auth = OIDCAuthenticator(OIDCConfig(ISSUER, "indai-api", ISSUER + "/keys"), transport=httpx.MockTransport(jwks))
    memberships = ConfiguredMembershipDirectory([
        TenantMembership(PrincipalId(ISSUER, "analyst"), A, frozenset({Role.ANALYST})),
        TenantMembership(PrincipalId(ISSUER, "analyst"), B, frozenset({Role.VIEWER})),
        TenantMembership(PrincipalId(ISSUER, "second"), B, frozenset({Role.ANALYST})),
        TenantMembership(PrincipalId(ISSUER, "viewer"), A, frozenset({Role.VIEWER})),
    ])
    security = APISecurity(auth, memberships)
    rag = Mock(side_effect=AssertionError("Denied request reached RAG"))
    provider = Mock(side_effect=AssertionError("Denied request reached LLM"))
    service = AnalysisService(authorizer=security.authorizer, rag_factory=rag, provider_factory=provider)
    # Spy, not an authorization replacement: service is the real implementation.
    service.analyze = Mock(wraps=service.analyze)
    app = create_app(service, RUNTIME, security=security)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, service, state, rag, provider, security
    auth.close()


def headers(key, subject="analyst", tenant="tenant-a", **claims):
    return {"Authorization": "Bearer " + signed(key, subject, **claims), "X-Tenant-ID": tenant}


def assert_no_execution(environment):
    _, service, _, rag, provider, _ = environment
    service.analyze.assert_not_called()
    rag.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("authorization", [None, "Basic hidden", "Bearer", "Bearer bad", "Bearer one two"])
def test_missing_and_invalid_bearer_are_401_before_execution(environment, authorization):
    client, *_ = environment
    supplied = {"X-Tenant-ID": "tenant-a"}
    if authorization is not None:
        supplied["Authorization"] = authorization
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=supplied)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_no_execution(environment)


def test_expired_token_is_401_and_not_echoed(environment, signing_key):
    supplied = headers(signing_key, exp=1)
    response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=supplied)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert supplied["Authorization"] not in response.text
    assert_no_execution(environment)


def test_wrong_signature_is_401_before_service(environment):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(other_key))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_no_execution(environment)


@pytest.mark.parametrize("subject, tenant", [("viewer", "tenant-a"), ("analyst", "tenant-b"), ("analyst", "unknown"), ("absent", "tenant-a")])
def test_no_permission_or_membership_is_403(environment, signing_key, subject, tenant):
    response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key, subject, tenant))
    assert response.status_code == 403
    assert_no_execution(environment)


@pytest.mark.parametrize("tenant", [None, "", " ", "tenant/a", "x" * 129])
def test_explicit_tenant_is_required_and_validated(environment, signing_key, tenant):
    supplied = headers(signing_key)
    if tenant is None:
        supplied.pop("X-Tenant-ID")
    else:
        supplied["X-Tenant-ID"] = tenant
    response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=supplied)
    assert response.status_code == 400
    assert_no_execution(environment)


def test_duplicate_headers_are_denied(environment, signing_key):
    client = environment[0]
    pairs = list(headers(signing_key).items())
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=pairs + [("Authorization", "Bearer extra")])
    assert response.status_code == 401
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=pairs + [("X-Tenant-ID", "tenant-b")])
    assert response.status_code == 400
    assert_no_execution(environment)


def test_authorized_operation_preserves_results_and_propagates_context(environment, signing_key):
    client, service, _, rag, provider, _ = environment
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key))
    assert response.status_code == 200
    assert "25 GWh" in response.json()["summary"]
    assert response.json()["diagnostics"] == {"llm_calls": 0, "rag_calls": 0, "tool_calls": 2}
    scope = service.analyze.call_args.args[1]
    assert scope.principal == PrincipalId(ISSUER, "analyst")
    assert scope.tenant_id == A
    assert scope.correlation_id
    rag.assert_not_called()
    provider.assert_not_called()


def test_roles_in_token_do_not_grant_permissions(environment, signing_key):
    response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT},
                                   headers=headers(signing_key, "viewer", roles=["analyst"], permissions=["analysis:run"]))
    assert response.status_code == 403
    assert_no_execution(environment)


def test_body_cannot_supply_identity_and_invalid_values_are_not_echoed(environment, signing_key):
    response = environment[0].post("/api/v1/analysis", headers=headers(signing_key), json={
        "text": PROCUREMENT, "roles": ["analyst"], "access_token": "secret-body-token", "tenant_id": "tenant-a",
    })
    assert response.status_code == 422
    assert "secret-body-token" not in response.text
    assert_no_execution(environment)


def test_token_in_query_or_cookie_does_not_authenticate(environment, signing_key):
    client = environment[0]
    client.cookies.set("access_token", signed(signing_key))
    response = client.post("/api/v1/analysis", params={"access_token": signed(signing_key)},
                           json={"text": PROCUREMENT}, headers={"X-Tenant-ID": "tenant-a"})
    assert response.status_code == 401
    assert_no_execution(environment)


def test_jwks_unavailable_is_503_including_refresh_cooldown(environment, signing_key):
    environment[2]["fail"] = True
    for _ in range(2):
        response = environment[0].post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key))
        assert response.status_code == 503
        assert "private JWKS detail" not in response.text
    assert len(environment[2]["requests"]) == 1
    assert_no_execution(environment)


def test_missing_identity_configuration_is_closed(monkeypatch):
    monkeypatch.delenv("INDAI_OIDC_ISSUER", raising=False)
    service = Mock()
    with TestClient(create_app(service, RUNTIME), raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.post("/api/v1/analysis", json={"text": PROCUREMENT}).status_code == 401
        response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers={"Authorization": "Bearer supplied", "X-Tenant-ID": "tenant-a"})
        assert response.status_code == 503
    service.analyze.assert_not_called()


def test_concurrent_requests_have_independent_identity_tenant_and_correlation(environment, signing_key):
    client, service, *_ = environment
    supplied = [headers(signing_key, "analyst", "tenant-a"), headers(signing_key, "second", "tenant-b")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda h: client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=h), supplied * 4))
    assert all(response.status_code == 200 for response in responses)
    scopes = [call.args[1] for call in service.analyze.call_args_list]
    assert {(scope.principal.subject, scope.tenant_id) for scope in scopes} == {("analyst", A), ("second", B)}
    assert len({scope.correlation_id for scope in scopes}) == 8


@pytest.mark.parametrize("kind", ["none", "invalid", "missing_membership", "missing_permission", "revoked"])
def test_service_denies_before_any_expensive_work(kind):
    scope = context_fixture()
    configured = security_fixture()
    if kind == "none":
        scope = None
    elif kind == "invalid":
        scope = {"principal": "user"}
    elif kind == "missing_membership":
        configured = APISecurity(configured.authenticator, ConfiguredMembershipDirectory([]))
    elif kind == "missing_permission":
        scope = replace(scope, permissions=frozenset())
    elif kind == "revoked":
        configured = APISecurity(configured.authenticator, ConfiguredMembershipDirectory([
            TenantMembership(scope.principal, scope.tenant_id, frozenset({Role.VIEWER})),
        ]))
    supervisor, rag, provider, recorder = (Mock() for _ in range(4))
    service = AnalysisService(authorizer=configured.authorizer, supervisor_factory=supervisor,
                              rag_factory=rag, provider_factory=provider, recorder_factory=recorder)
    with pytest.raises(SecurityError):
        service.analyze(AnalysisRequest(PROCUREMENT, RUNTIME), scope)
    for collaborator in (supervisor, rag, provider, recorder):
        collaborator.assert_not_called()
    with pytest.raises(TypeError):
        service.analyze(AnalysisRequest(PROCUREMENT, RUNTIME))


def test_membership_infrastructure_failure_is_503_not_403(environment, signing_key):
    client, _, _, _, _, configured = environment
    class Unavailable:
        def resolve(self, principal, tenant):
            raise OSError("private database path")
    client.app.state.security = APISecurity(configured.authenticator, Unavailable())
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key))
    assert response.status_code == 503
    assert "private database path" not in response.text
    assert_no_execution(environment)


def test_authentication_source_failure_is_503_without_detail(environment, signing_key):
    class UnavailableAuthenticator:
        def authenticate(self, credentials):
            raise OSError("private authentication path")
    client, _, _, _, _, configured = environment
    client.app.state.security = APISecurity(UnavailableAuthenticator(), configured.memberships)
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key))
    assert response.status_code == 503
    assert "private authentication path" not in response.text
    assert_no_execution(environment)


def test_permission_revoked_between_http_dependency_and_service_is_denied(environment, signing_key):
    client, service, _, _, _, configured = environment
    class Revoked:
        def resolve(self, principal, tenant):
            return TenantMembership(principal, tenant, frozenset({Role.VIEWER}))
    service._authorizer = TenantAuthorizer(Revoked())
    response = client.post("/api/v1/analysis", json={"text": PROCUREMENT}, headers=headers(signing_key))
    assert response.status_code == 403
    environment[3].assert_not_called()
    environment[4].assert_not_called()


def test_client_credentials_are_per_operation_and_do_not_leak_concurrently():
    seen = []
    def handler(request):
        seen.append((request.headers["authorization"], request.headers["x-tenant-id"], json.loads(request.read())))
        return httpx.Response(200, json=PAYLOAD)
    client = AnalysisApiClient("http://test", transport=httpx.MockTransport(handler))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: client.analyze("question", access_token=f"token-{i}", tenant_id=f"tenant-{i}"), range(8)))
    assert {(token, tenant) for token, tenant, _ in seen} == {(f"Bearer token-{i}", f"tenant-{i}") for i in range(8)}
    assert all(body == {"text": "question"} for _, _, body in seen)
    assert "authorization" not in client._client.headers
    for token, tenant in ((None, "tenant"), ("token", None), ("token\r\ninjected", "tenant")):
        with pytest.raises(AnalysisApiCredentialsError):
            client.analyze("question", access_token=token, tenant_id=tenant)
    assert len(seen) == 8
    client._client.close()


def test_business_without_credentials_cannot_create_or_execute_job():
    client = Mock()
    for request in ("question", BusinessApiRequest("question")):
        with pytest.raises(AnalysisApiCredentialsError):
            create_business_api_job(request, client)
        with pytest.raises(AnalysisApiCredentialsError):
            ApiJobAdapter(client).run(request, 10)
    client.analyze.assert_not_called()
    assert "secret" not in repr(BusinessApiRequest("q", access_token="secret", tenant_id="a"))


def test_streamlit_entry_guards_execute_before_any_ui_or_job_work():
    # Execute the actual function bodies without importing/running the entire app.
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    names = {"start_supervisor_analysis", "start_business_api_analysis", "render_supervisor", "render_business"}
    nodes = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names}
    ui = Mock()
    from types import SimpleNamespace
    from business_auth import capture_business_request
    ui.session_state = {}
    ui.user = SimpleNamespace(is_logged_in=False)
    namespace = {"st": ui, "capture_business_request": capture_business_request}
    for name in names - {"render_business"}:
        exec(compile(ast.Module(body=[nodes[name]], type_ignores=[]), "app.py", "exec"), namespace)
    with pytest.raises(AuthenticationRequired):
        namespace["start_supervisor_analysis"]("q")
    with pytest.raises(AnalysisApiCredentialsError):
        namespace["start_business_api_analysis"]("q")
    namespace["render_supervisor"]()
    ui.warning.assert_called_once()
    ui.button.assert_not_called()
    business_button = next(node for node in ast.walk(nodes["render_business"]) if isinstance(node, ast.Call)
                           and any(kw.arg == "key" and isinstance(kw.value, ast.Constant) and kw.value.value == "business_start" for kw in node.keywords))
    assert any(kw.arg == "disabled" for kw in business_button.keywords)


def test_admin_owned_configuration_has_no_default_users(monkeypatch, tmp_path):
    monkeypatch.setenv("INDAI_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("INDAI_OIDC_AUDIENCE", "indai-api")
    monkeypatch.setenv("INDAI_OIDC_JWKS_URL", ISSUER + "/keys")
    path = tmp_path / "memberships.json"
    monkeypatch.setenv("INDAI_MEMBERSHIPS_FILE", str(path))
    path.write_text("[]", encoding="utf-8")
    configured = build_security()
    try:
        assert configured.memberships.resolve(PrincipalId(ISSUER, "analyst"), A) is None
    finally:
        configured.authenticator.close()
    path.write_text(json.dumps([{"issuer": ISSUER, "subject": "analyst", "tenant_id": "tenant-a", "roles": ["*"]}]), encoding="utf-8")
    with pytest.raises(IdentityInfrastructureError):
        build_security()
