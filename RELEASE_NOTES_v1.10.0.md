# indAI MA v1.10.0 — Operational Intelligence

**Status:** release candidate; the v1.10.0 tag has not been created.

## Operational Attention

Supply Portfolio now surfaces existing operational findings independently for
each position. Safety-stock breach, stockout-before-delivery and capacity-overflow
facts remain traceable to the original Supply Assurance result and projection.
`INVALID` and `MISSING_INPUTS` are shown as evaluation issues, separate from
operational facts. Completed positions without findings are shown as “No attention
facts.” Summary counts and filters are presentation-only.

## Explicit Portfolio What-if

For one explicitly selected position, a user can evaluate one hypothesis at a
time: a different planned-delivery date, planned-delivery quantity, or consumption
forecast rate. Baseline and alternative requests are evaluated by the existing
deterministic Supply Assurance service. The UI shows the declared change and the
structured results for both cases, preserving their identity and status.

## Decision Model foundation

Small immutable records retain the source position, baseline, declared input
change and ordered alternative results. They provide a structured representation
for later capabilities; they are not a decision engine and do not select an
alternative.

## Boundaries

This Supply Portfolio path is deterministic and has no dependency on generation
LLM, RAG, agents, Supervisor or Business API. It does not rank positions, score
alternatives, recommend or optimize actions, search scenarios automatically,
convert units, or aggregate physical quantities across positions or gases.

Existing energy/Commercial, Healthcare and Food & Beverage capabilities remain
separate and unchanged.
