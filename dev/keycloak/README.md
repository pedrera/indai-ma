# Keycloak local — C1.6-B1.3 (controlled startup verified)

> **Current status (2026-10-31): C1.6 — PILOTO LOCAL VALIDADO CON LIMITACIONES.**
> See the [final review and current runbook](../../docs/C1_6_FINAL_REVIEW.md) for
> executed evidence, residual risks, start/stop commands and certificate deadlines.
> Preparation/pending/trust instructions below record earlier stages; they are not
> a claim that later validation remains pending or permission to repeat setup.
> Current API trust is process-local via INDAI_OIDC_CA_FILE, not a new certifi or
> global Windows trust modification. Do not repeat legacy trust installation or
> token-file procedures for the current in-memory native pilot.

Development fixture only. Keycloak is an optional reference OIDC provider, not a
mandatory product dependency. No production users, grants, passwords or secrets
are provided. No container is started by the preparation/validation scripts.

## Selected image and resources

- Keycloak **26.8.1**, official release dated 2026-10-13, pinned to its Linux amd64
  manifest digest in `compose.yaml`; no `latest` tag or locally built image.
- Dedicated Compose project/network and one service; publish only
  **127.0.0.1:8443**. Internal management HTTP port 9000 is not published. The
  dedicated bridge network permits outbound connections, explicitly approved in
  C1.6-B1.3; no host networking or privileged mode.
- 2 CPU, 1536 MiB memory limit, 768 MiB reservation, heap 256–768 MiB, 256 PIDs.
  These leave headroom in the reported 7.6 GiB Docker VM. First startup/import may
  take longer than a minute; observe health before using it.
- H2 `dev-file` in a named volume persists realm/user data. The `start` command
  honors `KC_HTTP_ENABLED=false`, unlike `start-dev` in this version. This fixture
  remains for local development only; no PostgreSQL is introduced and using
  `start` does not make it production-ready.
- Bash TCP readiness probe on the internal management interface (no curl/custom
  image), 120-second startup allowance. Runtime health was verified in C1.6-B1.3.
  Restart policy is disabled.

## One coherent set of URLs

| Purpose | Value |
| --- | --- |
| Keycloak origin / KC_HOSTNAME | `https://localhost:8443` |
| Realm / issuer | `https://localhost:8443/realms/indai-ma-dev` |
| Discovery | `https://localhost:8443/realms/indai-ma-dev/.well-known/openid-configuration` |
| JWKS | `https://localhost:8443/realms/indai-ma-dev/protocol/openid-connect/certs` |
| Authorization | `https://localhost:8443/realms/indai-ma-dev/protocol/openid-connect/auth` |
| Token | `https://localhost:8443/realms/indai-ma-dev/protocol/openid-connect/token` |
| Browser Streamlit | `http://localhost:8501` |
| Login **and logout** callback | `http://localhost:8501/oauth2callback` |
| Local FastAPI | `http://127.0.0.1:8000` |
| API audience | `indai-ma-api` |

Do not substitute `127.0.0.1`, container service names, other ports or another
realm in the issuer. The certificate has localhost DNS and 127.0.0.1 IP SANs,
but the issuer stays exactly as above. Loopback HTTP is limited to the existing
local UI/API; OIDC discovery/JWKS and token exchange use HTTPS.

## Realm and clients

`realm/indai-ma-dev-realm.json` imports `indai-ma-dev` on first startup. RS256 is
the realm default; access lifetime is 300 seconds, idle session 900, maximum 1800.
Self-registration, email login and password reset are disabled for these fixtures.

`indai-ma-streamlit` is confidential, uses client-secret authentication and
Authorization Code + required PKCE S256. Redirect/origin lists contain only exact
localhost values. The at+jwt client attribute is explicitly enabled. A client-local
audience mapper targets `indai-ma-api`; a client-local string mapper emits
`client_id=indai-ma-streamlit` in access tokens only. Standard Keycloak token
issuance must supply `iss/sub/aud/exp/iat/jti`; actual emitted values are a pending
live check, not established by JSON syntax validation. ID tokens retain their
native OIDC role and are never accepted by the API as access credentials.

`indai-ma-api` is bearer-only/audience-only, with no redirects or auth flows.
Neither client enables implicit flow, Direct Access Grants, service accounts or
full scope. The UI client also disables refresh tokens, device/CIBA/token exchange
and lightweight access tokens. No business-role mapper is added.

## Development identities and backend authorization

User IDs are explicit stable UUIDs for this fixture, **not production identities**.
Passwords are environment placeholders in the import, resolved from an external
file at startup. There are no real names/emails or committed usable passwords.

