"""Opt-in loopback Code+PKCE check. No application server, tokens or login logs."""
import argparse
from contextlib import contextmanager
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import secrets
import ssl
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from dev.keycloak.check_local import ISSUER, AUDIENCE, JWKS, CASES
from security.config import OIDCConfig
from security.errors import InvalidCredentials, PermissionDenied, TenantAccessDenied, IdentityInfrastructureError
from security.memberships import ConfiguredMembershipDirectory, TenantMembership
from security.models import PrincipalId, TenantId
from security.oidc import OIDCAuthenticator
from security.permissions import Permission
from security.policy import SecurityContextFactory, TenantAuthorizer
from security.roles import Role

CLIENT = "indai-ma-streamlit"
REDIRECT = "http://localhost:8501/oauth2callback"
START = "http://localhost:8501/start"

PHASES = ("CALLBACK_VALIDATION", "TOKEN_EXCHANGE", "ACCESS_TOKEN_VALIDATION",
          "PRINCIPAL_MATCH", "ID_TOKEN_BINDING", "TENANT_AUTHORIZATION")


class DiagnosticFailure(Exception):
    """No original exception, credential or traceback retained by the report."""


def classify_error(error):
    # Exception names and HTTP codes are allowlisted; messages are never read.
    current, tls = error, False
    for _ in range(8):
        if current is None:
            break
        tls |= isinstance(current, ssl.SSLError)
        current = current.__cause__ or current.__context__
    if tls:
        return "TLS_ERROR", "SSLError", None
    for kind, code in ((httpx.HTTPStatusError, "HTTP_STATUS_ERROR"),
                       (httpx.TimeoutException, "TIMEOUT"), (httpx.TransportError, "TRANSPORT_ERROR"),
                       (InvalidCredentials, "INVALID_CREDENTIALS"),
                       (IdentityInfrastructureError, "IDENTITY_INFRASTRUCTURE_ERROR"),
                       (TenantAccessDenied, "TENANT_ACCESS_DENIED"),
                       (PermissionDenied, "PERMISSION_DENIED"),
                       (jwt.ExpiredSignatureError, "JWT_EXPIRED"),
                       (jwt.InvalidAudienceError, "JWT_AUDIENCE"),
                       (jwt.InvalidIssuerError, "JWT_ISSUER"),
                       (jwt.InvalidSignatureError, "JWT_SIGNATURE"),
                       (jwt.MissingRequiredClaimError, "JWT_REQUIRED_CLAIM"),
                       (jwt.PyJWTError, "JWT_ERROR"), (KeyError, "MISSING_FIELD"),
                       (ValueError, "INVALID_VALUE"), (TypeError, "INVALID_TYPE")):
        if isinstance(error, kind):
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            return code, kind.__name__, status if isinstance(status, int) and 100 <= status <= 599 else None
    return "UNEXPECTED_ERROR", "UnexpectedError", None


class Diagnostics:
    def __init__(self):
        self.attempt_id = secrets.token_hex(12)
        self.records = {name: {"phase": name, "result": "NOT_REACHED", "code": "NOT_REACHED",
                               "exception_class": None, "http_status": None, "duration_ms": None}
                        for name in PHASES}
        self.active = None

    @contextmanager
    def phase(self, name):
        self.active = self.records[name]
        record = self.active
        started = time.monotonic()
        try:
            yield
        except Exception as error:
            category, kind, status = classify_error(error)
            record.update(result="FAILED", code=name + "." + record.get("check", "EXECUTION") + "." + category,
                          exception_class=kind, http_status=status or record["http_status"])
            raise DiagnosticFailure() from None
        else:
            record.update(result="PASSED", code=name + ".OK")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.active = None

    def step(self, name):
        self.active["check"] = name  # Call-site constants only, never untrusted values.

    def require(self, name, condition):
        self.step(name)
        if not condition:
            raise ValueError()  # Stable check name supplies context, not exception text.

    def http(self, status):
        self.active["http_status"] = status

    def emit(self):
        print(json.dumps({"attempt_id": self.attempt_id, "phases": list(self.records.values())}), flush=True)


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def callback_code(query, expected_state):
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if (any(len(v) != 1 for v in values.values()) or "error" in values
            or set(values) - {"code", "state", "session_state", "iss"}
            or not values.get("code", [""])[0]
            or not hmac.compare_digest(values.get("state", [""])[0], expected_state)
            or ("iss" in values and values["iss"] != [ISSUER])):
        raise ValueError("Invalid callback")
    return values["code"][0]


