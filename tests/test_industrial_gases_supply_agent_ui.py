import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from llm_client import LLMResponse
from industrial_gases.portfolio_query import SupplyAgentSessionContext


APP = Path(__file__).resolve().parents[1] / "app.py"


def _render_portfolio_with_agent(callback):
    from industrial_gases.portfolio_ui import render_supply_portfolio
    render_supply_portfolio(on_run_supply_agent=callback)


def _render_portfolio_read_only():
    from industrial_gases.portfolio_ui import render_supply_portfolio
    render_supply_portfolio()


class SupplyAgentUITests(unittest.TestCase):
    def test_supply_agent_is_embedded_in_portfolio_and_dispatches_selected_position(self):
        callback = Mock()

        app = AppTest.from_function(_render_portfolio_with_agent, args=(callback,)).run()
        self.assertFalse(app.exception)
        self.assertIn("Supply Agent", "\n".join(item.value for item in app.subheader))
        self.assertIn("Position for analysis", [item.label for item in app.selectbox])
        self.assertIn("Question about the selected scope", [item.label for item in app.text_area])
        app.selectbox(key="supply_agent_item_id").set_value("alimentos-sur-malaga-n2")
        app.text_area(key="supply_agent_question").set_value("¿Qué dice el procedimiento de N2?")
        app.button(key="supply_agent_start").click().run()
        self.assertFalse(app.exception)
        callback.assert_called_once()
        portfolio, attention, item_id, question, context = callback.call_args.args
        self.assertEqual(item_id, "alimentos-sur-malaga-n2")
        self.assertEqual(question, "¿Qué dice el procedimiento de N2?")
        self.assertEqual(portfolio.items[-1].item_id, item_id)
        self.assertEqual(attention.items[-1].item_id, item_id)
        self.assertIsInstance(context, SupplyAgentSessionContext)

    def test_supply_agent_ui_offers_entire_portfolio_scope(self):
        callback = Mock()
        app = AppTest.from_function(_render_portfolio_with_agent, args=(callback,)).run()
        self.assertFalse(app.exception)
        selector = next(item for item in app.selectbox if item.key == "supply_agent_item_id")
        self.assertIn("Entire portfolio", selector.options)
        selector.set_value("Entire portfolio")
        app.text_area(key="supply_agent_question").set_value("¿Qué posiciones requieren atención?")
        app.button(key="supply_agent_start").click().run()
        self.assertFalse(app.exception)
        self.assertIsNone(callback.call_args.args[2])

    def test_portfolio_without_agent_dispatch_remains_read_only(self):
        app = AppTest.from_function(_render_portfolio_read_only).run()
        self.assertFalse(app.exception)
        self.assertIn("Supply Agent", "\n".join(item.value for item in app.subheader))
        self.assertTrue(app.button(key="supply_agent_start").disabled)

    def test_app_supply_agent_keeps_the_active_recorder_through_completion(self):
        class FakeProvider:
            provider_name = "lmstudio"
            model = "fixture-model"

            def __init__(self, recorder):
                self.recorder = recorder
                self.calls = 0

            def generate_response(self, messages, **kwargs):
                self.calls += 1
                time.sleep(0.03)
                return LLMResponse(json.dumps({
                    "action": "finish",
                    "answer": "The existing operational projection remains available.",
                    "decision_summary": "Report operational result",
                }))

            def cancel(self):
                pass

        provider_holder = []

        def provider_factory(*args, recorder=None, **kwargs):
            provider = FakeProvider(recorder)
            provider_holder.append(provider)
            return provider

        with patch("llm_client.get_supported_providers", return_value=["lmstudio"]), \
             patch("llm_client.get_default_provider_name", return_value="lmstudio"), \
             patch("llm_client.get_available_models", return_value=["fixture-model"]), \
             patch("llm_client.get_default_model_name", return_value="fixture-model"), \
             patch("llm_client.get_llm_provider", side_effect=provider_factory):
            app = AppTest.from_file(APP, default_timeout=30).run()
            app.radio(key="selected_mode").set_value("Supply Portfolio").run()
            app.text_area(key="supply_agent_question").set_value(
                "What is the existing operational projection?"
            ).run()
            app.button(key="supply_agent_start").click().run()
            for _ in range(12):
                if app.session_state["supply_agent_result"] is not None:
                    break
                time.sleep(0.03)
                app.run()

        self.assertFalse(app.exception)
        result = app.session_state["supply_agent_result"]
        self.assertIsNotNone(result)
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(len(provider_holder), 1)
        operation_id = app.session_state["execution_mode_ids"]["Supply Portfolio"]
        recorder = app.session_state["pipeline_recorder"]
        self.assertEqual(recorder.operation_id, operation_id)
        snapshot = app.session_state["execution_views"][operation_id].snapshot
        self.assertGreater(snapshot.elapsed_seconds, 0.02)
        stages = {event.stage for event in snapshot.events}
        self.assertIn("prompt_build", stages)
        self.assertIn("parse_validation", stages)
        self.assertIn("agent_final", stages)

    def test_app_portfolio_attention_selection_uses_no_generation_call_and_saves_context(self):
        class FakeProvider:
            provider_name = "lmstudio"
            model = "fixture-model"

            def __init__(self, recorder):
                self.recorder = recorder
                self.calls = 0

            def generate_response(self, messages, **kwargs):
                self.calls += 1
                return LLMResponse(json.dumps({
                    "action": "finish", "answer": "model must not select portfolio items",
                    "decision_summary": "unexpected",
                }))

            def cancel(self):
                pass

        providers = []

        def provider_factory(*args, recorder=None, **kwargs):
            provider = FakeProvider(recorder)
            providers.append(provider)
            return provider

        with patch("llm_client.get_supported_providers", return_value=["lmstudio"]), \
             patch("llm_client.get_default_provider_name", return_value="lmstudio"), \
             patch("llm_client.get_available_models", return_value=["fixture-model"]), \
             patch("llm_client.get_default_model_name", return_value="fixture-model"), \
             patch("llm_client.get_llm_provider", side_effect=provider_factory):
            app = AppTest.from_file(APP, default_timeout=30).run()
            app.radio(key="selected_mode").set_value("Supply Portfolio").run()
            app.selectbox(key="supply_agent_item_id").set_value("Entire portfolio").run()
            app.text_area(key="supply_agent_question").set_value(
                "¿Qué posiciones requieren atención?"
            ).run()
            app.button(key="supply_agent_start").click().run()
            for _ in range(12):
                if app.session_state["supply_agent_result"] is not None:
                    break
                time.sleep(0.03)
                app.run()

        self.assertFalse(app.exception)
        result = app.session_state["supply_agent_result"]
        self.assertEqual(result.portfolio_query.item_ids,
                         ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertEqual(app.text_area(key="supply_agent_question").label,
                         "Question about the selected scope")
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].calls, 0)
        self.assertEqual(app.session_state["supply_agent_context"].selected_item_ids,
                         result.portfolio_query.item_ids)
        visible = "\n".join(
            str(element.value) for collection in (app.markdown, app.caption)
            for element in collection
        )
        self.assertIn("CO2", visible)
        self.assertIn("Matched by:", visible)
        self.assertIn("Answer / explanation", visible)
        self.assertNotIn("LLM interpretation", visible)


if __name__ == "__main__":
    unittest.main()
