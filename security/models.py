"""Transport-independent values, not proof of authentication or membership.

Only a trusted boundary may construct SecurityContext after authenticating the
principal, validating active-tenant membership and resolving effective permissions.
Construction validates structure; it does not perform those security checks.
"""
from dataclasses import dataclass

from .permissions import Permission


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


@dataclass(frozen=True)
class PrincipalId:
    """Stable issuer/subject pair, never an email-based user identity.

    Preserve exact values: normalizing issuer or subject can merge identities.
    """

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        _require_identifier(self.issuer, "issuer")
        _require_identifier(self.subject, "subject")


@dataclass(frozen=True)
class TenantId:
    """Internal security tenant, independent of customer/industrial identifiers."""

    value: str

    def __post_init__(self) -> None:
        _require_identifier(self.value, "tenant_id")


@dataclass(frozen=True)
class ResourceRef:
    """Resource locator and owner; ownership must be resolved by trusted code."""

    resource_type: str
    resource_id: str
    tenant_id: TenantId

    def __post_init__(self) -> None:
        _require_identifier(self.resource_type, "resource_type")
        _require_identifier(self.resource_id, "resource_id")
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be a TenantId")


@dataclass(frozen=True)
class SecurityContext:
    """Immutable effective scope supplied by a trusted security boundary.

    No credentials, tokens, implicit grants or business/prompt-derived permissions.
    Convert external permission strings explicitly with Permission(value) before
    construction. Mutable input sets are defensively copied into a frozenset.
    """

    principal: PrincipalId
    tenant_id: TenantId
    permissions: frozenset[Permission]
    correlation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.principal, PrincipalId):
            raise TypeError("principal must be a PrincipalId")
        if not isinstance(self.tenant_id, TenantId):
            raise TypeError("tenant_id must be a TenantId")
        _require_identifier(self.correlation_id, "correlation_id")
        if not isinstance(self.permissions, (frozenset, set, tuple, list)):
            raise TypeError("permissions must be a collection of Permission values")
        if any(not isinstance(permission, Permission) for permission in self.permissions):
            raise TypeError("permissions must contain only Permission values")
        object.__setattr__(self, "permissions", frozenset(self.permissions))