def authorization_url(case, state, nonce, verifier):
    return ISSUER + "/protocol/openid-connect/auth?" + urlencode({
        "client_id": CLIENT, "redirect_uri": REDIRECT, "response_type": "code",
        "response_mode": "query", "scope": "openid profile", "state": state,
        "nonce": nonce, "code_challenge": challenge(verifier),
        "code_challenge_method": "S256", "prompt": "login", "login_hint": case,
    })


def rejected_access_diagnostics(access, context, case):
    """Forensic booleans only. Never returns identity or an authorization result.

    Called only AFTER C1.3 rejects a token; its result cannot make it acceptable.
    Claim checks are reported only after independent RS256 signature verification.
    """
    checks = {}
    try:
        if not isinstance(access, str) or len(access) > 16384:
            return {"diagnostic_error": "TOKEN_SIZE_OR_TYPE"}
        header = jwt.get_unverified_header(access)
        checks["header_alg_rs256"] = header.get("alg") == "RS256"
        checks["header_typ_access"] = header.get("typ") in ("at+jwt", "application/at+jwt")
        checks["header_extensions_allowed"] = not any(k in header for k in ("jku", "x5u", "jwk", "x5c", "crit", "b64"))
        kid = header.get("kid")
        checks["kid_valid"] = isinstance(kid, str) and bool(kid.strip()) and len(kid) <= 256
        if not checks["header_alg_rs256"] or not checks["kid_valid"]:
            return checks
        with httpx.Client(verify=context, trust_env=False, timeout=5, follow_redirects=False) as client:
            with client.stream("GET", JWKS) as response:
                response.raise_for_status()
                data = bytearray()
                started = time.monotonic()
                for part in response.iter_bytes(chunk_size=8192):
                    data.extend(part)
                    if len(data) > 262144 or time.monotonic() - started >= 5:
                        return {**checks, "diagnostic_error": "JWKS_LIMIT"}
            records = json.loads(data)["keys"]
        if not isinstance(records, list) or not 1 <= len(records) <= 64:
            return {**checks, "diagnostic_error": "JWKS_LIMIT"}
        matches = [r for r in records if isinstance(r, dict) and r.get("kid") == kid
                   and r.get("kty") == "RSA" and r.get("use", "sig") == "sig" and r.get("alg", "RS256") == "RS256"
                   and not any(k in r for k in ("d", "p", "q", "dp", "dq", "qi", "oth"))
                   and ("key_ops" not in r or r["key_ops"] == ["verify"])]
        checks["kid_unique_eligible_key"] = len(matches) == 1
        if len(matches) != 1:
            return checks
        key = jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key
        checks["rsa_key_at_least_2048"] = key.key_size >= 2048
        if not checks["rsa_key_at_least_2048"]:
            return checks
        # Diagnostic-only cryptographic decoding. This is never authentication:
        # it cannot return a principal, and the original rejection is re-raised.
        try:
            claims = jwt.decode(access, key, algorithms=["RS256"], options={
                "verify_signature": True, "verify_iss": False, "verify_aud": False,
                "verify_exp": False, "verify_iat": False, "verify_nbf": False,
                "verify_sub": False, "verify_jti": False, "require": []})
        except jwt.InvalidSignatureError:
            return {**checks, "signature_verified": False}
        checks["signature_verified"] = True
        for name in ("iss", "sub", "aud", "exp", "iat", "jti", "client_id"):
            checks["required_" + name + "_present"] = name in claims
        checks["issuer_matches"] = claims.get("iss") == ISSUER
        audience = claims.get("aud")
        checks["audience_matches"] = (audience == AUDIENCE if isinstance(audience, str)
                                       else isinstance(audience, list) and all(isinstance(a, str) for a in audience) and AUDIENCE in audience)
        checks["subject_matches_case"] = claims.get("sub") == CASES[case][0]
        checks["client_id_matches"] = claims.get("client_id") == CLIENT
        for name in ("sub", "client_id", "jti"):
            value = claims.get(name)
            checks[name + "_nonempty_string"] = isinstance(value, str) and bool(value.strip())
        now = time.time()
        for name in ("exp", "iat", "nbf"):
            value = claims.get(name)
            present = name in claims
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            checks[name + "_present"] = present
            checks[name + "_numeric_valid"] = valid if present else None
            checks[name + "_time_valid"] = (now - 15 < value if name == "exp" else value <= now + 15) if valid else None
        exp, iat = claims.get("exp"), claims.get("iat")
        checks["lifetime_order_valid"] = exp > iat if checks["exp_numeric_valid"] and checks["iat_numeric_valid"] else None
        return checks
    except Exception as error:
        return {**checks, "diagnostic_error": classify_error(error)[0]}


