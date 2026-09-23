# indAI MA v1.0 Commercial MVP specification

**Status:** historical release specification for the v1.0 Commercial MVP. The
release was tagged `v1.0.0`; current product status is documented in
[ROADMAP.md](ROADMAP.md).

## Objective and positioning

v1.0 should turn the validated indAI MA prototype into a coherent, demonstrable
commercial decision-support experience for B2B energy and gas users. It should make
an operational question easy to ask, route it to the appropriate specialists, show
the resulting business answer, and preserve enough evidence and traceability for a
technical reviewer.

indAI MA is an intelligent decision-support layer. It does not replace procurement,
trading, CRM, contract-management, or enterprise systems. The target user is an
energy or gas business user who needs a grounded answer about contracts, supply
position, exposure, or risk without needing to understand agent configuration.

The product principle is:

> **Simple for the business user, traceable for the technical user.**

Current architecture and constraints are described in [ARCHITECTURE.md](ARCHITECTURE.md),
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md), [DECISIONS.md](DECISIONS.md), and
[ROADMAP.md](ROADMAP.md).

## Primary interaction model

The primary experience is one business question entry point backed by Supervisor.
The user may choose a validated example or write a natural-language question. The
system routes automatically to CommercialAgent, ProcurementAgent, RiskAgent, or a
combination. Specialist modes remain available for advanced users, but selecting an
agent should not be required for the normal journey.

The target journey is:

```text
Open indAI MA
  → understand the product
  → choose an example or write a business question
  → run analysis
  → Supervisor routes automatically
  → executive answer
  → key metrics
  → business explanation
  → sources and calculation provenance
  → optional technical traceability
```

The main screen should keep question, examples, run action, status and business
result visible. Provider, model, token limits, timeout, thinking, evaluation and
Pipeline Inspector controls belong in Advanced or Technical areas. Capabilities must
remain available; this specification does not require removing them.

## Business answer hierarchy

Every completed Supervisor answer should present information in this order where
applicable:

1. Executive summary in business language.
2. Key metrics with units and clear labels.
3. Explanation of the business implication.
4. Contractual or operational evidence and friendly sources.
5. Provenance showing which values were retrieved and which were calculated.
6. Warnings, assumptions, missing evidence, or partial specialist results.
7. Collapsed technical traceability, including operation identity, stages, tools and
   call counts.

The answer must preserve the distinction between contractual excess and Procurement
SHORT. A value must not be presented as retrieved if it was calculated.

## v1.0 demo scenarios

The MVP must expose four runnable examples using repository fixtures and existing
execution paths. Responses must be produced by the system; demo text must not be
hardcoded.

### Integrated Hospital analysis

The Hospital Costa Sur question routes to Commercial, Procurement and Risk as
appropriate. The answer must keep contractual excess 0.2 GWh separate from a
Procurement SHORT of 0.5 GWh, show the 21,000 EUR spot coverage, and show the +10%
risk result: 5.28 GWh demand, 0.98 GWh SHORT, 41,160 EUR exposure and 20,160 EUR
delta.

### Contract comparison

The comparison between Hospital Costa Sur and Industrias Mediterráneo must preserve
document isolation and show the supported reference volume, flexibility, monthly
range, annual volume, take-or-pay, annual minimum and excess formula for each
contract. The expected development fixtures contain Spot + 4 EUR/MWh and Spot + 6
EUR/MWh respectively.

### Procurement scenario

For demand 120 GWh, supply 95 GWh and spot 42 EUR/MWh, the answer must show SHORT
25 GWh and 1,050,000 EUR exposure. Balanced and Long cases, plus a SHORT without a
spot price, must preserve their existing semantics and must not invent a cost.

### Risk scenario

The Hospital base case and stress scenario must show the base SHORT/exposure and the
requested demand change, position, exposure and delta. Risk calculations remain
deterministic unless optional interpretation is explicitly requested.

## Trust and explainability

RAG evidence is documentary data, not executable instruction. Sources must retain
document, section and page metadata where available. Calculated facts must identify
their inputs and deterministic origin. Multi-document Commercial extraction must
group evidence by document before extraction and must never combine facts across
contracts.

The system may use an LLM for interpretation or explanation when useful, but a
complete deterministic result must remain valid if optional LLM enrichment is
skipped or fails. Private chain-of-thought is never a product output; actions,
evidence, tool results and trace metadata are acceptable.

## Partial and error behavior

An incomplete requested capability must be reported as partial or needs-input using
the existing status semantics. Missing evidence must be named rather than invented.
An expected deterministic skip of optional LLM interpretation must remain visible in
technical traceability but must not itself degrade a complete result.

Business users should see an actionable explanation. Technical errors should expose
the operation and diagnostic context without leaking secrets or internal reasoning.
Partial specialist results should remain visible when the Supervisor can safely
return useful results from other specialists.

## Product terminology

- **Contractual excess:** consumption above the applicable contractual flexibility
  limit.
