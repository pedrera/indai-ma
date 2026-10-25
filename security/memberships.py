"""Explicit immutable configuration for tests and controlled development only.

This is not a production enterprise directory. Configuration must be supplied by
trusted composition code, never populated from request roles or business data.
Replace the configured adapter to apply membership changes; no global state.
"""
from dataclasses import dataclass
from typing import Iterable, Protocol
from types import MappingProxyType

from .models import PrincipalId, TenantId
from .permissions import Permission
from .ports import MembershipResolver
from .roles import Role, permissions_for_roles


@dataclass(frozen=True)
class TenantMembership:
    principal: PrincipalId
    tenant_id: TenantId
    roles: frozenset[Role]

    def __post_init__(self) -> None:
        if not isinstance(self.principal, PrincipalId) or not isinstance(self.tenant_id, TenantId):
            raise TypeError("membership requires PrincipalId and TenantId")
        if not isinstance(self.roles, (frozenset, set, tuple, list)):
            raise TypeError("roles must be a collection of Role values")
        if any(not isinstance(role, Role) for role in self.roles):
            raise TypeError("roles must contain only Role values")
        object.__setattr__(self, "roles", frozenset(self.roles))

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for_roles(self.roles)


class MembershipDirectory(MembershipResolver, Protocol):
    """Trusted authoritative lookup, extending the C1.1 boolean port compatibly."""

    def resolve(self, principal: PrincipalId, tenant_id: TenantId) -> TenantMembership | None:
        """Return this pair's current membership, or None; propagate failures."""
        ...


class MembershipListingDirectory(MembershipDirectory, Protocol):
    def list_for_principal(self, principal: PrincipalId) -> tuple[TenantMembership, ...]:
        """List only this authenticated principal's current memberships."""
        ...


@dataclass(frozen=True, init=False)
class ConfiguredMembershipDirectory:
    """Immutable snapshot; duplicate principal/tenant configuration is an error."""

    _memberships: MappingProxyType

    def __init__(self, memberships: Iterable[TenantMembership]) -> None:
        indexed = {}
        for membership in memberships:
            if not isinstance(membership, TenantMembership):
                raise TypeError("configuration must contain TenantMembership values")
            key = (membership.principal, membership.tenant_id)
            if key in indexed:
                raise ValueError("duplicate principal/tenant membership")
            indexed[key] = membership
        object.__setattr__(self, "_memberships", MappingProxyType(indexed))

    def resolve(self, principal: PrincipalId, tenant_id: TenantId) -> TenantMembership | None:
        if not isinstance(principal, PrincipalId) or not isinstance(tenant_id, TenantId):
            raise TypeError("lookup requires PrincipalId and TenantId")
        return self._memberships.get((principal, tenant_id))

    def is_member(self, principal: PrincipalId, tenant_id: TenantId) -> bool:
        return self.resolve(principal, tenant_id) is not None

    def list_for_principal(self, principal: PrincipalId) -> tuple[TenantMembership, ...]:
        if not isinstance(principal, PrincipalId):
            raise TypeError("lookup requires PrincipalId")
        return tuple(record for (owner, _), record in self._memberships.items() if owner == principal)
