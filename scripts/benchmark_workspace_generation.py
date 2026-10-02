from __future__ import annotations

"""Controlled real-provider benchmark for a generation-required Workspace turn.

This runner deliberately reuses production orchestration, SupplyAgent prompt
construction, provider adapters and validation. It records only fingerprints
and safe structured telemetry; prompt and source text are never written to the
benchmark report.
"""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

# Direct script invocation puts scripts/ on sys.path. Keep the documented
# repository-root command independent of shell PYTHONPATH settings.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from diagnostics import PerformanceRecorder, PerformanceSnapshot, PerformanceStatus
from execution_metrics import LLMCallMetrics, build_operation_metrics
from industrial_gases.conversational_workspace import (
    ConversationalWorkspaceOrchestrator,
    WorkspaceIntent,
)
from industrial_gases.industrial_knowledge import (
    lazy_demo_knowledge_service,
)
from industrial_gases.portfolio_query import SupplyAgentSessionContext
from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio
from industrial_gases.supply_agent import ProviderSupplyDecisionModel
from llm_client import get_llm_provider
from runtime_config import LLMRuntimeConfig


CASE_ID = "combined-contract-inventory"
CASE_QUESTION = "¿Qué dice el contrato y cuál es el inventario antes de la entrega?"
CASE_ITEM_ID = "hospital-costa-sur-o2"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RETRIEVAL_STAGES = ("query_embedding", "vector_search", "retrieved_context")


def stable_fingerprint(value: Any) -> str:
    """Return a deterministic SHA-256 without retaining the represented data."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def classify_run(index: int, *, warmup_runs: int) -> str:
    if index == 0:
        return "first_runner_run"
    if index <= warmup_runs:
        return "warmup"
    return "measured"


def _safe_id(value: Any) -> str:
    text = str(value)
    return text if _SAFE_ID.fullmatch(text) else "[redacted-id]"


def _source_records(response: Any) -> list[dict[str, Any]]:
    """Extract ordered source identity/evidence for hashing only."""
    records: list[dict[str, Any]] = []
    evidence = getattr(response, "evidence", None)
    for item_evidence in getattr(evidence, "items", ()):
        item_id = getattr(getattr(item_evidence, "item", None), "item_id", None)
        for retrieved in getattr(item_evidence, "knowledge_sources", ()):
            chunk = retrieved.chunk
            records.append({
                "item_id": item_id,
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "section": chunk.section,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "metadata": chunk.metadata,
                "score": retrieved.score,
            })
    for scoped in getattr(evidence, "global_sources", ()):
        chunk = scoped.source.chunk
        records.append({
            "global_scope": True,
            "item_ids": scoped.item_ids,
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "document_name": chunk.document_name,
            "section": chunk.section,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "ordinal": chunk.ordinal,
            "text": chunk.text,
            "metadata": chunk.metadata,
            "score": scoped.source.score,
        })
    return records


class _GenerationObserver:
    """Capture only safe request fingerprints/configuration at provider boundary."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.calls: list[dict[str, Any]] = []
        client = getattr(provider, "client", None)
        if client is not None:
            # Observe the final HTTP-compatible payload after the production
            # adapter has added its system message and thinking directive.
            provider.client = _ObservedOpenAIClient(client, self._record_http_request)

    @property
    def provider_name(self) -> str:
        return str(getattr(self.provider, "provider_name", "unknown"))

    @property
    def model(self) -> str:
        return str(getattr(self.provider, "model", "unknown"))

    @property
    def recorder(self) -> Any:
        return getattr(self.provider, "recorder", None)

    @recorder.setter
    def recorder(self, value: Any) -> None:
        self.provider.recorder = value

    def generate_response(self, messages, timeout_seconds=None, options=None):
        runtime = getattr(self.provider, "runtime_config", None)
        generation_config = {
            "provider": self.provider_name,
            "model": self.model,
            "timeout_seconds": timeout_seconds,
            "max_tokens": getattr(self.provider, "max_tokens", getattr(runtime, "max_tokens", None)),
            "max_output_tokens": getattr(
                self.provider, "max_output_tokens", getattr(runtime, "max_output_tokens", None),
            ),
            "thinking_enabled": getattr(self.provider, "thinking_enabled", False),
            "temperature": getattr(options, "temperature", None),
            "tool_calling_enabled": getattr(options, "tool_calling_enabled", None),
            "max_rounds": getattr(options, "max_rounds", None),
            "stream": True,
        }
        self.calls.append({
            "messages_fingerprint": stable_fingerprint(messages),
            "configuration_fingerprint": stable_fingerprint(generation_config),
            "generation_config": generation_config,
            "generation_options": asdict(options) if options is not None and hasattr(options, "__dataclass_fields__") else None,
        })
        return self.provider.generate_response(
            messages, timeout_seconds=timeout_seconds, options=options,
        )

    def _record_http_request(self, parameters: dict[str, Any], client: Any) -> None:
        if not self.calls:
            return
        call = self.calls[-1]
        endpoint = getattr(client, "base_url", None)
        endpoint_fingerprint = (
            stable_fingerprint(str(endpoint)) if endpoint is not None else None
        )
        api_config = {
            "provider": self.provider_name,
            "model": parameters.get("model", self.model),
            "max_tokens": parameters.get("max_tokens"),
            "max_completion_tokens": parameters.get("max_completion_tokens"),
            "temperature": parameters.get(
                "temperature", call["generation_config"].get("temperature"),
            ),
            "stream": parameters.get("stream"),
            "stream_options": parameters.get("stream_options"),
            "timeout_seconds": parameters.get("timeout"),
            "thinking_enabled": getattr(self.provider, "thinking_enabled", False),
            "endpoint_fingerprint": endpoint_fingerprint,
            "tools_fingerprint": (
                stable_fingerprint(parameters["tools"])
                if parameters.get("tools") is not None else None
            ),
            "generation_options": call.get("generation_options"),
        }
        call["messages_fingerprint"] = stable_fingerprint(parameters.get("messages", ()))
        call["configuration_fingerprint"] = stable_fingerprint(api_config)
        call["generation_config"] = api_config


