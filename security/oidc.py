"""RS256/RFC 9068 validation; returns identity only, never authorization grants."""
import json
import math
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable

import httpx
import jwt

from .config import OIDCConfig
from .errors import AuthenticationRequired, InvalidCredentials, IdentityInfrastructureError
from .models import PrincipalId


MAX_TOKEN_CHARS = 16384
MAX_JWKS_BYTES = 262144
MAX_JWKS_KEYS = 64


@dataclass(frozen=True)
class ValidatedAccessToken:
    """Validated metadata only; never retains the credential or role claims."""
    principal: PrincipalId
    expires_at: float


class OIDCAuthenticator:
    """Authenticator port implementation, isolated to one configured issuer.

    kid is an untrusted lookup key within configured JWKS, never proof of trust.
    Cache uses monotonic time; unknown kid triggers a rate-limited refresh.
    Expired keys are never used after a refresh failure. Rotation needs a new kid.
    transport/clock injection exists for offline tests, not to bypass validation.
    """

    def __init__(self, config: OIDCConfig, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = monotonic) -> None:
        if not isinstance(config, OIDCConfig):
            raise TypeError("config must be an OIDCConfig")
        self._config = config
        self._clock = clock
        self._client = httpx.Client(
            timeout=config.timeout_seconds, follow_redirects=False,
            trust_env=False, transport=transport,
        )
        self._lock = Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires_at = float("-inf")
        self._last_attempt = float("-inf")
        self._refresh_unavailable = False

    def close(self) -> None:
        self._client.close()

    def authenticate(self, credentials: str | None) -> PrincipalId:
        return self.validate_access_token(credentials).principal

    def validate_access_token(self, credentials: str | None) -> ValidatedAccessToken:
        if credentials is None or credentials == "":
            raise AuthenticationRequired("An access token is required")
        if not isinstance(credentials, str) or len(credentials) > MAX_TOKEN_CHARS:
            raise InvalidCredentials("Invalid access token")
        try:
            header = jwt.get_unverified_header(credentials)
            if (header.get("alg") not in self._config.algorithms
                    or header.get("typ") not in ("at+jwt", "application/at+jwt")
                    or any(name in header for name in ("jku", "x5u", "jwk", "x5c", "crit", "b64"))):
                raise InvalidCredentials("Unsupported access-token header")
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid.strip() or len(kid) > 256:
                raise InvalidCredentials("A valid signing key identifier is required")
            key = self._key(kid)
            claims = jwt.decode(
                credentials, key, algorithms=list(self._config.algorithms),
                issuer=self._config.issuer, audience=self._config.audience,
                leeway=self._config.leeway_seconds,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "jti", "client_id"]},
            )
            for name in ("sub", "client_id", "jti"):
                if not isinstance(claims[name], str) or not claims[name].strip():
                    raise InvalidCredentials("Invalid required access-token claim")
            for name in ("exp", "iat", "nbf"):
                if name in claims:
                    value = claims[name]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise InvalidCredentials("Invalid access-token timestamp")
            if claims["exp"] <= claims["iat"]:
                raise InvalidCredentials("Invalid access-token lifetime")
            return ValidatedAccessToken(PrincipalId(claims["iss"], claims["sub"]), float(claims["exp"]))
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
            raise InvalidCredentials("Access token could not be validated") from None

    def _key(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = self._clock()
            if now < self._expires_at and kid in self._keys:
                return self._keys[kid]
            if now - self._last_attempt < self._config.refresh_cooldown_seconds:
                if self._refresh_unavailable:
                    raise IdentityInfrastructureError("Signing key source is unavailable")
                raise InvalidCredentials("Signing key is unavailable")
            self._last_attempt = now
            try:
                keys = self._fetch_keys()
            except IdentityInfrastructureError:
                self._refresh_unavailable = True
                raise
            self._refresh_unavailable = False
            self._keys = keys
            self._expires_at = self._clock() + self._config.cache_ttl_seconds
            if kid not in keys:
                raise InvalidCredentials("Signing key is unavailable")
            return keys[kid]

    def _fetch_keys(self) -> dict[str, jwt.PyJWK]:
        try:
            started = self._clock()
            with self._client.stream("GET", self._config.jwks_url) as response:
                if response.status_code != 200:
                    raise IdentityInfrastructureError("Trusted signing keys are unavailable")
                data = bytearray()
                for part in response.iter_bytes(chunk_size=8192):
                    if self._clock() - started >= self._config.timeout_seconds:
                        raise IdentityInfrastructureError("Signing key request exceeded its time budget")
                    data.extend(part)
                    if len(data) > MAX_JWKS_BYTES:
                        raise IdentityInfrastructureError("Signing key response exceeds limits")
            payload = json.loads(data)
            records = payload["keys"]
            if not isinstance(records, list) or not 1 <= len(records) <= MAX_JWKS_KEYS:
                raise IdentityInfrastructureError("Invalid signing key set")
            keys = {}
            for record in records:
                if not isinstance(record, dict):
                    raise IdentityInfrastructureError("Invalid signing key set")
                if (record.get("kty") != "RSA" or record.get("use", "sig") != "sig"
                        or record.get("alg", "RS256") != "RS256"):
                    continue
                kid = record.get("kid")
                if (not isinstance(kid, str) or not kid.strip() or len(kid) > 256
                        or kid in keys or any(name in record for name in ("d", "p", "q", "dp", "dq", "qi", "oth"))
                        or ("key_ops" in record and record["key_ops"] != ["verify"])):
                    raise IdentityInfrastructureError("Invalid signing key metadata")
                key = jwt.PyJWK.from_dict(record, algorithm="RS256")
                if key.key.key_size < 2048:
                    raise IdentityInfrastructureError("Signing key is too weak")
                keys[kid] = key
            if not keys:
                raise IdentityInfrastructureError("No eligible signing keys")
            return keys
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise IdentityInfrastructureError("Trusted signing keys could not be loaded") from None
