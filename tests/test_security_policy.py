"""Authorization over explicit test identities; no identity provider required."""
from dataclasses import FrozenInstanceError, replace

import pytest

from security.errors import (
    AuthenticationRequired, InvalidCredentials, PermissionDenied,
    SecurityError, TenantAccessDenied, IdentityInfrastructureError,
)
from security.memberships import (
    ConfiguredMembershipDirectory, MembershipDirectory, TenantMembership,
)
from security.models import PrincipalId, ResourceRef, SecurityContext, TenantId
from security.permissions import Permission as P
from security.policy import SecurityContextFactory, TenantAuthorizer
from security.ports import Authenticator, Authorizer, MembershipResolver
from security.roles import ROLE_PERMISSIONS, Role, permissions_for_roles


PRINCIPAL = PrincipalId("test-issuer", "test-subject")
TENANT_A = TenantId("test-tenant-a")
TENANT_B = TenantId("test-tenant-b")


def membership(tenant=TENANT_A, roles=(Role.ANALYST,), principal=PRINCIPAL):
    return TenantMembership(principal, tenant, frozenset(roles))


def directory():
    return ConfiguredMembershipDirectory([
        membership(), membership(TENANT_B, (Role.VIEWER,)),
    ])


def context(tenant=TENANT_A, permissions=None, principal=PRINCIPAL):
    return SecurityContext(
        principal, tenant,
        membership(tenant).permissions if permissions is None else permissions,
        "test-correlation",
    )


class TestOnlyAuthenticator:
    def authenticate(self, credentials: str | None) -> PrincipalId:
        if credentials is None:
            raise AuthenticationRequired()
        if credentials != "explicit-test-credential":
            raise InvalidCredentials()
        return PRINCIPAL


def test_policy_is_compatible_with_c11_ports():
    configured = directory()
    boolean_port: MembershipResolver = configured
    directory_port: MembershipDirectory = configured
    auth_port: Authenticator = TestOnlyAuthenticator()
    authorizer: Authorizer = TenantAuthorizer(directory_port)
    scope = SecurityContextFactory(auth_port, directory_port).create(
        "explicit-test-credential", TENANT_A, "op",
    )
    assert boolean_port.is_member(PRINCIPAL, TENANT_A)
    assert authorizer.authorize(scope, P.ANALYSIS_RUN) is None


def test_permission_and_owned_resource_are_allowed():
    policy = TenantAuthorizer(directory())
    assert policy.authorize(context(), P.DOCUMENTS_READ) is None
    assert policy.authorize(context(), P.DOCUMENTS_READ, ResourceRef("document", "doc", TENANT_A)) is None


def test_permission_must_be_present_even_when_directory_grants_it():
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(directory()).authorize(context(permissions=frozenset()), P.ANALYSIS_RUN)


def test_authoritative_membership_is_required_even_with_all_permissions():
    with pytest.raises(TenantAccessDenied):
        TenantAuthorizer(ConfiguredMembershipDirectory([])).authorize(
            context(permissions=frozenset(P)), P.DOCUMENTS_READ,
        )


@pytest.mark.parametrize("principal", [PrincipalId("other-issuer", "test-subject"), PrincipalId("test-issuer", "other")])
def test_full_principal_identity_is_used_for_membership(principal):
    with pytest.raises(TenantAccessDenied):
        TenantAuthorizer(directory()).authorize(context(principal=principal), P.DOCUMENTS_READ)


@pytest.mark.parametrize("resource_type", ["document", "position", "shared_document", "global_policy"])
def test_cross_tenant_and_shared_resources_have_no_bypass(resource_type):
    with pytest.raises(TenantAccessDenied):
        TenantAuthorizer(directory()).authorize(
            context(), P.DOCUMENTS_READ, ResourceRef(resource_type, "same-id", TENANT_B),
        )


def test_customer_name_or_global_label_does_not_establish_membership():
    scope = context(tenant=TenantId("customer-123"))
    with pytest.raises(TenantAccessDenied):
        TenantAuthorizer(directory()).authorize(scope, P.DOCUMENTS_READ)


