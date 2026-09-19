"""Application boundary for analysis, independent of Streamlit and HTTP."""
from collections.abc import Callable
from uuid import uuid4

from business_output import build_supervisor_executive_sections
from diagnostics import PerformanceRecorder
from application_models import AnalysisRequest, AnalysisResult
from supervisor import Supervisor


class AnalysisServiceError(RuntimeError):
    """A failure while composing or starting an analysis operation."""


class AnalysisService:
    def __init__(self, *, provider_factory=None, rag_factory=None,
                 recorder_factory: Callable[[str], PerformanceRecorder] | None = None,
                 supervisor_factory=Supervisor):
        self._provider_factory = provider_factory
        self._rag_factory = rag_factory
        self._recorder_factory = recorder_factory or self._default_recorder
        self._supervisor_factory = supervisor_factory

    @staticmethod
    def _default_recorder(operation_id: str) -> PerformanceRecorder:
        return PerformanceRecorder(operation_id, "deterministic", "none", "supervisor")

    def analyze(self, request: AnalysisRequest) -> AnalysisResult:
        if not request.text or not request.text.strip():
            raise AnalysisServiceError("La consulta de análisis no puede estar vacía.")
        operation_id = request.operation_id or uuid4().hex[:8]
        recorder = self._recorder_factory(operation_id)

        def provider_factory(child_recorder):
            if self._provider_factory is None:
                return None
            return self._provider_factory(child_recorder, request.runtime)

        def rag_factory(child_recorder):
            if self._rag_factory is None:
                raise ValueError("No hay una fábrica RAG configurada para este análisis.")
            return self._rag_factory(child_recorder, request.runtime)

        try:
            supervisor = self._supervisor_factory(
                recorder=recorder,
                provider_factory=provider_factory if self._provider_factory else None,
                rag_factory=rag_factory if self._rag_factory else None,
                use_llm_synthesis=request.use_llm_synthesis,
            )
            supervisor_result = supervisor.run(request.text, request.runtime.timeout_seconds)
            executive = build_supervisor_executive_sections(supervisor_result)
            recorder.finish(supervisor_result.status.value)
            return AnalysisResult(supervisor_result, executive, recorder.snapshot(), operation_id)
        except Exception as error:
            recorder.finish("failed")
            if isinstance(error, AnalysisServiceError):
                raise
            raise AnalysisServiceError("No se pudo iniciar el análisis.") from error
