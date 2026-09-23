from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from industrial_gases import SupplyAssuranceService, SupplyPortfolioResult, SupplyPortfolioService
from industrial_gases.portfolio_ui import _canonical_portfolio_request, render_supply_portfolio


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _render_page():
    from industrial_gases.portfolio_ui import render_supply_portfolio
    render_supply_portfolio()


def _visible_text(app):
    parts = []
    for collection_name in (
        "header", "subheader", "markdown", "caption", "text", "info", "warning", "error",
    ):
        parts.extend(str(item.value) for item in getattr(app, collection_name, ()))
    parts.extend(f"{item.label} {item.value}" for item in app.metric)
    return "\n".join(parts)


class SupplyPortfolioUITests(unittest.TestCase):
    def test_canonical_positions_render_independently_without_aggregation_or_portfolio_state(self):
        portfolio_service = SupplyPortfolioService()
        service_spy = Mock(wraps=portfolio_service)
        with patch("industrial_gases.portfolio_ui.SupplyPortfolioService", return_value=service_spy):
            app = AppTest.from_function(_render_page).run()

        self.assertFalse(app.exception)
        service_spy.evaluate.assert_called_once()
        request = service_spy.evaluate.call_args.args[0]
        self.assertEqual(
            [item.item_id for item in request.items],
            ["hospital-costa-sur-o2", "alimentos-sur-malaga-co2", "alimentos-sur-malaga-n2"],
        )
        text = _visible_text(app)
        for visible in (
            "Supply Portfolio",
            "Each supply position is evaluated independently",
            "quantities are not aggregated across positions",
            "hospital-costa-sur-o2",
            "Hospital Costa Sur",
            "O2 / Medicinal oxygen",
            "Operational unit: kg",
            "alimentos-sur-malaga-co2",
            "Alimentos del Sur",
            "Málaga Production Plant",
            "Beverage carbonation",
            "alimentos-sur-malaga-n2",
            "Modified atmosphere / inerting",
            "Operational unit: Nm3",
            "Status: COMPLETED",
            "Current inventory",
            "Days of supply",
            "Inventory before delivery",
            "Configured safety stock",
            "Safety stock gap",
            "Stockout before delivery",
            "Planned delivery",
            "Inventory after delivery",
            "safety stock.",
            "No findings reported.",
        ):
            self.assertIn(visible, text)
        for forbidden in (
            "Total inventory", "Total consumption", "Total capacity", "Total delivery",
            "Total safety stock", "Combined days of supply", "Total required delivery",
            "Portfolio OK", "Portfolio Warning", "Portfolio Critical", "Portfolio INVALID",
            "score", "ranking", "recommendation", "winner",
        ):
            self.assertNotIn(forbidden.lower(), text.lower())
        self.assertEqual(text.count("No findings reported."), 1)

    def test_mixed_completed_invalid_completed_items_render_independently(self):
        canonical = _canonical_portfolio_request()
        actual = SupplyPortfolioService().evaluate(canonical)
        invalid_result = replace(
            actual.items[1].result,
            status="INVALID",
            projection=None,
            validation_errors=("installation_product_mismatch",),
            findings=(),
        )
        mixed_result = SupplyPortfolioResult((
            actual.items[0],
            replace(actual.items[1], result=invalid_result),
            actual.items[2],
        ))
        portfolio_service = Mock()
        portfolio_service.evaluate.return_value = mixed_result

        with patch("industrial_gases.portfolio_ui.SupplyPortfolioService", return_value=portfolio_service):
            app = AppTest.from_function(_render_page).run()

        self.assertFalse(app.exception)
        text = _visible_text(app)
        self.assertIn("hospital-costa-sur-o2", text)
        self.assertIn("Status: COMPLETED", text)
        self.assertIn("alimentos-sur-malaga-co2", text)
        self.assertIn("Status: INVALID", text)
        self.assertIn("installation_product_mismatch", text)
        self.assertIn("alimentos-sur-malaga-n2", text)
        self.assertIn("900 Nm3", text)
        self.assertNotIn("Inventory before delivery 100 kg", text)
        self.assertFalse(any("Traceback" in str(item.value) for item in app.exception))

    def test_missing_inputs_remain_item_level_and_do_not_suppress_other_results(self):
        canonical = _canonical_portfolio_request()
        actual = SupplyPortfolioService().evaluate(canonical)
        missing_result = replace(
            actual.items[1].result,
            status="MISSING_INPUTS",
            projection=None,
            missing_inputs=("safety_stock",),
            validation_errors=(),
            findings=(),
        )
        mixed_result = SupplyPortfolioResult((
            actual.items[0],
            replace(actual.items[1], result=missing_result),
            actual.items[2],
        ))
        portfolio_service = Mock()
        portfolio_service.evaluate.return_value = mixed_result

        with patch("industrial_gases.portfolio_ui.SupplyPortfolioService", return_value=portfolio_service):
            app = AppTest.from_function(_render_page).run()

        self.assertFalse(app.exception)
        text = _visible_text(app)
        self.assertIn("Status: MISSING_INPUTS", text)
        self.assertIn("Missing inputs:", text)
        self.assertIn("- Safety stock", text)
        self.assertIn("hospital-costa-sur-o2", text)
        self.assertIn("alimentos-sur-malaga-n2", text)
        self.assertIn("900 Nm3", text)

    def test_app_mode_uses_portfolio_service_without_prohibited_external_paths(self):
        assurance_service = SupplyAssuranceService()
        portfolio_spy = Mock(wraps=SupplyPortfolioService(assurance_service))
        with (
            patch("llm_client.get_available_models", return_value=[]),
            patch("llm_client.get_llm_provider", side_effect=AssertionError("LLM provider used")),
            patch("industrial_gases.interpreter.SupplyAssuranceInterpreter",
                  side_effect=AssertionError("text interpreter used")),
            patch("industrial_gases.llm_interpreter.LLMSupplyAssuranceInterpreter",
                  side_effect=AssertionError("LLM interpreter used")),
            patch("rag_service.RAGService", side_effect=AssertionError("RAG used")),
            patch("procurement_agent.ProcurementAgent", side_effect=AssertionError("Agent used")),
            patch("commercial_agent.CommercialAgent", side_effect=AssertionError("Agent used")),
            patch("risk_agent.RiskAgent", side_effect=AssertionError("Agent used")),
            patch("supervisor.Supervisor", side_effect=AssertionError("Supervisor used")),
            patch("api_client.AnalysisApiClient", side_effect=AssertionError("Business API used")),
            patch("business_api_adapter.create_business_api_job",
                  side_effect=AssertionError("Business API job used")),
            patch("industrial_gases.portfolio_ui.SupplyPortfolioService", return_value=portfolio_spy) as factory,
        ):
            app = AppTest.from_file(APP, default_timeout=30).run()
            self.assertFalse(app.exception)
            mode = app.radio(key="selected_mode")
            self.assertIn("Supply Portfolio", mode.options)
            self.assertIn("Healthcare Supply Assurance", mode.options)
            self.assertIn("Food & Beverage Supply Assurance", mode.options)
            mode.set_value("Supply Portfolio").run()

        self.assertFalse(app.exception)
        self.assertTrue(any(item.value == "Supply Portfolio" for item in app.header))
        self.assertEqual(factory.call_count, 1)
        portfolio_spy.evaluate.assert_called_once()
        self.assertEqual(len(portfolio_spy.evaluate.call_args.args[0].items), 3)


if __name__ == "__main__":
    unittest.main()
