# indAI MA v0.7.0 phase 1 — Release notes

## Scope

This phase introduces the first explicit agent foundations while preserving
Chat, documentary RAG, quantitative Gas B2B analysis, and the existing
deterministic business tools.

## Added

- Procurement execution selector: Deterministic, Planner Agent, and ReAct Agent.
- `ProcurementAgentPlanner` with structured Pydantic plans and a maximum of five
  planned actions.
- Explicit ReAct state, decisions, observations, decision/tool budgets, and
  bounded recovery from premature completion.
- Scoped tool registry with schema validation and no arbitrary Python execution.
- Shared eligibility and execution policies for optional and unnecessary tools.
- Per-LLM-call records for purpose, wall time, stream/setup time, character
  counts, optional tokens, status, provider, and model.
- Independent operation wall time and non-additive nested timing semantics.
- `ExecutionComparisonRecord`, independent of Streamlit.
- Agent execution summaries, structured plans, skipped actions, and final
  response validation in Pipeline Inspector and copied diagnostics.
- Copy response, Copy diagnostics, and Copy all browser clipboard actions.
- Canonical deterministic fallback when a final response contradicts observable
  procurement results.

## Safety and validation

- Unknown tools, invalid schemas, missing/extra arguments, ungrounded values,
  duplicate identical actions, and excessive plan length are rejected.
- Planner execution is blocked when required plan actions are missing.
- ReAct cannot finish while an explicitly requested deterministic observation is
  missing.
- Spot exposure proposed for a non-SHORT position is recorded and skipped.
- Unsupported economic amounts and completion without requested scenario
  evidence cannot become authoritative user-facing results.
- Traces contain observable decisions and summaries, not private chain-of-thought.
- Metrics and clipboard failures remain isolated from business execution.
- Secrets and authorization-like values are redacted from copied diagnostics.

## Compatibility

- Gas B2B Portfolio Analysis remains available as an independent deterministic
  flow.
- Normal Chat, RAG Chat, document indexing/retrieval, and provider selection are
  retained.
- LM Studio and OpenAI continue to use the shared `LLMProvider` abstraction.
- No agent framework or OpenTelemetry dependency was added.

## Known limitations

- LM Studio does not reliably provide server inference duration or token usage;
  unavailable values are reported rather than estimated.
- Local Qwen latency is high and variable. The current measurements are
  exploratory single samples.
- Planner and ReAct can propose unnecessary actions; deterministic policies may
  skip them and preserve the observable evidence of the poor decision.
- Comparison records are modeled but are not yet persisted or displayed in a
  multi-run dashboard.
- Planned actions execute sequentially.
- Scenario demand is calculated, but scenario spot exposure is not inferred
  unless a deterministic exposure calculation is explicitly executed.
- This phase contains one domain agent only; multi-agent orchestration is out of
  scope.

## Verification target

The phase is expected to pass the complete standard-library `unittest` suite
and a Streamlit health check before commit. See
[`docs/v0.7.0-phase-1-benchmark.md`](docs/v0.7.0-phase-1-benchmark.md) for the
manual reference cases.
