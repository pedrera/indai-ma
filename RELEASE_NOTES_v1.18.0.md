# indAI MA v1.18.0 — Objectives & Constraints

## Release purpose and status

Local release candidate, based on published v1.17.0 at
`7d5aa93039c046d2e7269b193380c3632e200792`. Publication is a separate Git operation.

v1.17 answers “¿Qué ocurre con A, B o C?” v1.18 adds “¿Qué alternativa satisface
nuestros criterios explícitos?” for one resolved Industrial Gases position and
its retained SupplyAssuranceScenarioSet. It reports each scenario's criterion
satisfaction; it does not choose an alternative or make an automatic decision.

## Architecture

```text
Explicit criterion sentences / typed definitions
    → immutable SupplyCriteria bound to one retained set revision
    → full factual comparison of existing scenario results
    → evaluate_supply_criteria(criteria, comparison, source_binding)
    → ScenarioSetCriteriaEvaluation
    → Workspace state / satisfaction matrix / Copy
```

`industrial_gases.objective_constraint_models` contains the immutable definition,
predicate, source and result contracts. `industrial_gases.objective_constraint_evaluation`
exposes `evaluate_supply_criteria` and `RetainedScenarioSetSource`. Use these module
imports; the new symbols are not re-exported from the package root. Existing
package exports remain unchanged.

SupplyAssuranceService still owns physical projections. The evaluator reads
already-calculated facts and projection provenance; no new physical metrics or
formulas are introduced. Domain input validation and service findings are not
automatically promoted to business constraints.

## Definitions and outcomes

An **objective** is an explicitly measurable target, not a preference to optimize.
A **hard constraint** is an explicitly mandatory condition. Both retain a typed
predicate and explicit definition origin, reference and revision. IDs are unique
across both kinds. Lists are copied into ordered immutable tuples; no random IDs
or mutable defaults are introduced.

Each criterion returns:

| Outcome | Meaning |
| --- | --- |
| SATISFIED | A verified available fact satisfies the submitted predicate. |
| VIOLATED | A verified available fact contradicts it. |
| UNKNOWN | Reliable evaluation is unavailable, with a controlled reason. |

Hard constraints aggregate independently for each scenario: any violation yields
VIOLATED; otherwise any unknown yields UNKNOWN; all submitted constraints
satisfied yields SATISFIED; no hard constraints yields NOT_APPLICABLE. Individual
unknowns remain visible even when another constraint is violated. Objective
outcomes do not change this aggregate. Contradictory explicit constraints are
evaluated independently rather than silently rewritten.

These states assess only submitted criteria. They do not establish overall
safety, feasibility, readiness, relative desirability or a recommended action.

## Eligible metrics and units

The typed evaluator accepts exactly the existing factual-comparison whitelist:

| Metric | Type and meaning |
| --- | --- |
| `consumption_until_delivery` | Nonnegative operational quantity |
| `inventory_immediately_before_delivery` | Nonnegative operational quantity |
| `safety_stock_gap_before_delivery` | Signed operational quantity |
| `stockout_before_delivery` | Boolean, no unit |
| `inventory_immediately_after_delivery` | Nonnegative operational quantity |
| `required_delivery_volume` | Nonnegative operational quantity |
| `capacity_exceeded` | Boolean, no unit |

Numeric targets must be finite Decimal values and use `lt`, `le`, `eq`, `ge` or
`gt`. Only safety-stock-gap targets can be negative. Boolean targets must be
actual bool values and use `eq`; numeric zero is not coerced to false.

Numeric definitions require an exact unit from `kg`, `t`, `L`, `m3`, `Nm3` or
`Sm3`. There is no conversion, including kg/t or Nm3/Sm3. A supported target unit
that differs from the verified fact's unit produces UNKNOWN, not satisfaction.
Unsupported units and malformed definitions are rejected.

Existing physical semantics remain authoritative: safety-stock gap subtracts
configured safety stock from clamped pre-delivery inventory; stockout uses the
unclamped projection and is false when inventory reaches zero exactly at delivery.
Capacity equality does not mean capacity exceeded. Zero safety stock can coexist
with stockout and a nonnegative gap, so these facts remain independent. Days of
supply, stockout timestamps, capacity overflow magnitude, costs and emissions
are not additional criterion metrics in this release.

## Bounded Workspace grammar

The typed API supports seven metrics and five numeric operators. Workspace
sentence recognition is intentionally narrower: safety-stock gap and inventory
before delivery with “at least” / “al menos”, plus explicit stockout and capacity
Boolean expectations. It is not general natural-language objective extraction.

Create a set using the existing v1.17 grammar:

```text
For Hospital Costa Sur compare delivery in 3 days and 2 days.
```

Then submit either objectives or hard constraints:

```text
Objetivo: la brecha de stock de seguridad debe ser al menos 0 kg; la capacidad no debe superarse.
```

```text
La brecha de stock de seguridad debe ser al menos 0 kg; la capacidad no debe superarse.
```

```text
Objective: Safety-stock gap must be at least 0 kg; There must be no stockout before delivery.
```

Other accepted sentences include `Inventory before delivery must be at least
1000 kg`, `El inventario antes de la entrega debe ser al menos 1000 kg`,
`No debe haber agotamiento antes de la entrega` and `Capacity must not be
exceeded`. Explicit positive Boolean counterparts are also recognized; no
Boolean expectation is inferred from a vague goal.

