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
    def test_top_review_consumption_uses_revised_forecast_without_mutating_baseline(self):
        from decision_plan import compose_decision_plan
        from decision_alternatives import compose_decision_alternatives
        from take_or_pay import calculate_take_or_pay_projection
        baseline = calculate_take_or_pay_projection(40.8, 30, 8)
        result = SimpleNamespace(specialist_results=[_specialist(
            "CommercialAgent", SimpleNamespace(take_or_pay_projection=baseline)
        )])
        recommendation = SimpleNamespace(actions=(SimpleNamespace(
            id="contractual-take-or-pay", category="contractual", action="Revisar TOP",
            rationale=None, supporting_metrics=()),), is_complete=True, warnings=())
        plan = compose_decision_alternatives(compose_decision_plan(recommendation))
        evaluated = compose_alternative_evaluations(
            result, plan, AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=11)
        )
        scenario = evaluated.steps[0].alternatives[0].evaluation
        self.assertEqual(scenario.status, "EVALUATED")
        self.assertEqual(scenario.inputs.revised_remaining_forecast_consumption_gwh, 11)
        self.assertEqual([(item.metric, item.value, item.origin) for item in scenario.outcomes], [
            ("projected_consumption_gwh", 41.0, "derived"),
            ("projected_top_deficit_gwh", 0.0, "derived"),
        ])
        self.assertEqual((baseline.projected_annual_consumption_gwh,
                          baseline.projected_take_or_pay_deficit_gwh,
                          baseline.status), (38, 2.8, "BELOW_MINIMUM"))

    def test_top_maintain_projects_existing_take_or_pay_result(self):
        from decision_plan import compose_decision_plan
        from decision_alternatives import compose_decision_alternatives
        projection = SimpleNamespace(
            projected_annual_consumption_gwh=38,
            projected_take_or_pay_deficit_gwh=2.8,
            status="BELOW_MINIMUM",
        )
        result = SimpleNamespace(specialist_results=[_specialist(
            "CommercialAgent", SimpleNamespace(take_or_pay_projection=projection)
        )])
        recommendation = SimpleNamespace(actions=(SimpleNamespace(
            id="contractual-take-or-pay", category="contractual", action="Revisar TOP",
            rationale=None, supporting_metrics=()),), is_complete=True, warnings=())
        plan = compose_decision_alternatives(compose_decision_plan(recommendation))
        evaluated = compose_alternative_evaluations(result, plan)
        alternatives = evaluated.steps[0].alternatives
        maintain = alternatives[2]
        self.assertEqual(maintain.id, "top-maintain-forecast")
        self.assertEqual(maintain.evaluation.status, "EVALUATED")
        self.assertIsNone(maintain.evaluation.inputs)
        self.assertEqual([(outcome.metric, outcome.value, outcome.unit, outcome.origin)
                          for outcome in maintain.evaluation.outcomes], [
            ("projected_consumption_gwh", 38.0, "GWh", "structured_result"),
            ("projected_top_deficit_gwh", 2.8, "GWh", "structured_result"),
        ])
        self.assertEqual(alternatives[0].evaluation.status, "NOT_EVALUATED")
        self.assertEqual(alternatives[0].evaluation.missing_inputs,
                         ("revised_remaining_forecast_consumption_gwh",))
        self.assertIsNone(alternatives[1].evaluation)

    def test_top_alternatives_remain_unevaluated_without_projection(self):
        from decision_plan import compose_decision_plan
        from decision_alternatives import compose_decision_alternatives
        result = SimpleNamespace(specialist_results=[])
        recommendation = SimpleNamespace(actions=(SimpleNamespace(
            id="contractual-take-or-pay", category="contractual", action="Revisar TOP",
            rationale=None, supporting_metrics=()),), is_complete=True, warnings=())
        evaluated = compose_alternative_evaluations(result, compose_decision_alternatives(compose_decision_plan(recommendation)))
        self.assertTrue(all(item.evaluation is None for item in evaluated.steps[0].alternatives))

    def test_long_maintain_is_evaluated_from_structured_position_only(self):
        result = SimpleNamespace(specialist_results=[_specialist(
            "ProcurementAgent", SimpleNamespace(tool_executions=[{
                "name": "calculate_supply_position",
                "result": {"position_gwh": 0.7, "interpretation": "LONG"},
            }]))])
        recommendation = SimpleNamespace(actions=(SimpleNamespace(
            id="operational-long", category="operational", action="Revisar LONG",
            rationale=None, supporting_metrics=()),), is_complete=True, warnings=())
        plan = __import__("decision_plan").compose_decision_plan(recommendation)
        evaluated = compose_alternative_evaluations(result, plan)
        alternatives = evaluated.steps[0].alternatives
        self.assertEqual([item.id for item in alternatives], [
            "operational-long-maintain", "operational-long-reallocation", "operational-long-reduce-future"])
        maintain, reallocation, reduce_future = alternatives
        self.assertEqual(maintain.evaluation.status, "EVALUATED")
        self.assertIsNone(maintain.evaluation.inputs)
        self.assertEqual((maintain.evaluation.outcomes[0].metric,
                          maintain.evaluation.outcomes[0].value,
                          maintain.evaluation.outcomes[0].unit,
                          maintain.evaluation.outcomes[0].origin),
                         ("remaining_long_gwh", 0.7, "GWh", "structured_result"))
        self.assertIsNone(reallocation.evaluation)
        self.assertIsNone(reduce_future.evaluation)
        self.assertNotIn("EUR", str(maintain.evaluation))

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

    def test_evaluation_inputs_only_contain_relevant_inputs_per_alternative(self):
        result = SimpleNamespace(specialist_results=[_specialist("ProcurementAgent", _procurement())])
        plan = DecisionPlan((DecisionStep("operational-short", "operational", "Cubrir", alternatives=(
            DecisionAlternative("operational-short-full", "Full", "", "operational-short"),
            DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),
            DecisionAlternative("operational-short-maintain", "Maintain", "", "operational-short"),)),), True)
        for inputs, full_expected, partial_expected in [
            (AlternativeEvaluationInputs(0.3, 40), (None, 40), (0.3, 40)),
            (AlternativeEvaluationInputs(0.3), (None, None), (0.3, None)),
            (AlternativeEvaluationInputs(coverage_price_eur_mwh=40), (None, 40), (None, 40)),
            (None, (None, None), (None, None)),
        ]:
            with self.subTest(inputs=inputs):
                alternatives = compose_alternative_evaluations(result, plan, inputs).steps[0].alternatives
                full, partial, maintain = alternatives
                self.assertIsNone(maintain.evaluation.inputs)
                self.assertEqual((getattr(full.evaluation.inputs, 'coverage_volume_gwh', None),
                                  getattr(full.evaluation.inputs, 'coverage_price_eur_mwh', None)), full_expected)
                self.assertEqual((getattr(partial.evaluation.inputs, 'coverage_volume_gwh', None),
                                  getattr(partial.evaluation.inputs, 'coverage_price_eur_mwh', None)), partial_expected)
                if inputs is None or inputs.coverage_volume_gwh is None:
                    self.assertEqual(partial.evaluation.status, 'NOT_EVALUATED')
                else:
                    self.assertEqual(partial.evaluation.status, 'EVALUATED')

    def test_short_and_top_inputs_are_isolated_in_both_directions(self):
        from take_or_pay import calculate_take_or_pay_projection
        result = SimpleNamespace(specialist_results=[
            _specialist("ProcurementAgent", _procurement()),
            _specialist("CommercialAgent", SimpleNamespace(
                take_or_pay_projection=calculate_take_or_pay_projection(40.8, 30, 8))),
        ])
        plan = DecisionPlan((
            DecisionStep("operational-short", "operational", "Cubrir", alternatives=(
                DecisionAlternative("operational-short-full", "Full", "", "operational-short"),
                DecisionAlternative("operational-short-partial", "Partial", "", "operational-short"),
                DecisionAlternative("operational-short-maintain", "Maintain", "", "operational-short"),)),
            DecisionStep("contractual-take-or-pay", "contractual", "TOP", alternatives=(
                DecisionAlternative("top-review-consumption", "Review", "", "contractual-take-or-pay"),
                DecisionAlternative("top-maintain-forecast", "Maintain", "", "contractual-take-or-pay"),)),
        ), True, readiness="READY")
        for inputs in (
            AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=11),
            AlternativeEvaluationInputs(coverage_volume_gwh=0.3, coverage_price_eur_mwh=40),
            AlternativeEvaluationInputs(0.3, 40, 11),
            AlternativeEvaluationInputs(0, 0, 0),
        ):
            with self.subTest(inputs=inputs):
                evaluated = compose_alternative_evaluations(result, plan, inputs)
                short = {item.id: item.evaluation for item in evaluated.steps[0].alternatives}
                top = {item.id: item.evaluation for item in evaluated.steps[1].alternatives}
                self.assertIsNone(short["operational-short-maintain"].inputs)
                self.assertEqual(getattr(short["operational-short-partial"].inputs, "coverage_volume_gwh", None), inputs.coverage_volume_gwh)
                self.assertEqual(getattr(short["operational-short-partial"].inputs, "coverage_price_eur_mwh", None), inputs.coverage_price_eur_mwh)
                self.assertIsNone(getattr(short["operational-short-partial"].inputs, "revised_remaining_forecast_consumption_gwh", None))
                self.assertEqual(getattr(top["top-review-consumption"].inputs, "revised_remaining_forecast_consumption_gwh", None), inputs.revised_remaining_forecast_consumption_gwh)
                self.assertIsNone(getattr(top["top-review-consumption"].inputs, "coverage_volume_gwh", None))
                self.assertIsNone(getattr(top["top-review-consumption"].inputs, "coverage_price_eur_mwh", None))
                self.assertIsNone(top["top-maintain-forecast"].inputs)

    def test_coverage_price_validation_and_missing_price_are_explicit(self):
        for invalid in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                AlternativeEvaluationInputs(coverage_price_eur_mwh=invalid)
        self.assertEqual(AlternativeEvaluationInputs(coverage_price_eur_mwh=0).coverage_price_eur_mwh, 0)

    def test_revised_forecast_validation_preserves_none_and_zero(self):
        self.assertIsNone(AlternativeEvaluationInputs().revised_remaining_forecast_consumption_gwh)
        self.assertEqual(AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=0).revised_remaining_forecast_consumption_gwh, 0)
        for invalid in (-1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=invalid)


if __name__ == "__main__":
    unittest.main()
