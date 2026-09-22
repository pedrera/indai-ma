from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import unittest

from streamlit.testing.v1 import AppTest

from industrial_gases import ConsumptionRate, Quantity, SupplyAssuranceService
from industrial_gases.healthcare_ui import (
    _build_supply_assurance_alternative,
    _build_request,
    render_supply_assurance_result,
)
from industrial_gases.supply_scenarios import (
    SupplyAssuranceAlternative,
    evaluate_supply_assurance_alternative,
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

    def _render_scenario(self, alternative):
        baseline_request = _build_request()
        result = evaluate_supply_assurance_alternative(
            baseline_request, alternative, SupplyAssuranceService(),
        )

        def page(scenario_result, baseline_request):
            import streamlit as st
            from industrial_gases.healthcare_ui import _render_supply_assurance_scenario
            st.session_state.healthcare_supply_assurance_request = baseline_request
            _render_supply_assurance_scenario(scenario_result)

        return AppTest.from_function(page, args=(result, baseline_request)).run(), result

    def test_earlier_delivery_what_if_shows_relevant_projection_values(self):
        request = _build_request()
        alternative = _build_supply_assurance_alternative(request, "Earlier delivery", 3)
        app, result = self._render_scenario(alternative)

        self.assertFalse(app.exception)
        self.assertEqual(result.alternative_result.projection.inventory_immediately_before_delivery,
                         Quantity(1100, "kg"))
        visible_text = "\n".join(item.value for item in (*app.markdown, *app.text))
        self.assertIn("Delivery timing — Baseline: 4 days; Alternative: 3 days", visible_text)
        self.assertIn("WHAT CHANGED", visible_text)
        self.assertIn("BASELINE vs ALTERNATIVE", visible_text)
        self.assertIn("Baseline findings", visible_text)
        self.assertIn("Alternative findings", visible_text)
        self.assertGreaterEqual(visible_text.count("below the configured safety stock"), 2)
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["Consumption until delivery"], "2100 kg")
        self.assertEqual(metrics["Inventory before delivery"], "1100 kg")
        self.assertEqual(metrics["Gap to safety stock"], "-400 kg")
        self.assertEqual(metrics["Stockout before delivery"], "No")
        self.assertEqual(metrics["Inventory after delivery"], "5100 kg")
        self.assertEqual(metrics["Minimum quantity needed at delivery"], "400 kg")

    def test_delivery_quantity_what_if_focuses_on_post_delivery_and_capacity(self):
        request = _build_request()
        alternative = _build_supply_assurance_alternative(
            request, "Different planned delivery quantity", 5000,
        )
        app, result = self._render_scenario(alternative)

        self.assertFalse(app.exception)
        self.assertEqual(result.alternative_result.projection.inventory_immediately_after_delivery,
                         Quantity(5400, "kg"))
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics, {
            "Inventory after delivery": "5400 kg",
            "Capacity exceeded": "No",
            "Overflow": "0 kg",
        })
        visible_text = "\n".join(item.value for item in (*app.markdown, *app.text))
        self.assertIn("Planned delivery quantity — Baseline: 4000 kg; Alternative: 5000 kg", visible_text)

    def test_hypothetical_consumption_forecast_is_explicit(self):
        request = _build_request()
        alternative = _build_supply_assurance_alternative(
            request, "Hypothetical consumption forecast", 500,
        )
        app, result = self._render_scenario(alternative)

        self.assertFalse(app.exception)
        self.assertEqual(result.alternative_result.projection.days_of_supply, Decimal("6.4"))
        self.assertEqual(result.alternative_result.projection.inventory_immediately_before_delivery,
                         Quantity(1200, "kg"))
        metrics = {metric.label: metric.value for metric in app.metric}
        self.assertEqual(metrics["Consumption until delivery"], "2000 kg")
        self.assertEqual(metrics["Gap to safety stock"], "-300 kg")
        self.assertEqual(metrics["Inventory after delivery"], "5200 kg")
        self.assertEqual(metrics["Minimum quantity needed at delivery"], "300 kg")
        self.assertIn("Hypothetical consumption forecast",
                      "\n".join(item.value for item in (*app.markdown, *app.text)))

    def test_invalid_what_if_is_shown_without_partial_metrics_or_traceback(self):
        request = _build_request()
        invalid_request = replace(
            request,
            consumption_forecast=replace(
                request.consumption_forecast,
                rate=ConsumptionRate(700, "Nm3", "day"),
            ),
        )
        alternative = SupplyAssuranceAlternative(
            "healthcare-revised-consumption-forecast",
            "Hypothetical consumption forecast",
            invalid_request,
        )
        app, result = self._render_scenario(alternative)

        self.assertFalse(app.exception)
        self.assertEqual(result.baseline_result.status, "COMPLETED")
        self.assertEqual(result.alternative_result.status, "INVALID")
        self.assertFalse(app.metric)
        visible_text = "\n".join(item.value for item in (*app.caption, *app.markdown, *app.text))
        self.assertIn("Status: COMPLETED", visible_text)
        self.assertIn("Status: INVALID", visible_text)
        self.assertIn("inventory and consumption rate units are incompatible", visible_text)

    def test_missing_input_what_if_is_shown_without_partial_metrics(self):
        request = _build_request()
        alternative = SupplyAssuranceAlternative(
            "healthcare-earlier-delivery",
            "Earlier delivery",
            replace(request, delivery_plan=None),
        )
        app, result = self._render_scenario(alternative)

        self.assertFalse(app.exception)
        self.assertEqual(result.baseline_result.status, "COMPLETED")
        self.assertEqual(result.alternative_result.status, "MISSING_INPUTS")
        self.assertFalse(app.metric)
        visible_text = "\n".join(item.value for item in (*app.caption, *app.markdown, *app.text))
        self.assertIn("Status: COMPLETED", visible_text)
        self.assertIn("Status: MISSING_INPUTS", visible_text)
        self.assertIn("Missing inputs:", visible_text)
        self.assertIn("Planned delivery", visible_text)

    def test_what_if_disclaimer_does_not_present_a_recommendation_or_ranking(self):
        def page():
            import streamlit as st
            from industrial_gases.healthcare_ui import render_healthcare_supply_assurance
            render_healthcare_supply_assurance()

        app = AppTest.from_function(page).run()
        self.assertFalse(app.exception)
        visible_text = "\n".join(
            item.value for group in (app.caption, app.markdown, app.text) for item in group
        ).lower()
        self.assertIn("does not recommend or rank alternatives", visible_text)
        for affirmative in ("recommended quantity", "best option", "winner", "better", "worse", "optimal"):
            self.assertNotIn(affirmative, visible_text)

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
            baseline_request = app.session_state["healthcare_supply_assurance_request"]

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
            self.assertIn("does not recommend or rank alternatives", visible_text)
            for affirmative in ("recommended quantity", "best option", "winner", "better", "worse", "optimal"):
                self.assertNotIn(affirmative, visible_text)

            scenario_expectations = (
                ("Earlier delivery", "2100 kg", "1100 kg"),
                ("Different planned delivery quantity", "5400 kg", "0 kg"),
                ("Hypothetical consumption forecast", "2000 kg", "1200 kg"),
            )
            for scenario_type, first_value, second_value in scenario_expectations:
                app.selectbox(key="healthcare_what_if_type").set_value(scenario_type).run()
                app.button(key="FormSubmitter:healthcare_what_if_form-Analyze baseline vs alternative").click().run()
                self.assertFalse(app.exception)
                scenario_result = app.session_state["healthcare_supply_scenario_result"]
                self.assertEqual(scenario_result.baseline_result.status, "COMPLETED")
                self.assertEqual(scenario_result.alternative_result.status, "COMPLETED")
                alternative_request = scenario_result.alternative.alternative_request
                if scenario_type == "Earlier delivery":
                    self.assertEqual(alternative_request.consumption_forecast,
                                     baseline_request.consumption_forecast)
                    self.assertEqual(alternative_request.delivery_plan.planned_quantity,
                                     baseline_request.delivery_plan.planned_quantity)
                    self.assertEqual(scenario_result.baseline_result.projection.days_of_supply,
                                     scenario_result.alternative_result.projection.days_of_supply)
                elif scenario_type == "Different planned delivery quantity":
                    self.assertEqual(alternative_request.delivery_plan.planned_delivery_at,
                                     baseline_request.delivery_plan.planned_delivery_at)
                    self.assertEqual(alternative_request.consumption_forecast,
                                     baseline_request.consumption_forecast)
                    baseline_projection = scenario_result.baseline_result.projection
                    alternative_projection = scenario_result.alternative_result.projection
                    self.assertEqual(alternative_projection.consumption_until_delivery,
                                     baseline_projection.consumption_until_delivery)
                    self.assertEqual(alternative_projection.inventory_immediately_before_delivery,
                                     baseline_projection.inventory_immediately_before_delivery)
                    self.assertEqual(alternative_projection.safety_stock_gap_before_delivery,
                                     baseline_projection.safety_stock_gap_before_delivery)
                    self.assertEqual(alternative_projection.stockout_before_delivery,
                                     baseline_projection.stockout_before_delivery)
                    self.assertEqual(alternative_projection.required_delivery_volume,
                                     baseline_projection.required_delivery_volume)
                else:
                    self.assertEqual(alternative_request.delivery_plan, baseline_request.delivery_plan)
                    self.assertEqual(alternative_request.inventory_snapshot,
                                     baseline_request.inventory_snapshot)
                    self.assertEqual(alternative_request.consumption_forecast.rate,
                                     ConsumptionRate(500, "kg", "day"))
                visible_metrics = {metric.label: metric.value for metric in app.metric}
                self.assertIn(first_value, visible_metrics.values())
                self.assertIn(second_value, visible_metrics.values())

            app.selectbox(key="healthcare_what_if_type").set_value(
                "Different planned delivery quantity",
            ).run()
            app.number_input(key="healthcare_what_if_delivery_quantity_kg").set_value(10000).run()
            app.button(key="FormSubmitter:healthcare_what_if_form-Analyze baseline vs alternative").click().run()
            self.assertFalse(app.exception)
            overflow_result = app.session_state["healthcare_supply_scenario_result"]
            overflow_projection = overflow_result.alternative_result.projection
            self.assertEqual(overflow_projection.inventory_immediately_after_delivery, Quantity(10400, "kg"))
            self.assertTrue(overflow_projection.capacity_exceeded)
            self.assertEqual(overflow_projection.capacity_overflow, Quantity(400, "kg"))
            self.assertEqual(overflow_projection.inventory_immediately_before_delivery,
                             overflow_result.baseline_result.projection.inventory_immediately_before_delivery)
            self.assertEqual(overflow_projection.safety_stock_gap_before_delivery,
                             overflow_result.baseline_result.projection.safety_stock_gap_before_delivery)
            overflow_metrics = {metric.label: metric.value for metric in app.metric}
            self.assertIn("10400 kg", overflow_metrics.values())
            self.assertIn("Yes", overflow_metrics.values())
            self.assertIn("400 kg", overflow_metrics.values())
            self.assertEqual(app.session_state["healthcare_supply_assurance_request"], baseline_request)

            app.number_input(key="healthcare_inventory_kg").set_value(11000).run()
            app.button(key="FormSubmitter:healthcare_supply_assurance_form-Analyze supply assurance").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["healthcare_supply_assurance_result"].status, "INVALID")
            self.assertFalse(app.metric)


if __name__ == "__main__":
    unittest.main()