def test_factory_resolves_different_roles_each_time_tenant_changes():
    factory = SecurityContextFactory(TestOnlyAuthenticator(), directory())
    a = factory.create("explicit-test-credential", TENANT_A, "a")
    b = factory.create("explicit-test-credential", TENANT_B, "b")
    a_again = factory.create("explicit-test-credential", TENANT_A, "a2")
    assert P.ANALYSIS_RUN in a.permissions
    assert P.ANALYSIS_RUN not in b.permissions
    assert a_again.permissions == a.permissions
    assert b.permissions == ROLE_PERMISSIONS[Role.VIEWER]
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(directory()).authorize(b, P.ANALYSIS_RUN)
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(directory()).authorize(replace(a, tenant_id=TENANT_B), P.DOCUMENTS_READ)


def test_identical_resource_ids_are_isolated_by_the_active_owner_tenant():
    configured = directory()
    factory = SecurityContextFactory(TestOnlyAuthenticator(), configured)
    policy = TenantAuthorizer(configured)
    for active, other in ((TENANT_A, TENANT_B), (TENANT_B, TENANT_A)):
        scope = factory.create("explicit-test-credential", active, "op")
        assert policy.authorize(scope, P.DOCUMENTS_READ, ResourceRef("document", "same-id", active)) is None
        with pytest.raises(TenantAccessDenied):
            policy.authorize(scope, P.DOCUMENTS_READ, ResourceRef("document", "same-id", other))


def test_combined_roles_grant_only_the_explicit_tenant_local_union():
    configured = ConfiguredMembershipDirectory([
        membership(roles=(Role.ANALYST, Role.APPROVER)),
        membership(TENANT_B, (Role.VIEWER,)),
    ])
    factory = SecurityContextFactory(TestOnlyAuthenticator(), configured)
    a = factory.create("explicit-test-credential", TENANT_A, "a")
    b = factory.create("explicit-test-credential", TENANT_B, "b")
    assert a.permissions == ROLE_PERMISSIONS[Role.ANALYST] | ROLE_PERMISSIONS[Role.APPROVER]
    assert P.DECISIONS_EVALUATE not in b.permissions
    assert P.DOCUMENTS_DELETE not in a.permissions


@pytest.mark.parametrize("tenant", [None, "test-tenant-a", TenantId("missing")])
def test_no_default_tenant_or_implicit_membership(tenant):
    with pytest.raises(TenantAccessDenied):
        SecurityContextFactory(TestOnlyAuthenticator(), directory()).create(
            "explicit-test-credential", tenant, "op",
        )


@pytest.mark.parametrize("credentials, error", [(None, AuthenticationRequired), ("bad", InvalidCredentials)])
def test_factory_preserves_authentication_failures(credentials, error):
    with pytest.raises(error):
        SecurityContextFactory(TestOnlyAuthenticator(), directory()).create(credentials, TENANT_A, "op")


def test_factory_does_not_accept_client_roles_or_permissions():
    factory = SecurityContextFactory(TestOnlyAuthenticator(), directory())
    with pytest.raises(TypeError):
        factory.create("explicit-test-credential", TENANT_A, "op", roles=[Role.TENANT_ADMIN])
    with pytest.raises(TypeError):
        factory.create("explicit-test-credential", TENANT_A, "op", permissions=frozenset(P))


def test_injected_authenticator_must_return_a_principal():
    class InvalidAuthenticator:
        def authenticate(self, credentials):
            return "test-subject"

    with pytest.raises(AuthenticationRequired):
        SecurityContextFactory(InvalidAuthenticator(), directory()).create("credential", TENANT_A, "op")


def test_admin_and_approver_have_no_approval_or_physical_permissions():
    configured = ConfiguredMembershipDirectory([
        membership(roles=(Role.TENANT_ADMIN,)),
        membership(TENANT_B, (Role.APPROVER,)),
    ])
    factory = SecurityContextFactory(TestOnlyAuthenticator(), configured)
    admin = factory.create("explicit-test-credential", TENANT_A, "admin")
    approver = factory.create("explicit-test-credential", TENANT_B, "approver")
    policy = TenantAuthorizer(configured)
    assert policy.authorize(admin, P.DOCUMENTS_INGEST) is None
    assert policy.authorize(approver, P.DECISIONS_EVALUATE) is None
    for permission in (P.ANALYSIS_RUN, P.SCENARIOS_EVALUATE, P.CRITERIA_EVALUATE, P.DECISIONS_EVALUATE):
        with pytest.raises(PermissionDenied):
            policy.authorize(admin, permission)
    for scope in (admin, approver):
        for permission in ("decisions:approve", "industrial:execute"):
            with pytest.raises(ValueError):
                P(permission)
            with pytest.raises(PermissionDenied):
                policy.authorize(scope, permission)
    assert len(P) == 9