def check_session_endpoint(access, principal, expires_at, directory, diagnostic):
    """Only fixed loopback GETs; no analysis POST and no token persistence."""
    expected = {m.tenant_id.value: sorted(p.value for p in m.permissions)
                for m in directory.list_for_principal(principal)}
    statuses = {}
    with httpx.Client(trust_env=False, timeout=10, follow_redirects=False) as client:
        for label, tenant in (("without_tenant", None), ("tenant_alpha", "tenant-alpha"),
                              ("tenant_beta", "tenant-beta"), ("malformed_tenant", "tenant/invalid")):
            diagnostic.step("SESSION_GET_" + label.upper())
            headers = {"Authorization": "Bearer " + access}
            if tenant is not None:
                headers["X-Tenant-ID"] = tenant
            response = client.get("http://127.0.0.1:8000/api/v1/session", headers=headers)
            diagnostic.http(response.status_code)
            response.raise_for_status()
            body = response.json()
            diagnostic.require("SESSION_IDENTITY_AND_CACHE", response.status_code == 200
                and response.headers.get("cache-control") == "no-store"
                and body.get("issuer") == principal.issuer and body.get("subject") == principal.subject
                and body.get("expires_at") == expires_at)
            tenants = body.get("tenants")
            diagnostic.require("SESSION_TENANT_LIST", isinstance(tenants, list)
                and len(tenants) == len(expected)
                and all(isinstance(t, dict) and set(t) == {"tenant_id", "permissions"} for t in tenants))
            actual = {t["tenant_id"]: t["permissions"] for t in tenants}
            diagnostic.require("SESSION_BACKEND_PERMISSIONS", actual == expected)
            statuses[label] = response.status_code
    return {"result": "PASSED", "GET_statuses": statuses,
            "tenants": expected, "tenant_header_does_not_change_listing": True,
            "analysis_executed": False}


