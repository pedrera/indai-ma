"""Security boundaries of the opt-in callback; no live login or services."""
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from dev.keycloak.interactive_pkce import ISSUER, REDIRECT, authorization_url, callback_code, challenge


def test_pkce_matches_rfc7636_vector_and_authorization_has_required_binding():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    url = urlsplit(authorization_url("analyst-a", "random-state", "random-nonce", verifier))
    q = parse_qs(url.query)
    assert url.scheme == "https" and url.netloc == "localhost:8443"
    assert q["redirect_uri"] == [REDIRECT]
    assert q["response_type"] == ["code"] and q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["random-state"] and q["nonce"] == ["random-nonce"]
    assert q["prompt"] == ["login"]
    assert verifier not in url.query


@pytest.mark.parametrize("query", [
    "code=one", "code=one&state=wrong", "code=&state=expected",
    "code=one&code=two&state=expected", "code=one&state=expected&state=expected",
    "error=access_denied&state=expected", "code=one&state=expected&unexpected=value",
    "code=one&state=expected&iss=https%3A%2F%2Fevil.example",
])
def test_bad_callback_cannot_reach_token_exchange(query):
    with pytest.raises(ValueError):
        callback_code(query, "expected")


def test_callback_accepts_only_matching_state_and_optional_exact_issuer():
    assert callback_code(urlencode({"code": "one", "state": "expected", "iss": ISSUER}), "expected") == "one"


@pytest.fixture
def live_flow(monkeypatch):
    import ssl
    import time
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from dev.keycloak import interactive_pkce as module

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public.update(kid="test-key", use="sig", alg="RS256")
    state = {"fault": None, "posts": 0, "nonce": "PRIVATE_NONCE_SENTINEL",
             "subject": module.CASES["analyst-a"][0], "tokens": None}
    ctx = ssl.create_default_context()
    monkeypatch.setattr(module.ssl, "create_default_context", lambda **kwargs: ctx)

    def handler(request):
        if request.method == "POST":
            state["posts"] += 1
            if state["fault"] == "transport":
                raise httpx.ConnectError("PRIVATE_PASSWORD_SENTINEL", request=request)
            if state["fault"] == "tls":
                try:
                    raise ssl.SSLCertVerificationError("PRIVATE_SECRET_SENTINEL")
                except ssl.SSLError as cause:
                    raise httpx.ConnectError("PRIVATE_CODE_SENTINEL", request=request) from cause
            if state["fault"] == "status":
                return httpx.Response(401, text="PRIVATE_RESPONSE_SENTINEL")
            now = int(time.time())
            claims = dict(iss=ISSUER, sub=state["subject"], aud=module.AUDIENCE,
                          exp=now+300, iat=now, jti="PRIVATE_JTI_SENTINEL", client_id=module.CLIENT)
            claims.update(state.get("claim_changes", {}))
            for name in state.get("remove_claims", []):
                claims.pop(name, None)
            access = jwt.encode(claims, key, algorithm="RS256", headers={"typ": state.get("typ", "at+jwt"), "kid": "test-key"})
            identity = jwt.encode({**claims, "aud": module.CLIENT, "nonce": state["nonce"]},
                                  key, algorithm="RS256", headers={"typ": "JWT", "kid": "test-key"})
            state["tokens"] = {"access_token": access if state["fault"] != "jwt" else "PRIVATE_INVALID_JWT_SENTINEL",
                               "id_token": identity, "refresh_token": "PRIVATE_REFRESH_SENTINEL", "token_type": "Bearer"}
            return httpx.Response(200, json=state["tokens"])
        return httpx.Response(200, json={"keys": [public]})

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(module.httpx, "HTTPTransport", lambda **kwargs: transport)
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: original_client(
        **{**kwargs, "transport": transport}))
    return module, state, ctx


def run_flow(flow, capsys):
    import json
    from pathlib import Path
    module, state, ctx = flow
    diagnostic = module.Diagnostics()
    result = None
    try:
        result = module.execute_attempt(
            urlencode({"code": "PRIVATE_CODE_SENTINEL", "state": "PRIVATE_STATE_SENTINEL"}),
            "PRIVATE_STATE_SENTINEL", "analyst-a", "PRIVATE_NONCE_SENTINEL",
            "PRIVATE_VERIFIER_SENTINEL", "PRIVATE_CLIENT_SECRET_SENTINEL", ctx,
            {"token_endpoint": ISSUER + "/protocol/openid-connect/token"},
            Path(__file__).resolve().parents[1] / "dev/keycloak", diagnostic)
    except module.DiagnosticFailure:
        pass
    diagnostic.emit()
    output = capsys.readouterr().out
    assert "PRIVATE_" not in output
    for token in (state["tokens"] or {}).values():
        if isinstance(token, str) and token != "Bearer":
            assert token not in output
    return json.loads(output), result


@pytest.mark.parametrize("fault,phase,category,status", [
    ("transport", "TOKEN_EXCHANGE", "TRANSPORT_ERROR", None),
    ("tls", "TOKEN_EXCHANGE", "TLS_ERROR", None),
    ("status", "TOKEN_EXCHANGE", "HTTP_STATUS_ERROR", 401),
    ("jwt", "ACCESS_TOKEN_VALIDATION", "INVALID_CREDENTIALS", None),
    ("principal", "PRINCIPAL_MATCH", "INVALID_VALUE", None),
    ("nonce", "ID_TOKEN_BINDING", "INVALID_VALUE", None),
    ("denial", "TENANT_AUTHORIZATION", "PERMISSION_DENIED", None),
])
def test_failures_identify_phase_and_do_not_leak(live_flow, monkeypatch, capsys, fault, phase, category, status):
    module, state, _ = live_flow
    state["fault"] = fault
    if fault == "principal":
        state["subject"] = "different-subject"
    if fault == "nonce":
        state["nonce"] = "PRIVATE_WRONG_NONCE_SENTINEL"
    if fault == "denial":
        def deny(*args):
            raise module.PermissionDenied("PRIVATE_PERMISSION_SENTINEL")
        monkeypatch.setattr(module.TenantAuthorizer, "authorize", deny)
    report, result = run_flow(live_flow, capsys)
    assert result is None
    records = report["phases"]
    i = module.PHASES.index(phase)
    assert all(r["result"] == "PASSED" for r in records[:i])
    assert records[i]["result"] == "FAILED"
    assert records[i]["code"].endswith("." + category)
    assert records[i]["http_status"] == status
    assert all(r["result"] == "NOT_REACHED" for r in records[i+1:])
    assert all(r["duration_ms"] >= 0 for r in records[:i+1])


