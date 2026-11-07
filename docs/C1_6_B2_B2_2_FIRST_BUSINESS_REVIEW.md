# C1.6-B2-B2.2 — First authenticated Business execution: review only

Historical preparation review. The later authorized execution and final regression
are recorded in the [C1.6 final review](C1_6_FINAL_REVIEW.md); the pending language
below does not authorize a repeat execution.

No live analysis request is authorized by preparing this increment. The private
Streamlit TOML contains `[business_first_run]` with explicit expected issuer,
subject and tenant, and `approved=false`. Tokens/passwords are never included in
this section. Keycloak, memberships, C1.3 and backend calculation logic are
unchanged. Other views remain unavailable through the session-only entry.

## Security and execution path

Native Streamlit login and public `st.user.tokens.get("access")` feed
`refresh_business_session`: the backend cryptographically authenticates the
access token and lists only authoritative memberships. Native and backend
identity/expiry must agree. Tenant selection starts empty and remains explicit.
The pilot compares that verified principal/selected tenant with the configured
expected scope; these expected values never authenticate or grant permissions.
The UI emits only readiness booleans, without IDs/claims/credentials.

An explicit future approved click calls `perform_first_run`, which refreshes the
backend session and calls existing `capture_business_request` again. The request
captures credentials only for that operation, with principal/tenant/session-epoch
binding. `ApiJobAdapter.run` calls `AnalysisApiClient.analyze` (no local fallback).
Bearer is in Authorization and the selected tenant in X-Tenant-ID, not in the
business text. FastAPI validates C1.3, builds SecurityContext through
SecurityContextFactory and authorizes analysis:run with TenantAuthorizer.
AnalysisService authorizes again before creating a recorder or Supervisor.
RAG/provider factories are deferred. No Supervisor/provider/RAG is constructed
on denial.

This restricted pilot uses the existing adapter synchronously, with a 30-second
client timeout, and does not start a background AgentJob. The HTTP client closes
after success or failure, and the request reference is released. Session metadata
is refreshed after the response; results are retained/rendered only under the
same valid principal/tenant/epoch. Existing Business state cleanup removes pilot
results on logout, identity/tenant change or invalidation. Normal Business jobs
retain their existing scoped cleanup/cancellation; the pilot does not enable them.

A process-local lock permits only one attempted analysis across tabs/sessions in
this Streamlit process. Quota is consumed before networking, including on error
or timeout. It does not reset on logout; process restart resets it. This is not
durable backend idempotency. Never retry a timeout automatically: the server may
have completed the operation even if the client lost its response.

## Fixed proposed input

```text
La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento.
```

Routing inspection is unambiguous and selects only ProcurementAgent. No contract,
customer, document or alternative evaluation is requested. After authorization,
Supervisor creates its operation-local recorder/cache and Procurement workflow:
CanonicalProcurementModel, MemoizedRegistry/LocalToolRegistry, and the existing
calculate_supply_position and calculate_spot_exposure tools. API composition has
use_llm_synthesis=false. The exact route does not call provider_factory or
rag_factory; defining those closures does not initialize their dependencies.
Executive projection and recommendation composition remain deterministic.

Expected result (a proposal, not an observed live execution):

- HTTP 200 AND business status completed, not merely an HTTP success.
- Only ProcurementAgent, completed; deterministic routing/synthesis.
- Demand 120 GWh minus supply 95 GWh: SHORT 25 GWh.
- Spot exposure 25,000 MWh × 42 EUR/MWh = 1,050,000 EUR.
- Two tool executions, zero LLM and zero RAG calls.
- Calculated provenance, no retrieved document evidence or invented citations.

An HTTP 200 response with partial/failed/needs_input/validation_failed business
status is not a successful pilot. Inspect specialist status, diagnostics, warnings
and numeric results. A transport error yields no success claim and never invokes
local analysis. Unexpected RAG/LLM calls or routing are discrepancies to review,
not justification to relax authorization or retry.

## Manual sequence AFTER review and explicit authorization

1. Keep the session-only entry, Keycloak and FastAPI; do not enable Workspace or
   technical views. Use the isolated Chrome profile and root localhost:8501 URL.
2. Login/renew as analyst-a if needed; require the identity-match confirmation,
   backend session HTTP 200 and only tenant-alpha with analysis:run. Select
   tenant-alpha explicitly. If any condition differs, stop.
3. Only after review, set `approved=true` in the external TOML's existing
   `[business_first_run]` section. Do not change the expected subject or tenant
   to accommodate an unexpected login. Configuration reload may require another
   login/tenant selection. No private file is copied to the repository.
4. Confirm the fixed input above and click “Ejecutar una única prueba Business”
   once. There is no editable prompt or automatic run. Never press a normal
   Business/Workspace analysis control for this pilot.
5. Compare the result against the checklist; do not repeat on uncertainty.
   Review diagnostics before any subsequent user or scenario. Restore
   `approved=false` after the single attempt under the next authorized action.

## Observed vs pending

Code review, route inspection, synthetic HTTP propagation, denied service
initialization, one-attempt behavior and UI isolation are checked offline. Tests
use explicit fixture identities and mocked HTTP; they are not real user analysis.
Native login/session success was reported in B2-B2.1. Direct confirmation of the
pilot's expected identity in the current browser remains pending the safe
identity-match UI/readiness event. No live POST or business result is claimed.
During preparation the user reloaded and reported the existing “no valid access
token for this API” message. The current session therefore remains blocked; this
generic message alone does not prove expiration as the exact cause. Renew the
native login manually and confirm expected identity and explicit tenant before
any approval or execution. No validation is relaxed to accept that session.
Resource/document multitenancy remains out of scope and is avoided by this input.
