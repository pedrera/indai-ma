import unittest

from decision_alternatives import compose_decision_alternatives
from decision_plan import DecisionPlan, DecisionStep


class DecisionAlternativesTests(unittest.TestCase):
    def test_canonical_steps_have_stable_alternatives(self):
        steps = (
            DecisionStep("operational-short", "operational", "SHORT", readiness="READY"),
            DecisionStep("contractual-take-or-pay", "contractual", "TOP", readiness="READY"),
            DecisionStep("contractual-monthly-excess", "contractual", "Exceso", readiness="READY"),
            DecisionStep("risk-price-stress-20", "risk", "PRICE", readiness="READY"),
        )
        result = compose_decision_alternatives(DecisionPlan(steps, True, readiness="READY"))
        self.assertEqual([len(step.alternatives) for step in result.steps], [3, 3, 2, 3])
        self.assertEqual([item.id for item in result.steps[0].alternatives], [
            "operational-short-full", "operational-short-partial", "operational-short-maintain"])
        self.assertTrue(all(item.source_step_id == "operational-short" for item in result.steps[0].alternatives))
        self.assertNotIn("future", " ".join(item.description for item in result.steps[3].alternatives).lower())

    def test_partial_is_allowed_blocked_is_empty_and_unknown_is_empty(self):
        steps = (
            DecisionStep("operational-short", "operational", "SHORT", readiness="PARTIALLY_READY",
                         missing_information=("spot_price_eur_mwh",)),
            DecisionStep("risk-price-stress-20", "risk", "PRICE", readiness="BLOCKED",
                         missing_information=("spot_price_eur_mwh",)),
            DecisionStep("unknown", "risk", "Unknown", readiness="READY"),
        )
        result = compose_decision_alternatives(DecisionPlan(steps, False, readiness="BLOCKED"))
        self.assertEqual(len(result.steps[0].alternatives), 3)
        self.assertEqual(result.steps[0].missing_information, ("spot_price_eur_mwh",))
        self.assertEqual(result.steps[1].alternatives, ())
        self.assertEqual(result.steps[2].alternatives, ())
        self.assertEqual(result.readiness, "BLOCKED")

    def test_legacy_and_none_are_compatible(self):
        step = DecisionStep("operational-short", "operational", "SHORT")
        result = compose_decision_alternatives(DecisionPlan((step,), True))
        self.assertEqual(len(result.steps[0].alternatives), 3)
        self.assertIsNone(compose_decision_alternatives(None))


if __name__ == "__main__":
    unittest.main()
