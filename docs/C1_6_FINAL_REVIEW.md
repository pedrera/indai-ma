# C1.6 — PILOTO LOCAL VALIDADO CON LIMITACIONES

Final review: 2026-10-31. This closes the local reference-provider pilot, not
production readiness. It authorizes no new Business POST, C2 or C3. This document
supersedes historical pending-status/operational statements in the earlier reviews;
those documents retain their original evidence and scope.

## Formal approval — 2026-10-31

The user explicitly approved **C1.6 — PILOTO LOCAL VALIDADO CON LIMITACIONES**.
This is the formal closure decision, not a pending recommendation. It does not
authorize production deployment or expansion of multi-tenant scope. All residual
risks and outstanding checks below remain open. approved=false and
approved_post=false were verified again at formal closure. No new Business run,
Keycloak modification, certificate renewal or Git write was performed.

| Subincrement | Consolidated status |
| --- | --- |
| C1.6-B2-B1 | Complete: real FastAPI /session integration for analyst-a, analyst-b, viewer-a and outsider; correct backend memberships |
| C1.6-B2-B2.1 | Complete: native Streamlit OIDC and real authenticated /session |
| C1.6-B2-B2.2 | Complete: one authorized analyst-a / alpha Procurement run; deterministic result and zero LLM/RAG |
| C1.6-B2-B2.3 | Complete: four real correlated denials; original untraceable request retains its distinct classification |
| C1.6-B2-B2.4 | Local iteration complete: lifecycle corrections/tests and manual identity cycle; browser expiry and real pending-work isolation remain open |
| C1.6-B2-B2.5 | Complete: 1529 tests, 230 subtests, 349 focused security tests, 38/38 fast; one symlink skip; pip check correct |

Formal closure support: [TLS renewal procedure and audit disposal policy](C1_6_OPERATIONS_FOLLOWUP.md)
and [proposed Git baseline / full pending-file inventory](C1_6_GIT_BASELINE_PROPOSAL.md).

## Validated architecture

Isolated Chrome -> native Streamlit OIDC (st.login/logout/user, exposed access
token) -> GET /api/v1/session -> unchanged C1.3 cryptographic verification ->
backend issuer+subject memberships -> explicit tenant selection -> per-operation
Bearer + X-Tenant-ID -> TenantAuthorizer analysis:run -> AnalysisService
reauthorization -> Supervisor -> deterministic specialist tools. ID tokens are
not API access credentials. Frontend roles never establish grants. No anonymous
or local execution fallback exists in this protected Business path.

INDAI_STREAMLIT_SESSION_ONLY=1 limits the active pilot UI. Both private flags are
false: business_first_run.approved and business_denial_probe.approved_post.
Process-local one-shot quotas are not durable backend idempotency.

## Final regression

| Check | Result |
| --- | --- |
| python -m pytest --ignore=artifacts -q | 1529 passed; 230 subtests; 1 skipped |
| Focused contracts/policy/OIDC/HTTP/audit/session/lifecycle/pilot tests | 349 passed; 1 skipped |
| python -m evals --mode fast | 38/38; 110/110 numeric assertions; zero generation LLM calls |
| python -m pip check | No broken requirements |

Skip: tests/test_security_audit.py, Symlinks unavailable in this Windows context.
This does not prove the skipped scenario. Tests use local transports and test
tokens, not the real pilot POST. The artifacts exclusion respects the explicit
no-inspection constraint. Successful final summaries reported no test warnings;
read-only Git checks emitted only LF/CRLF working-copy notices.

## Evidence matrix

### Real checks

- Keycloak 26.8.1 running/healthy, Linux amd64 image pinned by digest; published
  only at 127.0.0.1:8443. Keycloak HTTP disabled. Live discovery/JWKS HTTP 200,
  exact issuer, two currently published RSA keys, TLS 1.3 verified with local CA
  and localhost hostname validation.
- Native login and GET /session 200 for all four identities: analyst-a alpha with
  analysis, analyst-b beta with analysis, viewer-a alpha documents/portfolio,
  outsider empty. X-Tenant-ID does not expand listings. Analyst-b session is
  historical B2-B1 evidence, not a real analyst-b Business analysis.
- One previously observed synchronous Business pilot: analyst-a / tenant-alpha,
  ProcurementAgent completed, SHORT 25 GWh, EUR 1,050,000 exposure, two tools,
  zero LLM/RAG. Its earlier UI diagnostics are the evidence; no persistent audit
  correlation ID is invented for that operation.

| Principal | Tenant | HTTP / exception | Stage | Correlation ID |
| --- | --- | --- | --- | --- |
| viewer-a | tenant-alpha | 403 PermissionDenied | OPERATION_AUTHORIZATION | 7ec069f35d3d4ee1a3676cbe913ff769 |
| viewer-a | tenant-beta | 403 TenantAccessDenied | CONTEXT_FACTORY | 2f04e41ba9af4d7da48d9be5a6325502 |
| outsider | tenant-alpha | 403 TenantAccessDenied | CONTEXT_FACTORY | fd98740ab956407dbb4a33cd14f6256c |
| outsider | tenant-beta | 403 TenantAccessDenied | CONTEXT_FACTORY | a2927c7f596b4ed5bda20f4e2c9de11a |

