# indAI MA v1.19.0 — Decision Engine

## Purpose and status

Local release candidate on `main`, based on published v1.18.0 at
`6be79efeb688882f4615d3976e57bdeded351278`. Publication is a separate operation.
v1.17 retains scenario facts; v1.18 evaluates explicit criteria; v1.19 can answer
whether exactly one candidate satisfies the submitted mandatory constraints in
an explicitly authorized scope. It does not determine the best business action.

## Production flow and API

```text
SupplyAssuranceScenarioSet → SupplyAssuranceService projections
→ full ScenarioSetComparison → explicit SupplyCriteria → evaluate_supply_criteria
→ explicit Workspace selection command → SupplyDecisionPolicy / resolved scope
→ canonical evidence verification → evaluate_supply_decision
→ WorkspaceResponse.decision_evaluation → Streamlit eligibility matrix / Copy
```

Use direct module imports; existing package-root exports remain unchanged:

- `industrial_gases.decision_engine_models`: immutable policy, scope, candidate
  assessment, result, enums and typed validation errors.
- `industrial_gases.decision_engine.evaluate_supply_decision(criteria_evaluation,
  decision_policy, source_binding)`: pure verification and decision function.
- `industrial_gases.workspace_decision`: bounded command recognition, explicit
  policy construction, session reuse key and recorded-result explanation.

This engine is distinct from the earlier Supply Portfolio operational Decision
Model and from Energy/Business decision plans. No new dependency or physical
metric is introduced. The seven v1.18 metrics, finite Decimal targets, signed
safety-stock gap, strict Boolean types and exact operational units are retained.
No implicit conversions or automatic criterion generation are added.

## Policy, scope and authorization

Only `UNIQUE_HARD_CONSTRAINT_COMPLIANT` is supported. Objectives do not determine
eligibility or break ties. Creating a scenario set, submitting constraints,
comparing A/B, or asking which is best does not authorize selection.

| Scope | Authorized candidates |
| --- | --- |
| EXPLICIT_ALTERNATIVES | All explicit alternatives, in original order; baseline is evidence only. |
| ALL_SCENARIOS | Baseline followed by every explicit alternative; requires express inclusion. |

There are no arbitrary candidate subsets or order-based tie-breakers. A partial
visual A/B comparison never determines scope or supplies complete evidence.
Unsupported preferences are rejected, not silently ignored. Missing policy in
the typed API records POLICY_REQUIRED without creating an implicit scope.

Start with the existing scenario grammar:

```text
For Hospital Costa Sur compare delivery in 3 days and 2 days.
```

Then submit criteria as a separate turn:

```text
La brecha de stock de seguridad debe ser al menos 0 kg; la capacidad no debe superarse.
```

Finally authorize selection with one complete command:

```text
Aplica la política de selección estricta a las alternativas.
Selecciona la única alternativa que cumpla todas las restricciones.
Apply strict selection to the alternatives.
Select the unique alternative satisfying all hard constraints.
```

For baseline inclusion, use `Aplica la política de selección estricta incluyendo
el escenario base.` or `Apply strict selection including the baseline scenario.`
These are bounded alternatives, not a general language parser. Submit only one
selection command per turn. Combined criteria/selection commands and appended
preferences or unsupported scopes request clarification. `¿Cuál cumple?`,
`¿Cuál es mejor?`, `Compara A y B` and `¿Qué harías?` do not authorize a policy.

## Eligibility and decisions

All hard constraints satisfied means ELIGIBLE. A demonstrated violation means
INELIGIBLE even if another constraint is UNKNOWN; both references remain.
Otherwise UNKNOWN means UNRESOLVED. Without hard constraints, the aggregate
remains NOT_APPLICABLE and eligibility is None. Objectives are retained separately.

Let E be eligible candidates and U unresolved candidates:

| Condition | Status | Reason |
| --- | --- | --- |
| No hard constraints | UNDECIDABLE | NO_HARD_CONSTRAINTS |
| E = 1, U = 0 | DECIDED | UNIQUE_ELIGIBLE_CANDIDATE |
| E = 0, U = 0 | UNDECIDABLE | NO_ELIGIBLE_CANDIDATE |
| E >= 2 | UNDECIDABLE | MULTIPLE_ELIGIBLE_CANDIDATES |
| E <= 1, U > 0 | INSUFFICIENT_EVIDENCE | ELIGIBILITY_UNRESOLVED |

Only DECIDED carries a selected scenario ID. It means exclusively unique
compliance within this scope, not overall safety, optimality, profitability,
business recommendation or permission to execute an action.

## Hospital Costa Sur acceptance

The existing fixture has delivery horizons baseline 4 days, A 3 days, B 2 days.
Safety-stock gaps are -1100 kg, -400 kg and +300 kg respectively. None exceeds
capacity or projects stockout. Workspace keeps stable domain IDs
`delivery-horizon-days-3` and `delivery-horizon-days-2`; A/B are display labels.

| Case | Explicit hard constraints / source | A | B | Expected and verified result |
| --- | --- | --- | --- | --- |
| A | Gap >= 0 kg; capacity not exceeded | INELIGIBLE | ELIGIBLE | DECIDED, B selected |
| B | No stockout; capacity not exceeded | ELIGIBLE | ELIGIBLE | UNDECIDABLE, MULTIPLE_ELIGIBLE_CANDIDATES |
| C | No stockout; capacity not exceeded; B request lacks consumption forecast | ELIGIBLE | UNRESOLVED | INSUFFICIENT_EVIDENCE, ELIGIBILITY_UNRESOLVED |

