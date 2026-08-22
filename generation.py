import os
from dataclasses import dataclass
from enum import Enum
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic

from llm_client import GenerationCancelledError, LLMProvider, LLMTimeoutError


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


class GenerationJob:
    def __init__(
        self,
        provider: LLMProvider,
        messages: list[dict[str, str]],
        timeout_seconds: float,
    ) -> None:
        self.provider = provider
        self.messages = [dict(message) for message in messages]
        self.timeout_seconds = timeout_seconds
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
        try:
            content = self.provider.generate_response(
                self.messages,
                timeout_seconds=self.timeout_seconds,
            )
            cancel_reason = self._get_cancel_reason()
            if cancel_reason is not None:
                result = GenerationResult(status=cancel_reason)
            else:
                result = GenerationResult(
                    status=GenerationStatus.COMPLETED,
                    content=content,
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

        self._result_queue.put(result)

    def _get_cancel_reason(self) -> GenerationStatus | None:
        with self._lock:
            return self._cancel_reason


def get_generation_timeout_seconds() -> float:
    raw_value = os.getenv("LLM_TIMEOUT_SECONDS", "300").strip()
    try:
        timeout_seconds = float(raw_value)
    except ValueError as error:
        raise ValueError("LLM_TIMEOUT_SECONDS debe ser un número.") from error

    if timeout_seconds <= 0:
        raise ValueError("LLM_TIMEOUT_SECONDS debe ser mayor que cero.")

    return timeout_seconds