| Username | Subject UUID suffix | Backend tenant | Backend role / analysis |
| --- | --- | --- | --- |
| analyst-a | `000000000001` | tenant-alpha | analyst / allowed |
| analyst-b | `000000000002` | tenant-beta | analyst / allowed |
| viewer-a | `000000000003` | tenant-alpha | viewer / denied |
| outsider | `000000000004` | none | no membership / denied |

Full UUIDs and the exact issuer are in `backend-memberships.json`. The preparer
copies it to the external directory; configure the backend to load that copy.
Membership is by issuer+subject, never by username/email or client-provided roles.
No seed hook is added to application code. Changing the username does not change
authorization. Verify the imported UUIDs before the first integration test;
unexpected subjects must fail closed rather than acquire fallback membership.

## Commands — only after reviewing and approving this configuration

The commands below have **not** been run against a live Keycloak. Run from the
repository root in PowerShell. Never paste actual credentials into Git or chat.

### 1. Create external private configuration

```powershell
$privateRoot = Join-Path $env:LOCALAPPDATA 'indai-ma-keycloak-dev'
$compose = (Resolve-Path 'dev/keycloak/compose.yaml').Path
.\venv\Scripts\python.exe dev/keycloak/prepare_local.py --output $privateRoot
docker compose --env-file "$privateRoot/compose.env" -f $compose config --quiet
```

The preparer refuses existing or repository-local output directories. It creates
an owner-protected directory (Windows current-user and SYSTEM ACL; Unix mode 0700),
unique random bootstrap/user passwords, a client secret and a separate cookie
secret. Nothing is printed except completion. Generated files:

- `compose.env`: paths, bootstrap credentials, client secret, four user passwords.
- `streamlit.toml`: native OIDC settings, client/cookie secrets and access exposure.
- `backend.env` and `backend-memberships.json`: exact backend configuration.
- `ca/`: 30-day local root certificate/private key, PEM and public DER certificate.
- `tls/`: 14-day localhost server certificate chain and private key only.

Only `tls/` is mounted read-only; the CA private key is never mounted. Treat the
entire directory and Docker data volume as sensitive. Docker administrators can
inspect container environment variables. Use a local editor to retrieve test
passwords when needed; do not print the environment, run `config` without
`--quiet`, enable debug header/body logging or commit exports of the realm/volume.

### 2. Explicit local TLS trust (separate security action)

**Review before executing:** these commands change trust, not TLS verification.
They are not executed by Compose, preparation, tests or the application.

C1.3 sets `trust_env=False`, so `SSL_CERT_FILE` alone does not configure its JWKS
trust. `trust_python.py` explicitly adds this generated public CA to **this venv's
certifi bundle**, preserving a backup and all existing public roots. It refuses
global Python and refuses overwriting a subsequently changed bundle. No validator,
hostname check, signature check, audience or issuer check is modified/disabled.

```powershell
.\venv\Scripts\python.exe dev/keycloak/trust_python.py --ca-file "$privateRoot/ca/ca-cert.pem" --install
# Browser/PowerShell trust, current user only. Review the certificate first.
$localCa = Import-Certificate -FilePath "$privateRoot/ca/ca-cert.cer" -CertStoreLocation Cert:\CurrentUser\Root
```

Restart Python clients after changing trust. Protect the CA key: this root can
issue other certificates if stolen. It is temporary local trust, never suitable
for a shared/production Python environment. Dependency upgrades can replace the
certifi file: re-check the bundle and the helper's original-backup checks before
reapplying; do not disable verification to work around a mismatch.

Rollback the Python change explicitly with the same CA file:

```powershell
.\venv\Scripts\python.exe dev/keycloak/trust_python.py --ca-file "$privateRoot/ca/ca-cert.pem" --remove
```

Remove only the matching generated root from the current user's certificate store
using its reviewed thumbprint/certificate manager. Do not remove other roots.
Expired certificates require a deliberate new configuration/rotation; the preparer
does not overwrite secrets or regenerate credentials silently.

### 3. Start / inspect / stop Keycloak (pending approval)

```powershell
docker compose --env-file "$privateRoot/compose.env" -f $compose up -d
docker compose --env-file "$privateRoot/compose.env" -f $compose ps
docker compose --env-file "$privateRoot/compose.env" -f $compose logs --tail 60 keycloak
.\venv\Scripts\python.exe dev/keycloak/check_local.py

# Stop but retain the container and data:
docker compose --env-file "$privateRoot/compose.env" -f $compose stop
# Remove the container/network while preserving the development database volume:
docker compose --env-file "$privateRoot/compose.env" -f $compose down
```

