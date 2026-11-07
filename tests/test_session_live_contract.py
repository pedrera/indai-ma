"""Session GET contract and explicit CA composition; no real logins or analyses."""
import json
import ssl

import certifi
import pytest

from api.composition import build_security
from dev.keycloak import interactive_pkce, prepare_local
from security.errors import IdentityInfrastructureError
from tests.test_security_http import environment, signing_key, signed, assert_no_execution, ISSUER


@pytest.mark.parametrize("tenant", [None, "tenant-b", "tenant/invalid", " "])
def test_session_tenant_header_cannot_change_identity_listing(environment, signing_key, tenant):
    headers = {"Authorization": "Bearer " + signed(signing_key, "second")}
    if tenant is not None:
        headers["X-Tenant-ID"] = tenant
    response = environment[0].get("/api/v1/session", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [t["tenant_id"] for t in response.json()["tenants"]] == ["tenant-b"]
    assert_no_execution(environment)


@pytest.mark.parametrize("subject,expected,can_analyze", [
    ("analyst", {"tenant-a", "tenant-b"}, True),
    ("second", {"tenant-b"}, True), ("viewer", {"tenant-a"}, False), ("absent", set(), False),
])
def test_session_listing_is_not_analysis_permission(environment, signing_key, subject, expected, can_analyze):
    response = environment[0].get("/api/v1/session", headers={"Authorization": "Bearer " + signed(signing_key, subject)})
    assert response.status_code == 200
    tenants = response.json()["tenants"]
    assert {t["tenant_id"] for t in tenants} == expected
    assert any("analysis:run" in t["permissions"] for t in tenants) == can_analyze
    assert_no_execution(environment)


def test_session_outage_is_503_without_private_details(environment, signing_key):
    environment[2]["fail"] = True
    response = environment[0].get("/api/v1/session", headers={"Authorization": "Bearer " + signed(signing_key)})
    assert response.status_code == 503
    assert response.json()["code"] == "identity_unavailable"
    assert "private" not in response.text and ISSUER not in response.text
    assert_no_execution(environment)


@pytest.mark.parametrize("authorization", [None, "Bearer", "Bearer ", "Basic invalid", "Bearer invalid"])
def test_session_missing_or_malformed_credentials(environment, authorization):
    headers = {"X-Tenant-ID": "tenant/invalid"}
    if authorization is not None:
        headers["Authorization"] = authorization
    response = environment[0].get("/api/v1/session", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_no_execution(environment)


def configured_env(tmp_path, monkeypatch):
    path = tmp_path / "memberships.json"
    path.write_text("[]")
    for name, value in {"INDAI_OIDC_ISSUER": ISSUER, "INDAI_OIDC_AUDIENCE": "indai-api",
                        "INDAI_OIDC_JWKS_URL": ISSUER + "/keys", "INDAI_MEMBERSHIPS_FILE": str(path)}.items():
        monkeypatch.setenv(name, value)


def test_explicit_ca_is_verified_and_does_not_modify_default_bundle(tmp_path, monkeypatch):
    from pathlib import Path
    from api import composition
    configured_env(tmp_path, monkeypatch)
    prepare_local.tls_files(tmp_path)
    ca_file = tmp_path / "ca/ca-cert.pem"
    monkeypatch.setenv("INDAI_OIDC_CA_FILE", str(ca_file))
    before = Path(certifi.where()).read_bytes()
    original_transport = composition.httpx.HTTPTransport
    captured = {}
    def transport(**kwargs):
        captured.update(kwargs)
        return original_transport(**kwargs)
    monkeypatch.setattr(composition.httpx, "HTTPTransport", transport)
    security = build_security()
    try:
        context = captured["verify"]
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert captured["trust_env"] is False
        assert ssl.PEM_cert_to_DER_cert(ca_file.read_text()) in context.get_ca_certs(binary_form=True)
        assert Path(certifi.where()).read_bytes() == before
    finally:
        security.authenticator.close()


@pytest.mark.parametrize("ca_value", ["relative.pem", "missing", "invalid"])
def test_unusable_ca_configuration_fails_closed(tmp_path, monkeypatch, ca_value):
    configured_env(tmp_path, monkeypatch)
    path = tmp_path / "bad.pem"
    path.write_text("not a certificate")
    value = {"relative.pem": "relative.pem", "missing": str(tmp_path / "missing.pem"), "invalid": str(path)}[ca_value]
    monkeypatch.setenv("INDAI_OIDC_CA_FILE", value)
    with pytest.raises(IdentityInfrastructureError):
        build_security()


def test_loopback_session_checker_never_posts_or_follows_redirects(monkeypatch):
    import httpx
    from security.models import PrincipalId, TenantId
    from security.memberships import ConfiguredMembershipDirectory, TenantMembership
    from security.roles import Role
    principal = PrincipalId(interactive_pkce.ISSUER, interactive_pkce.CASES["analyst-a"][0])
    directory = ConfiguredMembershipDirectory([TenantMembership(principal, TenantId("tenant-alpha"), frozenset({Role.ANALYST}))])
    permissions = sorted(p.value for p in directory.list_for_principal(principal)[0].permissions)
    seen = []
    def handler(request):
        assert request.method == "GET" and str(request.url) == "http://127.0.0.1:8000/api/v1/session"
        seen.append(request.headers.get("x-tenant-id"))
        return httpx.Response(200, headers={"Cache-Control": "no-store"}, json={
            "issuer": principal.issuer, "subject": principal.subject, "expires_at": 100,
            "tenants": [{"tenant_id": "tenant-alpha", "permissions": permissions}]})
    original = httpx.Client
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(interactive_pkce.httpx, "Client", client)
    diagnostic = interactive_pkce.Diagnostics()
    with diagnostic.phase("TENANT_AUTHORIZATION"):
        result = interactive_pkce.check_session_endpoint("PRIVATE_TOKEN_SENTINEL", principal, 100, directory, diagnostic)
    assert len(seen) == 4 and "tenant-beta" in seen
    assert result["result"] == "PASSED" and "PRIVATE_" not in json.dumps(result)