def validate_pair(tokens, case, nonce, ca_file, memberships, diagnostic, session_check=False):
    auth = None
    try:
        with diagnostic.phase("ACCESS_TOKEN_VALIDATION"):
            diagnostic.step("TOKEN_FIELDS_AND_TLS_CONTEXT")
            context = ssl.create_default_context(cafile=str(ca_file))
            access, identity = tokens["access_token"], tokens["id_token"]
            diagnostic.require("TOKEN_TYPE", tokens.get("token_type", "").lower() == "bearer")
            # Real HTTPS transport with an explicit CA, through the existing injection port.
            # C1.3 code, claim policy and signature verification remain unchanged.
            auth = OIDCAuthenticator(OIDCConfig(ISSUER, AUDIENCE, JWKS),
                                     transport=httpx.HTTPTransport(verify=context, trust_env=False))
            diagnostic.step("C1_3_AUTHENTICATE")
            try:
                validated = auth.validate_access_token(access)
            except InvalidCredentials:
                diagnostic.active["rejected_access_checks"] = rejected_access_diagnostics(access, context, case)
                raise
            diagnostic.step("ACCESS_HEADER_PROFILE")
            header = jwt.get_unverified_header(access)
            if header.get("alg") != "RS256" or header.get("typ") != "at+jwt":
                raise ValueError("Unexpected access token profile")
            diagnostic.step("JWKS_FETCH")
            with httpx.Client(verify=context, trust_env=False, timeout=5, follow_redirects=False) as client:
                response = client.get(JWKS)
                diagnostic.http(response.status_code)
                response.raise_for_status()
                keys = response.json()["keys"]

            def key_for(token):
                head = jwt.get_unverified_header(token)
                if head.get("alg") != "RS256":
                    raise ValueError("Unexpected signature algorithm")
                matches = [k for k in keys if k.get("kid") == head.get("kid") and k.get("use") == "sig" and k.get("alg") == "RS256"]
                if len(matches) != 1:
                    raise ValueError("Ambiguous signing key")
                return jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key

            diagnostic.step("ACCESS_CLAIMS")
            claims = jwt.decode(access, key_for(access), algorithms=["RS256"],
                                issuer=ISSUER, audience=AUDIENCE, leeway=15,
                                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id"]})
            diagnostic.step("CLIENT_ID_MATCH")
            if claims["client_id"] != CLIENT:
                raise ValueError("Unexpected issuing client")
        with diagnostic.phase("PRINCIPAL_MATCH"):
            diagnostic.step("EXPECTED_IDENTITY")
            if validated.principal != PrincipalId(ISSUER, CASES[case][0]):
                raise ValueError("Unexpected development user")
        with diagnostic.phase("ID_TOKEN_BINDING"):
            diagnostic.step("ID_SIGNATURE_CLAIMS")
            id_claims = jwt.decode(identity, key_for(identity), algorithms=["RS256"],
                                   issuer=ISSUER, audience=CLIENT, leeway=15,
                                   options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]})
            diagnostic.require("ID_SUBJECT_MATCH", id_claims["sub"] == validated.principal.subject)
            diagnostic.require("NONCE_MATCH", hmac.compare_digest(id_claims["nonce"], nonce))
            diagnostic.require("AUTHORIZED_PARTY_MATCH", "azp" not in id_claims or id_claims["azp"] == CLIENT)
            diagnostic.step("ACCESS_HASH_BINDING")
            if "at_hash" in id_claims:
                digest = hashlib.sha256(access.encode("ascii")).digest()[:16]
                expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
                if not hmac.compare_digest(id_claims["at_hash"], expected):
                    raise ValueError("Invalid access token binding")
            diagnostic.step("ID_TOKEN_REJECTION")
            try:
                auth.validate_access_token(identity)
            except InvalidCredentials:
                pass
            else:
                raise ValueError("ID token accepted as access token")
        with diagnostic.phase("TENANT_AUTHORIZATION"):
            diagnostic.step("DIRECTORY_LOAD")
            records = json.loads(memberships.read_text(encoding="utf-8"))
            directory = ConfiguredMembershipDirectory(TenantMembership(
                PrincipalId(r["issuer"], r["subject"]), TenantId(r["tenant_id"]),
                frozenset(Role(role) for role in r["roles"])) for r in records)
            factory, authorizer = SecurityContextFactory(auth, directory), TenantAuthorizer(directory)
            outcomes = {}
            for tenant in ("tenant-alpha", "tenant-beta"):
                diagnostic.step("AUTHORIZE_" + tenant.upper().replace("-", "_"))
                denial_class = None
                try:
                    scope = factory.create(access, TenantId(tenant), "local-pkce-validation")
                    authorizer.authorize(scope, Permission.ANALYSIS_RUN)
                    allowed = True
                except (TenantAccessDenied, PermissionDenied) as denied:
                    denial_class = classify_error(denied)[1]
                    allowed = False
                outcomes[tenant] = {"result": "allowed" if allowed else "denied", "exception_class": denial_class}
                diagnostic.active["tenants"] = outcomes.copy()
                if allowed != (CASES[case][2] and tenant == CASES[case][1]):
                    if not allowed:
                        raise PermissionDenied()
                    raise ValueError("Unexpected authorization result")
            session_result = "not_called"
            if session_check:
                session_result = check_session_endpoint(access, validated.principal, validated.expires_at,
                                                        directory, diagnostic)
        return {"case": case, "C1.3": "passed", "alg": "RS256", "typ": "at+jwt",
                "required_claims": "verified", "nbf_present": "nbf" in claims,
                "issuer_audience_subject_client_kid": "verified", "ID_token_rejected": True,
                "analysis_permission_only_no_execution": outcomes,
                "session_endpoint": session_result, "expired_wrong_issuer_wrong_audience": "offline_tests_only"}
    finally:
        tokens.clear()
        if auth is not None:
            auth.close()


