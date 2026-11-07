# Streamlit OIDC / Business (C1.5)

## Current C1.6 closure status

As of 2026-10-31, native login, session integration, the fixed authorized pilot,
four correlated denials and manual identity transitions have been validated.
See [C1.6 final review](C1_6_FINAL_REVIEW.md) for current evidence, operational
commands and limitations. Historical pending statements below describe earlier
increments. Both private approval flags remain false; this is not production ready.

## C1.6-B2-B2.1 native session-only startup

For the controlled native-login stage, set `INDAI_STREAMLIT_SESSION_ONLY=1` only
in the Streamlit process. `app.py` renders `render_native_session_validation`
and stops before workspace state, vector-store initialization, provider discovery,
technical views or job setup. Login/logout and token access use the existing
public Streamlit APIs, with no second OAuth implementation. The existing backend
session check validates identity, expiry and permissions. Tenant selection starts
empty and must be explicit; the analysis button remains disabled even after
selection because this increment authorizes no analysis. This flag only narrows
available UI functionality and does not grant identity or permissions. Without
it, the existing app behavior is preserved; this does not secure other views.

The private TOML retains the existing confidential client, exact localhost
callback, access-only exposure and PKCE S256. Its client kwargs now explicitly
request `prompt=login` for a fresh human login. Do not log its secrets. The
controlled startup supplies `SSL_CERT_FILE` with the verified local public CA
only to the Streamlit process, `NO_PROXY=127.0.0.1,localhost` and the fixed local
API base URL. No certifi bundle, Windows store, Keycloak configuration or C1.3
policy is changed. Preflight used the installed native Streamlit/Authlib OAuth
client class to verify discovery and JWKS over HTTPS and confirm exact issuer
and S256 support, without performing a login or token exchange. The actual native
browser login and subsequent backend session result remain pending human action.
The session-only entry also replaces native auth-route log messages/exception
dumps with a fixed event string and suppresses HTTP/Authlib debug diagnostics;
provider error descriptions, URLs, claims and credentials are never emitted by
that logger. This is diagnostics filtering, not a change to OAuth validation.
The external private directory is the confirmed physical package-cache path
documented in the Keycloak README, not an assumed virtualized AppData location.

Start only after the temporary PKCE callback has stopped, with Streamlit bound
to 127.0.0.1:8501 and existing secrets passed through `--secrets.files`. Keycloak
and FastAPI remain active. Browser trust must be supplied by the approved isolated
profile. Do not bypass certificate errors or collect browser cookies/tokens for
diagnosis. A successful HTTP healthcheck proves availability, not authentication.

The reviewable concrete local fixture is in [Keycloak C1.6-A](../dev/keycloak/README.md):
realm `indai-ma-dev`, clients `indai-ma-streamlit` / `indai-ma-api`, HTTPS localhost,
external secrets and explicit local CA trust. It has not been started. The generic
example names below are illustrative; do not mix them with that fixture's names.

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

### C1.6-B2-B2.3 denial UI preparation

With an authenticated backend session but no analysis:run permission, the
session-only entry displays only the backend tenant/permission table and a
disabled Analyze button. Existing memberships may be selected in a separate
read-only tenant widget; this never sets BusinessSession.tenant_id, creates a
SecurityContext, or permits capture_business_request. An identity without any
memberships has no tenant selector. No first-run pilot panel is exposed to these
users. Logout/state invalidation clears this read-only widget with other Business
state. This is UI presentation, not a membership/authorization policy change.

The live negative POST tests are a separate, explicitly confirmed step after
manual viewer-a login and session verification. Keep approved=false, do not use
the UI Analyze button, and do not collect tokens in chat or on disk. The current
API returns generic forbidden HTTP 403 for both PermissionDenied and
TenantAccessDenied; distinguishing those types and proving no Supervisor/RAG/LLM
construction requires safe server-side evidence before a negative POST. Do not
infer exact exception types or absence of execution from a 403 response alone.