`Objective:` / `Objetivo:` marks an objective. Unprefixed mandatory sentences,
or `Constraint:` / `Restricción:` prefixes, mark hard constraints. Separate
complete clauses with semicolons or newlines; a scenario request can precede
criteria using the same separator. Numeric literals use a decimal point and
unit spellings are exact. This is not a general sentence/conjunction parser.

If any clause is unsupported, malformed, incomplete or an identical duplicate,
the request asks for clarification without evaluating a valid subset. “Make the
operation safer”, “Optimize delivery”, “Minimize risk” and choosing the best
alternative do not generate criteria. Criterion definitions are explicit,
not extracted from service findings or documentary text.

## Binding, lifecycle and provenance

The evaluator validates the set reference/revision, full customer/site/application/
gas-product/installation identity, baseline result references, alternative
definitions/order, projection identity/reference time and supplied comparison
facts. Labels are presentation, not identifiers. Global mismatches raise
SourceBindingError and do not emit a compliance result. Invalid or missing
branches, absent facts/projections, invalid fact types/units and unavailable or
mismatched provenance yield UNKNOWN independently; valid branches still evaluate.

The evaluator rejects incomplete A/B scenario coverage. Workspace constructs a
full comparison from retained results for criteria, while preserving a partial
factual display. No projection is rerun for criteria-only follow-ups. Identical
definitions reuse their evaluation; ordinary factual turns retain it. A new set,
focus change, single-scenario replacement or conversation reset clears unrelated
criteria. Each criteria submission replaces the prior criteria definition set;
there is no implicit accumulation of earlier sentences.

Workspace revisions combine a session-local generation counter with a deterministic
fingerprint of the retained requests/results. Criteria definitions have their own
content revision. Result records include criterion kind/ID, full predicate and
definition source, observed value/unit, scenario ID, set identity/revision,
result/projection/provenance references, evaluator version and outcome/reason.
Existing projection provenance objects remain unchanged; references address their
original records. These are in-memory integrity and traceability boundaries, not
durable audit storage, authenticated signatures or a global cross-session registry.
The typed evaluator relies on the caller's authoritative retained-source binding.

TASK 4B corrects a scope-resolution collision: “al menos ... capacidad” matched the
general factual-comparison detector. A fully parsed criteria-only follow-up now
selects the retained focused item directly, keeping revision/identity/source
checks. New scenario requests, documentary routing and ordinary portfolio queries
retain their existing paths.

## Streamlit and Copy

The existing Workspace response renders an ordered criterion matrix and a separate
hard-constraint aggregate. Spanish labels include Objetivo, Restricción obligatoria,
Cumple, No cumple, No evaluable, and cumple/incumple/desconocido/no aplica for
constraint compliance. Definitions, observed values, exact units and unknown
reasons remain visible. UI and Copy share formatted rows; Copy additionally
includes the available source details shown in the references expander. The UI
does not evaluate predicates or rank alternatives. Existing comparison tables,
documentary source/citation behavior, portfolio cards and chat state are retained.

## Acceptance evidence

The fixture-backed production Workspace and Streamlit AppTest paths exercise set
creation plus criteria, two Spanish hard-constraint follow-ups, partial A/B
displays, objective versus constraint prefixes, cache reuse, revision replacement,
focus/reset, unknown facts, source integrity, Copy parity and dependency boundaries.
The TASK 4B regression was observed failing before correction and passing after it.
This is offline functional acceptance, not a live LM Studio or browser-provider run.

For Hospital Costa Sur, baseline delivery is +4 days, A is +3 days and B is +2 days:

| Submitted criterion / fact | Baseline | A | B |
| --- | --- | --- | --- |
| Existing safety-stock gap | -1,100 kg | -400 kg | +300 kg |
| Gap >= 0 kg | VIOLATED | VIOLATED | SATISFIED |
| Capacity exceeded == false | SATISFIED | SATISFIED | SATISFIED |
| Aggregate when both are hard constraints | VIOLATED | VIOLATED | SATISFIED |

When gap is an objective and only capacity is a hard constraint, capacity
compliance is SATISFIED in all three branches; the objective outcomes remain as
shown. Neither result selects a winner.

The full suite passed **932 tests**. Focused TASK 1–4B and existing Workspace tests
passed **285 tests and 47 subtests**. FAST passed **38/38**, with **110/110 numeric
assertions**, **4/4 source assertions**, **100% routing**, **0 guardrail failures**,
**0 unsupported-answer failures**, **0 generation-LLM calls**, **17 RAG calls** and
**79 tool calls**. FAST counts are suite-wide. Criteria-only interpretation and
evaluation themselves make **0 generation-LLM, 0 embedding, 0 RAG and 0 physical
service/calculation calls**; a new definition evaluates once and an identical
repeat reuses it. New scenario creation uses the existing deterministic service.

## Deferred scope

No preferences, winner selection, ranking, recommendation, scoring, optimization,
automatic objectives/constraints/scenarios, arbitrary natural-language criteria,
cross-position/cross-gas aggregation, transversal Energy criteria framework,
mixed-dimension scenario generation, persistence or decision execution is added.
Energy/Business API, RAG, document extraction, citations and physical calculations
are unchanged. Optional LLM and retrieval paths elsewhere in the application
remain available; this release does not claim the entire application is No-LLM.