The retained private JSONL has 17 events, four complete request_received through
request_completed sequences, expected typed denials, valid authentication and 403.
No analysis_service_entered or Supervisor construction events occurred. Safe API
diagnostics also reported service_dependency_entered=false and
analysis_handler_entered=false. Together with the reviewed deferred factories,
this establishes rejection before service, Supervisor and RAG/LLM initialization.

Original request b526933c25e74080b1975df09ab8099b remains separately classified:
HTTP 403 real evidence; concrete exception NOT VERIFIABLE; absence of Supervisor
supported by code/tests, NOT retrospectively verified. The later viewer-alpha
request is independent evidence, not a reinterpretation of the original.

Manual isolated-profile cycle analyst-a -> viewer-a -> outsider -> analyst-a was
completed. Screenshots and backend-principal comparisons confirmed correct grants,
/session 200, no visible inherited result, empty/new selection and disabled
execution. Intervening logouts recorded native_logged_in=false; viewer/outsider
diagnostics confirmed no pending job. The final analyst did not recover the old
Procurement result. No unexpected SSO account reuse was observed. This is not proof
of global Keycloak logout, token revocation or real asynchronous isolation.

### Automated versus pending evidence

Tests establish expired-JWT rejection, HTTP 401 cleanup, logout/cancellation failure
closure, identity/tenant/new-session changes, per-operation credentials, separate
sessions, and delayed-response discard without erasing a newer job. Test-only
multi-tenant fixtures do not alter real grants.

PENDING: natural access-token expiry in the browser; isolation with real concurrent
pending Business work. No new real POST was sent in this review.

## Configuration and secret review

- FastAPI 127.0.0.1:8000 and Streamlit 127.0.0.1:8501 listener addresses verified;
  Keycloak Docker binding verified. Health/ready checks returned 200. No restart
  was performed in this review.
- Issuer https://localhost:8443/realms/indai-ma-dev; audience indai-ma-api;
  confidential client indai-ma-streamlit; exact callback
  http://localhost:8501/oauth2callback; prompt=login; access-token exposure only.
- API explicit CA via INDAI_OIDC_CA_FILE; native client via SSL_CERT_FILE. No
  verify=False, CERT_NONE or insecure browser flag found in the scoped security
  path. security/oidc.py and security/policy.py have no working-tree diff.
- Current client/cookie/password secret values were compared against tracked text
  files, excluding artifacts: no matches; no tracked PEM private-key markers.
  Secret material remains external. This is a scoped present-state check, not an
  exhaustive Git-history secret scan or dependency CVE assessment.
- Both approval flags false; temporary PKCE callback not introduced. Other views
  remain outside this security boundary and must stay unexposed.

## Certificate management: action soon

| Certificate | Expiration UTC | Europe/Madrid | Required action |
| --- | --- | --- | --- |
| localhost leaf | 2026-11-08 15:30:15Z | 2026-11-08 16:30:15 | Renew before this date (8 calendar days from review) |
| Local CA | 2026-11-24 15:30:15Z | 2026-11-24 16:30:15 | Plan explicit CA/trust rotation (24 calendar days) |

Both valid at review. Leaf SAN includes localhost and 127.0.0.1; CA has no SAN,
which is normal. Renewal requires separate authorization, preserved issuer/SAN,
protected keys, planned Keycloak restart and repeat TLS/discovery/JWKS/login
checks. Do not run initial preparation over existing secrets. New CA trust needs
explicit isolated-browser and Python review; public certificates only, no private
key import or global trust changes automatically. No renewal was executed.

## Prioritized residual risks

| Severity | Risk / impact | Resolution gate |
| --- | --- | --- |
| HIGH outside pilot | RAG lacks tenant/resource isolation; possible cross-tenant disclosure if exposed | Enforce before real tenant documents or RAG expansion |
| HIGH outside pilot | Workspace, Industrial Gases and other direct views are not covered | Protect/disable before broader UI exposure; keep session-only entry |
| HIGH if overdue | Leaf/CA expiration blocks identity infrastructure | Authorized renewal before Nov 8 / Nov 24 |
| MEDIUM | Natural browser expiry not observed | Next no-POST browser expiry/renewal check |
| MEDIUM | Real pending concurrent work isolation untested | Separate authorized real-work stage |
| MEDIUM | Local logout does not revoke issued tokens or prove global SSO exit | Define revocation/revalidation policy before wider use |
| MEDIUM | Local cancel cannot stop backend; timeout can conceal completion | Backend cancellation/idempotency design before general asynchronous use; no automatic retry |
| MEDIUM | Local JSONL mutable by operator/admin, process-only locking, no central durability | Production audit integrity/monitoring/rotation before shared use |
| MEDIUM | H2/local memberships and in-memory jobs; no PostgreSQL persistence | Separate persistence/migration increment before production |
| LOW in local scope | Symlink test omitted; one-shot quotas reset on process restart | Exercise supported symlink environment; retain controlled single-worker procedure |