No `down -v`/volume deletion command is provided. Import-at-start skips an existing
realm; editing the JSON or changing environment passwords does not update a
persisted realm. A reset or secret rotation must be deliberate and separately
reviewed. Do not re-import over a running realm or silently reassign subject IDs.

### 4. Configure backend and native Streamlit (later integration)

```powershell
$env:INDAI_OIDC_ISSUER = 'https://localhost:8443/realms/indai-ma-dev'
$env:INDAI_OIDC_AUDIENCE = 'indai-ma-api'
$env:INDAI_OIDC_JWKS_URL = "$($env:INDAI_OIDC_ISSUER)/protocol/openid-connect/certs"
$env:INDAI_MEMBERSHIPS_FILE = Join-Path $privateRoot 'backend-memberships.json'
.\venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000
# In a second PowerShell, set $privateRoot again:
.\venv\Scripts\python.exe -m streamlit run app.py --secrets.files "$privateRoot/streamlit.toml"
```

Browse **http://localhost:8501**, choose Business, log in and explicitly select an
authorized tenant. Supervisor technical mode remains disabled; Workspace and other
direct views/document access are still outside this security boundary.

## Executed checks versus pending live checks

C1.6-A executed only: Docker version/daemon/Linux amd64 checks, no-active-container
check, local listener check, official image-manifest inspection without layer pull,
Compose parsing/rendering with temporary nonsecret test values, offline realm/
permission/URL/TLS-generation tests. Real trust stores were not changed. Real
credentials/TLS files were not generated for the user. No services were started.

After approval/startup, `check_local.py` checks HTTPS discovery and exact issuer,
authorization/token/JWKS endpoints and RSA key publication. Full pending acceptance:

1. Bootstrap and realm import, health, external TLS permissions and browser trust.
2. Actual four imported subjects and nonempty externally supplied credentials.
3. Native Authorization Code + S256, callback and logout with exact URIs.
4. An issued access token with at+jwt/RS256 and all RFC 9068 required claims,
   issuer and audience, verified by the unchanged OIDCAuthenticator.
5. GET /api/v1/session for each identity: analyst-a→alpha, analyst-b→beta,
   viewer-a→alpha without analysis, outsider→empty memberships.
6. POST analysis: correct tenant 200 for analysts; cross-tenant/viewer/outsider
   403; anonymous/invalid/expired/ID-token 401 with no analysis execution.
7. User/tenant switches, expiration, logout and late-job isolation in Streamlit.

For a legitimately obtained Authorization Code access token in an **external**
temporary file (never copied from cookies or committed), the later acceptance
harness can run, for each case:

```powershell
.\venv\Scripts\python.exe dev/keycloak/check_local.py --token-file "$privateRoot/analyst-a.access-token" --case analyst-a
```

The harness prints no token or private response body and never obtains a token
through password grants. `--case` also accepts analyst-b, viewer-a and outsider;
it checks both tenants and anonymous denial. Token acquisition/harness execution
is pending C1.6-B, not performed by C1.6-A.

## Windows packaged-app path virtualization (C1.6-B1.1)

On this machine, preparation launched from the packaged Codex application wrote
the private AppData files under
`C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev`.
Windows processes saw the logical AppData path, while Docker/WSL saw an empty
directory there. A read-only diagnostic container, including UID 1000, could read
the existing certificate and key at the physical package-cache path.

Only the external `compose.env` TLS directory was changed to that confirmed
physical path. No keys were moved, regenerated or made more broadly accessible.
Do not copy this machine-specific path into Compose or assume every installation
uses it. Check the physical location from Docker before preparing another setup;
the preparer does not currently detect this Windows virtualization automatically.
Packaged-app cache lifecycle can affect these files; retain them securely when
changing or removing the application. Do not delete the private directory or
reset the persistent realm as a troubleshooting shortcut.

Two subsequent blockers were diagnosed and corrected with explicit approval in
C1.6-B1.3. Keycloak 26.8.1 forces application HTTP on in development mode, so
Compose now uses `start`. Docker Engine 24.0.7 skips external port programming
for internal networks, so the dedicated network is now a non-internal bridge.
This intentionally permits outbound connections from Keycloak. Only
`127.0.0.1:8443` is published; application HTTP 8080 is closed and management
HTTP 9000 remains unpublished. The existing data volume was retained.

