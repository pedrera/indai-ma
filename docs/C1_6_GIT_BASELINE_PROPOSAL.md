# C1.6 — Git baseline proposal and reconciliation

Prepared 2026-10-31 after formal user approval of the local pilot with limitations.
Updated 2026-11-07 after TLS A2 passed. The branch and tag were already present
when this documentation follow-up began. This follow-up makes no Git writes.

## Proposed baseline

- Original base branch: `main`. Base HEAD:
  2f6193b1dc0a3cc7c9d98162d6b7233eabda504b.
- Agreed review branch: `codex/c1-6-local-pilot-baseline`.
- Agreed commit message: `feat(security): consolidate C1.6 local OIDC pilot and TLS A2`.
- Agreed annotated tag: `c1.6-local-pilot-tls-a2`, with message
  `C1.6 local pilot validated with limitations; TLS A2 passed`.
- Observed HEAD on the review branch and target commit of the tag:
  `9984c57bc80925e628e7bbffc0cd921349736133`. Its parent is the original base.
  The five documentation follow-up edits are outside that tagged snapshot.
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
- The baseline records a local pilot validated with limitations. TLS A2
  passed on 2026-11-07. C2 and C3 remain unstarted.
  [C1.6 closure](C1_6_FINAL_REVIEW.md) · [A2 closure](C1_6_TLS_ROTATION_A2_REVIEW.md)

## Exact original staging allowlist

The paths below are the 42 candidates relative to the original base. M marks
files modified from the base. A marks additions. The allowlist matches the
observed baseline commit outside `artifacts/`.

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
| A | [business_denial_probe.py](../business_denial_probe.py) |
| A | [business_first_run.py](../business_first_run.py) |
| A | [dev/keycloak/README.md](../dev/keycloak/README.md) |
| A | [dev/keycloak/backend-memberships.json](../dev/keycloak/backend-memberships.json) |
| A | [dev/keycloak/check_local.py](../dev/keycloak/check_local.py) |
| A | [dev/keycloak/compose.yaml](../dev/keycloak/compose.yaml) |
| A | [dev/keycloak/interactive_pkce.py](../dev/keycloak/interactive_pkce.py) |
| A | [dev/keycloak/prepare_local.py](../dev/keycloak/prepare_local.py) |
| A | [dev/keycloak/realm/indai-ma-dev-realm.json](../dev/keycloak/realm/indai-ma-dev-realm.json) |
| A | [dev/keycloak/trust_python.py](../dev/keycloak/trust_python.py) |
| A | [docs/C1_6_B2_B2_2_FIRST_BUSINESS_REVIEW.md](../docs/C1_6_B2_B2_2_FIRST_BUSINESS_REVIEW.md) |
| A | [docs/C1_6_B2_B2_3_DENIAL_REVIEW.md](../docs/C1_6_B2_B2_3_DENIAL_REVIEW.md) |
| A | [docs/C1_6_B2_B2_4_SESSION_LIFECYCLE.md](../docs/C1_6_B2_B2_4_SESSION_LIFECYCLE.md) |
| A | [docs/C1_6_FINAL_REVIEW.md](../docs/C1_6_FINAL_REVIEW.md) |
| A | [docs/C1_6_GIT_BASELINE_PROPOSAL.md](../docs/C1_6_GIT_BASELINE_PROPOSAL.md) |
| A | [docs/C1_6_OPERATIONS_FOLLOWUP.md](../docs/C1_6_OPERATIONS_FOLLOWUP.md) |
| A | [security_audit.py](../security_audit.py) |
| A | [tests/test_business_denial_probe.py](../tests/test_business_denial_probe.py) |
| A | [tests/test_business_first_run.py](../tests/test_business_first_run.py) |
| A | [tests/test_business_session_lifecycle.py](../tests/test_business_session_lifecycle.py) |
| A | [tests/test_interactive_pkce.py](../tests/test_interactive_pkce.py) |
| A | [tests/test_keycloak_dev_config.py](../tests/test_keycloak_dev_config.py) |
| A | [tests/test_security_audit.py](../tests/test_security_audit.py) |
| A | [tests/test_session_live_contract.py](../tests/test_session_live_contract.py) |

The first 38 paths belong to C1.6. The four TLS documents complete the allowlist:

| Status | File |
| --- | --- |
| A | [docs/C1_6_TLS_EXPIRY_MAINTENANCE_PLAN.md](C1_6_TLS_EXPIRY_MAINTENANCE_PLAN.md) |
| A | [docs/C1_6_TLS_ROTATION_A1_REVIEW.md](C1_6_TLS_ROTATION_A1_REVIEW.md) |
| A | [docs/C1_6_TLS_ROTATION_A1_1_REVIEW.md](C1_6_TLS_ROTATION_A1_1_REVIEW.md) |
| A | [docs/C1_6_TLS_ROTATION_A2_REVIEW.md](C1_6_TLS_ROTATION_A2_REVIEW.md) |

Total: **42 files**, 14 modified and 28 added.

## Recorded offline regression

The first full run below used native Windows temporary storage. It produced
1528 passed, 230 subtests passed, one skip and one failure. The Streamlit
embedding-pool test timed out after 20 seconds in a rerun outside the catalog
mock. The subsequent full run disabled the LM Studio endpoint only in the
test process. It produced **1529 passed, 230 subtests passed and one skip**.
The one-space override prevents the local dotenv setting from supplying an
endpoint, and the client trims the value to empty.

```powershell
# Initial full run
$env:PYTHONDONTWRITEBYTECODE='1'; .\venv\Scripts\python.exe -m pytest --ignore=artifacts -p no:cacheprovider -q

# Isolated retry, one passed
$env:PYTHONDONTWRITEBYTECODE='1'; .\venv\Scripts\python.exe -m pytest --ignore=artifacts -p no:cacheprovider -q tests/test_execution_ux.py::ExecutionUXTests::test_workspace_embedding_pool_is_lazy_and_isolated_per_streamlit_session

# Successful full rerun in its own process
$env:PYTHONDONTWRITEBYTECODE='1'; $env:LMSTUDIO_BASE_URL=' '; .\venv\Scripts\python.exe -m pytest --ignore=artifacts -p no:cacheprovider -q -rs
```

Focused security regression: **349 passed and one skip**. Both reported skips
refer to the same Windows symlink test in `tests/test_security_audit.py`.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; .\venv\Scripts\python.exe -m pytest -p no:cacheprovider -q -rs tests/test_security_contracts.py tests/test_security_policy.py tests/test_security_oidc.py tests/test_security_http.py tests/test_security_audit.py tests/test_streamlit_auth.py tests/test_business_session_lifecycle.py tests/test_business_first_run.py tests/test_business_denial_probe.py
```

Fast: **38/38 cases, 110/110 numeric assertions and zero LLM calls**. The
runner printed its summary to the console without generating a report.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; @'
import logging
from evals.offline import OfflineEnvironment
from evals.runner import EvaluationRunner,load_cases,summarize
logging.getLogger("indai_ma.performance").setLevel(logging.WARNING)
with OfflineEnvironment() as env:
    results=EvaluationRunner(env.supervisor).run(load_cases(mode="fast"))
summary=summarize(results)
print(summary.model_dump_json(indent=2))
for result in results:
    if not result.passed:
        print(result.case_id, result.errors)
raise SystemExit(bool(summary.failed_cases))
'@ | .\venv\Scripts\python.exe -
```

Dependency check reported no broken requirements:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; .\venv\Scripts\python.exe -m pip check
```
