# indAI MA v1.17.0 — Scenario & Alternative Intelligence

## Release purpose

v1.17.0 answers: **“¿Qué ocurre con A, B o C?”** For one resolved Industrial
Gases position, the Conversational Workspace can retain the real current
baseline, evaluate several explicitly stated alternatives, and present their
factual consequences. It does not answer which alternative is best.

## Architecture

The Workspace follows the existing deterministic scenario-set and comparison
contracts:

```text
Natural-language request
    ↓
scope resolution
    ↓
explicit alternative extraction / validation
    ↓
SupplyAssuranceScenarioSet
    ↓
evaluate_supply_assurance_scenario_set()
    ↓
SupplyAssuranceScenarioSetResult
    ↓
compare_supply_assurance_scenario_set()
    ↓
ScenarioSetComparison
    ↓
Workspace state / factual follow-up / presentation
```

The baseline is evaluated once. Each alternative is evaluated independently
against that baseline request; alternatives do not chain from one another. All
branches retain the same customer, site, application, gas product and
installation identity, and caller order is preserved. Missing-input and invalid
service results remain explicit. Physical calculations stay in
`SupplyAssuranceService`; comparison reads those results and does not recalculate
them.

## Deterministic scenario-set contract

v1.17 supports explicit, same-dimension alternative sets for:

- delivery horizon;
- planned delivery quantity;
- consumption rate.

An alternative must state the required value and supported unit. Ambiguous scope,
mixed dimensions, incomplete values or incompatible units request input instead
of partially executing a scenario set. A valid set keeps the current request as
its baseline and preserves alternatives in the order stated by the user.

## Factual comparison

`ScenarioSetComparison` exposes existing factual values and units from the
scenario results, including consumption to delivery, inventory before and after
delivery, safety-stock gap, stockout, required delivery volume and capacity
exceeded. It identifies unavailable values and non-comparable metrics without
ranking or aggregating them. The comparison layer adds no physical calculations.

## Conversational Workspace and Copy

The Workspace displays **Baseline**, **A**, **B**, and further alternatives in
their retained order. A factual follow-up such as “Compare A and B” resolves to
the retained set and compares its existing results without reevaluation. The
response and Copy output preserve labels, order, values, units and unavailable
states.

## Acceptance evidence

Functional acceptance exercised the Conversational Workspace orchestrator with
the Hospital Costa Sur / O₂ fixture and fixture-backed model and knowledge
providers. It was **not** a live LM Studio run.

For delivery horizon, the baseline is the current delivery at +4 days, A is +3
days, and B is +2 days:

| Existing factual metric | Baseline | A | B |
| --- | ---: | ---: | ---: |
| Inventory before delivery | 400 kg | 1,100 kg | 1,800 kg |
| Consumption to delivery | 2,800 kg | 2,100 kg | 1,400 kg |
| Safety-stock gap | -1,100 kg | -400 kg | +300 kg |
| Inventory after delivery | 4,400 kg | 5,100 kg | 5,800 kg |
| Required delivery volume | 1,100 kg | 400 kg | 0 kg |

Stockout before delivery and capacity exceeded were both false in all three
branches.

For planned delivery quantity, the baseline is 4,000 kg, A is 4,500 kg, and B is
5,000 kg. Inventory before delivery was 400 kg in each branch; inventory after
delivery was 4,400 / 4,900 / 5,400 kg. Consumption to delivery was 2,800 kg in
each branch.

For consumption rate, the baseline is 700 kg/day, A is 800 kg/day, and B is 900
kg/day. Consumption to delivery was 2,800 / 3,200 / 3,600 kg; inventory before
delivery was 400 / 0 / 0 kg; stockout before delivery was false / false / true;
inventory after delivery was 4,400 / 4,000 / 4,000 kg; and required delivery
volume was 1,100 / 1,500 / 1,900 kg.

Ambiguous scope, mixed-dimension alternatives, missing units and incompatible
units returned `NEEDS_INPUT` without partial scenario-set execution. “Compare A
and B” reused retained results without recalculation. “Which is best?” returned
`scenario_ranking_not_supported`.

The full test suite passed **719 tests**. FAST passed **38/38** with **100%
routing**, **110/110 numeric assertions**, **4/4 source assertions**, **0
unsupported-answer failures**, **0 guardrail failures**, **0 LLM calls**, **17 RAG
calls**, and **79 tool calls**. These FAST counts are suite-level metrics, not
per-scenario-set usage. Deterministic scenario-set evaluation and factual
comparison themselves make **0 generation-LLM, 0 embedding, and 0 RAG calls**.

## Model and RAG boundaries

The deterministic scenario-set evaluation/comparison path does not invoke a
generation LLM, embeddings or RAG. This statement applies to that path only; it
does not mean all of indAI MA is No-LLM. Other Workspace requests can follow
different model or retrieval paths.

## Intentionally unsupported

v1.17 does not provide winner selection, ranking, recommendation, scoring,
objectives, preferences, constraints, optimization, mixed-dimension alternative
sets, automatic what-if generation, coordinated Commercial/Procurement/Risk
hypothetical evaluation, multi-position or multi-gas aggregate alternatives, or
combined documentary and scenario-set synthesis.

## Next roadmap step

**v1.18 — Objectives & Constraints** is the next step: represent what the
user/business is trying to achieve and which constraints must be satisfied when
evaluating alternatives. This release does not implement that capability.
