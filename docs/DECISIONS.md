# Retrospective architectural decisions

These entries document decisions visible in the implementation; they are not
claims that formal ADRs existed at the time.

## ADR-001 — Deterministic-first execution

**Status:** implemented. **Context:** complete contractual and numeric paths do not
need generation. **Decision:** prefer deterministic extraction and tools, with LLM
interpretation optional. **Consequences:** predictable cost and latency; wording
coverage remains bounded by deterministic rules.

## ADR-002 — RAG retrieves, tools calculate, LLM interprets

**Status:** implemented. **Context:** evidence and computation have different
failure modes. **Decision:** keep those responsibilities separate. **Consequences:**
results can be grounded and audited without an LLM call.

## ADR-003 — Provider abstraction

**Status:** implemented. **Decision:** provider clients are behind `llm_client.py`
and specialist adapters. **Consequence:** local providers can be replaced without
changing business tools.

## ADR-004/005 — Specialist boundaries and Supervisor ownership

**Status:** implemented. Specialists do not call specialists; `Supervisor` owns
routing, collection and synthesis.

## ADR-006 — Multi-Agent does not require Multi-LLM

**Status:** implemented. Deterministic Supervisor and specialist paths can complete
with zero generation calls.

## ADR-007 — Capability-dependent validation

**Status:** implemented. Guardrails and result status distinguish blocked input,
missing required capability, and expected optional interpretation skips.

## ADR-008/009 — Response and provenance contracts

**Status:** implemented. Documentary comparisons are structured separately from
quantitative calculations; retrieved facts, calculations, and friendly sources are
represented distinctly.

## ADR-010/011 — Independent contracts and semantic chunks

**Status:** implemented. Multi-contract extraction groups by document before
extraction, and structural chunking preserves related contractual statements.

## ADR-012/013 — Offline evaluation and test distinction

**Status:** implemented. FAST evaluation is deterministic/offline. Unit/integration
tests check coded behavior; evaluations check business outcomes and grounding.

## ADR-014 — Optional enrichment does not invalidate deterministic results

**Status:** implemented. An expected skipped interpretation stage is diagnostic
information, not a failure of an otherwise complete result.
