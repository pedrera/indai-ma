import unittest

from application_models import AnalysisResult
from business_output import ExecutiveResultProjection, supervisor_text
from presentation_normalization import normalize_supervisor_result
from supervisor_models import SupervisorResult, SupervisorRoutingDecision, SupervisorStatus


def supervisor_result():
    return SupervisorResult(
        status=SupervisorStatus.NEEDS_INPUT,
        routing=SupervisorRoutingDecision(selected_agents=[]),
        summary="Faltan datos.",
    )


class PresentationNormalizationTests(unittest.TestCase):
    def test_analysis_result_becomes_supervisor_result(self):
        domain = supervisor_result()
        application = AnalysisResult(domain, ExecutiveResultProjection("Resumen"), None, "op-1")
        self.assertIs(normalize_supervisor_result(application), domain)

    def test_supervisor_result_is_returned_by_identity(self):
        domain = supervisor_result()
        self.assertIs(normalize_supervisor_result(domain), domain)

    def test_normalization_does_not_mutate_objects(self):
        domain = supervisor_result()
        application = AnalysisResult(domain, ExecutiveResultProjection("Resumen"), None, "op-1")
        before = domain.model_dump(mode="json")
        normalized = normalize_supervisor_result(application)
        self.assertEqual(normalized.model_dump(mode="json"), before)
        self.assertEqual(application.operation_id, "op-1")

    def test_regression_path_normalizes_before_supervisor_text(self):
        domain = supervisor_result()
        application = AnalysisResult(domain, ExecutiveResultProjection("Resumen"), None, "op-1")
        text = supervisor_text(normalize_supervisor_result(application))
        self.assertIn("Faltan datos.", text)


if __name__ == "__main__":
    unittest.main()
