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
            "contractual_excess_price_eur_mwh": 46,
        }, inputs={"spot_price_eur_mwh": 42, "excess_surcharge_eur_mwh": 4})])
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
        self.assertIn("+4 EUR/MWh", recommendation.contractual_implication)
        self.assertIn("46 EUR/MWh", recommendation.contractual_implication)
        self.assertNotIn("Spot + 46", recommendation.contractual_implication)
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

    def test_price_stress_uses_structured_risk_values_without_recalculation(self):
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2, "contractual_excess_price_eur_mwh": 46,
        }, inputs={"excess_surcharge_eur_mwh": 4})])
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5,
                              interpretation="SHORT", spot_price_eur_mwh=42,
                              spot_exposure_eur=21000)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20,
                                demand_gwh=4.8, spot_price_eur_mwh=50.4,
                                spot_exposure_eur=25200)
        delta = SimpleNamespace(scenario_name="Precio spot +20 %", scenario_type="PRICE",
                                stress_percent=20, exposure_change_eur=4200)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[price], deltas=[delta], status="completed")
        result = SimpleNamespace(specialist_results=[specialist("CommercialAgent", commercial),
                                                     specialist("ProcurementAgent", procurement()),
                                                     specialist("RiskAgent", risk)])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertEqual(recommendation.action, "Cubrir el SHORT operativo.")
        for value in ("20", "42", "50.4", "21,000", "25,200", "+4,200"):
            self.assertIn(value, recommendation.risk_implication)
        self.assertIn("0.2", recommendation.contractual_implication)
        self.assertIn("+4 EUR/MWh", recommendation.contractual_implication)
        self.assertIn("46 EUR/MWh", recommendation.contractual_implication)

    def test_demand_and_price_stress_remain_separate(self):
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5, interpretation="SHORT",
                               spot_price_eur_mwh=42, spot_exposure_eur=21000)
        demand = SimpleNamespace(scenario_type="DEMAND", stress_percent=10, demand_gwh=5.28)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20, demand_gwh=4.8,
                                spot_price_eur_mwh=50.4, spot_exposure_eur=25200)
        d1 = SimpleNamespace(scenario_name="Demanda +10 %", exposure_change_eur=20160)
        d2 = SimpleNamespace(scenario_name="Precio spot +20 %", exposure_change_eur=4200)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[demand, price], deltas=[d1, d2], status="completed")
        result = SimpleNamespace(specialist_results=[specialist("RiskAgent", risk)])
        recommendation = compose_business_recommendation(result)
        self.assertIn("demanda +10%", recommendation.risk_implication)
        self.assertIn("spot del 20%", recommendation.risk_implication)

    def test_price_stress_without_exposure_is_incomplete(self):
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5, interpretation="SHORT",
                               spot_price_eur_mwh=None, spot_exposure_eur=None)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20, demand_gwh=4.8,
                                spot_price_eur_mwh=None, spot_exposure_eur=None)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[price], deltas=[], status="partial")
        recommendation = compose_business_recommendation(SimpleNamespace(
            specialist_results=[specialist("RiskAgent", risk)]))
        self.assertFalse(recommendation.is_complete)
        self.assertTrue(recommendation.warnings)


if __name__ == "__main__":
    unittest.main()
