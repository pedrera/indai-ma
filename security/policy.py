"""Deterministic tenant authorization used by the analysis application boundary.

The composition root must inject a trusted membership directory. Authentication
must establish the principal, and resource ownership must come from trusted
storage, not a request/LLM-provided ResourceRef. Python values are not credentials.
"""
from .errors import (AuthenticationRequired, PermissionDenied, TenantAccessDenied,
                     IdentityInfrastructureError, SecurityError)
from .memberships import MembershipDirectory, TenantMembership
from .models import PrincipalId, ResourceRef, SecurityContext, TenantId
from .permissions import Permission
from .ports import Authenticator


def _membership(directory: MembershipDirectory, principal: PrincipalId,
                tenant_id: TenantId) -> TenantMembership:
    try:
        membership = directory.resolve(principal, tenant_id)
    except SecurityError:
        raise
    except Exception:
        raise IdentityInfrastructureError("Membership source is unavailable") from None
    if (not isinstance(membership, TenantMembership)
            or membership.principal != principal or membership.tenant_id != tenant_id):
        raise TenantAccessDenied("No valid membership for the active tenant")
    return membership


class SecurityContextFactory:
    """Trusted boundary: authenticate, resolve the selected tenant, then build.

    Never accepts roles or permissions from its caller. Call again after a tenant
    change. Injected ports must be trusted; this is not an OIDC implementation.
    """

    def __init__(self, authenticator: Authenticator, memberships: MembershipDirectory) -> None:
        self._authenticator = authenticator
        self._memberships = memberships

    def create(self, credentials: str | None, tenant_id: TenantId,
               correlation_id: str) -> SecurityContext:
        if not isinstance(tenant_id, TenantId):
            raise TenantAccessDenied("An explicit active TenantId is required")
        try:
            principal = self._authenticator.authenticate(credentials)
        except SecurityError:
            raise
        except Exception:
            raise IdentityInfrastructureError("Authentication source is unavailable") from None
        if not isinstance(principal, PrincipalId):
            raise AuthenticationRequired("Authenticator did not establish a principal")
        membership = _membership(self._memberships, principal, tenant_id)
        return SecurityContext(principal, tenant_id, membership.permissions, correlation_id)


class TenantAuthorizer:
    """Compatible with Authorizer; re-resolves grants on every authorization.

    Context permissions may narrow authoritative grants, never widen them. Shared
    resources have no exception: a different owner tenant is always denied.
    """

    def __init__(self, memberships: MembershipDirectory) -> None:
        self._memberships = memberships

    def authorize(self, context: SecurityContext, permission: Permission,
                  resource: ResourceRef | None = None) -> None:
        if not isinstance(context, SecurityContext):
            raise AuthenticationRequired("A trusted SecurityContext is required")
        if not isinstance(permission, Permission):
            raise PermissionDenied("An explicit typed permission is required")
        membership = _membership(self._memberships, context.principal, context.tenant_id)
        if not context.permissions <= membership.permissions:
            raise PermissionDenied("Context grants exceed current tenant permissions")
        if permission not in context.permissions:
            raise PermissionDenied("Required permission is absent")
        if resource is not None:
            if not isinstance(resource, ResourceRef):
                raise PermissionDenied("A trusted ResourceRef is required")
            if resource.tenant_id != context.tenant_id:
                raise TenantAccessDenied("Resource belongs to another tenant")
