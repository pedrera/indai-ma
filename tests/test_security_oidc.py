"""Real RSA signatures with ephemeral test keys and offline JWKS transport."""
from time import time

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from security.config import OIDCConfig
from security.errors import AuthenticationRequired, InvalidCredentials, TenantAccessDenied, IdentityInfrastructureError
from security.memberships import ConfiguredMembershipDirectory
from security.models import PrincipalId, TenantId
from security.oidc import OIDCAuthenticator, MAX_JWKS_BYTES
from security.policy import SecurityContextFactory
from security.ports import Authenticator


ISSUER = "https://identity.example/realm"
JWKS_URL = ISSUER + "/certs"


@pytest.fixture(scope="module")
def keys():
    return [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)]


def public(key, kid="one", **changes):
    record = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**record, "kid": kid, "alg": "RS256", "use": "sig", **changes}


def token(key, *, claims=None, headers=None, remove=()):
    now = int(time())
    payload = {
        "iss": ISSUER, "sub": "subject", "aud": "indai-api", "exp": now + 300,
        "iat": now - 10, "jti": "test-token-id", "client_id": "test-client",
        **(claims or {}),
    }
    for name in remove:
        payload.pop(name, None)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "one", "typ": "at+jwt", **(headers or {})})


@pytest.fixture
def harness(keys):
    state = {"keys": [public(keys[0])], "status": 200, "requests": [], "time": 100.0}

    def handler(request):
        state["requests"].append(request)
        if state.get("error"):
            raise httpx.ConnectError("private network detail", request=request)
        return httpx.Response(state["status"], json={"keys": state["keys"]})

    auth = OIDCAuthenticator(
        OIDCConfig(ISSUER, "indai-api", JWKS_URL),
        transport=httpx.MockTransport(handler), clock=lambda: state["time"],
    )
    yield auth, state
    auth.close()


def test_valid_token_returns_only_validated_principal(keys, harness):
    auth, state = harness
    port: Authenticator = auth
    result = port.authenticate(token(keys[0], claims={"roles": ["tenant_admin"], "tenant": "anything", "permissions": ["*"]}))
    assert result == PrincipalId(ISSUER, "subject")
    assert not hasattr(result, "permissions")
    assert not hasattr(result, "tenant_id")
    assert str(state["requests"][0].url) == JWKS_URL
    assert "authorization" not in state["requests"][0].headers
    factory = SecurityContextFactory(auth, ConfiguredMembershipDirectory([]))
    with pytest.raises(TenantAccessDenied):
        factory.create(token(keys[0]), TenantId("anything"), "op")


@pytest.mark.parametrize("claims", [
    {"iss": "https://other.example"}, {"aud": "other-api"},
    {"exp": 1}, {"nbf": int(time()) + 1000}, {"iat": int(time()) + 1000},
    {"sub": ""}, {"sub": " \t"}, {"sub": 123}, {"sub": None},
    {"client_id": ""}, {"jti": ""}, {"exp": True}, {"iat": "1"},
    {"nbf": False}, {"exp": float("nan")}, {"exp": float("inf")},
    {"exp": int(time()) + 10, "iat": int(time()) + 10},
])
def test_invalid_claims_are_denied(keys, harness, claims):
    auth, _ = harness
    with pytest.raises(InvalidCredentials):
        auth.authenticate(token(keys[0], claims=claims))


@pytest.mark.parametrize("name", ["iss", "aud", "exp", "iat", "sub", "client_id", "jti"])
def test_required_profile_claims_cannot_be_omitted(keys, harness, name):
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(token(keys[0], remove=(name,)))


def test_invalid_signature_is_denied(keys, harness):
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(token(keys[1]))


def test_missing_kid_is_denied_before_network(keys, harness):
    encoded = jwt.encode({"sub": "subject"}, keys[0], algorithm="RS256", headers={"typ": "at+jwt"})
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(encoded)
    assert not harness[1]["requests"]


@pytest.mark.parametrize("typ", ["JWT", "id+jwt", "Bearer", None])
def test_id_tokens_and_other_profiles_are_denied_before_network(keys, harness, typ):
    auth, state = harness
    with pytest.raises(InvalidCredentials):
        auth.authenticate(token(keys[0], headers={"typ": typ}))
    assert not state["requests"]


