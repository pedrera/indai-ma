import unittest

from business_output import build_supervisor_executive_sections
from business_recommendation import BusinessRecommendation
from supervisor_models import SupervisorResult, SupervisorRoutingDecision, SupervisorStatus


class BusinessRecommendationProjectionTests(unittest.TestCase):
    def make_result(self, recommendation=None):
        return SupervisorResult(
            status=SupervisorStatus.COMPLETED,
            routing=SupervisorRoutingDecision(selected_agents=[]),
            recommendation=recommendation,
        )

    def test_projection_preserves_every_recommendation_field(self):
        recommendation = BusinessRecommendation(
            action="Cubrir el SHORT operativo.", is_complete=True,
            rationale=("Existe exceso contractual.", "Son magnitudes distintas."),
            contractual_implication="Exceso contractual: 0.2 GWh.",
            operational_implication="SHORT: 0.5 GWh; exposición: 21000 EUR.",
            risk_implication="Riesgo base SHORT.",
            supporting_metrics=(("Exceso contractual", "0.2 GWh"), ("SHORT", "0.5 GWh"),
                                ("Exposición spot", "21000 EUR")),
            warnings=(),
        )
        projection = build_supervisor_executive_sections(self.make_result(recommendation))
        self.assertIs(projection.recommendation, recommendation)
        self.assertEqual(projection.recommendation, recommendation)
        self.assertEqual(projection.recommendation.supporting_metrics[0][1], "0.2 GWh")
        self.assertEqual(projection.recommendation.supporting_metrics[1][1], "0.5 GWh")
        self.assertEqual(projection.recommendation.supporting_metrics[2][1], "21000 EUR")

    def test_missing_recommendation_keeps_previous_projection_shape(self):
        projection = build_supervisor_executive_sections(self.make_result())
        self.assertIsNone(projection.recommendation)
        self.assertEqual(projection.summary, "Se han completado los análisis disponibles.")

    def test_incomplete_recommendation_preserves_status_and_warnings(self):
        recommendation = BusinessRecommendation(
            action="Obtener demanda.", is_complete=False,
            warnings=("Falta demanda.",),
        )
        projected = build_supervisor_executive_sections(self.make_result(recommendation)).recommendation
        self.assertFalse(projected.is_complete)
        self.assertEqual(projected.warnings, ("Falta demanda.",))


if __name__ == "__main__":
    unittest.main()