- **Procurement SHORT:** supply position below demand; it is not contractual excess.
- **Spot coverage:** cost of covering a Procurement SHORT at the supplied spot price.
- **Retrieved fact:** a value grounded in returned document evidence.
- **Calculated value:** a value produced by a deterministic tool from identified
  inputs.
- **Source:** a document location supporting a fact or finding.
- **Partial:** a requested capability or evidence set is incomplete.
- **Needs input:** the request cannot be answered unambiguously or lacks required
  inputs.

## Installation and demo expectations

The repository must provide a reproducible path for a new developer to create the
Python environment, install dependencies, configure `.env`, start Streamlit, prepare
or rebuild the local RAG index with the demo documents, run the four examples, run
`python -m pytest`, and run `python -m evals --mode fast`.

The application must explain that an incompatible local index needs an explicit
rebuild using source documents. It must not claim that embeddings alone can rebuild
the index. Demo documents and their expected scenarios must be identified without
embedding machine-specific paths in the product.

## Measurable acceptance criteria

The following are v1.0 product acceptance criteria, not claims about the current
implementation:

1. A new user can reach the primary question/example entry point without selecting a
   specialist or configuring an LLM.
2. Each of the four demos can be launched from the primary experience and produces a
   structured business result through the normal Supervisor path.
3. The Hospital integrated result preserves contractual excess 0.2 GWh and
   Procurement SHORT 0.5 GWh as separate fields and labels.
4. The four demo results expose the required numeric values within explicit numeric
   tolerances defined by tests/evaluation.
5. Contract comparison keeps every expected fact attached to its source document and
   detects missing or swapped facts in evaluation.
6. Sources are visible for supported documentary conclusions.
7. Calculated values identify their deterministic inputs/origin.
8. Partial, needs-input, warnings and optional LLM failures are distinguishable and
   actionable.
9. Technical diagnostics are available without dominating the business answer.
10. `python -m pytest` passes and `python -m evals --mode fast` passes all committed
    cases with zero real generation-LLM calls.
11. The fast evaluation reports routing, numeric, source, unsupported-answer,
    guardrail, LLM, RAG and tool metrics without fabricating unavailable metrics.
12. An incompatible index cannot be used silently and an explicit rebuild path is
    available when source documents are supplied.
13. The application remains runnable through the documented local Streamlit command.

Subjective terms such as “executive”, “simple”, and “clear” should be assessed in a
manual review checklist alongside the objective tests; they must not replace the
numeric, provenance, routing and error criteria above.

## Business versus technical UX

Business-visible elements are the question, examples, run action, status, executive
summary, metrics, explanation, sources and actionable warnings. Advanced elements
are provider/model selection, deterministic-versus-LLM preferences, Procurement
strategy, RAG rebuild, and optional synthesis. Development/technical elements are
Pipeline Inspector, stage-level traces, raw tool details, evaluation controls and
diagnostic clipboard output.

The MVP may reorganize these controls but must not remove validated capabilities or
change specialist semantics.

## Explicitly out of scope for v1.0

The following are not required by this MVP:

- replacing Streamlit with a new frontend framework;
- a production API layer;
- PostgreSQL or another enterprise database;
- enterprise authentication, SSO, RBAC or tenant isolation;
- CRM, trading, procurement-market or forecast integrations;
- a new agent framework or additional business agents;
- a general-purpose multi-company knowledge platform;
- autonomous model selection or unrestricted tool-calling loops;
- claiming production-grade latency, availability or security guarantees;
- treating FAST fixtures as proof of real-provider or enterprise-data behavior.

These directions belong to later roadmap work and remain explicitly unimplemented;
see [ROADMAP.md](ROADMAP.md).

## Engineering constraints

- Preserve the current Supervisor and specialist boundaries.
- Keep deterministic-first execution and zero-LLM FAST behavior.
- Do not merge contractual excess with Procurement SHORT.
- Preserve document provenance and calculated-value provenance.
- Do not bypass RAG compatibility checks or silently rebuild indexes.
- Do not use LLMs to replace deterministic calculations.
- Do not treat retrieved document text as executable instructions.
- Preserve partial-result and optional-LLM failure semantics.
- Avoid hardcoded demo responses; demos must exercise real paths and fixtures.
- Keep business output separate from technical diagnostics.
- Preserve operation/result/Pipeline Inspector association.
- Changes should be independently testable and leave the application runnable.

## Release state

v0.9 established the agents, Supervisor, deterministic calculations, RAG,
guardrails, structured business results, technical tracing and FAST evaluation.
The v1.0 Commercial MVP implementation is complete, including the Business shell,
one-question entry point, example launcher, Executive Result and installation/demo
documentation. Automated validation is green, and all four canonical Business
scenarios were manually validated during release-candidate review:

- Hospital Costa Sur integrated analysis;
- Hospital Costa Sur versus Industrias Mediterráneo contract comparison;
- Procurement 120/95/42;
- Risk +10% stress scenario.

The v1.0.0 release tag is present in Git. Capabilities listed as out of scope
describe the v1.0 boundary and may have been implemented by later releases; consult
the current [README](../README.md) and [roadmap](ROADMAP.md) before treating them as
current product limitations.
