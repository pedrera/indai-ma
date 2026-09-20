import unittest
import math
from types import SimpleNamespace

from alternative_evaluation import AlternativeEvaluationInputs, compose_alternative_evaluations
from business_recommendation import compose_business_recommendation
from decision_plan import DecisionAlternative, DecisionPlan, DecisionStep


def _specialist(name, result):
    return SimpleNamespace(agent_name=name, result=result)


def _procurement(exposure=21000):
    executions = [
        {"name": "calculate_supply_position", "result": {
            "position_gwh": -0.5, "interpretation": "SHORT",
        }},
        {"name": "calculate_spot_exposure", "result": {
            "short_position_gwh": 0.5, "spot_price_eur_mwh": 42,
            "exposure_eur": exposure,
        }},
    ] if exposure is not None else [{"name": "calculate_supply_position", "result": {
        "position_gwh": -0.5, "interpretation": "SHORT",
    }}]
    return SimpleNamespace(tool_executions=executions)


class AlternativeEvaluationTests(unittest.TestCase):
    def test_short_alternatives_are_evaluated_without_assuming_partial_coverage(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement())])
        recommendation = SimpleNamespace(actions=(SimpleNamespace(
            id="operational-short", category="operational", action="Cubrir", rationale=None,
            supporting_metrics=()),), is_complete=True, warnings=())
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir", readiness="READY",
            alternatives=(DecisionAlternative("operational-short-full", "Full", "", "operational-short"),
                          DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),
                          DecisionAlternative("operational-short-maintain", "Maintain", "", "operational-short"))),),
            True, readiness="READY")
        enriched = compose_alternative_evaluations(result, plan)
        alternatives = enriched.steps[0].alternatives
        self.assertEqual([item.id for item in alternatives], [
            "operational-short-full", "operational-short-partial", "operational-short-maintain"])
        self.assertEqual(alternatives[0].evaluation.status, "EVALUATED")
        self.assertEqual([(o.metric, o.value) for o in alternatives[0].evaluation.outcomes], [
            ("covered_volume_gwh", 0.5), ("remaining_short_gwh", 0)])
        self.assertEqual(alternatives[1].evaluation.status, "NOT_EVALUATED")
        self.assertEqual(alternatives[1].evaluation.missing_inputs, ("coverage_volume_gwh",))
        self.assertEqual(alternatives[2].evaluation.status, "EVALUATED")
        self.assertEqual(dict((o.metric, o.value) for o in alternatives[2].evaluation.outcomes), {
            "covered_volume_gwh": 0, "remaining_short_gwh": 0.5, "spot_exposure_eur": 21000})
        self.assertEqual(alternatives[0].evaluation.alternative_id, alternatives[0].id)
        self.assertEqual(enriched.steps[0].readiness, "READY")

    def test_no_spot_exposure_keeps_maintain_physical_outcomes_only(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement(None))])
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir",
            alternatives=(DecisionAlternative("operational-short-maintain", "Maintain", "", "operational-short"),)),),
            True, readiness="READY")
        maintain = compose_alternative_evaluations(result, plan).steps[0].alternatives[0].evaluation
        self.assertEqual([o.metric for o in maintain.outcomes], ["covered_volume_gwh", "remaining_short_gwh"])
        self.assertNotIn("coverage_cost_eur", [o.metric for o in maintain.outcomes])

    def test_business_composition_enriches_only_short_alternatives(self):
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2, "contractual_min_gwh": 3.4,
            "contractual_max_gwh": 4.6, "forecast_demand_gwh": 4.8}, inputs={})])
        result = SimpleNamespace(specialist_results=[
            _specialist("CommercialAgent", commercial),
            _specialist("ProcurementAgent", _procurement()),
        ])
        recommendation = compose_business_recommendation(result)
        steps = recommendation.decision_plan.steps
        self.assertEqual([len(step.alternatives) for step in steps], [3, 2])
        self.assertEqual(sum(item.evaluation is not None for step in steps for item in step.alternatives), 3)
        self.assertEqual([item.evaluation.status for item in steps[0].alternatives], [
            "EVALUATED", "NOT_EVALUATED", "EVALUATED"])
        self.assertTrue(all(item.evaluation is None for step in steps[1:] for item in step.alternatives))

    def test_partial_uses_only_explicit_validated_coverage_input(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement())])
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir", alternatives=(
            DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),)),), True)
        evaluation = compose_alternative_evaluations(
            result, plan, AlternativeEvaluationInputs(coverage_volume_gwh=0.3)
        ).steps[0].alternatives[0].evaluation
        self.assertEqual(evaluation.status, "EVALUATED")
        self.assertEqual([(o.metric, o.value, o.origin) for o in evaluation.outcomes], [
            ("covered_volume_gwh", 0.3, "scenario_input"),
            ("remaining_short_gwh", 0.2, "derived"),
        ])
        self.assertEqual(evaluation.missing_inputs, ())
        self.assertNotIn("coverage_cost_eur", [o.metric for o in evaluation.outcomes])

    def test_partial_coverage_boundaries_and_invalid_values(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement())])
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir", alternatives=(
            DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),)),), True)
        for coverage, remaining in ((0, 0.5), (0.5, 0)):
            evaluation = compose_alternative_evaluations(result, plan, AlternativeEvaluationInputs(coverage)).steps[0].alternatives[0].evaluation
            self.assertEqual([o.value for o in evaluation.outcomes], [coverage, remaining])
        for invalid in (-0.1, 0.7, float("nan"), float("inf")):
            with self.subTest(invalid=invalid):
                if invalid < 0 or not math.isfinite(invalid):
                    with self.assertRaises(ValueError):
                        AlternativeEvaluationInputs(invalid)
                else:
                    with self.assertRaises(ValueError):
                        compose_alternative_evaluations(result, plan, AlternativeEvaluationInputs(invalid))

    def test_explicit_coverage_price_is_separate_from_spot(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement())])
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir", alternatives=(
            DecisionAlternative("operational-short-full", "Full", "", "operational-short"),
            DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),
            DecisionAlternative("operational-short-maintain", "Maintain", "", "operational-short"),)),), True)
        enriched = compose_alternative_evaluations(
            result, plan, AlternativeEvaluationInputs(0.3, 40)
        )
        full, partial, maintain = enriched.steps[0].alternatives
        self.assertEqual(dict((o.metric, o.value) for o in full.evaluation.outcomes)["coverage_cost_eur"], 20000)
        self.assertEqual(dict((o.metric, o.value) for o in partial.evaluation.outcomes)["coverage_cost_eur"], 12000)
        self.assertEqual(dict((o.metric, o.value) for o in partial.evaluation.outcomes)["covered_volume_gwh"], 0.3)
        self.assertNotIn("coverage_cost_eur", [o.metric for o in maintain.evaluation.outcomes])
        self.assertEqual(partial.evaluation.inputs, AlternativeEvaluationInputs(0.3, 40))
        self.assertNotIn("12600", str(partial.evaluation.outcomes))

    def test_coverage_price_validation_and_missing_price_are_explicit(self):
        for invalid in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                AlternativeEvaluationInputs(coverage_price_eur_mwh=invalid)
        self.assertEqual(AlternativeEvaluationInputs(coverage_price_eur_mwh=0).coverage_price_eur_mwh, 0)


if __name__ == "__main__":
    unittest.main()