def execute_attempt(query, state, case, nonce, verifier, client_secret, context, discovery, root, diagnostic, session_check=False):
    tokens, response = {}, None
    try:
        with diagnostic.phase("CALLBACK_VALIDATION"):
            diagnostic.step("STATE_CODE_ISSUER")
            code = callback_code(query, state)
        with diagnostic.phase("TOKEN_EXCHANGE"):
            diagnostic.step("HTTPS_CODE_EXCHANGE")
            with httpx.Client(verify=context, trust_env=False, timeout=10, follow_redirects=False) as client:
                response = client.post(discovery["token_endpoint"],
                    auth=(CLIENT, client_secret), data={"grant_type": "authorization_code",
                    "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier})
                diagnostic.http(response.status_code)
                diagnostic.step("TOKEN_ENDPOINT_STATUS")
                response.raise_for_status()
                diagnostic.step("TOKEN_RESPONSE_FORMAT")
                tokens = response.json()
                diagnostic.require("TOKEN_RESPONSE_OBJECT", isinstance(tokens, dict))
        return validate_pair(tokens, case, nonce, root / "ca/ca-cert.pem",
                             root / "backend-memberships.json", diagnostic, session_check=session_check)
    finally:
        if isinstance(tokens, dict):
            tokens.clear()
        if response is not None:
            response.close()


def run(private_root, case, lifetime=600, session_check=False):
    root = private_root.resolve()
    if root.is_relative_to(REPO):
        raise ValueError("Private configuration must be external")
    values = dict(line.split("=", 1) for line in (root / "compose.env").read_text().splitlines() if line)
    client_secret = json.loads(values["INDAI_STREAMLIT_CLIENT_SECRET"])
    context = ssl.create_default_context(cafile=str(root / "ca/ca-cert.pem"))
    with httpx.Client(verify=context, trust_env=False, timeout=5, follow_redirects=False) as client:
        response = client.get(ISSUER + "/.well-known/openid-configuration")
        response.raise_for_status()
        discovery = response.json()
        if any(discovery.get(k) != v for k, v in {
            "issuer": ISSUER, "jwks_uri": JWKS,
            "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
            "token_endpoint": ISSUER + "/protocol/openid-connect/token"}.items()):
            raise ValueError("Untrusted discovery")
    state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
    deadline, used = time.monotonic() + lifetime, False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log callback URLs, authorization codes or credentials.

        def reply(self, status, text, location=None):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))

        def do_GET(self):
            nonlocal used
            self.connection.settimeout(5)
            if (self.headers.get_all("Host") != ["localhost:8501"] or len(self.path) > 8192
                    or time.monotonic() >= deadline):
                return self.reply(400, "Invalid local request")
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                return self.reply(400, "Invalid local request")
            if parsed.path == "/start" and not parsed.query and not used:
                return self.reply(302, "Continue with Keycloak", authorization_url(case, state, nonce, verifier))
            if parsed.path != "/oauth2callback" or used:
                return self.reply(404, "Not found")
            used = True  # One exchange attempt; no code replay, including after failure.
            diagnostic = Diagnostics()
            try:
                result = execute_attempt(parsed.query, state, case, nonce, verifier, client_secret,
                                         context, discovery, root, diagnostic, session_check=session_check)
                print(json.dumps(result), flush=True)
                self.reply(200, "Validation complete. You can close this tab. No analysis was executed.")
            except DiagnosticFailure:
                self.reply(400, "Validation failed. No analysis was executed. Close this tab.")
            finally:
                diagnostic.emit()

    class LocalServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    with LocalServer(("127.0.0.1", 8501), Handler) as server:
        server.timeout = 1
        print(f"Ready for {case}: {START} (expires in {lifetime}s). Tokens stay in memory; session GET check={session_check}; no API started by callback.", flush=True)
        while not used and time.monotonic() < deadline:
            server.handle_request()
    print("Callback closed; token memory released on process exit.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--case", required=True, choices=tuple(CASES))
    parser.add_argument("--session-check", action="store_true", help="Check only fixed loopback GET /api/v1/session after validation")
    args = parser.parse_args()
    try:
        run(args.private_root, args.case, session_check=args.session_check)
    except Exception:
        print("Callback could not start; inspect local configuration. No secrets logged.", file=sys.stderr)
        raise SystemExit(1) from None
