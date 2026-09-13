import logging
from dataclasses import dataclass, field, replace
from enum import Enum
from threading import Lock
from time import perf_counter, time
from typing import Any
from uuid import uuid4


LOGGER_NAME = "indai_ma.performance"


class PerformanceStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


PIPELINE_STAGES = (
    "prompt_build",
    "provider_start",
    "http_request",
    "model_inference",
    "tool_execution",
    "final_response",
)
GAS_PIPELINE_STAGES = (
    "input_parsing",
    "scenario_generation",
    "tool_execution",
    "provider_start",
    "http_request",
    "model_inference",
    "parse_validation",
    "final_response",
)
RAG_INDEX_PIPELINE_STAGES = (
    "document_parsing",
    "chunking",
    "embedding",
    "index_persistence",
)
RAG_CHAT_PIPELINE_STAGES = (
    "query_embedding",
    "vector_search",
    "retrieved_context",
    "provider_start",
    "http_request",
    "model_inference",
    "tool_execution",
    "final_response",
)
GAS_RAG_PIPELINE_STAGES = (
    "query_embedding",
    "vector_search",
    "retrieved_context",
    *GAS_PIPELINE_STAGES,
)
GAS_DOCUMENTARY_PIPELINE_STAGES = (
    "query_embedding",
    "vector_search",
    "retrieved_context",
    "provider_start",
    "http_request",
    "model_inference",
    "final_response",
)
GAS_POSITION_PIPELINE_STAGES = (
    "query_embedding",
    "vector_search",
    "retrieved_context",
    "input_parsing",
    "tool_execution",
    "contractual_calculation",
    "provider_start",
    "http_request",
    "model_inference",
    "final_response",
)
PROCUREMENT_AGENT_PIPELINE_STAGES = (
    "agent_start",
    "agent_decision",
    "tool_execution",
    "agent_observation",
    "final_response_validation",
    "agent_final",
)
COMMERCIAL_AGENT_PIPELINE_STAGES = (
    "agent_start", "query_embedding", "vector_search", "retrieved_context",
    "agent_decision", "tool_execution", "llm_interpretation", "commercial_interpretation", "structured_result", "agent_final",
)
RISK_AGENT_PIPELINE_STAGES = (
    "agent_start", "input_parsing", "base_scenario", "stress_scenario",
    "tool_execution", "risk_delta", "risk_interpretation", "llm_call", "risk_result", "agent_final",
)
PROCUREMENT_PLANNER_PIPELINE_STAGES = (
    "agent_start",
    "llm_call",
    "agent_plan_created",
    "plan_validation",
    "tool_execution",
    "agent_observation",
    "final_response_validation",
    "agent_final",
)
PROCUREMENT_DETERMINISTIC_PIPELINE_STAGES = (
    "deterministic_start",
    "tool_execution",
    "llm_call",
    "final_response_validation",
    "deterministic_final",
)


@dataclass(frozen=True)
class PerformanceEvent:
    event_id: str
    operation_id: str
    stage: str
    status: PerformanceStatus
    started_at: float | None = None
    completed_at: float | None = None
    duration_seconds: float | None = None
    round: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceSnapshot:
    operation_id: str
    provider: str
    model: str
    mode: str
    status: str
    started_at: float
    elapsed_seconds: float
    events: tuple[PerformanceEvent, ...]


def get_performance_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger


