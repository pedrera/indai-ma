import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from industrial_gases.factual_comparison import (
    comparison_answer,
    compare_portfolio_facts,
    comparison_field_for_question,
    is_factual_comparison_question,
    compare_scenarios,
    compare_supply_assurance_scenario_set,
    scenario_comparison_answer,
)
from industrial_gases import (
    Quantity,
    SupplyAssuranceAlternative,
    SupplyAssuranceScenarioSet,
    SupplyAssuranceService,
    evaluate_supply_assurance_scenario_set,
)
from industrial_gases.operational_attention import OperationalAttentionService
from industrial_gases.portfolio import SupplyPortfolioResult
from industrial_gases.portfolio_query import PortfolioQuery, SupplyPortfolioQueryService, WorkspaceScenario
from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio


class FactualComparisonTests(unittest.TestCase):
    def setUp(self):
        self.portfolio, self.attention = evaluate_demo_supply_portfolio()

    def _query(self, item_ids):
        return SupplyPortfolioQueryService().select(
            self.portfolio, self.attention, PortfolioQuery(item_ids=tuple(item_ids)),
        )

    def test_safety_stock_gap_compares_shortfall_magnitude_and_retains_signed_values(self):
        query = self._query(("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        comparison = compare_portfolio_facts(query, "safety_stock_gap_before_delivery", spanish=True)
        self.assertTrue(comparison.comparable)
        self.assertEqual(comparison.semantics, "shortfall_magnitude; raw signed gap is retained in each value")
        self.assertEqual(tuple(value.value for value in comparison.values), (-1100, -50))
        self.assertEqual(tuple(value.comparison_value for value in comparison.values), (1100, 50))
        self.assertEqual(comparison.relations[0].relation, "left_greater")
        answer = comparison_answer(comparison, spanish=True)
        self.assertIn("-1,100 kg", answer)
        self.assertIn("1,100 kg", answer)
        self.assertIn("50 kg", answer)
        self.assertIn("-50 kg", answer)
        self.assertNotIn("-1, 100 kg", answer)
        self.assertNotRegex(answer, r"-1,\s+100 kg")
        self.assertIn("déficit en valor absoluto", answer)
        self.assertNotIn("crítico", answer.casefold())
        self.assertNotIn("recomend", answer.casefold())

    def test_cross_unit_physical_quantities_are_not_comparable(self):
        query = self._query(("hospital-costa-sur-o2", "alimentos-sur-malaga-n2"))
        comparison = compare_portfolio_facts(query, "safety_stock_gap_before_delivery")
        self.assertFalse(comparison.comparable)
        self.assertEqual(comparison.reason, "incompatible_operational_units")
        self.assertEqual(comparison.relations, ())
        self.assertIn("unidades operativas distintas", comparison_answer(comparison, spanish=True))

    def test_boolean_stockout_and_capacity_facts_are_compared_independently(self):
        query = self._query(("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        stockout = compare_portfolio_facts(query, "stockout_before_delivery")
        capacity = compare_portfolio_facts(query, "capacity_exceeded")
        self.assertTrue(stockout.comparable)
        self.assertEqual(tuple(value.value for value in stockout.values), (False, False))
        self.assertEqual(stockout.relations[0].relation, "equal")
        self.assertTrue(capacity.comparable)
        self.assertEqual(tuple(value.value for value in capacity.values), (False, False))
        self.assertIn("Ninguna", comparison_answer(stockout, spanish=True))
        self.assertIn("Ninguna", comparison_answer(capacity, spanish=True))

        items = list(self.portfolio.items)
        items[0] = replace(items[0], result=replace(
            items[0].result, projection=replace(items[0].result.projection, stockout_before_delivery=True),
        ))
        items[1] = replace(items[1], result=replace(
            items[1].result, projection=replace(items[1].result.projection, capacity_exceeded=True),
        ))
        portfolio = SupplyPortfolioResult(tuple(items))
        attention = OperationalAttentionService().project(portfolio)
        query = SupplyPortfolioQueryService().select(
            portfolio, attention, PortfolioQuery(item_ids=(items[0].item_id, items[1].item_id)),
        )
        stockout_true = compare_portfolio_facts(query, "stockout_before_delivery")
        capacity_true = compare_portfolio_facts(query, "capacity_exceeded")
        self.assertEqual(tuple(value.value for value in stockout_true.values), (True, False))
        self.assertEqual(tuple(value.value for value in capacity_true.values), (False, True))
        self.assertIn("Hospital Costa Sur", comparison_answer(stockout_true, spanish=True))
        self.assertIn("Málaga Production Plant", comparison_answer(capacity_true, spanish=True))

    def test_inventory_comparison_uses_exact_operational_unit(self):
        query = self._query(("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        comparison = compare_portfolio_facts(query, "inventory_immediately_before_delivery")
        self.assertTrue(comparison.comparable)
        self.assertEqual(tuple(value.unit for value in comparison.values), ("kg", "kg"))

    def test_comparison_fallback_labels_do_not_expose_internal_ids(self):
        items = list(self.portfolio.items)
        original = items[0]
        request = replace(
            original.request,
            site=replace(original.request.site, name=""),
            gas_product=replace(original.request.gas_product, name=""),
        )
        items[0] = replace(original, request=request)
        portfolio = SupplyPortfolioResult(tuple(items))
        attention = OperationalAttentionService().project(portfolio)
        query = SupplyPortfolioQueryService().select(
            portfolio, attention,
            PortfolioQuery(item_ids=("hospital-costa-sur-o2", "alimentos-sur-malaga-co2")),
        )
        comparison = compare_portfolio_facts(
            query, "safety_stock_gap_before_delivery", spanish=True,
        )
        answer = comparison_answer(comparison, spanish=True)
        self.assertIn("Ubicación no disponible · Oxígeno medicinal (O₂)", answer)
        self.assertNotIn("hospital-costa-sur-site", answer)
        self.assertNotIn("medical-oxygen", answer)

    def test_missing_or_invalid_projection_is_reported_as_non_comparable(self):
        for status in ("INVALID", "MISSING_INPUTS"):
            with self.subTest(status=status):
                items = list(self.portfolio.items)
                items[1] = replace(items[1], result=replace(
                    items[1].result, status=status, projection=None,
                    missing_inputs=("delivery_plan",) if status == "MISSING_INPUTS" else (),
                    validation_errors=("invalid_fixture",) if status == "INVALID" else (),
                    findings=(),
                ))
                portfolio = SupplyPortfolioResult(tuple(items))
                attention = OperationalAttentionService().project(portfolio)
                query = SupplyPortfolioQueryService().select(
                    portfolio, attention,
                    PortfolioQuery(item_ids=(items[0].item_id, items[1].item_id)),
                )
                comparison = compare_portfolio_facts(query, "safety_stock_gap_before_delivery")
                self.assertFalse(comparison.comparable)
                self.assertEqual(comparison.reason, "projection_unavailable")
                self.assertIsNone(comparison.values[1].value)

    def test_question_detection_is_specific_to_fact_comparisons(self):
        self.assertTrue(is_factual_comparison_question("¿Cuál tiene mayor brecha frente al stock de seguridad?"))
        self.assertTrue(is_factual_comparison_question("Does either position exceed capacity?"))
        self.assertFalse(is_factual_comparison_question("¿La otra supera capacidad?"))
        self.assertTrue(is_factual_comparison_question("Compare inventory before delivery"))
        self.assertFalse(is_factual_comparison_question(
            "¿Qué información contractual aplica a las posiciones con brecha de stock de seguridad?"
        ))
        self.assertEqual(
            comparison_field_for_question("¿Cuáles posiciones tienen agotamiento antes de la entrega?"),
            "stockout_before_delivery",
        )

    def test_scenario_comparison_preserves_signed_values_and_handles_unavailable_and_units(self):
        source = self.portfolio.items[0].result
        baseline = WorkspaceScenario("hospital-costa-sur-o2", "baseline", "Actual", None, source)
        alternative = WorkspaceScenario("hospital-costa-sur-o2", "delivery-offset:-2", "2 días antes", -2, source)
        signed = compare_scenarios((baseline, alternative), fields=("safety_stock_gap_before_delivery",), spanish=True)
        self.assertTrue(signed.metrics[0].comparable)
        self.assertEqual(tuple(value.value for value in signed.metrics[0].values), (-1100, -1100))
        self.assertIn("-1,100 kg", scenario_comparison_answer(signed, question_kind="values", spanish=True))

        invalid_result = replace(source, status="INVALID", projection=None, findings=(), validation_errors=("invalid",))
        missing_result = replace(source, status="MISSING_INPUTS", projection=None, findings=(), missing_inputs=("delivery_plan",))
        for result, status in ((invalid_result, "INVALID"), (missing_result, "MISSING_INPUTS")):
            compared = compare_scenarios((baseline, replace(alternative, result=result)))
            self.assertFalse(compared.metrics[0].comparable)
            self.assertEqual(compared.metrics[0].reason, "projection_unavailable")
            self.assertEqual(compared.metrics[0].values[1].unavailable_reason, f"evaluation_{status.casefold()}")
        from industrial_gases.conversational_workspace_ui import (
            _render_scenario_comparison, _scenario_comparison_copy_text,
            _scenario_comparison_rows,
        )
        from streamlit.testing.v1 import AppTest
        invalid_rows = _scenario_comparison_rows(
            compare_scenarios((baseline, replace(alternative, result=invalid_result))), "es",
        )
        missing_rows = _scenario_comparison_rows(
            compare_scenarios((baseline, replace(alternative, result=missing_result))), "es",
        )
        self.assertIn("Evaluación inválida", tuple(invalid_rows[0].values()))
        self.assertIn("Faltan datos", tuple(missing_rows[0].values()))
        for result, expected_status in (
            (invalid_result, "Evaluación inválida"),
            (missing_result, "Faltan datos"),
        ):
            compared = compare_scenarios((baseline, replace(alternative, result=result)), spanish=True)
            copied = _scenario_comparison_copy_text(compared, "es")
            self.assertIn(expected_status, copied)
            self.assertIn("Proyección no disponible", copied)

            def render(value):
                from industrial_gases.conversational_workspace_ui import _render_scenario_comparison
                _render_scenario_comparison(value, "es")

            app = AppTest.from_function(render, args=(compared,)).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(
                "Falta una proyección válida" in str(element.value)
                for element in app.caption
            ))
            self.assertIn(expected_status, tuple(app.table[0].value.iloc[0]))

        from industrial_gases.models import Quantity
        altered_projection = replace(
            source.projection,
            inventory_immediately_before_delivery=Quantity(1100, "Nm3"),
        )
        altered = replace(alternative, result=replace(source, projection=altered_projection))
        incompatible = compare_scenarios(
            (baseline, altered), fields=("inventory_immediately_before_delivery",),
        )
        self.assertFalse(incompatible.metrics[0].comparable)
        self.assertEqual(incompatible.metrics[0].reason, "incompatible_operational_units")

    def _hospital_scenario_set(self):
        item = next(item for item in self.portfolio.items
                    if item.item_id == "hospital-costa-sur-o2")
        baseline = item.request
        delivery_at = baseline.delivery_plan.planned_delivery_at
        alternatives = (
            SupplyAssuranceAlternative(
                "delivery-plus-3", "Entrega en +3 días",
                replace(baseline, delivery_plan=replace(
                    baseline.delivery_plan, planned_delivery_at=delivery_at - timedelta(days=1),
                )),
            ),
            SupplyAssuranceAlternative(
                "delivery-plus-2", "Entrega en +2 días",
                replace(baseline, delivery_plan=replace(
                    baseline.delivery_plan, planned_delivery_at=delivery_at - timedelta(days=2),
                )),
            ),
        )
        return SupplyAssuranceScenarioSet(baseline, alternatives)

    def test_supply_scenario_set_comparison_preserves_baseline_order_identity_and_known_values(self):
        scenario_set = self._hospital_scenario_set()
        results = evaluate_supply_assurance_scenario_set(scenario_set, SupplyAssuranceService())

        comparison = compare_supply_assurance_scenario_set(
            results, fields=("inventory_immediately_before_delivery",), spanish=True,
        )

        metric = comparison.metrics[0]
        self.assertEqual(tuple(value.scenario_id for value in metric.values),
                         ("baseline", "delivery-plus-3", "delivery-plus-2"))
        self.assertEqual(tuple(value.value for value in metric.values), (400, 1100, 1800))
        self.assertEqual(tuple(value.unit for value in metric.values), ("kg", "kg", "kg"))
        self.assertTrue(metric.comparable)
        self.assertEqual(metric.comparable_scenario_groups,
                         (("baseline", "delivery-plus-3", "delivery-plus-2"),))
        self.assertEqual(comparison.position_identity.customer_id, "hospital-costa-sur")
        self.assertEqual(comparison.position_identity.site_id,
                         scenario_set.baseline_request.site.site_id)
        self.assertEqual(comparison.position_identity.application_id,
                         scenario_set.baseline_request.application.application_id)
        self.assertEqual(comparison.position_identity.gas_product_id,
                         scenario_set.baseline_request.gas_product.gas_product_id)
        self.assertEqual(comparison.position_identity.installation_id,
                         scenario_set.baseline_request.installation.installation_id)
        self.assertFalse(hasattr(comparison, "winner"))
        self.assertFalse(hasattr(comparison, "ranking"))
        self.assertFalse(hasattr(comparison, "score"))

    def test_supply_scenario_comparison_reads_results_without_service_calls_or_mutation(self):
        scenario_set = self._hospital_scenario_set()
        service = Mock(wraps=SupplyAssuranceService())
        results = evaluate_supply_assurance_scenario_set(scenario_set, service)
        source_before = (results.baseline_result, results.scenario_results)
        service.reset_mock()

        comparison = compare_supply_assurance_scenario_set(results)

        service.assess.assert_not_called()
        self.assertEqual((results.baseline_result, results.scenario_results), source_before)
        self.assertEqual(len(comparison.metrics), 7)

    def test_invalid_and_missing_supply_alternatives_remain_visible_and_valid_values_compare(self):
        scenario_set = self._hospital_scenario_set()
        baseline = scenario_set.baseline_request
        valid = scenario_set.alternatives[0]
        missing = SupplyAssuranceAlternative(
            "missing-delivery", "Entrega pendiente", replace(baseline, delivery_plan=None),
        )
        invalid = SupplyAssuranceAlternative(
            "invalid-unit", "Unidad incompatible",
            replace(baseline, safety_stock=Quantity(1500, "Nm3")),
        )
        valid_later = scenario_set.alternatives[1]
        results = evaluate_supply_assurance_scenario_set(
            SupplyAssuranceScenarioSet(baseline, (valid, missing, invalid, valid_later)),
            SupplyAssuranceService(),
        )

        comparison = compare_supply_assurance_scenario_set(
            results, fields=("inventory_immediately_before_delivery",),
        )
        metric = comparison.metrics[0]
        self.assertEqual(tuple(value.scenario_id for value in metric.values),
                         ("baseline", "delivery-plus-3", "missing-delivery", "invalid-unit", "delivery-plus-2"))
        self.assertEqual(tuple(value.value for value in metric.values), (400, 1100, None, None, 1800))
        self.assertEqual(metric.values[2].unavailable_reason, "evaluation_missing_inputs")
        self.assertEqual(metric.values[3].unavailable_reason, "evaluation_invalid")
        self.assertFalse(metric.comparable)
        self.assertEqual(metric.reason, "projection_unavailable")
        self.assertEqual(metric.comparable_scenario_groups,
                         (("baseline", "delivery-plus-3", "delivery-plus-2"),))

    def test_supply_scenario_comparison_does_not_compare_incompatible_units(self):
        scenario_set = self._hospital_scenario_set()
        results = evaluate_supply_assurance_scenario_set(scenario_set, SupplyAssuranceService())
        branch = results.scenario_results[0]
        projection = replace(
            branch.alternative_result.projection,
            inventory_immediately_before_delivery=Quantity(1100, "Nm3"),
        )
        altered_branch = replace(
            branch,
            alternative_result=replace(branch.alternative_result, projection=projection),
        )
        altered_results = replace(results, scenario_results=(altered_branch, *results.scenario_results[1:]))

        comparison = compare_supply_assurance_scenario_set(
            altered_results, fields=("inventory_immediately_before_delivery",),
        )

        metric = comparison.metrics[0]
        self.assertEqual(tuple(value.unit for value in metric.values), ("kg", "Nm3", "kg"))
        self.assertFalse(metric.comparable)
        self.assertEqual(metric.reason, "incompatible_operational_units")
        self.assertEqual(metric.comparable_scenario_groups,
                         (("baseline", "delivery-plus-2"),))

    def test_supply_scenario_comparison_rejects_cross_identity_results(self):
        scenario_set = self._hospital_scenario_set()
        results = evaluate_supply_assurance_scenario_set(scenario_set, SupplyAssuranceService())
        branch = results.scenario_results[0]
        altered_branch = replace(
            branch,
            alternative_result=replace(branch.alternative_result, installation_id="other-tank"),
        )
        cross_identity_results = replace(
            results, scenario_results=(altered_branch, *results.scenario_results[1:]),
        )

        with self.assertRaisesRegex(ValueError, "different portfolio position"):
            compare_supply_assurance_scenario_set(cross_identity_results)


if __name__ == "__main__":
    unittest.main()
