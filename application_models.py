"""Transport-independent application contracts for the main analysis use case."""
from dataclasses import dataclass
from typing import Any

from business_output import ExecutiveResultProjection
from diagnostics import PerformanceSnapshot
from runtime_config import LLMRuntimeConfig
from supervisor_models import SupervisorResult
from alternative_evaluation import AlternativeEvaluationInputs


@dataclass(frozen=True)
class AnalysisRequest:
    text: str
    runtime: LLMRuntimeConfig
    use_llm_synthesis: bool = False
    operation_id: str | None = None
    alternative_evaluation: AlternativeEvaluationInputs | None = None


@dataclass(frozen=True)
class AnalysisResult:
    supervisor_result: SupervisorResult
    executive: ExecutiveResultProjection
    snapshot: PerformanceSnapshot | None
    operation_id: str

    @property
    def status(self):
        return self.supervisor_result.status

    @property
    def content(self) -> str:
        return self.supervisor_result.content

    @property
    def tool_executions(self) -> list[dict[str, Any]]:
        return self.supervisor_result.tool_executions

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        # AgentJob's existing adapter stores the SupervisorResult in session
        # state. Keep that boundary compatible while the service exposes the
        # richer AnalysisResult to non-UI callers.
        return self.supervisor_result.model_dump(mode=mode)
