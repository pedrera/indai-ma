"""Contract tests only: doubles below are not production security adapters."""
from dataclasses import FrozenInstanceError, fields

import pytest

from security.errors import (
    AuthenticationRequired, InvalidCredentials, PermissionDenied,
    SecurityError, TenantAccessDenied,
)
from security.models import PrincipalId, ResourceRef, SecurityContext, TenantId
from security.permissions import Permission
from security.ports import Authenticator, Authorizer, MembershipResolver


def context(permissions=frozenset()):
    return SecurityContext(
        PrincipalId("issuer-a", "subject-a"), TenantId("tenant-a"),
        permissions, "operation-a",
    )


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
@pytest.mark.parametrize("make", [
    lambda value: PrincipalId(value, "subject"),
    lambda value: PrincipalId("issuer", value),
    TenantId,
    lambda value: ResourceRef(value, "id", TenantId("a")),
    lambda value: ResourceRef("document", value, TenantId("a")),
    lambda value: SecurityContext(PrincipalId("i", "s"), TenantId("a"), frozenset(), value),
])
def test_empty_identifiers_are_rejected(make, value):
    with pytest.raises(ValueError):
        make(value)


@pytest.mark.parametrize("value", [None, 1, [], {}])
@pytest.mark.parametrize("make", [
    lambda value: PrincipalId(value, "subject"),
    lambda value: PrincipalId("issuer", value),
    TenantId,
    lambda value: ResourceRef(value, "id", TenantId("a")),
    lambda value: ResourceRef("document", value, TenantId("a")),
    lambda value: SecurityContext(PrincipalId("i", "s"), TenantId("a"), frozenset(), value),
])
def test_identifiers_require_strings(make, value):
    with pytest.raises(TypeError):
        make(value)


def test_principal_equality_and_hash_use_issuer_and_subject():
    principal = PrincipalId("issuer-a", "same-subject")
    assert principal == PrincipalId("issuer-a", "same-subject")
    assert hash(principal) == hash(PrincipalId("issuer-a", "same-subject"))
    assert principal != PrincipalId("issuer-b", "same-subject")
    assert principal != PrincipalId("issuer-a", "other-subject")
    assert len({principal, PrincipalId("issuer-b", "same-subject")}) == 2
    assert PrincipalId("issuer-a", "subject") != PrincipalId("issuer-a", " subject ")


def test_tenant_is_a_separate_value_not_a_customer_identifier():
    tenant = TenantId("customer-123")
    assert tenant != "customer-123"
    assert tenant == TenantId("customer-123")
    assert hash(tenant) == hash(TenantId("customer-123"))
    assert tenant != TenantId("other")
    assert [field.name for field in fields(TenantId)] == ["value"]
    with pytest.raises(TypeError):
        ResourceRef("document", "id", "customer-123")


@pytest.mark.parametrize("value, field, replacement", [
    (PrincipalId("i", "s"), "subject", "other"),
    (TenantId("a"), "value", "b"),
    (ResourceRef("document", "id", TenantId("a")), "resource_id", "other"),
    (context(), "correlation_id", "other"),
    (context(), "permissions", frozenset({Permission.ANALYSIS_RUN})),
])
def test_frozen_records_reject_assignment(value, field, replacement):
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, replacement)


def test_resource_identity_includes_type_id_and_owner():
    resource = ResourceRef("document", "id", TenantId("a"))
    assert resource == ResourceRef("document", "id", TenantId("a"))
    assert hash(resource) == hash(ResourceRef("document", "id", TenantId("a")))
    assert resource != ResourceRef("document", "id", TenantId("b"))
    assert resource != ResourceRef("position", "id", TenantId("a"))
    assert resource != ResourceRef("document", "other", TenantId("a"))


def test_context_defensively_freezes_permissions_and_nested_values():
    supplied = {Permission.DOCUMENTS_READ}
    scope = context(supplied)
    supplied.add(Permission.DOCUMENTS_DELETE)
    assert scope.permissions == frozenset({Permission.DOCUMENTS_READ})
    assert isinstance(scope.permissions, frozenset)
    with pytest.raises(AttributeError):
        scope.permissions.add(Permission.ANALYSIS_RUN)
    with pytest.raises(FrozenInstanceError):
        scope.principal.subject = "other"
    with pytest.raises(FrozenInstanceError):
        scope.tenant_id.value = "other"