class _ObservedOpenAIClient:
    """Transparent client proxy that fingerprints outgoing messages, never content."""

    def __init__(self, delegate: Any, observer: Callable[[dict[str, Any], Any], None]) -> None:
        self._delegate = delegate
        self._observer = observer
        self._chat_proxy = _ObservedChat(self)

    @property
    def chat(self):
        return self._chat_proxy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def _create(self, parameters: dict[str, Any]):
        self._observer(parameters, self._delegate)
        return self._delegate.chat.completions.create(**parameters)


class _ObservedChat:
    def __init__(self, client: _ObservedOpenAIClient) -> None:
        self.completions = _ObservedCompletions(client)


class _ObservedCompletions:
    def __init__(self, client: _ObservedOpenAIClient) -> None:
        self._client = client

    def create(self, **parameters: Any):
        return self._client._create(parameters)


@dataclass(frozen=True)
class BenchmarkRun:
    run_index: int
    classification: str
    case_id: str
    operation_id: str
    provider: str
    model: str
    operation_status: str
    operation_seconds: float | None
    retrieval_seconds: float | None
    generation_call_count: int
    prompt_fingerprints: tuple[str, ...]
    evidence_fingerprint: str | None
    source_chunk_ids: tuple[str, ...]
    generation_configuration_fingerprints: tuple[str, ...]
    effective_generation_configuration: tuple[dict[str, Any], ...]
    prompt_characters: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    request_setup_seconds: float | None
    request_to_stream_open_seconds: float | None
    time_to_first_token_seconds: float | None
    stream_initial_wait_seconds: float | None
    stream_generation_seconds: float | None
    output_tokens_per_second: float | None
    total_llm_wall_seconds: float | None
    server_inference_seconds: None
    citation_status: str
    citation_count: int | None
    unknown_citation_ids: tuple[str, ...]
    missing_citation: bool | None
    malformed_citation: bool | None
    scope_mismatch: bool | None
    unsupported_claim: bool | None
    deterministic_eligibility_reason: str | None
    deterministic_response_used: bool | None
    semantic_guard_applied: bool | None
    functional_pass: bool
    functional_failures: tuple[str, ...]
    failure_type: str | None
    failure_message: str | None
    failure_phase: str | None
    failure_pipeline_stage: str | None
    failure_traceback: tuple[str, ...]
    comparable: bool
    non_comparable_reasons: tuple[str, ...]
    successful_performance_sample: bool


