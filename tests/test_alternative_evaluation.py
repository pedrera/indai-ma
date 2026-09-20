import unittest
from types import SimpleNamespace

from alternative_evaluation import compose_alternative_evaluations
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


if __name__ == "__main__":
    unittest.main()