Case C constructs incomplete request data before calling the real physical
service. The resulting MISSING_INPUTS branch is retained with its original
provenance and subsequently evaluated by Workspace. No already-evaluated outcome
is altered to manufacture UNKNOWN. Its retained-source setup is a typed fixture:
the conversational grammar does not create a missing forecast. AppTest and Copy
exercise the same production path after this setup.

## Integrity, reuse and provenance

The engine validates identity, reference/revision, ordered scenario manifest,
complete criterion coverage, evaluator version and references. It reconstructs
the full factual comparison from original retained results and reruns the v1.18
logical evaluator. The complete canonical snapshot must match the incoming
evaluation, including observations, outcomes, reasons and provenance references.
Physical projections are never recalculated by this verification.

Source errors raise SupplyDecisionValidationError with typed reasons and chained
causes, separate from UNDECIDABLE and INSUFFICIENT_EVIDENCE. The caller remains
authoritative about the current revision; unseen newer revisions cannot be
detected by this in-memory engine.

An identical explicit request can reuse a verified decision only while source
objects/content, identity, set revision, criteria, policy, scope and evaluator/
engine versions match. Changing criteria, replacing a set, focus change or reset
removes the previous authorization. Scope/policy changes require a new result.
Documentary turns may preserve same-position state, but do not present an old
decision as the current answer. A source object replacement is reverified even
if its values compare equal, preserving the original-baseline reference check.

Snapshots retain policy/version/source, resolved scope, full criteria evaluation,
position identity, source set revision, observed facts and result/projection/
provenance references. Engine version is `supply-decision-v1`; criteria evaluator
version remains `supply-criteria-v1`. These are session-local references and
integrity checks, not persistent historical storage, authentication or audit.

## Streamlit and Copy

The decision block explains DECIDED, UNDECIDABLE and INSUFFICIENT_EVIDENCE in
Spanish. It shows controlled reasons, explicit scope, baseline participation,
ordered eligibility rows, original observations and exact units. UNKNOWN remains
distinct from a violation. A provenance expander exposes identity, revisions and
references. Copy shares the same presentation function and technical boundary.
It reads only the current response's decision, never a retained old decision.
The v1.18 satisfaction matrix and factual scenario comparison remain available.

## Validation and incidents

TASK 5 adds four production-path acceptance tests covering cases A/B/C and the
documentary/decision lifecycle. The focused v1.17/v1.18/TASK 1–4/Workspace/Copy
selection passes **535 tests and 54 subtests**. The isolated new acceptance file
passes **4 tests**. Full `python -m pytest`: **1123 passed in 76.27 seconds**.
FAST: **38/38**, zero generative-LLM calls, zero failures. No timeout or test
failure occurred in the TASK 5 full run. Whitespace checks include untracked
source/tests and documentation, excluding the intentionally untouched artifacts.

Real-browser visual acceptance was also performed through automated interaction
with the production `app.py` on loopback, separate from AppTest. Observed DECIDED
with B selected, UNDECIDABLE with A/B eligible, and INSUFFICIENT_EVIDENCE with
UNKNOWN caused by a `0 t` predicate over `kg` observations. This browser UNKNOWN
example is distinct from case C's incomplete-request fixture. The visible decision
blocks, exact units, controlled reasons and baseline exclusion were inspected in
screenshots; the provenance expander was opened. The actual Copy buttons were
clicked and clipboard text checked for all three states, including no selected
ID for the latter two. The selected-execution Inspector showed zero LLM calls.
This is agent-operated visual inspection, not a claim of a separate human UAT
session or live-provider validation. Screenshots were saved outside the repository;
the temporary browser tab and loopback server were closed after acceptance.

During TASK 4 an existing Healthcare AppTest timed out after 30 seconds:
`test_app_exposes_healthcare_mode_and_runs_without_other_services`. It passed in
isolation and the subsequent full suite passed 1119 tests. No test timeout or
production code was changed to suppress that incident. This record does not
establish a root cause or claim that intermittent timeouts are impossible.

The browser run emitted existing Streamlit component-deprecation warnings. After
closing the temporary tab and interrupting its server, Windows logged a
ConnectionResetError during shutdown. The server exited; this was not a test
failure or a failed selection. These observations did not justify production
changes in this acceptance/documentation task.

Selection, criteria verification, rendering and Copy acceptance guard physical
services/calculators, generative LLM providers, embeddings, RAG and sockets: all
have zero calls after fixture projection creation. Canonical logical predicate
reevaluation is allowed. This boundary excludes documentary retrieval and local
browser/WebSocket transport, which are separate existing application operations.

## Limits and deferred scope

Industrial Gases only, one focused position and one current retained scenario
set. No new selection policies, implicit preferences, candidate generation,
scoring, ranking, optimization, business recommendation, external execution,
persistent decision lifecycle, audit database or cross-domain Energy abstraction.
Energy, Business API, RAG extraction, documentary citations and physical formulas
are unchanged. UI acceptance uses real Streamlit AppTest with deterministic
fixtures; it does not establish live-provider or enterprise readiness.

See [README](README.md), [roadmap](docs/ROADMAP.md),
[ADR-020](docs/DECISIONS.md#adr-020--explicit-unique-compliance-selection-over-verified-retained-criteria)
and [v1.18 criteria notes](RELEASE_NOTES_v1.18.0.md).
