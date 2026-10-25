"""Explicit tenant-local roles; no hierarchy, wildcard or approval permission."""
from enum import Enum
from types import MappingProxyType

from .permissions import Permission as P


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    APPROVER = "approver"
    TENANT_ADMIN = "tenant_admin"


ROLE_PERMISSIONS = MappingProxyType({
    Role.VIEWER: frozenset({P.PORTFOLIO_READ, P.DOCUMENTS_READ}),
    Role.ANALYST: frozenset({
        P.PORTFOLIO_READ, P.DOCUMENTS_READ, P.ANALYSIS_RUN,
        P.SCENARIOS_EVALUATE, P.CRITERIA_EVALUATE,
    }),
    # The name reserves a future business role, not authorization to approve.
    Role.APPROVER: frozenset({P.PORTFOLIO_READ, P.DOCUMENTS_READ, P.DECISIONS_EVALUATE}),
    Role.TENANT_ADMIN: frozenset({
        P.DOCUMENTS_READ, P.DOCUMENTS_INGEST, P.DOCUMENTS_DELETE, P.DIAGNOSTICS_READ,
    }),
})


def permissions_for_roles(roles: frozenset[Role]) -> frozenset[P]:
    """Union only explicitly configured roles, rejecting raw strings."""
    if not isinstance(roles, frozenset) or any(not isinstance(role, Role) for role in roles):
        raise TypeError("roles must be a frozenset of Role values")
    return frozenset(permission for role in roles for permission in ROLE_PERMISSIONS[role])
