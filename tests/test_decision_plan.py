import unittest
from types import SimpleNamespace

from business_recommendation import BusinessAction, BusinessRecommendation
from decision_plan import compose_decision_plan


class DecisionPlanTests(unittest.TestCase):
    def canonical(self):
        actions = (
            BusinessAction("operational", "SHORT", supporting_metrics=(("SHORT", "0.5 GWh"),), id="operational-short"),
            BusinessAction("contractual", "TOP", supporting_metrics=(("Déficit", "2.8 GWh"),), id="contractual-take-or-pay"),
            BusinessAction("contractual", "Exceso", supporting_metrics=(("Exceso", "0.2 GWh"),), id="contractual-monthly-excess"),
            BusinessAction("risk", "PRICE", supporting_metrics=(("Delta", "+4,200 EUR"),), id="risk-price-stress-20"),
        )
        return BusinessRecommendation("SHORT", True, actions=actions)

    def test_canonical_plan_is_ordered_traceable_and_separate(self):
        plan = compose_decision_plan(self.canonical())
        self.assertEqual([step.id for step in plan.steps], [
            "operational-short", "contractual-take-or-pay",
            "contractual-monthly-excess", "risk-price-stress-20"])
        self.assertEqual(plan.steps[0].source_agent, "ProcurementAgent")
        self.assertEqual(plan.steps[1].source_agent, "CommercialAgent")
        self.assertEqual(plan.steps[3].source_agent, "RiskAgent")
        self.assertEqual(plan.steps[1].horizon, "before_period_close")
        self.assertEqual(plan.steps[3].decision_state, "monitor")
        self.assertEqual(plan.steps[3].depends_on, ("operational-short",))
        self.assertNotIn("3.3 GWh", str(plan))
        self.assertEqual(plan.steps[1].supporting_metrics, (("Déficit", "2.8 GWh"),))

    def test_price_without_short_has_no_artificial_dependency(self):
        recommendation = BusinessRecommendation("Revisar", True, actions=(
            BusinessAction("risk", "PRICE", id="risk-price-stress-20"),
        ))
        plan = compose_decision_plan(recommendation)
        self.assertEqual(plan.steps[0].depends_on, ())

    def test_long_and_balanced(self):
        long_plan = compose_decision_plan(BusinessRecommendation("LONG", True, actions=(
            BusinessAction("operational", "Revisar LONG", id="operational-long"),
        )))
        self.assertEqual(long_plan.steps[0].horizon, "current_period")
        self.assertIsNone(compose_decision_plan(BusinessRecommendation("BALANCED", True)))

    def test_legacy_business_action_construction_remains_valid(self):
        action = BusinessAction("operational", "Revisar")
        self.assertIsNone(action.id)
        self.assertIsNone(compose_decision_plan(BusinessRecommendation("Revisar", True, actions=(action,))))


if __name__ == "__main__":
    unittest.main()
