"""Security failures without transport status codes or credential payloads."""


class SecurityError(Exception):
    """Base for authentication and authorization failures."""


class IdentityInfrastructureError(SecurityError):
    """Identity configuration or authoritative infrastructure is unavailable."""


class AuthenticationRequired(SecurityError):
    """No authentication credentials were supplied."""


class InvalidCredentials(SecurityError):
    """Supplied credentials could not establish a valid identity."""


class PermissionDenied(SecurityError):
    """The authenticated principal may not perform the requested action."""


class TenantAccessDenied(PermissionDenied):
    """The principal may not access the selected tenant."""
