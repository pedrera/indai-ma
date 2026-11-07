# C1.6 — Proposed Git baseline (not executed)

Prepared 2026-10-31 after formal user approval of the local pilot with limitations.
No git add, commit, tag, checkout, branch creation, reset, merge or push is executed.
Existing changes are preserved; the status below includes changes from earlier
increments and is not a claim that all were authored in this closure turn.

## Proposed baseline

- Current read-only reference: branch main, HEAD
  2f6193b1dc0a3cc7c9d98162d6b7233eabda504b.
- Suggested future review branch: codex/c1-6-local-pilot-baseline. It is NOT created.
- Suggested future baseline title: C1.6 local OIDC pilot validated with limitations.
- Prefer one coherent reviewed baseline because app.py, business_auth.py and tests
  contain interdependent increments. Review in blocks: development infrastructure;
  native OIDC/API authorization; pilot/denial diagnostics and audit; lifecycle
  corrections; tests; formal documentation. Do not split shared files blindly.
- Before any separately authorized versioning operation: review all diffs/untracked
  content, verify fixtures contain placeholders rather than credentials, repeat the
  scoped secret check, confirm approvals false, and preserve the passed regression
  evidence (1529 tests/230 subtests, 349 security tests, 38/38 fast, pip check).
- Do not version external compose.env, streamlit.toml, backend.env, current private
  membership copy, certificate/private-key files, browser profile, JSONL audit,
  raw credential logs or any token. The repository backend-memberships.json is the
  fictitious test fixture, distinct from the deployed external snapshot.
- No new baseline/tag is asserted until the user authorizes actual Git writes.
  This proposal is neither a signed release nor a production-readiness label.

## Complete pending-file inventory

Only repository paths outside artifacts are enumerated. M = modified tracked file;
?? = untracked candidate requiring explicit review before staging.

| Status | File |
| --- | --- |
| M | [.gitignore](../.gitignore) |
| M | [api/app.py](../api/app.py) |
| M | [api/composition.py](../api/composition.py) |
| M | [api/errors.py](../api/errors.py) |
| M | [api/routes.py](../api/routes.py) |
| M | [api/security.py](../api/security.py) |
| M | [api_client.py](../api_client.py) |
| M | [app.py](../app.py) |
| M | [application_service.py](../application_service.py) |
| M | [business_api_adapter.py](../business_api_adapter.py) |
| M | [business_auth.py](../business_auth.py) |
| M | [docs/STREAMLIT_OIDC.md](../docs/STREAMLIT_OIDC.md) |
| M | [tests/test_execution_ux.py](../tests/test_execution_ux.py) |
| M | [tests/test_streamlit_auth.py](../tests/test_streamlit_auth.py) |
| ?? | [business_denial_probe.py](../business_denial_probe.py) |
| ?? | [business_first_run.py](../business_first_run.py) |
| ?? | [dev/keycloak/README.md](../dev/keycloak/README.md) |
| ?? | [dev/keycloak/backend-memberships.json](../dev/keycloak/backend-memberships.json) |
| ?? | [dev/keycloak/check_local.py](../dev/keycloak/check_local.py) |
| ?? | [dev/keycloak/compose.yaml](../dev/keycloak/compose.yaml) |
| ?? | [dev/keycloak/interactive_pkce.py](../dev/keycloak/interactive_pkce.py) |
| ?? | [dev/keycloak/prepare_local.py](../dev/keycloak/prepare_local.py) |
| ?? | [dev/keycloak/realm/indai-ma-dev-realm.json](../dev/keycloak/realm/indai-ma-dev-realm.json) |
| ?? | [dev/keycloak/trust_python.py](../dev/keycloak/trust_python.py) |
| ?? | [docs/C1_6_B2_B2_2_FIRST_BUSINESS_REVIEW.md](../docs/C1_6_B2_B2_2_FIRST_BUSINESS_REVIEW.md) |
| ?? | [docs/C1_6_B2_B2_3_DENIAL_REVIEW.md](../docs/C1_6_B2_B2_3_DENIAL_REVIEW.md) |
| ?? | [docs/C1_6_B2_B2_4_SESSION_LIFECYCLE.md](../docs/C1_6_B2_B2_4_SESSION_LIFECYCLE.md) |
| ?? | [docs/C1_6_FINAL_REVIEW.md](../docs/C1_6_FINAL_REVIEW.md) |
| ?? | [docs/C1_6_GIT_BASELINE_PROPOSAL.md](../docs/C1_6_GIT_BASELINE_PROPOSAL.md) |
| ?? | [docs/C1_6_OPERATIONS_FOLLOWUP.md](../docs/C1_6_OPERATIONS_FOLLOWUP.md) |
| ?? | [security_audit.py](../security_audit.py) |
| ?? | [tests/test_business_denial_probe.py](../tests/test_business_denial_probe.py) |
| ?? | [tests/test_business_first_run.py](../tests/test_business_first_run.py) |
| ?? | [tests/test_business_session_lifecycle.py](../tests/test_business_session_lifecycle.py) |
| ?? | [tests/test_interactive_pkce.py](../tests/test_interactive_pkce.py) |
| ?? | [tests/test_keycloak_dev_config.py](../tests/test_keycloak_dev_config.py) |
| ?? | [tests/test_security_audit.py](../tests/test_security_audit.py) |
| ?? | [tests/test_session_live_contract.py](../tests/test_session_live_contract.py) |

Snapshot: **14 modified tracked files; 24 untracked files; 38 total**.
The inventory includes this proposal and the operations follow-up. No staging was performed.
