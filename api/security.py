"""HTTP security boundary. Request bodies never establish identity or grants."""
from dataclasses import dataclass, field
import re
from uuid import uuid4

from fastapi import HTTPException, Request

from security.errors import AuthenticationRequired, InvalidCredentials, IdentityInfrastructureError
from security.memberships import MembershipDirectory
from security.models import SecurityContext, TenantId
from security.permissions import Permission
from security.policy import SecurityContextFactory, TenantAuthorizer
from security.ports import Authenticator


@dataclass(frozen=True)
class APISecurity:
    authenticator: Authenticator
    memberships: MembershipDirectory
    context_factory: SecurityContextFactory = field(init=False)
    authorizer: TenantAuthorizer = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "context_factory", SecurityContextFactory(self.authenticator, self.memberships))
        object.__setattr__(self, "authorizer", TenantAuthorizer(self.memberships))


def bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if not values:
        raise AuthenticationRequired("Bearer credentials are required")
    if len(values) != 1:
        raise InvalidCredentials("Ambiguous credentials")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9._~+/-]+=*)", values[0], flags=re.IGNORECASE)
    if match is None:
        raise InvalidCredentials("Invalid Bearer credentials")
    return match[1]


def analysis_security_context(request: Request) -> SecurityContext:
    token = bearer_token(request)
    security = request.app.state.security
    if security is None:
        raise IdentityInfrastructureError("Identity configuration is unavailable")
    tenants = request.headers.getlist("x-tenant-id")
    if len(tenants) != 1 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", tenants[0]) is None:
        raise HTTPException(400, "Selecciona un único X-Tenant-ID válido.")
    context = security.context_factory.create(token, TenantId(tenants[0]), uuid4().hex)
    security.authorizer.authorize(context, Permission.ANALYSIS_RUN)
    return context
