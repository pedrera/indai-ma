import inspect
import unittest
from unittest.mock import Mock

from application_models import AnalysisRequest
from application_service import AnalysisService
from diagnostics import PerformanceRecorder
from rag_models import RetrievalResult
from runtime_config import LLMRuntimeConfig
from tests.test_commercial_agent import contract_matches
from tests.test_supervisor import CANONICAL_BUSINESS_QUERY


class ApplicationServiceTests(unittest.TestCase):
    def request(self, **overrides):
        values = dict(
            text=CANONICAL_BUSINESS_QUERY,
            runtime=LLMRuntimeConfig(384, 384, 30, False),
            operation_id="service-test",
        )
        values.update(overrides)
        return AnalysisRequest(**values)

    def service(self, rag=None, **kwargs):
        rag = rag or Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), "fixture")
        return AnalysisService(
            rag_factory=lambda recorder, runtime: rag,
            recorder_factory=lambda operation: PerformanceRecorder(operation, "deterministic", "none", "supervisor"),
            **kwargs,
        ), rag

    def test_service_is_importable_without_streamlit(self):
        import application_service
        self.assertNotIn("streamlit", inspect.getsource(application_service))

    def test_canonical_analysis_returns_domain_and_executive_result(self):
        service, rag = self.service()
        result = service.analyze(self.request())
        self.assertEqual(result.operation_id, "service-test")
        self.assertEqual(result.supervisor_result.status.value, "completed")
        self.assertIn("0.2 GWh", result.executive.summary)
        self.assertIn("SHORT de 0.5 GWh", result.executive.summary)
        self.assertIn("21,000 EUR", result.executive.summary)
        self.assertEqual(result.supervisor_result.total_llm_calls, 0)
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(rag.retrieve.call_count, 1)

    def test_service_preserves_controlled_partial_result(self):
        service, _ = self.service()
        result = service.analyze(self.request(text="Analiza el contrato del Hospital Costa Sur."))
        self.assertIn(result.supervisor_result.status.value, {"completed", "partial"})

    def test_service_does_not_require_streamlit_session_state(self):
        service, _ = self.service()
        result = service.analyze(self.request())
        self.assertFalse(hasattr(result, "session_state"))

    def test_injected_supervisor_factory_is_used(self):
        supervisor = Mock()
        supervisor.run.return_value = Mock(
            status=Mock(value="completed"),
            content="ok",
            tool_executions=[],
            model_dump=lambda mode="json": {},
            warnings=[],
            specialist_results=[],
        )
        service, _ = self.service(supervisor_factory=lambda **kwargs: supervisor)
        result = service.analyze(self.request())
        supervisor.run.assert_called_once()
        self.assertEqual(result.operation_id, "service-test")


if __name__ == "__main__":
    unittest.main()