class PerformanceRecorder:
    """Thread-safe source of truth for logs and pipeline UI events."""

    def __init__(
        self,
        operation_id: str,
        provider: str,
        model: str,
        mode: str,
    ) -> None:
        self.operation_id = operation_id
        self.provider = provider
        self.model = model
        self.mode = mode
        self._started_at = perf_counter()
        self._created_at = time()
        self._status = PerformanceStatus.RUNNING.value
        self._completed_at: float | None = None
        self._events: list[PerformanceEvent] = []
        self._lock = Lock()
        self._logger = get_performance_logger()

    def start_stage(
        self,
        stage: str,
        *,
        round: int | None = None,
        **metadata: Any,
    ) -> str:
        event = PerformanceEvent(
            event_id=uuid4().hex,
            operation_id=self.operation_id,
            stage=stage,
            status=PerformanceStatus.RUNNING,
            started_at=perf_counter(),
            round=round,
            metadata=dict(metadata),
        )
        with self._lock:
            self._events.append(event)
        self._log(event)
        return event.event_id

    def complete_stage(self, event_id: str, **metadata: Any) -> None:
        self._finish_stage(
            event_id, PerformanceStatus.COMPLETED, metadata
        )

    def start_llm_call(
        self,
        *,
        call_number: int,
        purpose: str,
        provider_round: int,
        message_count: int,
        prompt_character_count: int,
        tool_schema_character_count: int,
    ) -> str | None:
        try:
            return self.start_stage(
                "llm_call",
                round=call_number,
                call_number=call_number,
                purpose=purpose,
                provider_round=provider_round,
                message_count=message_count,
                prompt_character_count=prompt_character_count,
                tool_schema_character_count=tool_schema_character_count,
                timing_semantics="wall_clock",
            )
        except Exception:
            self._logger.exception("Unable to start LLM call trace")
            return None

    def complete_llm_call(
        self,
        event_id: str | None,
        *,
        request_setup_seconds: float,
        response_stream_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        server_inference_seconds: float | None = None,
        response_character_count: int | None = None,
    ) -> None:
        if event_id is None:
            return
        try:
            self.complete_stage(
                event_id,
                request_setup_seconds=request_setup_seconds,
                response_stream_seconds=response_stream_seconds,
                server_inference_seconds=server_inference_seconds,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                response_character_count=response_character_count,
                nested_metrics=(
                    "request_setup_seconds",
                    "response_stream_seconds",
                    "server_inference_seconds",
                ),
            )
        except Exception:
            self._logger.exception("Unable to complete LLM call trace")

    def fail_llm_call(
        self,
        event_id: str | None,
        *,
        request_setup_seconds: float | None = None,
        response_stream_seconds: float | None = None,
    ) -> None:
        if event_id is None:
            return
        try:
            self.fail_stage(
                event_id,
                request_setup_seconds=request_setup_seconds,
                response_stream_seconds=response_stream_seconds,
                server_inference_seconds=None,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                nested_metrics=(
                    "request_setup_seconds",
                    "response_stream_seconds",
                ),
            )
        except Exception:
            self._logger.exception("Unable to fail LLM call trace")

    def update_latest_stage(self, stage: str, **metadata: Any) -> None:
        """Best-effort trace enrichment; it must never affect execution."""
        try:
            with self._lock:
                for index in range(len(self._events) - 1, -1, -1):
                    event = self._events[index]
                    if event.stage == stage:
                        self._events[index] = replace(
                            event, metadata={**event.metadata, **metadata}
                        )
                        return
        except Exception:
            self._logger.exception("Unable to enrich trace stage=%s", stage)

    def fail_stage(self, event_id: str, **metadata: Any) -> None:
        self._finish_stage(event_id, PerformanceStatus.FAILED, metadata)

    def record_stage(
        self,
        stage: str,
        *,
        duration_seconds: float = 0.0,
        status: PerformanceStatus = PerformanceStatus.COMPLETED,
        round: int | None = None,
        **metadata: Any,
    ) -> str:
        now = perf_counter()
        event = PerformanceEvent(
            event_id=uuid4().hex,
            operation_id=self.operation_id,
            stage=stage,
            status=status,
            started_at=now - duration_seconds,
            completed_at=now,
            duration_seconds=duration_seconds,
            round=round,
            metadata=dict(metadata),
        )
        with self._lock:
            self._events.append(event)
        self._log(event)
        return event.event_id

    def finish(self, status: str) -> None:
        now = perf_counter()
        with self._lock:
            if self._completed_at is not None:
                return
            self._completed_at = now
            self._status = status
            failed = status != PerformanceStatus.COMPLETED.value
            updated: list[PerformanceEvent] = []
            for event in self._events:
                if event.status == PerformanceStatus.RUNNING:
                    updated.append(
                        replace(
                            event,
                            status=(
                                PerformanceStatus.FAILED
                                if failed
                                else PerformanceStatus.COMPLETED
                            ),
                            completed_at=now,
                            duration_seconds=now - (event.started_at or now),
                        )
                    )
                else:
                    updated.append(event)
            self._events = updated
        self._logger.info(
            "stage=operation_total operation_id=%s mode=%s provider=%s "
            "model=%s status=%s duration_seconds=%.4f",
            self.operation_id,
            self.mode,
            self.provider,
            self.model,
            status,
            now - self._started_at,
        )

    def snapshot(self) -> PerformanceSnapshot:
        now = perf_counter()
        with self._lock:
            events = tuple(
                PerformanceEvent(
                    event_id=event.event_id,
                    operation_id=event.operation_id,
                    stage=event.stage,
                    status=event.status,
                    started_at=event.started_at,
                    completed_at=event.completed_at,
                    duration_seconds=event.duration_seconds,
                    round=event.round,
                    metadata=dict(event.metadata),
                )
                for event in self._events
            )
            completed_at = self._completed_at
            status = self._status
        return PerformanceSnapshot(
            operation_id=self.operation_id,
            provider=self.provider,
            model=self.model,
            mode=self.mode,
            status=status,
            started_at=self._created_at,
            elapsed_seconds=(completed_at or now) - self._started_at,
            events=events,
        )

    def _finish_stage(
        self,
        event_id: str,
        status: PerformanceStatus,
        metadata: dict[str, Any],
    ) -> None:
        now = perf_counter()
        completed_event: PerformanceEvent | None = None
        with self._lock:
            for index, event in enumerate(self._events):
                if event.event_id != event_id:
                    continue
                completed_event = replace(
                    event,
                    status=status,
                    completed_at=now,
                    duration_seconds=now - (event.started_at or now),
                    metadata={**event.metadata, **metadata},
                )
                self._events[index] = completed_event
                break
        if completed_event is not None:
            self._log(completed_event)

    def _log(self, event: PerformanceEvent) -> None:
        values = {
            "stage": event.stage,
            "operation_id": self.operation_id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "status": event.status.value,
            **({"round": event.round} if event.round is not None else {}),
            **event.metadata,
        }
        if event.duration_seconds is not None:
            values["duration_seconds"] = f"{event.duration_seconds:.4f}"
        message = " ".join(f"{key}={value}" for key, value in values.items())
        self._logger.info(message)