def test_role_mapping_is_explicit_and_has_no_inheritance():
    assert ROLE_PERMISSIONS[Role.VIEWER] == frozenset({P.PORTFOLIO_READ, P.DOCUMENTS_READ})
    assert ROLE_PERMISSIONS[Role.ANALYST] == frozenset({
        P.PORTFOLIO_READ, P.DOCUMENTS_READ, P.ANALYSIS_RUN, P.SCENARIOS_EVALUATE, P.CRITERIA_EVALUATE,
    })
    assert ROLE_PERMISSIONS[Role.APPROVER] == frozenset({P.PORTFOLIO_READ, P.DOCUMENTS_READ, P.DECISIONS_EVALUATE})
    assert ROLE_PERMISSIONS[Role.TENANT_ADMIN] == frozenset({
        P.DOCUMENTS_READ, P.DOCUMENTS_INGEST, P.DOCUMENTS_DELETE, P.DIAGNOSTICS_READ,
    })
    assert permissions_for_roles(frozenset({Role.VIEWER, Role.APPROVER})) == (
        ROLE_PERMISSIONS[Role.VIEWER] | ROLE_PERMISSIONS[Role.APPROVER]
    )
    assert permissions_for_roles(frozenset()) == frozenset()
    with pytest.raises(TypeError):
        ROLE_PERMISSIONS[Role.VIEWER] = frozenset(P)


def test_explicit_membership_without_roles_grants_nothing():
    configured = ConfiguredMembershipDirectory([membership(roles=())])
    assert configured.is_member(PRINCIPAL, TENANT_A)
    scope = SecurityContextFactory(TestOnlyAuthenticator(), configured).create("explicit-test-credential", TENANT_A, "op")
    assert scope.permissions == frozenset()
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(configured).authorize(scope, P.DOCUMENTS_READ)


@pytest.mark.parametrize("permission", ["documents:read", "unknown:permission", "*", None, 1])
def test_raw_and_unknown_required_permissions_are_denied(permission):
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(directory()).authorize(context(), permission)


@pytest.mark.parametrize("role", ["viewer", "unknown", "*", None])
def test_untyped_roles_are_rejected_without_implicit_grants(role):
    with pytest.raises(TypeError):
        TenantMembership(PRINCIPAL, TENANT_A, [role])
    with pytest.raises(TypeError):
        permissions_for_roles(frozenset({role}))


@pytest.mark.parametrize("roles", [None, "viewer", {}, [Role.VIEWER, "viewer"]])
def test_invalid_role_collections_are_rejected_before_deduplication(roles):
    with pytest.raises(TypeError):
        TenantMembership(PRINCIPAL, TENANT_A, roles)


@pytest.mark.parametrize("principal, tenant", [("user", TENANT_A), (PRINCIPAL, "tenant")])
def test_membership_and_lookup_require_typed_identifiers(principal, tenant):
    with pytest.raises(TypeError):
        TenantMembership(principal, tenant, frozenset())
    with pytest.raises(TypeError):
        directory().resolve(principal, tenant)


def test_context_cannot_elevate_even_for_a_member():
    policy = TenantAuthorizer(directory())
    for permission in (P.DOCUMENTS_DELETE, P.DIAGNOSTICS_READ, P.DECISIONS_EVALUATE):
        scope = context(permissions=context().permissions | {permission})
        with pytest.raises(PermissionDenied):
            policy.authorize(scope, permission)
        # Reject the forged context as a whole, even for a lesser action.
        with pytest.raises(PermissionDenied):
            policy.authorize(scope, P.DOCUMENTS_READ)