def test_application_at_jwt_and_array_audience_are_supported(keys, harness):
    assert harness[0].authenticate(token(keys[0], headers={"typ": "application/at+jwt"}, claims={"aud": ["other", "indai-api"]})) == PrincipalId(ISSUER, "subject")


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
def test_unapproved_algorithms_are_denied_without_jwks(harness, algorithm):
    auth, state = harness
    encoded = jwt.encode({"sub": "subject"}, "test-secret-is-at-least-thirty-two-bytes" if algorithm == "HS256" else None,
                         algorithm=algorithm, headers={"kid": "one", "typ": "at+jwt"})
    with pytest.raises(InvalidCredentials):
        auth.authenticate(encoded)
    assert not state["requests"]


@pytest.mark.parametrize("name", ["jku", "x5u", "jwk", "x5c", "crit", "b64"])
def test_token_headers_cannot_supply_urls_keys_or_extensions(keys, harness, name):
    auth, state = harness
    with pytest.raises(InvalidCredentials):
        auth.authenticate(token(keys[0], headers={name: False if name == "b64" else "https://attacker.invalid/key"}))
    assert not state["requests"]


@pytest.mark.parametrize("kid", ["", " ", "x" * 257])
def test_invalid_kid_is_denied_without_network(keys, harness, kid):
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(token(keys[0], headers={"kid": kid}))
    assert not harness[1]["requests"]


@pytest.mark.parametrize("credentials", ["bad", "a.b.c", " ", 123, b"bytes", "x" * 16385])
def test_malformed_tokens_are_denied(harness, credentials):
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(credentials)


@pytest.mark.parametrize("credentials", [None, ""])
def test_absent_credentials_require_authentication(harness, credentials):
    with pytest.raises(AuthenticationRequired):
        harness[0].authenticate(credentials)


def test_rotation_cache_expiry_and_removed_keys(keys, harness):
    auth, state = harness
    assert auth.authenticate(token(keys[0]))
    assert auth.authenticate(token(keys[0]))
    assert len(state["requests"]) == 1
    state["keys"] = [public(keys[1], "two")]
    # Cooldown bounds unknown-kid refresh amplification.
    with pytest.raises(InvalidCredentials):
        auth.authenticate(token(keys[1], headers={"kid": "two"}))
    assert len(state["requests"]) == 1
    state["time"] += 11
    assert auth.authenticate(token(keys[1], headers={"kid": "two"}))
    assert len(state["requests"]) == 2
    with pytest.raises(InvalidCredentials):
        auth.authenticate(token(keys[0]))
    state["time"] += 301
    assert auth.authenticate(token(keys[1], headers={"kid": "two"}))
    assert len(state["requests"]) == 3


def test_unknown_kid_never_selects_another_key(keys, harness):
    with pytest.raises(InvalidCredentials):
        harness[0].authenticate(token(keys[0], headers={"kid": "missing"}))


def test_jwks_failure_has_no_stale_expired_fallback_and_is_rate_limited(keys, harness):
    auth, state = harness
    auth.authenticate(token(keys[0]))
    state["time"] += 301
    state["error"] = True
    for _ in range(2):
        with pytest.raises(IdentityInfrastructureError) as caught:
            auth.authenticate(token(keys[0]))
        assert "private network detail" not in str(caught.value)
    assert len(state["requests"]) == 2
    state["time"] += 11
    state["error"] = False
    assert auth.authenticate(token(keys[0]))


@pytest.mark.parametrize("status", [301, 302, 307, 401, 404, 500])
def test_non_success_and_redirects_are_denied(keys, harness, status):
    auth, state = harness
    state["status"] = status
    with pytest.raises(IdentityInfrastructureError):
        auth.authenticate(token(keys[0]))
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("changes", [{"alg": "HS256"}, {"kty": "oct"}, {"use": "enc"}, {"key_ops": ["sign"]}, {"d": "private"}, {"kid": ""}, {"n": "bad"}])
def test_ineligible_or_invalid_keys_are_denied(keys, harness, changes):
    auth, state = harness
    state["keys"] = [public(keys[0], **changes)]
    with pytest.raises(IdentityInfrastructureError):
        auth.authenticate(token(keys[0]))


def test_duplicate_kid_and_weak_keys_are_denied(keys, harness):
    auth, state = harness
    state["keys"] = [public(keys[0]), public(keys[1])]
    with pytest.raises(IdentityInfrastructureError):
        auth.authenticate(token(keys[0]))
    state["time"] += 11
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    state["keys"] = [public(weak)]
    # The token is signed by a strong key: rejection must happen while loading
    # the weak public key, before attempting signature verification.
    with pytest.raises(IdentityInfrastructureError):
        auth.authenticate(token(keys[0]))


