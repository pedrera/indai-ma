import unittest
from types import SimpleNamespace

from business_recommendation import BusinessAction, BusinessRecommendation
from decision_plan import DecisionPlan, DecisionStep
from decision_readiness import compose_decision_readiness


def execution(name, result):
    return {"name": name, "result": result}


class DecisionReadinessTests(unittest.TestCase):
    def plan(self, *steps):
        return DecisionPlan(tuple(steps), True)

    def supervisor(self, procurement=None, commercial=None, risk=None):
        items = []
        for name, result in (("ProcurementAgent", procurement), ("CommercialAgent", commercial), ("RiskAgent", risk)):
            if result is not None:
                items.append(SimpleNamespace(agent_name=name, result=result))
        return SimpleNamespace(specialist_results=items)

    def test_canonical_plan_is_ready_without_recalculation(self):
        procurement = SimpleNamespace(missing_inputs=(), tool_executions=[
            execution("calculate_supply_position", {"interpretation": "SHORT", "position_gwh": -0.5}),
            execution("calculate_spot_exposure", {"exposure_eur": 21000}),
        ])
        commercial = SimpleNamespace(missing_inputs=(), take_or_pay_projection=object(), calculations=[
            SimpleNamespace(result={"contractual_excess_gwh": 999})])
        risk = SimpleNamespace(missing_inputs=(), stress_scenarios=[SimpleNamespace(
            scenario_type="PRICE", spot_price_eur_mwh=50.4, spot_exposure_eur=25200)])
        steps = (
            DecisionStep("operational-short", "operational", "SHORT"),
            DecisionStep("contractual-take-or-pay", "contractual", "TOP"),
            DecisionStep("contractual-monthly-excess", "contractual", "Exceso"),
            DecisionStep("risk-price-stress-20", "risk", "PRICE"),
        )
        result = compose_decision_readiness(self.supervisor(procurement, commercial, risk), None, self.plan(*steps))
        self.assertEqual(result.readiness, "READY")
        self.assertEqual([step.readiness for step in result.steps], ["READY"] * 4)

    def test_short_without_spot_is_partial_and_does_not_change_position(self):
        procurement = SimpleNamespace(missing_inputs=("spot_price_eur_mwh",), tool_executions=[
            execution("calculate_supply_position", {"interpretation": "SHORT", "position_gwh": -0.5})])
        step = DecisionStep("operational-short", "operational", "Cubrir")
        result = compose_decision_readiness(self.supervisor(procurement=procurement), None, self.plan(step))
        self.assertEqual(result.readiness, "PARTIALLY_READY")
        self.assertEqual(result.steps[0].missing_information, ("spot_price_eur_mwh",))
        self.assertEqual(procurement.tool_executions[0]["result"]["position_gwh"], -0.5)

    def test_price_without_spot_is_blocked_only_for_existing_step(self):
        risk = SimpleNamespace(missing_inputs=("spot_price_eur_mwh",), stress_scenarios=[])
        step = DecisionStep("risk-price-stress-20", "risk", "PRICE")
        result = compose_decision_readiness(self.supervisor(risk=risk), None, self.plan(step))
        self.assertEqual(result.readiness, "BLOCKED")
        self.assertEqual(result.steps[0].missing_information, ("spot_price_eur_mwh",))

    def test_warning_text_does_not_drive_readiness_and_legacy_plan_is_unchanged(self):
        step = DecisionStep("operational-long", "operational", "LONG")
        result = compose_decision_readiness(SimpleNamespace(specialist_results=[]),
                                            BusinessRecommendation("x", False, warnings=("missing fake",)), DecisionPlan((step,), False))
        self.assertEqual(result.readiness, "READY")
        self.assertFalse(result.is_complete)
        self.assertIsNone(compose_decision_readiness(SimpleNamespace(specialist_results=[]), None, None))


if __name__ == "__main__":
    unittest.main()
