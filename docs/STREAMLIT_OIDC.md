# Streamlit OIDC / Business (C1.5)

Streamlit is the OIDC client; there is no BFF. Native public APIs are `st.login`,
`st.user`, `st.user.tokens.access` and `st.logout`. Streamlit >=1.62.0 and
Authlib >=1.9.1,<2 are required. Backend RS256/RFC 9068 validation stays mandatory.
No Keycloak installation or running provider is included in this increment.

## External secrets

Keep actual secrets outside the project and source control. Configure Streamlit's
public `secrets.files` option to point to an access-controlled external TOML file,
for example with `streamlit run app.py --secrets.files C:/private/indai-auth.toml`.
Do not create that file in this repository. `.streamlit/secrets.toml` is ignored
as defense against accidental staging, not a recommendation to store secrets here.

The external file needs an unnamed/default provider. Values below are placeholders,
not working credentials:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "<independently generated high-entropy secret>"
client_id = "indai-streamlit"
client_secret = "<secret managed outside the repository>"
server_metadata_url = "https://identity.dev.example/realms/indai-dev/.well-known/openid-configuration"
expose_tokens = ["access"]

[auth.client_kwargs]
scope = "openid profile"
code_challenge_method = "S256"
```

Use TLS for deployed Streamlit and API URLs. Loopback HTTP redirect is for local
development only. The issuer/JWKS must remain HTTPS under C1.3. Supply certificates
trusted by each runtime; never turn off verification. Disable protocol/header/body
debug logging in deployment: authorization codes, PKCE verifiers and credentials
must not be logged by proxies or dependency diagnostic settings.

## Proposed Keycloak configuration

- Use a maintained Keycloak version supporting the access-token-header option
  introduced in 26.2. Create realm `indai-dev` and a confidential OIDC client
  `indai-streamlit`; enable Standard Flow (Authorization Code) and require PKCE S256.
  Disable implicit flow, direct password grants and service accounts for this UI.
- Register only exact redirect URI `http://localhost:8501/oauth2callback` locally;
  add only the exact deployed HTTPS callback when deploying. Configure the matching
  logout redirect explicitly; no wildcard callback/origin policy.
- Enable Clients → Advanced → Fine grain OpenID Connect configuration →
  **Use "at+jwt" as access token header type**. This setting is off by default.
  Sign with RS256; do not enable lightweight/opaque tokens for this client.
- Add a dedicated audience mapper for the API identifier `indai-api`. Ensure
  `client_id` is the actual requesting client ID. For this single confidential
  client, a client-specific hardcoded string claim mapper can emit
  `client_id="indai-streamlit"`; never attach it to unrelated clients/scopes.
- Verify a genuinely issued access token includes `iss`, `sub`, `aud`, `exp`,
  `iat`, `jti`, `client_id` and protected `typ=at+jwt`, and passes OIDCAuthenticator.
  Enabling the header setting alone does not prove full profile compatibility.
  Keep the normal ID token for native OIDC login; never substitute it for access.
- Configure backend `INDAI_OIDC_ISSUER` to the exact HTTPS realm issuer,
  `INDAI_OIDC_AUDIENCE=indai-api`, and `INDAI_OIDC_JWKS_URL` to that realm's
  `/protocol/openid-connect/certs`. Discovery is used by the native client;
  the backend still uses its explicit preconfigured trusted JWKS endpoint.
- Keep roles/memberships in the backend's admin-controlled MembershipDirectory.
  Configure real issuer/subject/tenant memberships externally using C1.4's format.
  Keycloak role claims do not grant application permissions. No users are seeded.

This is a proposal verified against public configuration capabilities, not a claim
that an actual Keycloak instance has issued a compatible token in this workspace.
Live login, callback, certificate, logout and token-profile acceptance must be
tested against the selected provider before deployment; failures keep Business closed.

## Session and request boundaries

`GET /api/v1/session` requires a cryptographically valid access token and returns
issuer, subject, validated expiration and only that principal's tenant permissions.
It returns no token and sets `Cache-Control: no-store`. Invalid credentials are 401;
identity/configuration/listing infrastructure errors are 503. No analysis occurs.

Business compares the native ID identity with the backend-validated access-token
identity. Neither email nor frontend role claims establish identity/membership.
Every rerun, job start and polling cycle revalidates backend session metadata.
The user must explicitly choose a tenant with analysis:run; there is no default.
Analysis POST and AnalysisService independently recheck authorization.

Only immutable noncredential metadata is held in Business session state: principal,
native-login marker, expiration, tenant permissions, selected tenant and random
session epoch. Access tokens are read from the public native API for each HTTP
operation; they are never copied to session metadata or prompts/Inspector output.
A running operation owns its credential until completion/cancellation/timeout;
there are no process-global tokens or background refresh credentials.

Expiration is the earlier of native identity expiration and backend-validated
access-token expiration, with no frontend grace period. Expired/missing/mismatched
tokens block Business. Renewal is deliberate logout/login, not refresh-token
handling. Streamlit's persistent identity cookie is not evidence of a live API
token; the native framework owns its cookies and OIDC state/nonce/PKCE exchange.
Application code never reads cookies, authorization codes, query tokens or private
Streamlit structures. Other open browser tabs may retain native identity until
their session/token expires; logout is not global token revocation.

Logout and user/tenant changes detach and locally cancel Business jobs, erase
Business results, input fields and Inspector references. Each job captures
principal/tenant/epoch; completion is accepted only in that same live scope.
Cancellation does not cancel a backend request already executing. API errors and
permission revocation do not enable fallback execution. Backend configured
memberships remain a snapshot until administratively reloaded/restarted.

Supervisor technical UI remains disabled. Workspace, Industrial Gases, other
technical views and document administration remain outside this increment's
security boundary. Document/resource isolation is still pending, so this increment
does not make the prototype safe for shared enterprise document multi-tenancy.

References: [Streamlit 1.62 st.user](https://docs.streamlit.io/1.62.0/develop/api-reference/user/st.user),
[native login](https://docs.streamlit.io/develop/api-reference/user/st.login),
[Keycloak header option](https://www.keycloak.org/2025/04/keycloak-2620-released),
[Keycloak protocol mappers](https://www.keycloak.org/admin-api/protocol-mappers).