def _optional_sum(values: list[int | float | None]) -> int | float | None:
    if not values or any(value is None for value in values):
        return None
    return sum(values)  # type: ignore[arg-type]


def _first_event(snapshot: PerformanceSnapshot, stage: str):
    return next((event for event in snapshot.events if event.stage == stage), None)


def _citation_fields(snapshot: PerformanceSnapshot) -> dict[str, Any]:
    events = [event for event in snapshot.events if event.stage == "citation_validation"]
    if not events:
        return {
            "status": "not_reached", "count": None, "unknown_ids": (),
            "missing": None, "malformed": None, "scope_mismatch": None,
            "unsupported_claim": None,
        }
    metadata = events[-1].metadata
    valid = metadata.get("valid") is True and events[-1].status.value == "completed"
    return {
        "status": "passed" if valid else "failed",
        "count": metadata.get("citation_count"),
        "unknown_ids": tuple(_safe_id(value) for value in metadata.get("unknown_chunk_ids", ())),
        "missing": metadata.get("missing_citation"),
        "malformed": metadata.get("malformed_citation"),
        "scope_mismatch": metadata.get("scope_mismatch"),
        "unsupported_claim": metadata.get("unsupported_claim"),
    }


def _metrics_fields(snapshot: PerformanceSnapshot, calls: tuple[LLMCallMetrics, ...]) -> dict[str, Any]:
    def optional(metric: str):
        return _optional_sum([getattr(call, metric) for call in calls])

    generation_times = [call.stream_generation_time for call in calls]
    output_counts = [call.output_tokens for call in calls]
    total_output = _optional_sum(output_counts)
    total_generation = _optional_sum(generation_times)
    rate = (
        total_output / total_generation
        if total_output is not None and total_generation is not None and total_generation > 0
        else calls[0].tokens_per_second if len(calls) == 1 else None
    )
    operation_metrics = build_operation_metrics(snapshot)
    return {
        "prompt_characters": optional("prompt_character_count"),
        "input_tokens": optional("input_tokens"),
        "output_tokens": total_output,
        "total_tokens": optional("total_tokens"),
        "request_setup_seconds": optional("request_setup_time"),
        "request_to_stream_open_seconds": optional("request_to_stream_time"),
        "time_to_first_token_seconds": (
            calls[0].time_to_first_token_time if calls else None
        ),
        "stream_initial_wait_seconds": (
            calls[0].stream_initial_wait_time if calls else None
        ),
        "stream_generation_seconds": total_generation,
        "output_tokens_per_second": rate,
        "total_llm_wall_seconds": (
            operation_metrics.llm_request_wall_time_total if calls else None
        ),
        # Current provider telemetry does not expose a reliable server-side
        # inference value. Keep this explicitly unavailable.
        "server_inference_seconds": None,
        "retrieval_seconds": operation_metrics.retrieval_time_total,
    }


def _functional_checks(response: Any, snapshot: PerformanceSnapshot,
                       call_count: int, citation: dict[str, Any]) -> tuple[str, ...]:
    failures: list[str] = []
    if response is None:
        return ("workspace_operation_failed",)
    if getattr(getattr(response, "status", None), "value", None) != "completed":
        failures.append("operation_not_completed")
    if getattr(getattr(response, "route", None), "intent", None) != WorkspaceIntent.COMBINED:
        failures.append("expected_combined_route_not_used")
    if tuple(getattr(response, "selected_item_ids", ())) != (CASE_ITEM_ID,):
        failures.append("expected_focused_item_not_selected")
    if call_count < 1:
        failures.append("generation_not_performed")
    elif call_count != 1:
        failures.append("expected_single_generation_call_not_used")

    eligibility = _first_event(snapshot, "deterministic_documentary_eligibility")
    if eligibility is None:
        failures.append("documentary_eligibility_not_recorded")
    elif (eligibility.metadata.get("deterministic_documentary_eligibility") is not False
          or eligibility.metadata.get("eligibility_reason") != "operational_context_required"):
        failures.append("expected_generation_required_documentary_path_not_used")
    deterministic = _first_event(snapshot, "deterministic_documentary_response")
    if deterministic is None or deterministic.metadata.get("deterministic_documentary_response_used") is not False:
        failures.append("deterministic_documentary_response_unexpected")
    if citation["status"] != "passed":
        failures.append("citation_validation_not_passed")
    if any(bool(event.metadata.get("semantic_guard_applied"))
           for event in snapshot.events):
        failures.append("semantic_guard_applied")
    if not getattr(response, "documentary_sources", ()):
        failures.append("scoped_documentary_evidence_missing")
    return tuple(failures)


