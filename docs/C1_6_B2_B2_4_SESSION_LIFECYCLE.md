# C1.6-B2-B2.4 — Session lifecycle review

Final regression and current closure decision: [C1.6 final review](C1_6_FINAL_REVIEW.md).

This iteration sends no real POST /api/v1/analysis and leaves both private approvals
false. Keycloak, memberships, C1.3, TLS policy and the private audit configuration
are unchanged. Unit/API integration tests use generated test tokens and offline
transports only. Multi-tenant fixtures are test data, not new real memberships.

## Findings and corrections

- finish_generation previously classified a completion using current session state.
  A detached old Business completion could interfere with a newer job, or enter a
  different mode's completion path. It now rejects mismatched originating job
  instances before mutating anything, and checks Business binding independently
  of the current generation kind. The polling caller passes the actual source job.
- Anonymous reruns previously cleared residual Business state only when an old
  BusinessSession existed. Cleanup now also detects residual jobs, results, views
  and tenant selections when the session object is missing. Clean anonymous
  reruns preserve their draft widgets; cleanup is not repeated without residual
  security state. Presentation-only tests explicitly stub their auth refresh as
  well as their existing auth render double; production has no test bypass.
- Analysis HTTP 401 now raises AnalysisApiCredentialsError, matching session GET
  authentication failures. Backend session validation invalidates Business state,
  selected tenant and result references after an authentication failure. No token
  refresh, anonymous fallback or retry is added.
- Business worker completion releases its captured request (and token reference)
  and closes its owned client. Caller-supplied clients retain caller ownership.
  An HTTP request already executing necessarily holds its own credentials until
  it finishes; logout cannot erase that call's stack or revoke the issued token.

## Validation matrix

| Scenario | Test evidence | Real browser evidence |
| --- | --- | --- |
| Local logout clears context, tenant, jobs and prior results | Existing logout tests, native widget callback test, cancellation/logout failure tests | User confirmed this iteration: Iniciar sesión shown, no prior tenants/results, Analyze disabled; safe native diagnostic reports logged_in=false |
| analyst-a to viewer-a | New identity matrix; only new backend grants retained; no default tenant | Previously manually switched during denial validation; no new controlled full transition in this iteration |
| viewer-a to outsider | Same matrix and cleanup assertions | Prior live outsider screen/session confirmed empty memberships |
| outsider to analyst-a | Same matrix; explicit tenant selection required again | Pending controlled browser transition without POST |
| Authorized tenant a to b | Existing tests plus new delayed-worker tenant test; epoch changes and old result rejected | Test fixtures only: real users have no cross-tenant grant |
| Unauthorized tenant selection | Existing selection and HTTP authorization tests | Real viewer/outsider cross-tenant denials completed in B2-B2.3 |
| Expired JWT | Existing C1.3 and /session tests with signed expired test tokens | No clock or Keycloak token lifetime change; natural expiry/manual renewal previously observed, not repeated here |
| HTTP 401 in Streamlit refresh | New test with actual HTTP client/MockTransport: context/results cleared, client closed | Controlled native expired-token browser sequence remains pending |
| Principal/tenant/new-login changes during pending work | Blocking fake HTTP operation plus real AgentJob; old work finishes but result stays cancelled/discarded locally | Pending if a later stage authorizes a real asynchronous job |
| Old callback after job replacement or mode change | New completion-function tests; current job and result remain intact | No real asynchronous Business job launched |
| Audit authentication vs authorization | Existing offline audit tests and previous correlated real denials | Persistent sink retained; no new real analysis request in this iteration |

## Cancellation semantics

AgentJob cancellation marks local state and discards the local result. ApiJobAdapter
has no backend cancellation endpoint. Tests explicitly allow the simulated backend
call to finish after local cancellation, proving that local disposal is not a claim
of remote termination. No exactly-once processing, server-side revocation of an
in-flight request, or distributed logout is provided by this UI.

