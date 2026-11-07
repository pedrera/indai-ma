"""Offline development configuration checks; never start Docker or change trust."""
import json
from pathlib import Path
import ssl
import sys
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
import pytest

from dev.keycloak import prepare_local, trust_python
from security.config import OIDCConfig
from security.errors import PermissionDenied, TenantAccessDenied
from security.memberships import ConfiguredMembershipDirectory, TenantMembership
from security.models import PrincipalId, SecurityContext, TenantId
from security.permissions import Permission
from security.policy import TenantAuthorizer
from security.roles import Role


HERE = Path(__file__).resolve().parents[1] / "dev/keycloak"
REALM = json.loads((HERE / "realm/indai-ma-dev-realm.json").read_text(encoding="utf-8"))
MEMBERSHIPS = json.loads((HERE / "backend-memberships.json").read_text(encoding="utf-8"))


def test_client_grants_are_bounded_and_api_is_audience_only():
    streamlit, api = REALM["clients"]
    assert REALM["realm"] == "indai-ma-dev"
    assert REALM["defaultSignatureAlgorithm"] == "RS256"
    assert streamlit["clientId"] == "indai-ma-streamlit"
    assert streamlit["standardFlowEnabled"] and not streamlit["publicClient"]
    assert streamlit["attributes"]["pkce.code.challenge.method"] == "S256"
    assert streamlit["attributes"]["access.token.header.type.rfc9068"] == "true"
    assert streamlit["redirectUris"] == ["http://localhost:8501/oauth2callback"]
    assert streamlit["attributes"]["post.logout.redirect.uris"] == streamlit["redirectUris"][0]
    assert streamlit["webOrigins"] == ["http://localhost:8501"]
    assert api["clientId"] == "indai-ma-api" and api["bearerOnly"]
    assert not api["standardFlowEnabled"]
    for client in (streamlit, api):
        for grant in ("implicitFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled", "fullScopeAllowed"):
            assert not client[grant]
    mappers = streamlit["protocolMappers"]
    assert mappers[0]["config"]["included.client.audience"] == "indai-ma-api"
    assert mappers[1]["config"]["claim.name"] == "client_id"
    assert mappers[1]["config"]["claim.value"] == streamlit["clientId"]
    subjects = [mapper for mapper in mappers if mapper["protocolMapper"] == "oidc-sub-mapper"]
    assert len(subjects) == 1
    assert subjects[0]["config"]["access.token.claim"] == "true"
    assert "claim.value" not in subjects[0]["config"]  # Real authenticated user ID, never a fixed subject.
    assert all("role" not in mapper["protocolMapper"] for mapper in mappers)


def test_secrets_are_placeholders_and_dev_subjects_are_unique():
    assert REALM["clients"][0]["secret"] == "${INDAI_STREAMLIT_CLIENT_SECRET}"
    assert {user["username"] for user in REALM["users"]} == {"analyst-a", "analyst-b", "viewer-a", "outsider"}
    assert len({user["id"] for user in REALM["users"]}) == 4
    for user in REALM["users"]:
        assert user["credentials"][0]["value"].startswith("${KC_")
        assert "realmRoles" not in user and "clientRoles" not in user
        assert "email" not in user


@pytest.mark.parametrize("name, tenant, allowed", [
    ("analyst-a", "tenant-alpha", True), ("analyst-a", "tenant-beta", False),
    ("analyst-b", "tenant-beta", True), ("analyst-b", "tenant-alpha", False),
    ("viewer-a", "tenant-alpha", False), ("outsider", "tenant-alpha", False),
])
def test_backend_authorizes_only_explicit_development_memberships(name, tenant, allowed):
    records = [TenantMembership(PrincipalId(row["issuer"], row["subject"]), TenantId(row["tenant_id"]),
                               frozenset(Role(role) for role in row["roles"])) for row in MEMBERSHIPS]
    source = ConfiguredMembershipDirectory(records)
    subject = next(user["id"] for user in REALM["users"] if user["username"] == name)
    principal = PrincipalId(prepare_local.ISSUER, subject)
    membership = source.resolve(principal, TenantId(tenant))
    scope = SecurityContext(principal, TenantId(tenant), membership.permissions if membership else frozenset(), "test")
    if allowed:
        assert TenantAuthorizer(source).authorize(scope, Permission.ANALYSIS_RUN) is None
    else:
        with pytest.raises(PermissionDenied):
            TenantAuthorizer(source).authorize(scope, Permission.ANALYSIS_RUN)


def test_external_preparation_is_private_consistent_and_never_installs_trust(tmp_path, monkeypatch):
    # An external temporary directory, not a project secrets file.
    root = prepare_local.prepare(tmp_path / "private")
    values = dict(line.split("=", 1) for line in (root / "compose.env").read_text().splitlines())
    assert len(values["KC_ANALYST_A_PASSWORD"].strip('"')) >= 32
    assert values["KC_ANALYST_A_PASSWORD"] != values["KC_ANALYST_B_PASSWORD"]
    frontend = (root / "streamlit.toml").read_text()
    assert 'client_id = "indai-ma-streamlit"' in frontend
    assert values["INDAI_STREAMLIT_CLIENT_SECRET"] in frontend
    assert prepare_local.ISSUER + "/.well-known/openid-configuration" in frontend
    assert json.loads((root / "backend-memberships.json").read_text()) == MEMBERSHIPS
    OIDCConfig(prepare_local.ISSUER, "indai-ma-api", prepare_local.ISSUER + "/protocol/openid-connect/certs")
    ca = x509.load_pem_x509_certificate((root / "ca/ca-cert.pem").read_bytes())
    leaf = x509.load_pem_x509_certificate((root / "tls/localhost-cert.pem").read_bytes())
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), leaf.signature_hash_algorithm)
    assert "localhost" in leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert leaf.not_valid_after_utc > datetime.now(timezone.utc)
    assert not (root / "tls/ca-key.pem").exists()
    # Exercise CA setup only against a fake venv bundle, never actual trust stores.
    fake_venv = tmp_path / "venv"
    fake_venv.mkdir()
    bundle = fake_venv / "bundle.pem"
    bundle.write_bytes(b"original roots\n")
    monkeypatch.setattr(sys, "prefix", str(fake_venv))
    monkeypatch.setattr(trust_python.certifi, "where", lambda: str(bundle))
    trust_python.configure(root / "ca/ca-cert.pem")
    assert (root / "ca/ca-cert.pem").read_bytes() in bundle.read_bytes()
    trust_python.configure(root / "ca/ca-cert.pem", remove=True)
    assert bundle.read_bytes() == b"original roots\n"
    bundle.write_bytes(b"changed by package update")
    with pytest.raises(ValueError):
        trust_python.configure(root / "ca/ca-cert.pem", remove=True)


def test_preparation_refuses_repository_paths_existing_directories_and_global_python(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        prepare_local.private_directory(HERE / "must-not-exist")
    with pytest.raises(ValueError):
        prepare_local.private_directory(tmp_path)
    with pytest.raises(ValueError):
        prepare_local.private_directory(Path("relative-path"))
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    with pytest.raises(ValueError):
        trust_python.configure(tmp_path / "nonexistent.pem")