def test_context_equality_hashing_and_no_implicit_permissions():
    assert context().permissions == frozenset()
    assert context() == context()
    assert hash(context()) == hash(context())
    first = context(frozenset({Permission.ANALYSIS_RUN, Permission.PORTFOLIO_READ}))
    second = context((Permission.PORTFOLIO_READ, Permission.ANALYSIS_RUN))
    assert first == second
    assert hash(first) == hash(second)
    assert first != context()
    assert first != SecurityContext(first.principal, first.tenant_id, first.permissions, "other")
    assert first != SecurityContext(PrincipalId("other", "s"), first.tenant_id, first.permissions, first.correlation_id)
    assert first != SecurityContext(first.principal, TenantId("b"), first.permissions, first.correlation_id)
    assert {field.name for field in fields(SecurityContext)} == {
        "principal", "tenant_id", "permissions", "correlation_id",
    }


@pytest.mark.parametrize("principal, tenant", [("user", TenantId("a")), (None, TenantId("a")), (PrincipalId("i", "s"), "a")])
def test_context_rejects_untyped_identity(principal, tenant):
    with pytest.raises(TypeError):
        SecurityContext(principal, tenant, frozenset(), "op")


def test_explicit_permission_catalog_and_external_conversion():
    assert {permission.value for permission in Permission} == {
        "analysis:run", "portfolio:read", "documents:read", "documents:ingest",
        "documents:delete", "scenarios:evaluate", "criteria:evaluate",
        "decisions:evaluate", "diagnostics:read",
    }
    parsed = Permission("documents:read")
    assert context(frozenset({parsed})).permissions == frozenset({Permission.DOCUMENTS_READ})


@pytest.mark.parametrize("value", ["unknown:permission", "*", "", "industrial:execute"])
def test_unknown_external_permissions_are_rejected(value):
    with pytest.raises(ValueError):
        Permission(value)


@pytest.mark.parametrize("permissions", [
    ["analysis:run"], ["unknown:permission"], [1], [None], None, "analysis:run", "", {},
    [Permission.ANALYSIS_RUN, "analysis:run"],
    ["analysis:run", Permission.ANALYSIS_RUN],
])
def test_context_rejects_untyped_or_invalid_permissions(permissions):
    with pytest.raises(TypeError):
        context(permissions)


@pytest.mark.parametrize("error", [AuthenticationRequired, InvalidCredentials, PermissionDenied, TenantAccessDenied])
def test_security_exception_hierarchy(error):
    assert issubclass(error, SecurityError)
    with pytest.raises(SecurityError):
        raise error("safe reason")
    assert not hasattr(error(), "status_code")
    assert not issubclass(error, ValueError)
    assert not issubclass(error, TypeError)
    assert issubclass(TenantAccessDenied, PermissionDenied)
    assert not issubclass(InvalidCredentials, PermissionDenied)


def test_ports_accept_structural_test_doubles():
    principal = PrincipalId("test-issuer", "test-subject")
    tenant = TenantId("test-tenant")

    class TestAuthenticator:
        def authenticate(self, credentials: str | None) -> PrincipalId:
            if credentials is None:
                raise AuthenticationRequired()
            if credentials != "test-only-credential":
                raise InvalidCredentials()
            return principal

    class TestMembershipResolver:
        def is_member(self, principal: PrincipalId, tenant_id: TenantId) -> bool:
            return principal == PrincipalId("test-issuer", "test-subject") and tenant_id == tenant

    class TestAuthorizer:
        def authorize(self, context: SecurityContext, permission: Permission,
                      resource: ResourceRef | None = None) -> None:
            if resource is not None and resource.tenant_id != context.tenant_id:
                raise TenantAccessDenied()
            if permission not in context.permissions:
                raise PermissionDenied()

    authenticator: Authenticator = TestAuthenticator()
    memberships: MembershipResolver = TestMembershipResolver()
    authorizer: Authorizer = TestAuthorizer()
    assert authenticator.authenticate("test-only-credential") == principal
    with pytest.raises(AuthenticationRequired):
        authenticator.authenticate(None)
    with pytest.raises(InvalidCredentials):
        authenticator.authenticate("invalid")
    assert memberships.is_member(principal, tenant)
    assert not memberships.is_member(principal, TenantId("other"))
    scope = SecurityContext(principal, tenant, frozenset({Permission.DOCUMENTS_READ}), "test-op")
    assert authorizer.authorize(scope, Permission.DOCUMENTS_READ, ResourceRef("document", "id", tenant)) is None
    assert authorizer.authorize(scope, Permission.DOCUMENTS_READ) is None
    with pytest.raises(PermissionDenied):
        authorizer.authorize(scope, Permission.DOCUMENTS_DELETE)
    with pytest.raises(TenantAccessDenied):
        authorizer.authorize(scope, Permission.DOCUMENTS_READ, ResourceRef("document", "id", TenantId("other")))
