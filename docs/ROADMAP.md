# Roadmap

## Current status

The stable baseline is **v1.15.0**. **v1.16.0 — Efficient & Deterministic
Intelligence** closes the current release scope. This roadmap distinguishes
implemented capabilities from future candidates; candidate items are not
commitments or claims about the current implementation.

## Completed

- **v0.1–v0.9 — Prototype foundations:** conversational LLM integration, gas
  analysis and deterministic tools, local document RAG, procurement agent
  foundations, Commercial/Risk specialists, multi-agent Supervisor, execution UX,
  evaluation and guardrails. Some intermediate milestones have no formal tag.
- **v1.0 — Commercial MVP:** Business entry point, Executive Result, canonical
  demos and local installation path.
- **v1.1 — Analysis/API boundary:** transport-independent AnalysisService and
  FastAPI/Business API integration.
- **v1.2 — Take-or-Pay:** deterministic TOP projection integrated into analysis.
- **v1.3 — Business actions:** structured deterministic actions in the Business
  result.
- **v1.4 — Decision plan:** deterministic decision-plan projection.
- **v1.5 — Decision readiness:** readiness and missing information surfaced.
- **v1.6 — Decision alternatives:** explicit alternative scenarios.
- **v1.7 — Alternative evaluation:** deterministic evaluation inputs/results.
- **v1.8 — Scenario transport and isolation:** LONG and TOP evaluation paths,
  revised TOP forecast inputs, API/UI transport and SHORT/TOP input isolation.
- **v1.9.0 — Industrial Gases Supply Assurance:** domain and
  deterministic calculations, structured and grounded interpretation, Healthcare
  screen and explicit what-if, isolated CO₂/N₂ Food & Beverage flows, and ordered
  independent Supply Portfolio evaluation. Published release.
- **v1.10.0 release candidate — Operational Intelligence:** operational attention
  facts and evaluation issues per position, filtered Supply Portfolio UI, explicit
  deterministic what-if comparisons and a structural Decision Model foundation.
- **v1.11.0 release candidate — Industrial AI:** fictional identity-tagged
  Industrial Knowledge on the existing RAG stack, identity-scoped retrieval, and
  an optional grounded Supply Agent embedded in Supply Portfolio. The agent reads
  existing domain and attention results, retrieves applicable documents, and
  delegates explicit what-if calculations to the existing scenario evaluator.
- **v1.11.1 — Real-provider corrective release:** grounded extraction corrections
  and evaluation harness support, preserving strict DTO and grounding behavior.
- **v1.12.0 — Portfolio Intelligence:** deterministic ordered
  portfolio selection, independent multi-position evidence, bounded structured
  follow-up context, per-item documentary retrieval/citation scopes, explicit
  single-item what-if and Supply Portfolio / Pipeline Inspector presentation.
  No cross-position physical aggregation or recommendation is included.
- **v1.13.0 — Conversational Workspace:** conversation-first
  default entry point that routes requests over existing deterministic portfolio
  selection, per-position evidence, identity-scoped documents and explicit
  single-item scenarios. Bounded follow-up context and structured cards preserve
  evidence boundaries; specialized demo views remain reachable in secondary
  navigation. No new calculations, aggregation or recommendations are added.
- **v1.14.0 — Conversational Intelligence:** bounded current-set,
  focus, documentary and scenario continuity; deterministic structured reference
  resolution; unit-safe factual comparisons over existing projections; scoped
  documentary follow-ups; original-baseline scenario follow-ups; and progressive
  conversational presentation. Published release.
- **v1.15.0 — Conversational Multi-Scenario Decision Support:**
  bounded item-scoped scenario history, original-baseline alternatives,
  deterministic factual comparisons, documentary continuity, Workspace and Copy
  presentation, and safe Pipeline Inspector diagnostics. The default Workspace
  has a persistent right-side Inspector with selectable history bounded to the
  latest 50 operations in the conversation; failures remain inspectable. No
  recommendation, ranking, optimization or automatic scenario search.
- **v1.16.0 — Efficient & Deterministic Intelligence:** documentary-only prompt
  projection; conservative deterministic documentary answers when direct evidence
  eligibility is proven; early documentary scope resolution that can avoid RAG,
  embedding and generation work when clarification is needed; unchanged strict
  citation validation with no post-generation citation injection; embedding
  telemetry, safe lifecycle diagnostics and warm provider/client reuse; and
  query-embedding plus Workspace-generation benchmark tooling. Local Qwen3-8B /
  LM Studio performance was characterized. “No-LLM” denotes zero generative-LLM
  calls; embedding inference may still occur when RAG retrieval is needed.
  Generation remains the fallback when synthesis or operational context is
  required. Experimental runtime tuning values are not production defaults.

## Future direction

The following forward roadmap is a sequence of candidates for design and
validation, not a commitment or a claim about current capabilities:

- **v1.17 — Scenario & Alternative Intelligence.**
- **v1.18 — Objectives & Constraints.**
- **v1.19 — Decision Engine.** Julia 1 may be evaluated as one possible
  technology during this work; it is not a committed product dependency.
- **v2.0 — Industrial Decision Intelligence.**
