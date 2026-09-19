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

## ADR-015 — Deterministic Executive Result projection

**Status:** implemented. Business presentation is derived deterministically from
structured specialist results; domain results remain the source of truth and
technical diagnostics remain separate. The projection must preserve provenance,
specialist boundaries and zero-LLM completeness without recalculating business
values.

## ADR-016 — Application Service as the transport-independent analysis boundary

**Status:** implemented in v1.1 Increment A. The main analysis use case is
exposed through `AnalysisService`, independent of Streamlit. UI and future
transport adapters construct an `AnalysisRequest`, invoke the service and
present or store its `AnalysisResult`.

The service composes the existing Supervisor, specialists, RAG/tools,
guardrails, deterministic Executive Result projection and observability. This
keeps application orchestration reusable from tests and future transports while
preserving the current specialist boundaries and behavior. Streamlit remains
responsible for jobs, threading, cancellation, reruns, session state and
rendering. FastAPI is intentionally not introduced in this increment.

## ADR-017 — FastAPI as an HTTP adapter over AnalysisService

**Status:** implemented in v1.1 Increment B. FastAPI exposes only health, readiness and the synchronous analysis endpoint. HTTP DTOs are representations of `AnalysisResult`, `SupervisorResult` and `ExecutiveResultProjection`; they are not sources of business truth and do not recalculate values.

The API owns transport validation and server-controlled runtime configuration, then constructs `AnalysisRequest` and invokes `AnalysisService`. It does not call Supervisor, specialists, RAG or tools directly. No operation persistence, polling, authentication or enterprise infrastructure is introduced in this increment.

## ADR-018 — Streamlit Business UI consumes the backend through the HTTP API

**Status:** implemented in v1.1 Increment C. Business execution crosses the
`AnalysisApiClient` boundary and reaches `AnalysisService` only through FastAPI.
The client sends the public `{text}` contract, and server configuration controls
providers, models, limits, LLM policy and RAG paths. There is no automatic direct
fallback when the API is unavailable. AgentJob remains responsible for frontend
threading, reruns, timeout display and local wait cancellation; backend execution
is not distributed-cancellable in this increment.

Technical modes may remain direct during the migration. The remote Business
Inspector exposes only safe aggregate diagnostics and never raw event streams,
prompts or credentials.