def test_real_crypto_fixture_success_preserves_expected_tenant_denial(live_flow, capsys):
    report, result = run_flow(live_flow, capsys)
    assert all(r["result"] == "PASSED" for r in report["phases"])
    assert result["ID_token_rejected"] is True
    tenants = result["analysis_permission_only_no_execution"]
    assert tenants["tenant-alpha"]["result"] == "allowed"
    assert tenants["tenant-beta"]["result"] == "denied"


def test_unexpected_exception_name_and_message_never_escape(capsys):
    from dev.keycloak.interactive_pkce import Diagnostics, DiagnosticFailure
    class PRIVATE_EXCEPTION_SENTINEL(Exception):
        pass
    diagnostic = Diagnostics()
    with pytest.raises(DiagnosticFailure):
        with diagnostic.phase("CALLBACK_VALIDATION"):
            raise PRIVATE_EXCEPTION_SENTINEL("PRIVATE_PASSWORD_SENTINEL")
    diagnostic.emit()
    text = capsys.readouterr().out
    assert "PRIVATE_" not in text
    assert '"exception_class": "UnexpectedError"' in text


def test_invalid_callback_stops_before_exchange(live_flow, capsys):
    from pathlib import Path
    module, state, ctx = live_flow
    diagnostic = module.Diagnostics()
    with pytest.raises(module.DiagnosticFailure):
        module.execute_attempt("code=PRIVATE_CODE_SENTINEL&state=wrong", "expected", "analyst-a",
                               "PRIVATE_NONCE_SENTINEL", "PRIVATE_VERIFIER_SENTINEL",
                               "PRIVATE_SECRET_SENTINEL", ctx, {}, Path("unused"), diagnostic)
    assert state["posts"] == 0
    assert diagnostic.records["CALLBACK_VALIDATION"]["result"] == "FAILED"
    assert all(diagnostic.records[p]["result"] == "NOT_REACHED" for p in module.PHASES[1:])
    diagnostic.emit()
    assert "PRIVATE_" not in capsys.readouterr().out


def test_token_mapping_is_cleared_on_validation_failure(live_flow):
    from pathlib import Path
    module, _, _ = live_flow
    tokens = {"access_token": "PRIVATE_INVALID_JWT_SENTINEL", "id_token": "PRIVATE_ID_SENTINEL", "token_type": "Bearer"}
    with pytest.raises(module.DiagnosticFailure):
        module.validate_pair(tokens, "analyst-a", "PRIVATE_NONCE_SENTINEL", Path("ca"), Path("directory"), module.Diagnostics())
    assert tokens == {}


@pytest.mark.parametrize("changes,removed,typ,check", [
    ({}, [], "JWT", "header_typ_access"),
    ({}, ["client_id"], "at+jwt", "required_client_id_present"),
    ({"aud": "PRIVATE_OTHER_AUDIENCE_SENTINEL"}, [], "at+jwt", "audience_matches"),
    ({"iss": "https://PRIVATE_OTHER_ISSUER_SENTINEL.example"}, [], "at+jwt", "issuer_matches"),
    ({"exp": 1}, [], "at+jwt", "exp_time_valid"),
    ({"client_id": "PRIVATE_OTHER_CLIENT_SENTINEL"}, [], "at+jwt", "client_id_matches"),
])
def test_rejected_claim_diagnostics_are_safe_and_cannot_authorize(live_flow, capsys, changes, removed, typ, check):
    module, state, _ = live_flow
    state.update(claim_changes=changes, remove_claims=removed, typ=typ)
    report, result = run_flow(live_flow, capsys)
    phase = report["phases"][2]
    # C1.3 does not bind the specific client_id value; the existing separate
    # client check rejects it after C1.3, so inspect the forensic helper directly.
    if check == "client_id_matches":
        checks = module.rejected_access_diagnostics(state["tokens"]["access_token"], live_flow[2], "analyst-a")
        assert checks[check] is False
        assert "PRIVATE_" not in __import__('json').dumps(checks)
    else:
        assert phase["rejected_access_checks"][check] is False
        assert phase["rejected_access_checks"]["signature_verified"] is True
    assert phase["result"] == "FAILED" and result is None
    assert all(r["result"] == "NOT_REACHED" for r in report["phases"][3:])


def test_bad_signature_never_produces_claim_diagnostics(live_flow):
    import base64
    import httpx
    module, state, ctx = live_flow
    with httpx.Client() as c:
        c.post(ISSUER + "/token")
    encoded = state["tokens"]["access_token"]
    parts = encoded.split(".")
    signature = bytearray(base64.urlsafe_b64decode(parts[2] + "=="))
    signature[0] ^= 1
    parts[2] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    checks = module.rejected_access_diagnostics(".".join(parts), ctx, "analyst-a")
    assert checks["signature_verified"] is False
    assert "issuer_matches" not in checks and "subject_matches_case" not in checks