def _fingerprint_tuple(calls: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    return tuple(str(call[key]) for call in calls)


def _safe_failure_message(error: BaseException) -> str:
    """Keep useful structural errors while avoiding arbitrary runtime payloads."""
    message = str(error)
    if isinstance(error, AttributeError):
        match = re.fullmatch(
            r"'([A-Za-z_][A-Za-z0-9_.]*)' object has no attribute '([A-Za-z_][A-Za-z0-9_]*)'",
            message,
        )
        if match:
            return f"{match.group(1)} object has no attribute {match.group(2)}"
    # Other exception messages can include prompts, source text, URLs or input
    # values. Their type and safe stack frames are retained instead.
    return "message omitted by benchmark safety filter"


def _safe_traceback_frames(error: BaseException) -> tuple[str, ...]:
    """Return traceback locations only: no source lines, locals, or exception text."""
    return tuple(
        f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)
    )


def _run_once(
    *, index: int, classification: str, provider_name: str, model_name: str,
    timeout_seconds: int, provider_factory: Callable[[PerformanceRecorder], Any],
    knowledge_factory: Callable[[PerformanceRecorder], Any],
    portfolio_factory: Callable[[], tuple[Any, Any]],
) -> BenchmarkRun:
    operation_id = uuid4().hex[:12]
    recorder = PerformanceRecorder(operation_id, provider_name, model_name, "conversational_workspace")
    response = None
    delegate = None
    observer: _GenerationObserver | None = None
    phase = "portfolio_setup"
    failure_type = failure_message = failure_phase = failure_pipeline_stage = None
    failure_traceback: tuple[str, ...] = ()
    try:
        portfolio, attention = portfolio_factory()
        phase = "provider_setup"
        delegate = provider_factory(recorder)
        observer = _GenerationObserver(delegate)
        decision_model = ProviderSupplyDecisionModel(observer)
        context = SupplyAgentSessionContext((CASE_ITEM_ID,), CASE_ITEM_ID)
        phase = "workspace_construction"
        workspace = ConversationalWorkspaceOrchestrator(
            portfolio=portfolio,
            attention=attention,
            knowledge=lambda: knowledge_factory(recorder),
            decision_model=decision_model,
            session_context=context,
            recorder=recorder,
        )
        phase = "workspace_execution"
        response = workspace.run(CASE_QUESTION, timeout_seconds)
    except Exception as error:
        failure_type = type(error).__name__
        failure_message = _safe_failure_message(error)
        failure_phase = phase
        failure_traceback = _safe_traceback_frames(error)
        active_stages = [
            event.stage for event in recorder.snapshot().events
            if event.status == PerformanceStatus.RUNNING
        ]
        failure_pipeline_stage = active_stages[-1] if active_stages else None
        recorder.record_stage(
            "benchmark_setup_or_execution_failure",
            error_type=failure_type,
            safe_message=failure_message,
            failing_phase=failure_phase,
            failing_pipeline_stage=failure_pipeline_stage,
            traceback_frames=failure_traceback,
        )
    recorder.finish(
        getattr(getattr(response, "status", None), "value", "failed")
        if response is not None else "failed"
    )
    snapshot = recorder.snapshot()
    operation_metrics = build_operation_metrics(snapshot)
    calls = operation_metrics.llm_calls
    citation = _citation_fields(snapshot)
    functional_failures = _functional_checks(response, snapshot, len(calls), citation)
    evidence_records = _source_records(response) if response is not None else []
    prompt_fingerprints = _fingerprint_tuple(observer.calls, "messages_fingerprint") if observer else ()
    config_fingerprints = _fingerprint_tuple(observer.calls, "configuration_fingerprint") if observer else ()
    config_details = tuple(call["generation_config"] for call in observer.calls) if observer else ()
    retrieval_seconds = _metrics_fields(snapshot, calls)["retrieval_seconds"]
    chunk_ids = tuple(
        _safe_id(record["chunk_id"])
        for record in evidence_records if record.get("chunk_id") is not None
    )
    metrics = _metrics_fields(snapshot, calls)
    return BenchmarkRun(
        run_index=index,
        classification=classification,
        case_id=CASE_ID,
        operation_id=operation_id,
        provider=observer.provider_name if observer else provider_name,
        model=observer.model if observer else model_name,
        operation_status=(
            getattr(getattr(response, "status", None), "value", snapshot.status)
            if response is not None else snapshot.status
        ),
        operation_seconds=snapshot.elapsed_seconds,
        retrieval_seconds=retrieval_seconds,
        generation_call_count=len(calls),
        prompt_fingerprints=prompt_fingerprints,
        evidence_fingerprint=stable_fingerprint(evidence_records) if evidence_records else None,
        source_chunk_ids=chunk_ids,
        generation_configuration_fingerprints=config_fingerprints,
        effective_generation_configuration=config_details,
        prompt_characters=metrics["prompt_characters"],
        input_tokens=metrics["input_tokens"],
        output_tokens=metrics["output_tokens"],
        total_tokens=metrics["total_tokens"],
        request_setup_seconds=metrics["request_setup_seconds"],
        request_to_stream_open_seconds=metrics["request_to_stream_open_seconds"],
        time_to_first_token_seconds=metrics["time_to_first_token_seconds"],
        stream_initial_wait_seconds=metrics["stream_initial_wait_seconds"],
        stream_generation_seconds=metrics["stream_generation_seconds"],
        output_tokens_per_second=metrics["output_tokens_per_second"],
        total_llm_wall_seconds=metrics["total_llm_wall_seconds"],
        server_inference_seconds=None,
        citation_status=citation["status"],
        citation_count=citation["count"],
        unknown_citation_ids=citation["unknown_ids"],
        missing_citation=citation["missing"],
        malformed_citation=citation["malformed"],
        scope_mismatch=citation["scope_mismatch"],
        unsupported_claim=citation["unsupported_claim"],
        deterministic_eligibility_reason=(
            _first_event(snapshot, "deterministic_documentary_eligibility").metadata.get("eligibility_reason")
            if _first_event(snapshot, "deterministic_documentary_eligibility") else None
        ),
        deterministic_response_used=(
            _first_event(snapshot, "deterministic_documentary_response").metadata.get(
                "deterministic_documentary_response_used",
            ) if _first_event(snapshot, "deterministic_documentary_response") else None
        ),
        semantic_guard_applied=any(
            bool(event.metadata.get("semantic_guard_applied")) for event in snapshot.events
        ),
        functional_pass=not functional_failures,
        functional_failures=functional_failures,
        failure_type=failure_type,
        failure_message=failure_message,
        failure_phase=failure_phase,
        failure_pipeline_stage=failure_pipeline_stage,
        failure_traceback=failure_traceback,
        comparable=False,
        non_comparable_reasons=(),
        successful_performance_sample=False,
    )