The optional private `[business_denial_probe]` section pins case, issuer, subject
and target tenant with `approved_post=false` until separate confirmation. It does
not change membership or authorization. The UI compares the backend-validated
principal, exact tenant listing, absence of analysis and pending jobs, and explicit
read-only selection. It uses the public native access token only for one fixed
loopback POST, with redirects/proxies/retries disabled and no token persistence.
Expected 403 results remain PENDING_REVIEW until server evidence is correlated.
Any non-403, invalid error body or transport failure blocks all later probes.
Each case/tenant is limited to one attempt per process; process restart is not
an automatic retry mechanism. No denied analysis capability is added to the UI.

API observability accepts only a single 32-character lowercase hex validation ID.
The security-error observer emits that ID, an allowlisted exception class,
authorization checkpoint and booleans for service-dependency/handler entry. It
does not change responses or enforcement. A rejection at CONTEXT_FACTORY or
OPERATION_AUTHORIZATION with both entry flags false establishes the rejection
before the handler could call AnalysisService. Source ordering and offline spies
additionally verify that Supervisor, RAG and providers are not constructed on
those denials. Do not label these booleans as measured constructor counters or
claim live evidence until the actual correlated server event is reviewed.

### Logout investigation and local fail-closed behavior

The old logout button ran in the script body, after the backend session GET.
That could delay handling a logout click while identity/session infrastructure
was slow. Cleanup also left the tenant widget selected and had no explicit
local guard if native logout failed or the browser redirect did not complete.
These are confirmed code gaps; they do not, by themselves, establish the exact
cause of a particular browser's apparently unchanged page.

The logout button now uses an on_click callback before the main script's session
refresh. It immediately blocks Business, removes selected tenant/results and
detaches/cancels Business jobs, then calls public st.logout. A cancellation error
does not preserve authorization. While native logout is pending/failed, the app
does not read the old native access token or call the backend to reconstruct the
Business session. It offers explicit retry or fresh native login, never an
automatic login fallback. The guard clears after observed native anonymous state
or a new native session; renewed access still requires backend validation and a
new explicit tenant selection. No public st.user fields/cookies are edited by
application code.

Safe events BUSINESS_LOGOUT_REQUESTED, NATIVE_LOGOUT_RETURNED (native_logged_in
boolean), NATIVE_LOGOUT_FAILED and cleanup/cancellation failure codes contain no
exception messages, credentials, cookies or redirect parameters. An event proves
only the corresponding server action, not successful browser navigation/SSO
termination. Native auth-route messages remain filtered as described above.

Installed Streamlit 1.62 clears current native user info and redirects the browser
through its logout endpoint. That endpoint clears local auth cookies and attempts
provider logout when discovery offers end_session_endpoint; Keycloak does offer
it. Provider lookup failure can fall back to local logout. Provider logout may
need user interaction; its completion is not inferred from calling st.logout.
Other existing tabs may retain their own sessions. Neither local state cleanup
nor cancelling a job revokes an already issued JWT or cancels an HTTP analysis
already accepted by the backend. This is not a global/durable token-revocation
mechanism. The current pilot remains approved=false and no live analysis is run.

Manual verification: reload the updated frontend, click logout once, check the
safe server events and browser transition, and complete any provider confirmation
in person. Expect anonymous UI with no selected tenant/results and Business
blocked. Only then start a fresh login as analyst-a; prompt=login remains required.
Expect backend session validation and an empty tenant selector until explicit
selection. Keep approved=false throughout. A controlled Streamlit restart loads
the fix but is not itself evidence of logout or SSO termination.

After the controlled restart, live safe events did confirm
BUSINESS_LOGOUT_REQUESTED followed by NATIVE_LOGOUT_RETURNED with
native_logged_in=false, and session/identity/tenant/permission readiness all
false. A later native session was backend-validated with the expected identity,
no explicit tenant selected and review_approved=false. This establishes observed
local logout and renewal for that attempt; it does not establish provider-wide
SSO termination or reconstruct the precise cause of the original browser symptom.

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
