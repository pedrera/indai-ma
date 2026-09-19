import unittest
from types import SimpleNamespace

from business_recommendation import compose_business_recommendation


def specialist(name, result):
    return SimpleNamespace(agent_name=name, result=result)


def procurement(position="SHORT", amount=0.5, exposure=21000):
    executions = [{"name": "calculate_supply_position", "result": {
        "interpretation": position, "position_gwh": -amount if position == "SHORT" else amount,
        "short_position_gwh": amount if position == "SHORT" else 0,
    }}]
    if exposure is not None:
        executions.append({"name": "calculate_spot_exposure", "result": {"exposure_eur": exposure}})
    return SimpleNamespace(tool_executions=executions)


class BusinessRecommendationTests(unittest.TestCase):
    def test_hospital_composes_structured_results_without_recalculation(self):
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2,
            "contractual_excess_price_eur_mwh": 4,
        })])
        risk = SimpleNamespace(base_scenario=SimpleNamespace(
            interpretation="SHORT", short_position_gwh=0.5), stress_scenarios=[])
        result = SimpleNamespace(specialist_results=[
            specialist("CommercialAgent", commercial),
            specialist("ProcurementAgent", procurement()),
            specialist("RiskAgent", risk),
        ])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertIn("cubrir", recommendation.action.lower())
        self.assertIn("0.2", recommendation.contractual_implication)
        self.assertIn("0.5", recommendation.operational_implication)
        self.assertIn("21000", dict(recommendation.supporting_metrics)["Exposición spot"])
        self.assertTrue(any("diferentes" in item for item in recommendation.rationale))

    def test_balanced_and_long_do_not_recommend_short_coverage(self):
        for interpretation in ("BALANCED", "LONG"):
            result = SimpleNamespace(specialist_results=[
                specialist("ProcurementAgent", procurement(interpretation, 0, None))
            ])
            recommendation = compose_business_recommendation(result)
            self.assertNotIn("cubrir el SHORT", recommendation.action)

    def test_missing_results_are_incomplete_and_content_is_not_parsed(self):
        result = SimpleNamespace(specialist_results=[
            specialist("ProcurementAgent", SimpleNamespace(
                content="SHORT 999 GWh", tool_executions=[]))
        ])
        recommendation = compose_business_recommendation(result)
        self.assertFalse(recommendation.is_complete)
        self.assertTrue(recommendation.warnings)
        self.assertNotIn("999", " ".join(value for pair in recommendation.supporting_metrics for value in pair))


if __name__ == "__main__":
    unittest.main()