Audit write failure sets an internal flag but does not change authorization.
Missing events alone never prove non-execution. Correlation IDs are validated
labels, not authenticated identities or guaranteed unique client identifiers.

## Current operations — manual, not executed by this review

External private directory on this machine (physical packaged-app path):
`C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev`.
Do not assume the virtualized logical AppData path is Docker-visible. Never dump
secret files, process environments or unfiltered docker inspect/config output.

### Keycloak

```powershell
$privateRoot = 'C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev'
$compose = (Resolve-Path 'dev/keycloak/compose.yaml').Path
docker compose --env-file "$privateRoot/compose.env" -f $compose config --quiet
docker compose --env-file "$privateRoot/compose.env" -f $compose up -d keycloak
docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' indai-ma-keycloak-dev-keycloak-1
# Stop without removing realm data:
docker compose --env-file "$privateRoot/compose.env" -f $compose stop keycloak
```

Do not use down -v, reimport users or regenerate credentials.

### FastAPI

Dedicated terminal, repository root. Load only the existing backend settings into
the Python process; audit opt-in is local to that process:

```powershell
@'
import os
from pathlib import Path
root=Path('C:/Users/pedre/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/indai-ma-keycloak-dev')
allowed={'INDAI_OIDC_ISSUER','INDAI_OIDC_AUDIENCE','INDAI_OIDC_JWKS_URL','INDAI_MEMBERSHIPS_FILE','INDAI_OIDC_CA_FILE'}
for line in (root/'backend.env').read_text().splitlines():
    key,sep,value=line.partition('=')
    if sep and key in allowed: os.environ[key]=value.strip().strip('"')
# Only after private audit file/ACL provisioning:
os.environ['INDAI_LOCAL_SECURITY_AUDIT']='1'
os.environ['INDAI_SECURITY_AUDIT_FILE']='C:/Users/pedre/indai-ma-private-audit/denials.jsonl'
from api.app import app
assert app.state.security is not None
assert app.state.security.authenticator._fetch_keys()  # TLS-verified JWKS preflight
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8000,access_log=False,log_level='warning')
'@ | .\venv\Scripts\python.exe -
```

Stop with Ctrl+C in its terminal and confirm port 8000 closes. Do not stop cached
PIDs without verifying identity. /health and /ready should return 200; unauthenticated
/session should return 401. Health success does not establish authentication.

### Streamlit

Separate terminal; both private approval flags false; no callback on port 8501:

```powershell
$privateRoot = 'C:\Users\pedre\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\indai-ma-keycloak-dev'
$env:INDAI_STREAMLIT_SESSION_ONLY='1'
$env:INDAI_API_BASE_URL='http://127.0.0.1:8000'
$env:SSL_CERT_FILE=Join-Path $privateRoot 'ca/ca-cert.pem'
$env:NO_PROXY='127.0.0.1,localhost'
.\venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false --logger.level warning --secrets.files "$privateRoot/streamlit.toml"
```

Use the approved isolated Chrome profile and public CA trust. Stop with Ctrl+C;
confirm 8501 closes before any separate callback. Never bypass TLS warnings.
Restart Python clients after an authorized CA/config rotation.

### Audit activation, retention and removal

Provisioning/ACL commands: [denial review](C1_6_B2_B2_3_DENIAL_REVIEW.md).
Current file: `C:\Users\pedre\indai-ma-private-audit\denials.jsonl`, user/SYSTEM/
Administrators only, directory inheritance disabled. Existing regular file, no
symlink/reparse path, single local API worker. component-check.jsonl is explicitly
a non-HTTP component probe, not real request evidence. No public endpoint exposes
these files.

Enable at API startup with INDAI_LOCAL_SECURITY_AUDIT=1 and explicit
INDAI_SECURITY_AUDIT_FILE. Disable by omitting/clearing both before restarting the
API; other services need not change. Preserve evidence through review, then enforce
the recommended maximum seven-day retention. Stop writer before rotation/deletion;
remove only an explicitly reviewed file with Remove-Item -LiteralPath, no recursive
computed-path deletion. At 10 MB/write failure audit stops, not authorization.
Verify audit health before any future approved experiment.

## Acceptance and closure

PASS: regression, actual native identity/session checks, one approved fixed Business
result, four correlated denials, browser identity cycle, closed approvals,
loopback/TLS configuration, external secrets, known certificate deadlines.

OPEN but accepted within THIS pilot: real browser expiry, real pending concurrency,
RAG resource isolation, other view protection, persistence and production audit/
revocation/cancellation/idempotency. These prevent production-readiness claims.

Decision: **C1.6 — PILOTO LOCAL VALIDADO CON LIMITACIONES**.
Formally approved by the user on 2026-10-31. Next: review/authorize the separate
certificate-renewal plan, then finish the no-POST natural-expiry browser check.
Any real-job trial, C2 or C3 requires separately approved scope; none is started.

Git read-only state: main, HEAD 2f6193b1dc0a3cc7c9d98162d6b7233eabda504b. Existing
local changes preserved. Only documentation changes belong to this final review.