def _mark_comparability(runs: list[BenchmarkRun]) -> list[BenchmarkRun]:
    reference: BenchmarkRun | None = None
    output: list[BenchmarkRun] = []
    for run in runs:
        reasons: list[str] = []
        if not run.generation_call_count:
            reasons.append("generation_not_performed")
        if run.deterministic_eligibility_reason != "operational_context_required":
            reasons.append("expected_generation_required_path_not_used")
        if run.generation_call_count and run.evidence_fingerprint is None:
            reasons.append("retrieved_evidence_fingerprint_missing")
        if run.generation_call_count and reference is None and not reasons:
            reference = run
        elif reference is not None:
            if run.prompt_fingerprints != reference.prompt_fingerprints:
                reasons.append("provider_messages_fingerprint_changed")
            if run.evidence_fingerprint != reference.evidence_fingerprint:
                reasons.append("retrieved_evidence_fingerprint_changed")
            if run.generation_configuration_fingerprints != reference.generation_configuration_fingerprints:
                reasons.append("generation_configuration_fingerprint_changed")
            if (run.provider, run.model) != (reference.provider, reference.model):
                reasons.append("provider_or_model_changed")
        comparable = not reasons and run.generation_call_count > 0
        successful_sample = (
            run.classification == "measured" and comparable and run.functional_pass
        )
        output.append(BenchmarkRun(
            **{
                **asdict(run),
                "comparable": comparable,
                "non_comparable_reasons": tuple(dict.fromkeys(reasons)),
                "successful_performance_sample": successful_sample,
            }
        ))
    return output


