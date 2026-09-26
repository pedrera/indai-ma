import unittest
from dataclasses import replace

from industrial_gases.factual_comparison import (
    comparison_answer,
    compare_portfolio_facts,
    comparison_field_for_question,
    is_factual_comparison_question,
)
from industrial_gases.operational_attention import OperationalAttentionService
from industrial_gases.portfolio import SupplyPortfolioResult
from industrial_gases.portfolio_query import PortfolioQuery, SupplyPortfolioQueryService
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


if __name__ == "__main__":
    unittest.main()
