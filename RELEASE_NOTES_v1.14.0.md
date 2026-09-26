# indAI MA v1.14.0 — Conversational Intelligence

**Release candidate.** The published baseline is v1.13.0. v1.14.0 has not yet
been released or tagged.

## Overview

The existing Conversational Workspace now carries bounded structured context
between business questions. People can move between the selected set and one
focused position, refer to another position or an explicit business identity,
continue a scoped document question, and ask for factual comparisons without
manually repeating the full position identity.

## Included

- Structured conversational continuity for current selected item IDs, focused
  item, previous intent/query, documentary scope and explicit scenario
  target/change. References are revalidated against the current portfolio, and
  New conversation resets the entire context.
- Deterministic reference resolution for focus, the other position, the current
  set and business identity references in practical Spanish and English. Stale
  or ambiguous references remain visible as clarification requests.
- Deterministic factual comparison of safety-stock gaps, inventory before
  delivery, stockout and capacity-exceeded facts. Shortfall comparisons use
  deficit magnitude while preserving the signed gap. Incompatible operational
  units are not converted or ordered.
- Documentary follow-ups retain their structured position scope and existing
  retrieval/citation validation. A clear question such as “What does it say about
  delivery?” can initiate scoped retrieval from a unique focused position without
  an intermediate document question; document scope is retained after usable
  retrieval. Documentary inventory is returned from source metadata when possible.
- Operational metric follow-ups continue to read deterministic projection facts
  directly, without retrieval or generation, including after a documentary turn.
- Relative delivery-time follow-ups evaluate each explicit change against the
  original structured baseline. Unsupported vague dates request clarification.
- Compact conversational presentation of structured comparison facts, with
  technical context and comparison details remaining in Evidence & trace.
- Added a secondary Copy action for the visible Conversational Workspace answer
  and its user-facing structured facts; trace and hidden context are excluded.
- Added safe Copy output for the selected Pipeline Inspector execution,
  including allowlisted stage metadata and semantic-guard diagnostics.
- Increased the default LLM request timeout to 640 seconds. Explicit
  `LLM_TIMEOUT_SECONDS` configuration continues to override the default.

## Boundaries

This release adds no supply formulas, thresholds, physical aggregation, unit
conversion, severity, priority, ranking, winner, recommendation, optimization,
logistics planning, automatic scenario search or batch alternatives. Existing
Supply Assurance, Operational Attention, Portfolio, Industrial Knowledge,
citation-validation and scenario-calculation services remain authoritative.
Decision Support / explicit multi-alternative comparison is future v1.15 scope,
not an implemented v1.14 capability.
