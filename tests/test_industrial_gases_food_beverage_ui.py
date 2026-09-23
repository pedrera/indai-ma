from pathlib import Path
from datetime import timezone
from unittest.mock import Mock, patch
import unittest

from streamlit.testing.v1 import AppTest

from industrial_gases import Quantity, SupplyAssuranceService


def _visible_text(app):
    parts = [
        item.value
        for collection in (app.header, app.subheader, app.markdown, app.caption, app.text, app.info)
        for item in collection
    ]
    parts.extend(f"{item.label} {item.value}" for item in app.metric)
    return "\n".join(parts)


def _render_food_beverage_page():
    from industrial_gases.food_beverage_ui import render_food_beverage_supply_assurance
    render_food_beverage_supply_assurance()


class FoodBeverageSupplyAssuranceUITests(unittest.TestCase):
    def test_canonical_f_and_b_demo_uses_one_service_and_keeps_gas_results_separate(self):
        service = Mock(wraps=SupplyAssuranceService())
        with patch(
            "industrial_gases.food_beverage_ui.SupplyAssuranceService",
            return_value=service,
        ):
            app = AppTest.from_function(_render_food_beverage_page).run()
            self.assertFalse(app.exception)
            app.button(
                key="FormSubmitter:food_beverage_supply_assurance_form-Analyze both gas supplies"
            ).click().run()

        self.assertFalse(app.exception)
        self.assertEqual(service.assess.call_count, 2)
        requests = app.session_state["food_beverage_supply_requests"]
        results = app.session_state["food_beverage_supply_results"]
        co2_request, n2_request = requests["co2"], requests["n2"]
        co2, n2 = results["co2"], results["n2"]

        self.assertEqual((co2.status, n2.status), ("COMPLETED", "COMPLETED"))
        self.assertEqual(co2.customer_id, n2.customer_id)
        self.assertEqual(co2.site_id, n2.site_id)
        self.assertNotEqual(co2.application_id, n2.application_id)
        self.assertNotEqual(co2.gas_product_id, n2.gas_product_id)
        self.assertNotEqual(co2.installation_id, n2.installation_id)
        self.assertIs(co2_request.site, n2_request.site)
        self.assertEqual(co2_request.site.customer_id, co2.customer_id)
        self.assertEqual(co2_request.reference_time.tzinfo, timezone.utc)
        self.assertEqual(n2_request.reference_time, n2_request.inventory_snapshot.observed_at)

        co2_projection = co2.projection
        self.assertEqual((co2_projection.gas_product_id, co2_projection.installation_id),
                         ("co2", "co2-bulk-installation"))
        self.assertEqual(co2_projection.current_inventory, Quantity(300, "kg"))
        self.assertEqual(co2_projection.consumption_until_delivery, Quantity(200, "kg"))
        self.assertEqual(co2_projection.inventory_immediately_before_delivery, Quantity(100, "kg"))
        self.assertEqual([finding.code for finding in co2.findings], ["safety_stock_breach"])

        n2_projection = n2.projection
        self.assertEqual((n2_projection.gas_product_id, n2_projection.installation_id),
                         ("n2", "n2-bulk-installation"))
        self.assertEqual(n2_projection.current_inventory, Quantity(900, "Nm3"))
        self.assertEqual(n2_projection.consumption_until_delivery, Quantity(400, "Nm3"))
        self.assertEqual(n2_projection.inventory_immediately_before_delivery, Quantity(500, "Nm3"))
        self.assertEqual(n2.findings, ())

        self.assertEqual(service.assess.call_args_list[0].args[0], co2_request)
        self.assertEqual(service.assess.call_args_list[1].args[0], n2_request)
        self.assertEqual(co2_request.consumption_forecast.rate.quantity_unit, "kg")
        self.assertEqual(n2_request.consumption_forecast.rate.quantity_unit, "Nm3")
        self.assertEqual(co2_request.application.gas_requirements[0].gas_product_id, "co2")
        self.assertEqual(n2_request.application.gas_requirements[0].gas_product_id, "n2")

        text = _visible_text(app).lower()
        self.assertIn("each gas is evaluated independently", text)
        self.assertIn("co2 · kg", text)
        self.assertIn("n2 · nm3", text)
        self.assertIn("safety stock", text)
        self.assertIn("below the configured safety stock", text)
        self.assertIn("no findings reported", text)
        self.assertIn("customer: alimentos-del-sur — alimentos del sur", text)
        self.assertIn("site: malaga-production-plant", text)
        self.assertIn("operational unit: kg", text)
        self.assertIn("operational unit: nm3", text)
        for forbidden in (
            "total inventory", "total consumption", "total delivery",
            "total safety stock", "total site supply", "combined autonomy",
            "recommend", "ranking", "winner", "optimal", "best option", "score",
        ):
            self.assertNotIn(forbidden, text)

    def test_changing_co2_inputs_does_not_change_n2_and_co2_can_fail_independently(self):
        app = AppTest.from_function(_render_food_beverage_page).run()
        app.button(
            key="FormSubmitter:food_beverage_supply_assurance_form-Analyze both gas supplies"
        ).click().run()
        self.assertFalse(app.exception)
        baseline_n2 = app.session_state["food_beverage_supply_results"]["n2"].projection

        app.number_input(key="food_beverage_co2_inventory").set_value(400)
        app.button(
            key="FormSubmitter:food_beverage_supply_assurance_form-Analyze both gas supplies"
        ).click().run()
        self.assertFalse(app.exception)
        changed_results = app.session_state["food_beverage_supply_results"]
        self.assertEqual(changed_results["co2"].projection.current_inventory, Quantity(400, "kg"))
        self.assertEqual(changed_results["n2"].projection, baseline_n2)

        app.number_input(key="food_beverage_co2_capacity").set_value(100)
        app.button(
            key="FormSubmitter:food_beverage_supply_assurance_form-Analyze both gas supplies"
        ).click().run()
        self.assertFalse(app.exception)
        results = app.session_state["food_beverage_supply_results"]
        self.assertEqual(results["co2"].status, "INVALID")
        self.assertIsNone(results["co2"].projection)
        self.assertTrue(results["co2"].validation_errors)
        self.assertEqual(results["n2"].status, "COMPLETED")
        self.assertEqual(results["n2"].projection, baseline_n2)
        text = _visible_text(app)
        self.assertIn("Status: INVALID", text)
        self.assertIn("Status: COMPLETED", text)
        self.assertIn("inventory exceeds installation capacity", text)
        self.assertIn("400 Nm3", text)

    def test_app_exposes_food_beverage_mode_without_calling_external_or_energy_paths(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        with (
            patch("llm_client.get_available_models", return_value=[]),
            patch("llm_client.get_llm_provider", side_effect=AssertionError("LLM provider used")),
            patch("rag_service.RAGService", side_effect=AssertionError("RAG used")),
            patch("procurement_agent.ProcurementAgent", side_effect=AssertionError("Procurement agent used")),
            patch("commercial_agent.CommercialAgent", side_effect=AssertionError("Commercial agent used")),
            patch("risk_agent.RiskAgent", side_effect=AssertionError("Risk agent used")),
            patch("supervisor.Supervisor", side_effect=AssertionError("Supervisor used")),
            patch("api_client.AnalysisApiClient", side_effect=AssertionError("Business API client used")),
            patch("business_api_adapter.create_business_api_job",
                  side_effect=AssertionError("Business API job used")),
        ):
            app = AppTest.from_file(str(app_path), default_timeout=30).run()
            self.assertFalse(app.exception)
            mode = app.radio(key="selected_mode")
            self.assertIn("Food & Beverage Supply Assurance", mode.options)
            self.assertIn("Healthcare Supply Assurance", mode.options)
            mode.set_value("Food & Beverage Supply Assurance").run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == "Food & Beverage Supply Assurance" for item in app.header))

            app.button(
                key="FormSubmitter:food_beverage_supply_assurance_form-Analyze both gas supplies"
            ).click().run()
            self.assertFalse(app.exception)
            results = app.session_state["food_beverage_supply_results"]
            self.assertEqual((results["co2"].status, results["n2"].status),
                             ("COMPLETED", "COMPLETED"))
            self.assertEqual(results["co2"].projection.consumption_until_delivery, Quantity(200, "kg"))
            self.assertEqual(results["n2"].projection.consumption_until_delivery, Quantity(400, "Nm3"))


if __name__ == "__main__":
    unittest.main()
