# indAI MA v1.12.0 — Portfolio Intelligence (release candidate)

Status: release candidate; not tagged or published.

## What is included

The Supply Agent embedded in Supply Portfolio can now answer questions over an
ordered set of independently evaluated positions. Structured filters select
positions deterministically from the existing `SupplyPortfolioResult`; every
match retains why it matched. Common attention and evaluation-status list
queries are returned from that deterministic selection without a generation
call.

Each selected position remains its own evidence scope. Bundles retain the
original request, identity, `SupplyAssuranceResult`, attention facts, any
explicit single-position scenario, scoped documentary chunks, and retrieval
failures. Applicable global Industrial Gases policy is represented separately.
Citation validation rejects references that cross a position boundary.

Supply Agent session context contains only bounded structured item IDs, the
focused item, the previous portfolio filter and an explicit scenario target.
Explicit identity in the current question takes precedence. Ambiguous or stale
references request clarification. A what-if still uses the existing
deterministic evaluator for exactly one explicitly resolved item.

The Supply Portfolio screen now offers an entire-portfolio scope alongside
individual positions and presents domain evidence, findings, sources, scenarios
and provenance separately for each position. Pipeline Inspector exposes actual
portfolio selection, contextual reference resolution, per-scope retrieval,
scenario execution and citation-validation stages.

## Boundaries

Supply Assurance remains the authority for calculations and projections;
Operational Attention remains the source of findings; the existing
Industrial Knowledge service remains responsible for scoped retrieval. The
model interprets and explains evidence but does not choose portfolio matches,
calculate supply facts or findings, create provenance, or authorize citations.

No physical quantities are aggregated across positions or gases. This release
adds no portfolio status or score, severity, priority, ranking, winner,
recommendation, optimization, batch what-if, automatic scenarios, logistics
planning, conversion, new thresholds, new agents, Supervisor integration, API
or UI top-level mode. Retrieval and validation failure remain visible per item
without discarding independent evidence from other positions.

## Validation

The release gate is still in progress. See the final implementation report for
the exact offline pytest and FAST results. No real-provider evaluation was run
as part of the deterministic release gate.
