"""Explicit trust configuration; never derive these values from a JWT."""
from dataclasses import dataclass
import math
from urllib.parse import urlsplit


def _https_url(value: str, name: str) -> None:
    if not isinstance(value, str) or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError(f"{name} must be an absolute HTTPS URL")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.query):
        raise ValueError(f"{name} must be an absolute HTTPS URL without credentials, query or fragment")
    try:
        parsed.port
    except ValueError:
        raise ValueError(f"{name} has an invalid port") from None


@dataclass(frozen=True)
class OIDCConfig:
    """RFC 9068 signed access tokens, RS256 only, preconfigured JWKS endpoint.

    HTTPS is required even for development. No discovery, environment defaults,
    issuer guessing or automatic network calls at import/construction time.
    """

    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256",)
    leeway_seconds: float = 15
    timeout_seconds: float = 5
    cache_ttl_seconds: float = 300
    refresh_cooldown_seconds: float = 10

    def __post_init__(self) -> None:
        _https_url(self.issuer, "issuer")
        _https_url(self.jwks_url, "jwks_url")
        if not isinstance(self.audience, str) or not self.audience.strip() or self.audience != self.audience.strip():
            raise ValueError("audience must be a non-empty exact identifier")
        if self.algorithms != ("RS256",) or not isinstance(self.algorithms, tuple):
            raise ValueError("Only the RS256 access-token profile is supported")
        for name, maximum, zero_allowed in (
            ("leeway_seconds", 60, True), ("timeout_seconds", 30, False),
            ("cache_ttl_seconds", 3600, False), ("refresh_cooldown_seconds", 60, False),
        ):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value > maximum
                    or (value < 0 if zero_allowed else value <= 0)):
                raise ValueError(f"Invalid {name}")
        if self.refresh_cooldown_seconds > self.cache_ttl_seconds:
            raise ValueError("Refresh cooldown must not exceed cache TTL")
