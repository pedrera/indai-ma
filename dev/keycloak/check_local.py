"""Future live checks only; never starts services or obtains tokens via passwords."""
import argparse
import json
from pathlib import Path
import sys

import httpx
import jwt

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from security.config import OIDCConfig
from security.oidc import OIDCAuthenticator

ISSUER = "https://localhost:8443/realms/indai-ma-dev"
AUDIENCE = "indai-ma-api"
JWKS = ISSUER + "/protocol/openid-connect/certs"
CASES = {
    "analyst-a": ("11111111-1111-4111-8111-000000000001", "tenant-alpha", True),
    "analyst-b": ("11111111-1111-4111-8111-000000000002", "tenant-beta", True),
    "viewer-a": ("11111111-1111-4111-8111-000000000003", "tenant-alpha", False),
    "outsider": ("11111111-1111-4111-8111-000000000004", None, False),
}


def check(api_url="http://127.0.0.1:8000", token_file=None, case=None):
    # The backend's unchanged validator reads certifi even with trust_env=False.
    with httpx.Client(verify=True, trust_env=False, timeout=10, follow_redirects=False) as client:
        discovery_response = client.get(ISSUER + "/.well-known/openid-configuration")
        discovery_response.raise_for_status()
        discovery = discovery_response.json()
        if (discovery.get("issuer") != ISSUER or discovery.get("jwks_uri") != JWKS
                or discovery.get("authorization_endpoint") != ISSUER + "/protocol/openid-connect/auth"
                or discovery.get("token_endpoint") != ISSUER + "/protocol/openid-connect/token"):
            raise ValueError("Discovery does not match the configured local origin")
        keys_response = client.get(JWKS)
        keys_response.raise_for_status()
        keys = keys_response.json().get("keys", [])
        if not any(key.get("kty") == "RSA" and key.get("use", "sig") == "sig" for key in keys):
            raise ValueError("No RSA signing keys published")
        print("Discovery, exact issuer/endpoints and TLS-verified RSA JWKS: OK")
        if token_file is None:
            return
        path = Path(token_file).resolve()
        if path.is_relative_to(REPO) or case not in CASES:
            raise ValueError("Use an external token file and explicit development case")
        token = path.read_text(encoding="utf-8").strip()
        authenticator = OIDCAuthenticator(OIDCConfig(ISSUER, AUDIENCE, JWKS))
        try:
            validated = authenticator.validate_access_token(token)
        finally:
            authenticator.close()
        expected_subject, tenant, allowed = CASES[case]
        if validated.principal.subject != expected_subject:
            raise ValueError("Token is not for the expected development identity")
        header = jwt.get_unverified_header(token)  # only after cryptographic validation
        if header.get("typ") != "at+jwt" or header.get("alg") != "RS256":
            raise ValueError("Unexpected access-token profile")
        api_headers = {"Authorization": "Bearer " + token}
        response = client.get(api_url.rstrip("/") + "/api/v1/session", headers=api_headers)
        response.raise_for_status()
        session = response.json()
        if session.get("issuer") != ISSUER or session.get("subject") != expected_subject:
            raise ValueError("Session identity mismatch")
        if {item["tenant_id"] for item in session["tenants"]} != ({tenant} if tenant else set()):
            raise ValueError("Unexpected backend tenant membership")
        if tenant is not None:
            permissions = session["tenants"][0]["permissions"]
            if ("analysis:run" in permissions) != allowed:
                raise ValueError("Unexpected backend analysis permission")
        query = {"text": "La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento."}
        for selected in ("tenant-alpha", "tenant-beta"):
            response = client.post(api_url.rstrip("/") + "/api/v1/analysis", json=query,
                                   headers={**api_headers, "X-Tenant-ID": selected})
            expected = 200 if allowed and selected == tenant else 403
            if response.status_code != expected:
                raise ValueError("Unexpected analysis authorization outcome")
        anonymous = client.post(api_url.rstrip("/") + "/api/v1/analysis", json=query)
        if anonymous.status_code != 401 or anonymous.headers.get("www-authenticate") != "Bearer":
            raise ValueError("Anonymous analysis must be denied")
        print("RFC9068 signature/claims, session, tenant/analysis denials and anonymous denial: OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--case", choices=tuple(CASES))
    args = parser.parse_args()
    try:
        check(args.api_url, args.token_file, args.case)
    except Exception:
        print("Local integration check failed; no credentials are printed. Inspect configuration and service status.", file=sys.stderr)
        raise SystemExit(1) from None
