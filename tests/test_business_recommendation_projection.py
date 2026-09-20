import unittest
from types import SimpleNamespace

from business_output import build_supervisor_executive_sections
from business_recommendation import BusinessRecommendation
from supervisor_models import SupervisorResult, SupervisorRoutingDecision, SupervisorStatus
from supervisor_models import SpecialistExecutionResult
from risk_models import RiskAgentResult, RiskScenarioResult, RiskStatus


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

    def test_risk_explanation_describes_structured_scenario_types(self):
        base = RiskScenarioResult(name="Base", demand_gwh=4.8, supply_gwh=4.3,
                                  position_gwh=-.5, interpretation="SHORT",
                                  short_position_gwh=.5, short_position_mwh=500,
                                  spot_price_eur_mwh=42, spot_exposure_eur=21000)
        variants = {
            "BASE": ([], "Análisis de la posición y exposición base."),
            "DEMAND": ([RiskScenarioResult(name="Demanda +10 %", scenario_type="DEMAND", stress_percent=10,
                                            demand_gwh=5.28, supply_gwh=4.3, position_gwh=-.98,
                                            interpretation="SHORT", short_position_gwh=.98, short_position_mwh=980,
                                            spot_price_eur_mwh=42, spot_exposure_eur=41160)],
                       "Análisis de sensibilidad de demanda, manteniendo constante el precio spot."),
            "PRICE": ([RiskScenarioResult(name="Precio spot +20 %", scenario_type="PRICE", stress_percent=20,
                                           demand_gwh=4.8, supply_gwh=4.3, position_gwh=-.5,
                                           interpretation="SHORT", short_position_gwh=.5, short_position_mwh=500,
                                           spot_price_eur_mwh=50.4, spot_exposure_eur=25200)],
                      "Análisis de sensibilidad al precio spot, manteniendo constantes demanda y suministro."),
        }
        variants["DEMAND+PRICE"] = (variants["DEMAND"][0] + variants["PRICE"][0],
                                     "Análisis de sensibilidad mediante escenarios independientes de demanda y precio spot.")
        for name, (scenarios, expected) in variants.items():
            with self.subTest(name=name):
                risk = RiskAgentResult(status=RiskStatus.COMPLETED, base_scenario=base,
                                       stress_scenarios=scenarios, summary="legacy summary")
                result = self.make_result()
                result = result.model_copy(update={"specialist_results": [
                    SpecialistExecutionResult(agent_name="RiskAgent", status="completed", result=risk)
                ]})
                explanations = dict(build_supervisor_executive_sections(result).explanations)
                self.assertEqual(explanations["Riesgo"], expected)

    def test_integrated_separation_only_mentions_short_when_position_is_short(self):
        commercial = SimpleNamespace(comparison=None, calculations=[SimpleNamespace(
            result={"forecast_demand_gwh": 3.6, "contractual_min_gwh": 3.4,
                    "contractual_max_gwh": 4.6, "contractual_excess_gwh": 0},
            inputs={})], contract_facts=[], summary="Contrato", warnings=[])

        def projected(interpretation, position):
            procurement = SimpleNamespace(result=SimpleNamespace(
                tool_executions=[{"name": "calculate_supply_position", "result": {
                    "interpretation": interpretation, "position_gwh": position,
                    "short_position_gwh": max(-position, 0)}}],
                content="Aprovisionamiento", warnings=[]), agent_name="ProcurementAgent")
            commercial_item = SimpleNamespace(result=commercial, agent_name="CommercialAgent")
            return build_supervisor_executive_sections(SimpleNamespace(
                warnings=[], specialist_results=[commercial_item, procurement], recommendation=None))

        short = projected("SHORT", -0.5)
        self.assertTrue(any("SHORT" in text for _, text in short.explanations))
        self.assertIn("SHORT", short.summary)

        for interpretation, position in (("LONG", 0.7), ("BALANCED", 0.0)):
            with self.subTest(interpretation=interpretation):
                result = projected(interpretation, position)
                self.assertNotIn("SHORT", result.summary)
                self.assertFalse(any("SHORT" in text for _, text in result.explanations))


if __name__ == "__main__":
    unittest.main()
