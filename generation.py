from dataclasses import dataclass, field, replace
from enum import Enum
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic, perf_counter
from typing import Any

from diagnostics import PerformanceRecorder, get_performance_logger
from llm_client import (
    GenerationCancelledError,
    GenerationOptions,
    LLMProvider,
    LLMTimeoutError,
)
from runtime_config import LLMRuntimeConfig


logger = get_performance_logger()


class GenerationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True)
class GenerationResult:
    status: GenerationStatus
    content: str | None = None
    error: str | None = None
    elapsed_seconds: float | None = None
    provider_name: str | None = None
    model_name: str | None = None
    tool_executions: list[dict] = field(default_factory=list)
    execution_status: str | None = None
    structured_result: dict | None = None


class GenerationJob:
    def __init__(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        timeout_seconds: float,
        recorder: PerformanceRecorder | None = None,
        options: GenerationOptions | None = None,
    ) -> None:
        self.provider = provider
        self.messages = [dict(message) for message in messages]
        self.timeout_seconds = timeout_seconds
        self.recorder = recorder
        self.options = options
        self.started_at = monotonic()
        self._cancel_reason: GenerationStatus | None = None
        self._lock = Lock()
        self._result_queue: Queue[GenerationResult] = Queue(maxsize=1)
        self._result: GenerationResult | None = None
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> GenerationResult:
        if self._result is not None:
            return self._result

        if monotonic() - self.started_at >= self.timeout_seconds:
            self.cancel(GenerationStatus.TIMED_OUT)

        try:
            self._result = self._result_queue.get_nowait()
        except Empty:
            return GenerationResult(status=GenerationStatus.RUNNING)

        return self._result

    def cancel(
        self,
        reason: GenerationStatus = GenerationStatus.CANCELLED,
    ) -> None:
        with self._lock:
            if self._cancel_reason is not None or self._result is not None:
                return
            self._cancel_reason = reason

        self.provider.cancel()

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_reason is not None

    @property
    def cancel_reason(self) -> GenerationStatus | None:
        return self._get_cancel_reason()

    def _run(self) -> None:
        run_started_at = monotonic()
        provider_started_at = perf_counter()
        try:
            print(
                "[LLM] Iniciando llamada "
                f"provider={self.provider.provider_name} "
                f"model={self.provider.model}",
                flush=True,
            )
            response = self.provider.generate_response(
                self.messages,
                timeout_seconds=self.timeout_seconds,
                options=self.options,
            )
            if (
                self.recorder is not None
                and self.recorder.mode != "gas_analysis"
            ):
                final_event = self.recorder.start_stage(
                    "final_response",
                    response_chars=len(response.content),
                )
                self.recorder.complete_stage(final_event)
            print(
                "[LLM] Respuesta completa recibida "
                f"elapsed_seconds={perf_counter() - provider_started_at:.3f}",
                flush=True,
            )
            cancel_reason = self._get_cancel_reason()
            if cancel_reason is not None:
                result = GenerationResult(status=cancel_reason)
            else:
                result = GenerationResult(
                    status=GenerationStatus.COMPLETED,
                    content=response.content,
                    tool_executions=response.tool_executions,
                )
        except GenerationCancelledError:
            result = GenerationResult(
                status=self._get_cancel_reason() or GenerationStatus.CANCELLED
            )
        except LLMTimeoutError as error:
            result = GenerationResult(
                status=GenerationStatus.TIMED_OUT,
                error=str(error),
            )
        except Exception as error:
            cancel_reason = self._get_cancel_reason()
            if cancel_reason is not None:
                result = GenerationResult(status=cancel_reason)
            else:
                result = GenerationResult(
                    status=GenerationStatus.FAILED,
                    error=str(error),
                )

        result = replace(
            result,
            elapsed_seconds=perf_counter() - provider_started_at,
            provider_name=self.provider.provider_name,
            model_name=self.provider.model,
        )

        logger.info(
            "stage=generation_job provider=%s model=%s status=%s "
            "duration_seconds=%.4f message_count=%d",
            self.provider.provider_name,
            self.provider.model,
            result.status.value,
            monotonic() - run_started_at,
            len(self.messages),
        )
        self._result_queue.put(result)

    def _get_cancel_reason(self) -> GenerationStatus | None:
        with self._lock:
            return self._cancel_reason


class AgentJob:
    """Runs an agent with the same polling and cancellation contract as generation."""

    def __init__(
        self,
        agent: Any,
        request: str,
        timeout_seconds: float,
        provider: LLMProvider,
    ) -> None:
        self.agent = agent
        self.request = request
        self.timeout_seconds = timeout_seconds
        self.provider = provider
        self.started_at = monotonic()
        self._cancel_reason: GenerationStatus | None = None
        self._lock = Lock()
        self._result_queue: Queue[GenerationResult] = Queue(maxsize=1)
        self._result: GenerationResult | None = None
        self._thread = Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def poll(self) -> GenerationResult:
        if self._result is not None:
            return self._result
        if monotonic() - self.started_at >= self.timeout_seconds:
            self.cancel(GenerationStatus.TIMED_OUT)
        try:
            self._result = self._result_queue.get_nowait()
        except Empty:
            return GenerationResult(status=GenerationStatus.RUNNING)
        return self._result

    def cancel(self, reason: GenerationStatus = GenerationStatus.CANCELLED) -> None:
        with self._lock:
            if self._cancel_reason is not None or self._result is not None:
                return
            self._cancel_reason = reason
        self.provider.cancel()

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_reason is not None

    @property
    def cancel_reason(self) -> GenerationStatus | None:
        with self._lock:
            return self._cancel_reason

    def _run(self) -> None:
        started_at = perf_counter()
        try:
            agent_result = self.agent.run(self.request, self.timeout_seconds)
            cancel_reason = self.cancel_reason
            if cancel_reason:
                result = GenerationResult(status=cancel_reason)
            else:
                result = GenerationResult(
                    status=GenerationStatus.COMPLETED,
                    content=agent_result.content,
                    tool_executions=list(agent_result.tool_executions),
                    execution_status=agent_result.status.value,
                    structured_result=(agent_result.model_dump(mode="json")
                                       if hasattr(agent_result, "model_dump") else None),
                )
        except GenerationCancelledError:
            result = GenerationResult(
                status=self.cancel_reason or GenerationStatus.CANCELLED
            )
        except LLMTimeoutError as error:
            result = GenerationResult(
                status=GenerationStatus.TIMED_OUT, error=str(error)
            )
        except Exception as error:
            result = GenerationResult(
                status=self.cancel_reason or GenerationStatus.FAILED,
                error=str(error),
            )
        self._result_queue.put(
            replace(
                result,
                elapsed_seconds=perf_counter() - started_at,
                provider_name=self.provider.provider_name,
                model_name=self.provider.model,
            )
        )


def get_generation_timeout_seconds() -> float:
    return float(LLMRuntimeConfig.from_environment().timeout_seconds)
