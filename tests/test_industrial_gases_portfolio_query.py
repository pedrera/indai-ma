import unittest
from dataclasses import replace

from industrial_gases.operational_attention import OperationalAttentionService
from industrial_gases.portfolio_query import PortfolioQuery, SupplyPortfolioQueryService
from industrial_gases.portfolio import SupplyPortfolioItemResult, SupplyPortfolioResult
from tests.test_industrial_gases_portfolio import canonical_portfolio
from industrial_gases.portfolio import SupplyPortfolioService


class SupplyPortfolioQueryTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = SupplyPortfolioService().evaluate(canonical_portfolio())
        self.attention = OperationalAttentionService().project(self.portfolio)
        self.service = SupplyPortfolioQueryService()

    def test_safety_stock_breach_selection_is_ordered_and_explains_match(self):
        result = self.service.select(
            self.portfolio, self.attention,
            PortfolioQuery(finding_code="safety_stock_breach"),
        )
        self.assertEqual(
            result.item_ids,
            ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"),
        )
        self.assertTrue(all("finding_code" in match.matched_by for match in result.matches))

    def test_no_attention_facts_selects_only_n2(self):
        result = self.service.select(
            self.portfolio, self.attention, PortfolioQuery(has_attention_facts=False),
        )
        self.assertEqual(result.item_ids, ("alimentos-sur-malaga-n2",))
        self.assertEqual(result.matches[0].attention.facts, ())

    def test_identity_filters_are_exact_and_preserve_portfolio_order(self):
        result = self.service.select(
            self.portfolio, self.attention,
            PortfolioQuery(customer="alimentos-del-sur"),
        )
        self.assertEqual(result.item_ids, (
            "alimentos-sur-malaga-co2", "alimentos-sur-malaga-n2",
        ))
        self.assertEqual(result.matches[0].matched_by, ("customer",))

    def test_evaluation_issues_include_invalid_and_missing_items_without_aggregate(self):
        base = self.portfolio.items
        invalid_result = replace(
            base[1].result, status="INVALID", projection=None, findings=(),
            missing_inputs=(), validation_errors=("invalid_input",),
        )
        missing_result = replace(
            base[2].result, status="MISSING_INPUTS", projection=None, findings=(),
            validation_errors=(), missing_inputs=("safety_stock",),
        )
        portfolio = SupplyPortfolioResult((
            base[0], replace(base[1], result=invalid_result), replace(base[2], result=missing_result),
        ))
        attention = OperationalAttentionService().project(portfolio)
        result = self.service.select(
            portfolio, attention, PortfolioQuery(evaluation_statuses=("INVALID", "MISSING_INPUTS")),
        )
        self.assertEqual(result.item_ids, (base[1].item_id, base[2].item_id))
        self.assertEqual(tuple(match.item.result.status for match in result.matches),
                         ("INVALID", "MISSING_INPUTS"))
        self.assertFalse(hasattr(result, "total_inventory"))


if __name__ == "__main__":
    unittest.main()
