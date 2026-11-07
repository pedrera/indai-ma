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
from security.errors import SecurityError, TenantAccessDenied, PermissionDenied
from security_audit import emit, correlation_id


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
    request.state.analysis_security_stage = "BEARER"
    try:
        token = bearer_token(request)
    except SecurityError as error:
        emit('authentication_denied', 'BEARER', error=error)
        raise
    security = request.app.state.security
    if security is None:
        raise IdentityInfrastructureError("Identity configuration is unavailable")
    tenants = request.headers.getlist("x-tenant-id")
    if len(tenants) != 1 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", tenants[0]) is None:
        raise HTTPException(400, "Selecciona un único X-Tenant-ID válido.")
    request.state.analysis_security_stage = "CONTEXT_FACTORY"
    class ObservedAuthenticator:
        def authenticate(self, credentials):
            try:
                principal = security.authenticator.authenticate(credentials)
            except SecurityError as error:
                emit('authentication_denied', 'AUTHENTICATION', error=error)
                raise
            emit('authentication_succeeded', 'AUTHENTICATION', principal=principal)
            return principal
    try:
        context = SecurityContextFactory(ObservedAuthenticator(), security.memberships).create(
            token, TenantId(tenants[0]), correlation_id())
    except TenantAccessDenied as error:
        emit('tenant_authorization_denied', 'CONTEXT_FACTORY', tenant=tenants[0], error=error)
        raise
    emit('tenant_authorization_succeeded', 'CONTEXT_FACTORY', tenant=tenants[0])
    request.state.analysis_security_stage = "OPERATION_AUTHORIZATION"
    try:
        security.authorizer.authorize(context, Permission.ANALYSIS_RUN)
    except PermissionDenied as error:
        emit('tenant_authorization_denied' if isinstance(error, TenantAccessDenied) else
             'permission_authorization_denied', 'OPERATION_AUTHORIZATION', error=error)
        raise
    emit('permission_authorization_succeeded', 'OPERATION_AUTHORIZATION')
    request.state.analysis_security_stage = "AUTHORIZED"
    return context