Controlled revalidation confirmed a healthy container running as UID 1000 with
zero restarts, discovery and JWKS HTTP 200, exact issuer
`https://localhost:8443/realms/indai-ma-dev`, and an RS256 signing key in JWKS.
TLS 1.3 validated the hostname and certificate chain using an explicit local CA
SSL context, with no trust-store changes. The served certificate matched the
generated leaf and expires on 2026-11-08 at 15:30:15 UTC.
The persisted realm was recognized (re-import correctly skipped). The Streamlit
Code + PKCE authorization request returned the login page, and the API client
rejected interactive authorization with HTTP 403 (bearer-only). No credentials
were submitted and no tokens were obtained. Detailed mapper/user inspection,
real access-token validation, ID-token rejection and backend integration remain
pending the separately authorized next increment. Streamlit and FastAPI were
not started.

Validation: 1426 tests and 230 subtests passed; fast evaluation passed 38/38 cases
and 110/110 numeric assertions, with zero LLM calls; pip check found no broken
requirements.

## Interactive callback — C1.6-B2-A

`interactive_pkce.py` is an opt-in, temporary loopback receiver, not Streamlit or
FastAPI. It reuses the existing exact `http://localhost:8501/oauth2callback` URI;
no redirect registration change is needed. Port 8501 must be free and Streamlit
must stay stopped. Run one case at a time with the external physical private path:

```powershell
.\venv\Scripts\python.exe dev/keycloak/interactive_pkce.py --private-root $privateRoot --case analyst-a
```

Open `http://localhost:8501/start` in a browser that already trusts the local CA.
If the browser reports a certificate error, stop; do not bypass it. Any change
to browser/Windows trust requires separate authorization. Retrieve each user's
password privately from the external configuration, never from chat or logs.
Repeat with analyst-b, viewer-a and outsider. Login must be performed by the user.

The receiver binds only to 127.0.0.1, requires the exact localhost Host header,
expires after ten minutes, enforces state/nonce and PKCE S256, and attempts only
one code exchange. Query logging is disabled. Tokens are neither printed nor
saved; only sanitized pass/fail metadata and permission decisions are emitted.
The process exits after the callback, releasing tokens and closing its listener.
C1.3 runs unchanged using its existing transport injection with a real HTTPS
transport and explicit CA verification; the venv trust bundle is not modified.
The ID token is also cryptographically validated and checked for nonce binding,
then its rejection by the access-token authenticator is verified. Tenant checks
use SecurityContextFactory and TenantAuthorizer, not token roles, and execute no
AnalysisService, RAG, LLM or business calculations. Wrong issuer/audience and
expiration checks currently remain offline fixtures, not claimed live results.
This receiver does not call `/api/v1/session` or start FastAPI. Persisted admin
configuration inspection remains a separate authenticated administrative step.

### Structured callback diagnostics — C1.6-B2-A.2

Each callback attempt emits a random attempt ID and records for
CALLBACK_VALIDATION, TOKEN_EXCHANGE, ACCESS_TOKEN_VALIDATION, PRINCIPAL_MATCH,
ID_TOKEN_BINDING and TENANT_AUTHORIZATION. Records contain PASSED, FAILED or
NOT_REACHED, a stable phase/check/error code, an allowlisted exception class,
HTTP status when available and elapsed milliseconds. Per-tenant decisions are
recorded without principal or token claims. Expected tenant denials remain valid
scenario outcomes; unexpected denials fail the attempt. TLS classification checks
exception types in a bounded cause/context chain without reading messages.

No raw exception message, stack dump, response body, authorization URL/query,
credential, nonce, state, verifier, code or JWT claim set is printed. Token maps
are cleared on failure as well as success, clients are closed, and the one-shot
receiver exits after an attempt (including an invalid callback). The browser
continues to receive only the generic result. A failed phase leaves all later
phases NOT_REACHED. Offline tests inject independent exchange, TLS, HTTP-status,
JWT, principal, nonce and authorization failures and test sentinel non-disclosure.
No historical attempt can be reconstructed from these new diagnostics; a new
human login is necessary. Never automatically retry a failed login.

When C1.3 rejects an access token, `rejected_access_checks` now adds only fixed
boolean/null fields for the header profile, eligible key selection, signature,
required-claim presence, issuer/audience/client/expected-subject matches and time
checks. Header observations are untrusted until the signature is verified;
claims are inspected only after independent RS256 verification against the exact
TLS-verified JWKS endpoint. The diagnostic decode intentionally separates claim
checks to report multiple faults. It cannot return a principal or authorize:
the original C1.3 rejection is always re-raised, and later phases stay NOT_REACHED.
Diagnostic fetch/parse errors emit only allowlisted categories, never raw errors
or values. Network response size, key count and time are bounded. C1.3 and all
production authentication/authorization policy remain unchanged.