def test_directory_configuration_is_a_frozen_defensive_snapshot():
    roles = {Role.VIEWER}
    record = TenantMembership(PRINCIPAL, TENANT_A, roles)
    supplied = [record]
    configured = ConfiguredMembershipDirectory(supplied)
    roles.add(Role.TENANT_ADMIN)
    supplied.clear()
    assert configured.resolve(PRINCIPAL, TENANT_A).roles == frozenset({Role.VIEWER})
    with pytest.raises(FrozenInstanceError):
        record.tenant_id = TENANT_B
    with pytest.raises(FrozenInstanceError):
        configured._memberships = {}
    with pytest.raises(TypeError):
        configured._memberships[(PRINCIPAL, TENANT_B)] = record


def test_duplicate_configuration_is_rejected_not_merged():
    with pytest.raises(ValueError):
        ConfiguredMembershipDirectory([membership(), membership(roles=(Role.TENANT_ADMIN,))])


@pytest.mark.parametrize("invalid", [None, {"principal": "user"}, "membership"])
def test_untyped_configuration_is_rejected(invalid):
    with pytest.raises(TypeError):
        ConfiguredMembershipDirectory([invalid])


@pytest.mark.parametrize("invalid", [None, {}, "context"])
def test_missing_or_untyped_context_never_grants_access(invalid):
    with pytest.raises(AuthenticationRequired):
        TenantAuthorizer(directory()).authorize(invalid, P.DOCUMENTS_READ)


def test_untyped_resource_is_denied():
    with pytest.raises(PermissionDenied):
        TenantAuthorizer(directory()).authorize(context(), P.DOCUMENTS_READ, {"tenant_id": TENANT_A})


def test_current_authoritative_membership_is_resolved_on_every_call():
    class ChangingDirectory:
        current = membership()
        calls = 0

        def resolve(self, principal, tenant_id):
            self.calls += 1
            return self.current

        def is_member(self, principal, tenant_id):
            return self.resolve(principal, tenant_id) is not None

    configured = ChangingDirectory()
    policy = TenantAuthorizer(configured)
    scope = context()
    assert policy.authorize(scope, P.ANALYSIS_RUN) is None
    configured.current = membership(roles=(Role.VIEWER,))
    with pytest.raises(PermissionDenied):
        policy.authorize(scope, P.ANALYSIS_RUN)
    configured.current = None
    with pytest.raises(TenantAccessDenied):
        policy.authorize(scope, P.DOCUMENTS_READ)
    assert configured.calls == 3


@pytest.mark.parametrize("wrong", [membership(TENANT_B), membership(principal=PrincipalId("other", "other")), True])
def test_mismatched_directory_results_never_establish_membership(wrong):
    class InvalidDirectory:
        def resolve(self, principal, tenant_id):
            return wrong

    with pytest.raises(TenantAccessDenied):
        TenantAuthorizer(InvalidDirectory()).authorize(context(), P.DOCUMENTS_READ)


@pytest.mark.parametrize("error", [RuntimeError("directory unavailable"), SecurityError("directory denied")])
def test_directory_failures_propagate_without_partial_results(error):
    class FailingDirectory:
        def resolve(self, principal, tenant_id):
            raise error

    expected = type(error) if isinstance(error, SecurityError) else IdentityInfrastructureError
    with pytest.raises(expected) as caught:
        TenantAuthorizer(FailingDirectory()).authorize(context(), P.DOCUMENTS_READ)
    if isinstance(error, SecurityError):
        assert caught.value is error
    with pytest.raises(expected) as caught:
        SecurityContextFactory(TestOnlyAuthenticator(), FailingDirectory()).create("explicit-test-credential", TENANT_A, "op")
    if isinstance(error, SecurityError):
        assert caught.value is error


def test_same_inputs_have_deterministic_outcomes():
    policy = TenantAuthorizer(directory())
    outcomes = []
    for _ in range(3):
        assert policy.authorize(context(), P.ANALYSIS_RUN) is None
        with pytest.raises(PermissionDenied) as caught:
            policy.authorize(context(), P.DOCUMENTS_DELETE)
        outcomes.append((type(caught.value), str(caught.value)))
    assert outcomes[0] == outcomes[1] == outcomes[2]
