from dataclasses import fields, replace
from datetime import timedelta
import unittest

from industrial_gases import (
    DecisionAlternative,
    DecisionAnalysis,
    DecisionContext,
    ExplicitChange,
    OperationalAttentionService,
    SupplyAssuranceAlternative,
    SupplyAssuranceService,
    SupplyPortfolioItem,
    SupplyPortfolioRequest,
    SupplyPortfolioService,
    evaluate_supply_assurance_alternative,
)
from industrial_gases.portfolio_ui import (
    _build_portfolio_what_if,
    _canonical_portfolio_request,
    _WHAT_IF_TYPES,
)


class IndustrialGasesDecisionModelTests(unittest.TestCase):
    def setUp(self):
        portfolio = SupplyPortfolioService().evaluate(_canonical_portfolio_request())
        self.attention = OperationalAttentionService().project(portfolio)
        self.items = {item.item_id: item for item in self.attention.items}

    def _analysis(self, item, hypothesis, value):
        alternative, change = _build_portfolio_what_if(item, hypothesis, value)
        scenario = evaluate_supply_assurance_alternative(
            item.request, alternative, SupplyAssuranceService(),
        )
        return DecisionAnalysis(
            DecisionContext(item),
            (DecisionAlternative(item.item_id, change, scenario),),
        )

    def test_decision_analysis_preserves_original_baseline_alternative_and_identity(self):
        item = self.items["hospital-costa-sur-o2"]
        new_date = (item.request.reference_time + timedelta(days=3)).date()
        analysis = self._analysis(item, _WHAT_IF_TYPES[0], new_date)

        alternative = analysis.alternatives[0]
        self.assertIs(analysis.context.source_item, item)
        self.assertIs(analysis.context.baseline_result, item.result)
        self.assertEqual(alternative.scenario.baseline_result, item.result)
        self.assertIs(alternative.result, alternative.scenario.alternative_result)
        self.assertEqual(alternative.item_id, item.item_id)
        self.assertEqual(alternative.alternative_request.installation.installation_id,
                         item.request.installation.installation_id)
        self.assertEqual(alternative.alternative_request.gas_product.gas_product_id,
                         item.request.gas_product.gas_product_id)
        self.assertEqual(alternative.change.field_path, "delivery_plan.planned_delivery_at")
        self.assertEqual(alternative.change.before, item.request.delivery_plan.planned_delivery_at)
        self.assertEqual(alternative.change.after.date(), new_date)
        self.assertEqual(alternative.result.projection.inventory_immediately_before_delivery.value, 1100)
        self.assertEqual(item.result.projection.inventory_immediately_before_delivery.value, 400)

    def test_quantity_what_if_changes_only_selected_co2_and_preserves_n2_and_o2(self):
        co2 = self.items["alimentos-sur-malaga-co2"]
        n2 = self.items["alimentos-sur-malaga-n2"]
        o2 = self.items["hospital-costa-sur-o2"]
        original_n2 = n2.result
        original_o2 = o2.result

        analysis = self._analysis(co2, _WHAT_IF_TYPES[1], 10000)

        changed = analysis.alternatives[0]
        self.assertEqual(changed.alternative_request.delivery_plan.planned_quantity.value, 10000)
        self.assertEqual(changed.alternative_request.delivery_plan.planned_delivery_at,
                         co2.request.delivery_plan.planned_delivery_at)
        self.assertEqual(changed.result.projection.inventory_immediately_before_delivery,
                         co2.result.projection.inventory_immediately_before_delivery)
        self.assertEqual(changed.result.projection.inventory_immediately_after_delivery.value, 10100)
        self.assertTrue(changed.result.projection.capacity_exceeded)
        self.assertEqual(changed.result.projection.capacity_overflow.value, 9100)
        self.assertIs(n2.result, original_n2)
        self.assertIs(o2.result, original_o2)
        self.assertEqual(n2.result.projection.inventory_immediately_after_delivery.unit, "Nm3")

    def test_n2_forecast_what_if_retains_nm3_unit_and_delivery_inputs(self):
        n2 = self.items["alimentos-sur-malaga-n2"]

        analysis = self._analysis(n2, _WHAT_IF_TYPES[2], 50)

        alternative = analysis.alternatives[0]
        projection = alternative.result.projection
        self.assertEqual(alternative.change.field_path, "consumption_forecast.rate")
        self.assertEqual(alternative.alternative_request.consumption_forecast.rate.quantity_unit, "Nm3")
        self.assertEqual(alternative.alternative_request.delivery_plan, n2.request.delivery_plan)
        self.assertEqual(projection.planned_delivery_quantity.unit, "Nm3")
        self.assertEqual(projection.consumption_until_delivery.value, 200)
        self.assertEqual(projection.inventory_immediately_before_delivery.value, 700)
        self.assertEqual(n2.result.projection.inventory_immediately_before_delivery.value, 500)

    def test_decision_analysis_keeps_explicit_alternative_order_without_selection_fields(self):
        item = self.items["hospital-costa-sur-o2"]
        timing = self._analysis(item, _WHAT_IF_TYPES[0],
                                (item.request.reference_time + timedelta(days=3)).date()).alternatives[0]
        quantity = self._analysis(item, _WHAT_IF_TYPES[1], 4500).alternatives[0]
        analysis = DecisionAnalysis(DecisionContext(item), (quantity, timing))

        self.assertEqual(analysis.alternatives, (quantity, timing))
        for forbidden in ("winner", "selected_alternative", "score", "severity", "priority", "recommendation"):
            self.assertNotIn(forbidden, {field.name for field in fields(analysis)})
        self.assertFalse(hasattr(analysis, "winner"))

    def test_decision_analysis_rejects_cross_position_scenario_wiring(self):
        o2 = self.items["hospital-costa-sur-o2"]
        co2 = self.items["alimentos-sur-malaga-co2"]
        alternative, change = _build_portfolio_what_if(
            o2, _WHAT_IF_TYPES[1], 5000,
        )
        scenario = evaluate_supply_assurance_alternative(
            o2.request, alternative, SupplyAssuranceService(),
        )

        with self.assertRaises(ValueError):
            DecisionAnalysis(
                DecisionContext(co2),
                (DecisionAlternative(co2.item_id, change, scenario),),
            )

    def test_invalid_and_missing_baselines_and_scenario_results_are_preserved(self):
        original_request = self.items["hospital-costa-sur-o2"].request
        service = SupplyAssuranceService()
        for status, request, missing_name in (
            ("INVALID", replace(original_request, consumption_forecast=replace(
                original_request.consumption_forecast,
                rate=replace(original_request.consumption_forecast.rate, quantity_unit="Nm3"),
            )), None),
            ("MISSING_INPUTS", replace(original_request, safety_stock=None), "safety_stock"),
        ):
            portfolio = SupplyPortfolioService(service).evaluate(
                SupplyPortfolioRequest((SupplyPortfolioItem(f"issue-{status}", request),))
            )
            item = OperationalAttentionService().project(portfolio).items[0]
            self.assertEqual(item.evaluation_status, status)
            self.assertIsNone(item.result.projection)
            self.assertEqual(item.result.missing_inputs, (missing_name,) if missing_name else ())
            self.assertEqual(item.facts, ())
            analysis = DecisionAnalysis(DecisionContext(item))
            self.assertIs(analysis.context.baseline_result, item.result)
            self.assertEqual(analysis.alternatives, ())

            changed_request = replace(
                request,
                delivery_plan=replace(
                    request.delivery_plan,
                    planned_delivery_at=request.reference_time + timedelta(days=3),
                ),
            )
            change = ExplicitChange(
                "delivery_plan.planned_delivery_at",
                request.delivery_plan.planned_delivery_at,
                changed_request.delivery_plan.planned_delivery_at,
            )
            scenario = evaluate_supply_assurance_alternative(
                request,
                SupplyAssuranceAlternative("issue-alternative", "Explicit timing", changed_request),
                service,
            )
            decision = DecisionAnalysis(
                DecisionContext(item),
                (DecisionAlternative(item.item_id, change, scenario),),
            )
            self.assertEqual(decision.alternatives[0].scenario.baseline_result.status, status)
            self.assertEqual(decision.alternatives[0].result.status, status)
            self.assertIsNone(decision.alternatives[0].result.projection)


if __name__ == "__main__":
    unittest.main()
