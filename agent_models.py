from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentPolicy:
    max_decisions: int = 5
    max_tool_calls: int = 3
    max_invalid_decisions: int = 2

    def __post_init__(self) -> None:
        if min(
            self.max_decisions,
            self.max_tool_calls,
            self.max_invalid_decisions,
        ) < 1:
            raise ValueError("Agent policy limits must be positive.")


@dataclass(frozen=True)
class AgentDecision:
    action: str
    decision_summary: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    missing_fields: tuple[str, ...] = ()
    question: str | None = None


@dataclass(frozen=True)
class AgentObservation:
    step_number: int
    kind: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    message: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    status: AgentStatus
    content: str
    tool_executions: tuple[dict[str, Any], ...]
    observations: tuple[AgentObservation, ...]
    decision_count: int
    termination_reason: str
