from dataclasses import dataclass
from typing import Any

from diagnostics import PerformanceEvent, PerformanceSnapshot


@dataclass(frozen=True)
class LLMCallMetrics:
    call_number: int
    purpose: str
    provider: str
    model: str
    request_wall_time: float
    request_setup_time: float | None
    response_stream_time: float | None
    inference_time: float | None
    overhead_time: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    status: str
    message_count: int | None
    prompt_character_count: int | None
    tool_schema_character_count: int | None
    response_character_count: int | None


@dataclass(frozen=True)
class OperationMetrics:
    total_operation_wall_time: float
    llm_calls: tuple[LLMCallMetrics, ...]
    llm_request_wall_time_total: float
    llm_inference_time_total: float | None
    llm_overhead_time_total: float | None
    tool_execution_time_total: float
    retrieval_time_total: float
    parsing_time_total: float
    prompt_character_count_total: int
    response_character_count_total: int

    @property
    def llm_call_count(self) -> int:
        return len(self.llm_calls)


@dataclass(frozen=True)
class ExecutionComparisonRecord:
    mode: str
    operation_id: str
    result_summary: str
    total_wall_time: float
    llm_call_count: int
    tool_call_count: int
    unique_tool_count: int
    tool_names: tuple[str, ...]
    llm_request_wall_time_total: float
    llm_inference_time: float | None
    tool_execution_time_total: float
    prompt_character_count_total: int
    response_character_count_total: int
    termination_reason: str | None
    status: str

    @property
    def result(self) -> str:
        return self.result_summary

    @property
    def llm_request_time(self) -> float:
        return self.llm_request_wall_time_total

    @property
    def tool_time(self) -> float:
        return self.tool_execution_time_total


def build_operation_metrics(snapshot: PerformanceSnapshot) -> OperationMetrics:
    calls = tuple(_llm_call(snapshot, event) for event in snapshot.events if event.stage == "llm_call")
    inference_values = [call.inference_time for call in calls]
    overhead_values = [call.overhead_time for call in calls]
    tool_events = [event for event in snapshot.events if event.stage == "tool_execution"]
    retrieval_stages = {"query_embedding", "vector_search", "retrieved_context"}
    parsing_stages = {"input_parsing", "parse_validation"}
    return OperationMetrics(
        total_operation_wall_time=snapshot.elapsed_seconds,
        llm_calls=calls,
        llm_request_wall_time_total=sum(call.request_wall_time for call in calls),
        llm_inference_time_total=(
            sum(value for value in inference_values if value is not None)
            if calls and all(value is not None for value in inference_values)
            else None
        ),
        llm_overhead_time_total=(
            sum(value for value in overhead_values if value is not None)
            if calls and all(value is not None for value in overhead_values)
            else None
        ),
        tool_execution_time_total=sum(
            float(event.metadata.get("tool_seconds", event.duration_seconds or 0) or 0)
            for event in tool_events
        ),
        retrieval_time_total=sum(
            float(event.duration_seconds or 0)
            for event in snapshot.events
            if event.stage in retrieval_stages
        ),
        parsing_time_total=sum(
            float(event.duration_seconds or 0)
            for event in snapshot.events
            if event.stage in parsing_stages
        ),
        prompt_character_count_total=sum(
            call.prompt_character_count or 0 for call in calls
        ),
        response_character_count_total=sum(
            call.response_character_count or 0 for call in calls
        ),
    )


def build_execution_comparison_record(
    snapshot: PerformanceSnapshot,
    result: str,
) -> ExecutionComparisonRecord:
    metrics = build_operation_metrics(snapshot)
    tool_events = [
        event for event in snapshot.events
        if event.stage == "tool_execution" and event.metadata.get("tool_call_count")
    ]
    tool_names = tuple(
        str(event.metadata.get("tool_name", "tool")) for event in tool_events
    )
    final_event = next(
        (
            event for event in reversed(snapshot.events)
            if event.stage in {"agent_final", "deterministic_final"}
        ),
        None,
    )
    mode = {
        "procurement_agent": "react_agent",
        "procurement_planner": "planner_agent",
        "procurement_deterministic": "deterministic",
        "gas_analysis": "deterministic",
    }.get(snapshot.mode, snapshot.mode)
    return ExecutionComparisonRecord(
        mode=mode,
        operation_id=snapshot.operation_id,
        result_summary=result,
        total_wall_time=metrics.total_operation_wall_time,
        llm_call_count=metrics.llm_call_count,
        tool_call_count=len(tool_events),
        unique_tool_count=len(set(tool_names)),
        tool_names=tool_names,
        llm_request_wall_time_total=metrics.llm_request_wall_time_total,
        llm_inference_time=metrics.llm_inference_time_total,
        tool_execution_time_total=metrics.tool_execution_time_total,
        prompt_character_count_total=metrics.prompt_character_count_total,
        response_character_count_total=metrics.response_character_count_total,
        termination_reason=(
            str(final_event.metadata.get("termination_reason"))
            if final_event and final_event.metadata.get("termination_reason")
            else None
        ),
        status=snapshot.status,
    )


def _llm_call(snapshot: PerformanceSnapshot, event: PerformanceEvent) -> LLMCallMetrics:
    metadata: dict[str, Any] = event.metadata
    wall = float(event.duration_seconds or 0)
    inference = _optional_float(metadata.get("server_inference_seconds"))
    overhead = max(0.0, wall - inference) if inference is not None else None
    return LLMCallMetrics(
        call_number=int(metadata.get("call_number", event.round or 0)),
        purpose=str(metadata.get("purpose", "generation")),
        provider=snapshot.provider,
        model=snapshot.model,
        request_wall_time=wall,
        request_setup_time=_optional_float(metadata.get("request_setup_seconds")),
        response_stream_time=_optional_float(metadata.get("response_stream_seconds")),
        inference_time=inference,
        overhead_time=overhead,
        input_tokens=_optional_int(metadata.get("input_tokens")),
        output_tokens=_optional_int(metadata.get("output_tokens")),
        total_tokens=_optional_int(metadata.get("total_tokens")),
        status=event.status.value,
        message_count=_optional_int(metadata.get("message_count")),
        prompt_character_count=_optional_int(metadata.get("prompt_character_count")),
        tool_schema_character_count=_optional_int(metadata.get("tool_schema_character_count")),
        response_character_count=_optional_int(metadata.get("response_character_count")),
    )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None