### Missing access-token subject: declared correction and live migration

A live signed access token passed signature, issuer, audience, client and time
checks but lacked `sub`, so C1.3 correctly rejected it. The declared client now
has a client-local `oidc-sub-mapper` (UI type **Subject (sub)**), name
**RFC9068 subject**, `access.token.claim=true`, `introspection.token.claim=false`.
This built-in mapper emits the authenticated Keycloak user's ID, never a fixed
claim or a username. Do not modify MembershipDirectory or weaken C1.3.

The existing persisted realm is not updated by editing the import JSON or by
restarting (`IGNORE_EXISTING`). Apply the same change through the authenticated
admin console in realm **indai-ma-dev**, client **indai-ma-streamlit**:
**Client scopes → indai-ma-streamlit-dedicated → Mappers → Configure a new
mapper → Subject (sub)**. First check for an existing subject mapper to avoid
duplicates; enable its access-token inclusion instead if it already exists.
Otherwise create the mapper above. Keep unrelated options unchanged. If this
UI is unavailable or differs, inspect before changing anything; do not switch
to a hardcoded subject or a different mapper. Do not reset the data volume or
enable password grants. An administrator must perform the interactive login
using the private bootstrap credentials; credentials are never copied to chat.

After saving, the next explicitly coordinated PKCE attempt must verify both
presence and actual expected subject through unchanged C1.3. The live change and
successful token validation remain pending until confirmed; the offline JSON
change alone is not evidence that the persisted mapper was updated.

### FastAPI real session integration — C1.6-B2-B1

The API composition optionally reads the admin-owned `INDAI_OIDC_CA_FILE` absolute
public CA path. It extends an in-memory certifi-based SSLContext and passes a
TLS-verifying HTTPTransport to unchanged C1.3. Missing, relative or invalid CA
paths fail closed; no venv/Windows store is changed. The external `backend.env`
now points memberships and CA at their confirmed physical private paths. Load
that configuration only into the API process. Start on 127.0.0.1:8000 with access
logging disabled; never print environment values, authorization headers or URLs
with query credentials. The controlled startup checks live JWKS using C1.3's
existing key-fetch routine in the same process before serving HTTP.

`GET /api/v1/session` uses the cryptographically validated principal and backend
MembershipDirectory, with Cache-Control no-store. It does not select an active
tenant or require analysis:run. It intentionally ignores X-Tenant-ID, including
malformed values: headers cannot expand or filter its authoritative listing.
Without/with invalid credentials it returns 401 and WWW-Authenticate Bearer.
Valid outsiders return 200 with an empty tenant list, not invented membership.
Infrastructure failures return generic 503; controlled tests inject JWKS failure
without stopping Keycloak. Analysis SecurityContext/authorization are separate
and are not invoked by this GET. No live analysis POST is used in this increment.

Run the existing callback with `--case analyst-a --session-check` only after the
API is ready. Coexistence of the API on 8000 and one callback on 8501 was explicitly
approved; Streamlit stays stopped. Tokens remain in memory. After the existing
six validations, the optional check performs only fixed loopback GETs with no
tenant header, tenant-alpha, tenant-beta and a malformed tenant. It checks exact
identity/expiration, no-store and the complete backend permission listing. It
never follows redirects, posts an analysis, persists tokens or contacts a remote
API. Reports contain only sanitized statuses/permissions. Each subsequent user
requires separate confirmation. A successful server startup or unauthenticated
negative check does not establish success of the pending real login/session test.

## Sources

- [Official 26.8.1 release](https://www.keycloak.org/2026/10/keycloak-2681-released).
- [Subject mapper (26.8.1)](https://raw.githubusercontent.com/keycloak/keycloak/26.8.1/services/src/main/java/org/keycloak/protocol/oidc/mappers/SubMapper.java).
- [Container configuration](https://www.keycloak.org/server/containers),
  [realm import/env placeholders](https://www.keycloak.org/server/importExport),
  [TLS](https://www.keycloak.org/server/enabletls),
  [health probe](https://www.keycloak.org/observability/health).
- [at+jwt option](https://www.keycloak.org/2025/04/keycloak-2620-released),
  [attribute constant](https://www.keycloak.org/docs-api/26.8.0/javadocs/constant-values.html),
  [client-local claim mapper](https://raw.githubusercontent.com/keycloak/keycloak/26.8.1/services/src/main/java/org/keycloak/protocol/oidc/mappers/HardcodedClaim.java).
