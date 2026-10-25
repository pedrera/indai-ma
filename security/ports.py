"""Provider-independent ports; no implementation grants access in this increment."""
from typing import Protocol

from .models import PrincipalId, ResourceRef, SecurityContext, TenantId
from .permissions import Permission


class Authenticator(Protocol):
    def authenticate(self, credentials: str | None) -> PrincipalId:
        """Validate credentials or raise AuthenticationRequired/InvalidCredentials.

        Credentials are transient adapter input, never part of SecurityContext.
        A future OIDC adapter must validate the access token before returning.
        """
        ...


class MembershipResolver(Protocol):
    def is_member(self, principal: PrincipalId, tenant_id: TenantId) -> bool:
        """Resolve authoritative membership; False is not permission to fall back.

        Infrastructure failures must propagate rather than imply membership.
        """
        ...


class Authorizer(Protocol):
    def authorize(
        self,
        context: SecurityContext,
        permission: Permission,
        resource: ResourceRef | None = None,
    ) -> None:
        """Return on allowance, otherwise raise PermissionDenied/TenantAccessDenied.

        Resource-free actions still require an explicit permission. Implementations
        must check resource ownership and policy, not just the permission set.
        """
        ...