Streamlit st.logout clears the local native session and application state. Provider
SSO may persist or require additional interaction. It does not prove revocation of
already issued access tokens. API authorization remains independently enforced.

## Next step and stop boundary

Finish the optional manual logout check and controlled native identity transitions
without pressing either Business button. Inspect the fresh backend-verified identity,
empty/new explicit tenant selection, absent previous results and disabled analysis.
Keep approved=false and approved_post=false. Do not enable a new POST or claim a
real asynchronous isolation trial has occurred. No backend cancellation capability
is added in this increment.

## Executed validation

- Full regression: 1529 passed, 1 skipped (symlink privilege unavailable),
  230 subtests passed. Command: python -m pytest --ignore=artifacts -q.
- Focused lifecycle suite: 15 passed. Auth/lifecycle/presentation regression:
  71 passed after correcting the anonymous-widget regression.
- Fast evaluation: 38/38, 110 numeric assertions, zero generation LLM calls.
- python -m pip check: no broken requirements.
- Manual logout confirmed by user and safe native log: logged_in=false.
- Final private checks: approved=false, approved_post=false; audit unchanged at
  17 events / 4 prior real requests. No new real analysis POST was sent.

Changed files for this increment: app.py, business_auth.py, api_client.py,
business_api_adapter.py, tests/test_business_session_lifecycle.py,
tests/test_streamlit_auth.py, tests/test_execution_ux.py and this review.
Existing unrelated local changes were preserved. No Git write operation occurred.

## Manual native OIDC identity transitions — 2026-10-31

Completed in the user's isolated Chrome profile with manual credential entry:
analyst-a -> viewer-a -> outsider -> analyst-a. Screenshots and safe native
backend-session diagnostics establish:

| Identity | Session HTTP | Backend memberships / permissions | Visible cleanup |
| --- | --- | --- | --- |
| Initial analyst-a | 200 | tenant-alpha; analysis:run, criteria:evaluate, documents:read, portfolio:read, scenarios:evaluate | No active tenant selection; no previous result shown; execution disabled |
| viewer-a | 200 | tenant-alpha; documents:read, portfolio:read | Read-only selector empty; no prior result shown; no pending job per diagnostic; Analyze and negative probe disabled |
| outsider | 200 | No tenants or Business permissions | No tenant selector; no prior result shown; no pending job per diagnostic; both buttons disabled |
| Returning analyst-a | 200 | tenant-alpha; original analyst permissions restored from backend | Active tenant remains unselected; no recovery of the old Procurement result; execution disabled |

Expected-principal comparison succeeded against the authenticated API metadata for
all identities, rather than inferring identity from grants alone. During viewer-a
and outsider checks only the private diagnostic expected identity was changed;
OIDC, actual memberships and both approval flags were unchanged. Each intervening
logout produced native_logged_in=false before the next login. No accidental return
to the previous principal or SSO account-switch incident was observed. These facts
do not establish global Keycloak SSO logout or token revocation.

Visible UI cleanup and local logout are observed in the real browser; mutation of
internal epoch/bindings, release of credentials from pending workers and rejection
of late callbacks remain supported by tests, since no real pending job was created.
The initial analyst had no selected tenant, so this manual sequence does not add a
real selected-tenant reset trial beyond the existing unit coverage.

Real access-token expiration remains PENDING: this sequence did not observe a
controlled expiry. It can be checked later by leaving the session idle past its
normal remaining access-token lifetime and then reloading, without changing realm
lifetimes or system time. No token extraction or automatic retry is needed. Keep
the offline expiry/401 tests as current evidence until the browser rejection and
cleanup are observed. Native ID-session lifetime and access-token lifetime differ.

Final checks: approved=false, approved_post=false. Persistent audit remains at
17 events for the same four prior negative POSTs; no new analysis request was sent.
No real asynchronous Business trial or subsequent increment was started.
