from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import unittest

from streamlit.testing.v1 import AppTest

from industrial_gases import Quantity, SupplyAssuranceService
from industrial_gases.healthcare_ui import (
    _build_request,
    render_healthcare_supply_assurance,
    render_supply_assurance_result,
)


class HealthcareSupplyAssuranceUITests(unittest.TestCase):
    def test_canonical_request_produces_expected_deterministic_projection(self):
        result = SupplyAssuranceService().assess(_build_request())

        self.assertEqual(result.status, "COMPLETED")
        projection = result.projection
        self.assertAlmostEqual(float(projection.days_of_supply), 3200 / 700, places=10)
        self.assertEqual(projection.consumption_until_delivery, Quantity(2800, "kg"))
        self.assertEqual(projection.inventory_immediately_before_delivery, Quantity(400, "kg"))
        self.assertEqual(projection.safety_stock, Quantity(1500, "kg"))
        self.assertEqual(projection.safety_stock_gap_before_delivery.value, Decimal("-1100"))
        self.assertEqual(projection.planned_delivery_quantity, Quantity(4000, "kg"))
        self.assertEqual(projection.inventory_immediately_after_delivery, Quantity(4400, "kg"))
        self.assertFalse(projection.stockout_before_delivery)
        self.assertEqual(projection.required_delivery_volume, Quantity(1100, "kg"))
        self.assertFalse(projection.capacity_exceeded)
        self.assertEqual(projection.capacity_overflow, Quantity(0, "kg"))

    def test_ui_displays_service_projection_without_recomputing_values(self):
        result = SupplyAssuranceService().assess(_build_request())
        projection = replace(
            result.projection,
            consumption_until_delivery=Quantity(1234, "kg"),
            inventory_immediately_before_delivery=Quantity(987, "kg"),
        )
        altered_result = replace(result, projection=projection)

        def page(result):
            from industrial_gases.healthcare_ui import render_supply_assurance_result
            render_supply_assurance_result(result)

        app = AppTest.from_function(page, args=(altered_result,)).run()
        self.assertFalse(app.exception)
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["Consumption until delivery"], "1234 kg")
        self.assertEqual(values["Inventory before delivery"], "987 kg")
        self.assertEqual(values["Minimum quantity needed at delivery"], "1100 kg")

    def test_invalid_domain_combination_is_rendered_from_service_result(self):
        result = SupplyAssuranceService().assess(_build_request(current_inventory_kg=11000))
        self.assertEqual(result.status, "INVALID")
        self.assertIsNone(result.projection)
        self.assertEqual(result.missing_inputs, ())
        self.assertTrue(result.validation_errors)

        def page(result):
            from industrial_gases.healthcare_ui import render_supply_assurance_result
            render_supply_assurance_result(result)

        app = AppTest.from_function(page, args=(result,)).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Status: INVALID" in item.value for item in app.caption))
        self.assertTrue(any("inventory exceeds installation capacity" in item.value
                            for item in app.markdown))
        self.assertFalse(app.metric)

    def test_healthcare_form_runs_canonical_scenario(self):
        def page():
            from industrial_gases.healthcare_ui import render_healthcare_supply_assurance
            render_healthcare_supply_assurance()

        app = AppTest.from_function(page).run()
        self.assertFalse(app.exception)
        self.assertTrue(any(item.value == "Healthcare Supply Assurance" for item in app.header))
        self.assertTrue(any("Deterministic analysis — no LLM or RAG" in item.value
                            for item in app.caption))

        app.button(key="FormSubmitter:healthcare_supply_assurance_form-Analyze supply assurance").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(
            app.session_state["healthcare_supply_assurance_result"].status,
            "COMPLETED",
        )
        values = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(values["Consumption until delivery"], "2800 kg")
        self.assertEqual(values["Inventory before delivery"], "400 kg")
        self.assertEqual(values["Minimum quantity needed at delivery"], "1100 kg")

    def test_app_exposes_healthcare_mode_and_runs_without_other_services(self):
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
            self.assertIn("Healthcare Supply Assurance", mode.options)
            for existing_mode in (
                "Business", "Chat", "Gas B2B Portfolio Analysis", "ProcurementAgent",
                "CommercialAgent", "RiskAgent", "Multi-Agent Supervisor", "Evaluation",
            ):
                self.assertIn(existing_mode, mode.options)

            mode.set_value("Healthcare Supply Assurance").run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == "Healthcare Supply Assurance" for item in app.header))

            app.button(key="FormSubmitter:healthcare_supply_assurance_form-Analyze supply assurance").click().run()
            self.assertFalse(app.exception)
            result = app.session_state["healthcare_supply_assurance_result"]
            self.assertEqual(result.status, "COMPLETED")
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["Days of supply"], "4.571429 days")
            self.assertEqual(metrics["Consumption until delivery"], "2800 kg")
            self.assertEqual(metrics["Inventory before delivery"], "400 kg")
            self.assertEqual(metrics["Configured safety stock"], "1500 kg")
            self.assertEqual(metrics["Gap to safety stock"], "-1100 kg")
            self.assertEqual(metrics["Planned delivery"], "4000 kg")
            self.assertEqual(metrics["Inventory after delivery"], "4400 kg")
            self.assertEqual(metrics["Stockout before delivery"], "No")
            self.assertEqual(metrics["Minimum quantity needed at delivery"], "1100 kg")
            self.assertEqual(metrics["Capacity exceeded"], "No")
            self.assertEqual(metrics["Overflow"], "0 kg")

            visible_text = "\n".join(
                item.value for collection in (app.markdown, app.caption, app.text)
                for item in collection
            ).lower()
            self.assertIn("deterministic analysis — no llm or rag", visible_text)
            self.assertIn("inputs", visible_text)
            self.assertIn("calculated results", visible_text)
            self.assertIn("findings", visible_text)
            self.assertIn(
                "inventory below configured safety stock is not the same as a stockout",
                visible_text,
            )
            self.assertIn("projected inventory before delivery is below the configured safety stock", visible_text)
            self.assertNotIn("stockout before delivery = yes", visible_text)
            self.assertNotIn("recommend", visible_text)
            self.assertNotIn("recomend", visible_text)

            app.number_input(key="healthcare_inventory_kg").set_value(11000).run()
            app.button(key="FormSubmitter:healthcare_supply_assurance_form-Analyze supply assurance").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["healthcare_supply_assurance_result"].status, "INVALID")
            self.assertFalse(app.metric)


if __name__ == "__main__":
    unittest.main()
