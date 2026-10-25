"""Explicit test-only identity; never imported by production composition."""
from api.security import APISecurity
from security.errors import AuthenticationRequired, InvalidCredentials
from security.memberships import ConfiguredMembershipDirectory, TenantMembership
from security.models import PrincipalId, TenantId
from security.roles import Role


PRINCIPAL = PrincipalId("https://tests.example", "analysis-test-subject")
TENANT = TenantId("analysis-test-tenant")
TOKEN = "explicit-test-only-credential"


class TestAuthenticator:
    def authenticate(self, credentials):
        if credentials is None:
            raise AuthenticationRequired()
        if credentials != TOKEN:
            raise InvalidCredentials()
        return PRINCIPAL


def security_fixture():
    return APISecurity(TestAuthenticator(), ConfiguredMembershipDirectory([
        TenantMembership(PRINCIPAL, TENANT, frozenset({Role.ANALYST})),
    ]))


def context_fixture():
    return security_fixture().context_factory.create(TOKEN, TENANT, "test-correlation")