_AGGREGATE_FIELDS = {
    "time_to_first_token_seconds": "TTFT",
    "stream_generation_seconds": "stream_generation",
    "output_tokens_per_second": "output_tokens_per_second",
    "total_llm_wall_seconds": "total_llm_wall_time",
    "operation_seconds": "total_operation_time",
}


def _statistics(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    size = len(ordered)
    median = (
        ordered[size // 2] if size % 2
        else (ordered[size // 2 - 1] + ordered[size // 2]) / 2
    )
    return {
        "count": size,
        "min": ordered[0],
        "median": median,
        "mean": sum(ordered) / size,
        "max": ordered[-1],
    }


def build_report(
    *, provider: str, model: str, warmup_runs: int, requested_measured_runs: int,
    runs: list[BenchmarkRun],
) -> dict[str, Any]:
    included = [run for run in runs if run.successful_performance_sample]
    aggregates: dict[str, Any] = {}
    for field, label in _AGGREGATE_FIELDS.items():
        values = [float(value) for run in included
                  if (value := getattr(run, field)) is not None]
        aggregates[label] = _statistics(values) if values else {"count": 0}
    return {
        "benchmark": "indAI MA Workspace generation benchmark",
        "case_id": CASE_ID,
        "provider": provider,
        "model": model,
        "run_plan": {
            "first_runner_runs": 1,
            "warmup_runs": warmup_runs,
            "requested_measured_runs": requested_measured_runs,
        },
        "successful_comparable_measured_runs": len(included),
        "requested_measured_runs_satisfied": len(included) == requested_measured_runs,
        "aggregate_statistics": aggregates,
        "runs": [asdict(run) for run in runs],
    }


def run_benchmark(
    *, provider_name: str, model_name: str, warmup_runs: int = 1, runs: int = 3,
    provider_factory: Callable[[PerformanceRecorder], Any],
    knowledge_factory: Callable[[PerformanceRecorder], Any],
    portfolio_factory: Callable[[], tuple[Any, Any]] = evaluate_demo_supply_portfolio,
) -> dict[str, Any]:
    if warmup_runs < 0:
        raise ValueError("warmup_runs must be zero or greater")
    if runs < 1:
        raise ValueError("runs must be at least one")
    results = [
        _run_once(
            index=index,
            classification=classify_run(index, warmup_runs=warmup_runs),
            provider_name=provider_name,
            model_name=model_name,
            timeout_seconds=LLMRuntimeConfig.from_environment().timeout_seconds,
            provider_factory=provider_factory,
            knowledge_factory=knowledge_factory,
            portfolio_factory=portfolio_factory,
        )
        for index in range(1 + warmup_runs + runs)
    ]
    compared = _mark_comparability(results)
    return build_report(
        provider=provider_name, model=model_name, warmup_runs=warmup_runs,
        requested_measured_runs=runs, runs=compared,
    )


def run_smoke(
    *, provider_name: str, model_name: str,
    provider_factory: Callable[[PerformanceRecorder], Any],
    knowledge_factory: Callable[[PerformanceRecorder], Any],
    portfolio_factory: Callable[[], tuple[Any, Any]] = evaluate_demo_supply_portfolio,
) -> dict[str, Any]:
    """Run exactly one functional Workspace operation for a cheap real-path check."""
    run = _run_once(
        index=1,
        classification="smoke",
        provider_name=provider_name,
        model_name=model_name,
        timeout_seconds=LLMRuntimeConfig.from_environment().timeout_seconds,
        provider_factory=provider_factory,
        knowledge_factory=knowledge_factory,
        portfolio_factory=portfolio_factory,
    )
    compared = _mark_comparability([run])
    report = build_report(
        provider=provider_name, model=model_name, warmup_runs=0,
        requested_measured_runs=0, runs=compared,
    )
    smoke_run = compared[0]
    report["mode"] = "smoke"
    report["smoke_passed"] = bool(
        smoke_run.functional_pass
        and smoke_run.generation_call_count == 1
        and smoke_run.citation_status == "passed"
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark real Workspace generation on a fixed case.")
    parser.add_argument("--provider", choices=("lmstudio",), default="lmstudio")
    parser.add_argument("--model", required=True)
    parser.add_argument("--case", choices=(CASE_ID,), default=CASE_ID)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--smoke", action="store_true",
        help="Run exactly one real Workspace operation; do not collect measured samples.",
    )
    parser.add_argument("--report", default="artifacts/workspace_generation_benchmark.json")
    args = parser.parse_args(argv)
    if not args.model.strip():
        parser.error("--model must not be empty")
    if args.warmup_runs < 0:
        parser.error("--warmup-runs must be zero or greater")
    if args.runs < 1:
        parser.error("--runs must be at least one")
    return args


def _print_table(report: dict[str, Any]) -> None:
    print("run  class              status        gen  citation  TTFT(s)  gen(s)  LLM(s)  op(s)  comparable")
    for run in report["runs"]:
        def show(value):
            return "n/a" if value is None else f"{value:.2f}"
        print(
            f"{run['run_index']:<4} {run['classification']:<18} "
            f"{run['operation_status']:<12} {run['generation_call_count']:<4} "
            f"{run['citation_status']:<9} {show(run['time_to_first_token_seconds']):>7} "
            f"{show(run['stream_generation_seconds']):>7} "
            f"{show(run['total_llm_wall_seconds']):>7} "
            f"{show(run['operation_seconds']):>6} {str(run['comparable']).lower()}"
        )
        if run["non_comparable_reasons"]:
            print("     non_comparable: " + ", ".join(run["non_comparable_reasons"]))
        if run["functional_failures"]:
            print("     functional_failure: " + ", ".join(run["functional_failures"]))
        if run.get("failure_type"):
            print(
                "     benchmark_failure: "
                f"{run['failure_type']}: {run.get('failure_message')} "
                f"phase={run.get('failure_phase')} "
                f"pipeline_stage={run.get('failure_pipeline_stage') or 'unavailable'}"
            )
            for frame in run.get("failure_traceback", ()):
                print(f"       at {frame}")
    if report.get("mode") == "smoke":
        print(f"Single-operation smoke validation: {'PASS' if report['smoke_passed'] else 'FAIL'}")
    else:
        print(
            f"Valid comparable measured runs: "
            f"{report['successful_comparable_measured_runs']}/"
            f"{report['run_plan']['requested_measured_runs']}"
        )


def _real_run(args: argparse.Namespace) -> dict[str, Any]:
    from embeddings import (
        LMStudioEmbeddingProviderPool,
        get_embedding_model_name,
        get_lmstudio_embedding_api_key,
    )

    runtime = LLMRuntimeConfig.from_environment()
    provider_factory = lambda recorder: get_llm_provider(
        args.provider, args.model, recorder=recorder, runtime_config=runtime,
    )
    embedding_pool = LMStudioEmbeddingProviderPool()
    knowledge_factory = lambda recorder: lazy_demo_knowledge_service(
        embedding_pool,
        model=get_embedding_model_name(),
        base_url=os.getenv("LMSTUDIO_BASE_URL"),
        api_key=get_lmstudio_embedding_api_key(),
        recorder=recorder,
    )()
    if args.smoke:
        return run_smoke(
            provider_name=args.provider, model_name=args.model,
            provider_factory=provider_factory, knowledge_factory=knowledge_factory,
        )
    return run_benchmark(
        provider_name=args.provider, model_name=args.model,
        warmup_runs=args.warmup_runs, runs=args.runs,
        provider_factory=provider_factory, knowledge_factory=knowledge_factory,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = _real_run(args)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table(report)
    print(f"JSON report: {report_path}")
    if report.get("mode") == "smoke":
        if not report["smoke_passed"]:
            print("ONE-RUN SMOKE VALIDATION FAILED")
            return 1
        return 0
    if not report["requested_measured_runs_satisfied"]:
        print("INSUFFICIENT VALID COMPARABLE MEASURED SAMPLES")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