@pytest.mark.parametrize("body", [b"invalid-json", b"[]", b"{}", b'{"keys":[]}', b'{"keys":{}}', b"x" * (MAX_JWKS_BYTES + 1)], ids=["json", "array", "missing", "empty", "mapping", "oversized"])
def test_invalid_or_oversized_jwks_is_denied(keys, body):
    auth = OIDCAuthenticator(OIDCConfig(ISSUER, "indai-api", JWKS_URL), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    try:
        with pytest.raises(IdentityInfrastructureError):
            auth.authenticate(token(keys[0]))
    finally:
        auth.close()


def test_different_issuers_never_share_cache_even_with_same_kid(keys, harness):
    original, state = harness
    original.authenticate(token(keys[0]))
    config = OIDCConfig("https://second.example", "indai-api", "https://second.example/keys")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"keys": [public(keys[1])]})
    second = OIDCAuthenticator(config, transport=httpx.MockTransport(handler))
    try:
        assert second.authenticate(token(keys[1], claims={"iss": config.issuer})) == PrincipalId(config.issuer, "subject")
        with pytest.raises(InvalidCredentials):
            second.authenticate(token(keys[0]))
        assert len(calls) == 1
        assert len(state["requests"]) == 1
    finally:
        second.close()


@pytest.mark.parametrize("changes", [
    {"issuer": ""}, {"audience": ""}, {"jwks_url": ""},
    {"issuer": "http://localhost"}, {"jwks_url": "file:///keys"},
    {"jwks_url": "https://user:secret@example/keys"}, {"jwks_url": "https://example/keys#fragment"},
    {"jwks_url": "https://example/keys?next=internal"},
    {"algorithms": ("HS256",)}, {"algorithms": ("RS256", "HS256")},
    {"algorithms": ["RS256"]}, {"leeway_seconds": -1}, {"leeway_seconds": 61},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"cache_ttl_seconds": 0}, {"refresh_cooldown_seconds": 0}, {"leeway_seconds": True},
])
def test_incomplete_or_unsafe_configuration_fails_before_network(changes):
    with pytest.raises((TypeError, ValueError)):
        OIDCConfig(**{**dict(issuer=ISSUER, audience="indai-api", jwks_url=JWKS_URL), **changes})


def test_frozen_config_and_bounded_leeway(keys):
    from dataclasses import FrozenInstanceError
    config = OIDCConfig(ISSUER, "indai-api", JWKS_URL, leeway_seconds=0)
    with pytest.raises(FrozenInstanceError):
        config.issuer = "https://attacker.example"
    auth = OIDCAuthenticator(config, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"keys": [public(keys[0])]})))
    try:
        with pytest.raises(InvalidCredentials):
            auth.authenticate(token(keys[0], claims={"exp": int(time()) - 1}))
    finally:
        auth.close()


def test_valid_nbf_and_small_clock_skew_are_accepted(keys, harness):
    assert harness[0].authenticate(token(keys[0], claims={"nbf": int(time()) - 1}))
    assert harness[0].authenticate(token(keys[0], claims={"exp": int(time()) - 1}))


def test_redirect_location_is_never_followed(keys):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
    auth = OIDCAuthenticator(OIDCConfig(ISSUER, "indai-api", JWKS_URL), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(IdentityInfrastructureError):
            auth.authenticate(token(keys[0]))
        assert [str(request.url) for request in requests] == [JWKS_URL]
    finally:
        auth.close()


def test_timeout_and_key_count_limits_fail_closed(keys):
    def timeout(request):
        raise httpx.ReadTimeout("private timeout detail", request=request)
    for handler in (timeout, lambda _: httpx.Response(200, json={"keys": [public(keys[0])] * 65})):
        auth = OIDCAuthenticator(OIDCConfig(ISSUER, "indai-api", JWKS_URL), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(IdentityInfrastructureError):
                auth.authenticate(token(keys[0]))
        finally:
            auth.close()


def test_concurrent_validation_shares_only_issuer_scoped_key_cache(keys, harness):
    from concurrent.futures import ThreadPoolExecutor
    auth, state = harness
    encoded = token(keys[0])
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(auth.authenticate, [encoded] * 12))
    assert results == [PrincipalId(ISSUER, "subject")] * 12
    assert len(state["requests"]) == 1
