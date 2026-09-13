import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from clipboard_text import build_diagnostics_clipboard_text
from diagnostics import PerformanceRecorder
from execution_metrics import build_operation_metrics
from gas_analysis import extract_demand_scenarios
from generation import AgentJob, GenerationStatus
from llm_client import LLMResponse
from risk_agent import RiskAgent, ProviderRiskModel
from risk_models import RiskInputs, RiskStatus
from tool_registry import LocalToolRegistry


REFERENCE = (
    "El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.\n"
    "Tenemos 4,3 GWh aprovisionados y el precio spot es de 42 EUR/MWh.\n\n"
    "Analiza nuestra exposición actual y qué ocurriría si la demanda\n"
    "aumentase un 10 %."
)


class RiskAgentTests(unittest.TestCase):
    def run_risk(self, request=REFERENCE, model=None, registry=None):
        self.recorder = PerformanceRecorder("risk-test", "deterministic", "none", "risk_agent")
        result = RiskAgent(model, self.recorder, registry).run(request)
        self.recorder.finish(result.status.value)
        return result

    def test_reference_base_stress_and_deltas(self):
        result = self.run_risk()
        self.assertEqual(result.status, RiskStatus.COMPLETED)
        base, stress, delta = result.base_scenario, result.stress_scenarios[0], result.deltas[0]
        self.assertEqual(base.demand_gwh, 4.8)
        self.assertEqual(base.supply_gwh, 4.3)
        self.assertEqual(base.position_gwh, -.5)
        self.assertEqual(base.interpretation, "SHORT")
        self.assertEqual(base.short_position_mwh, 500)
        self.assertEqual(base.spot_exposure_eur, 21000)
        self.assertEqual(stress.demand_gwh, 5.28)
        self.assertEqual(stress.supply_gwh, 4.3)
        self.assertEqual(stress.position_gwh, -.98)
        self.assertEqual(stress.short_position_mwh, 980)
        self.assertEqual(stress.spot_exposure_eur, 41160)
        self.assertEqual(delta.demand_change_gwh, .48)
        self.assertEqual(delta.short_position_change_gwh, .48)
        self.assertEqual(delta.position_change_gwh, -.48)
        self.assertEqual(delta.exposure_change_eur, 20160)
        self.assertEqual(result.risk_findings[1].direction, "worsening")

    def test_existing_tools_execute_in_scenario_order(self):
        registry = Mock(wraps=LocalToolRegistry(("calculate_demand_scenario", "calculate_supply_position", "calculate_spot_exposure")))
        self.run_risk(registry=registry)
        self.assertEqual([c.args[0] for c in registry.execute.call_args_list], [
            "calculate_supply_position", "calculate_spot_exposure", "calculate_demand_scenario",
            "calculate_supply_position", "calculate_spot_exposure"])
        self.assertEqual(registry.execute.call_args_list[3].args[1]["expected_demand_gwh"], 5.28)

    def test_long_balanced_and_short_transitions(self):
        for supply, expected in ((6., "LONG"), (5., "SHORT"), (4.8, "SHORT")):
            with self.subTest(supply=supply):
                result = self.run_risk(RiskInputs(demand_gwh=4.8, supply_gwh=supply,
                                                 spot_price_eur_mwh=42, stress_percentages=[10]))
                self.assertEqual(result.stress_scenarios[0].interpretation, expected)
                self.assertEqual(result.base_scenario.interpretation, "BALANCED" if supply == 4.8 else "LONG")
                self.assertEqual(result.base_scenario.spot_exposure_eur, 0)

    def test_missing_spot_preserves_positions_without_monetary_values(self):
        result = self.run_risk(REFERENCE.replace(" y el precio spot es de 42 EUR/MWh", ""))
        self.assertEqual(result.status, RiskStatus.PARTIAL)
        self.assertIsNone(result.base_scenario.spot_exposure_eur)
        self.assertIsNone(result.stress_scenarios[0].spot_exposure_eur)
        self.assertIsNone(result.deltas[0].exposure_change_eur)
        self.assertEqual(result.stress_scenarios[0].short_position_gwh, .98)
        self.assertNotIn("calculate_spot_exposure", result.tools_used)

    def test_multiple_scenarios_one_model_call_after_all_tools(self):
        model = Mock()
        def interpret(findings, timeout):
            self.assertEqual(len(findings), 3)
            self.assertEqual(sum(e.stage == "tool_execution" for e in self.recorder.snapshot().events), 8)
            return {"priority_finding_ids": ["stress_2"]}
        model.interpret.side_effect = interpret
        result = self.run_risk(REFERENCE.replace("aumentase un 10 %", "escenarios +10 % y +20 %"), model=model)
        model.interpret.assert_called_once()
        self.assertEqual([s.demand_gwh for s in result.stress_scenarios], [5.28, 5.76])
        self.assertEqual(result.risk_findings[0].finding_id, "stress_2")
        self.assertEqual(len(result.risk_findings), 3)

    def test_negative_scenario_improves_exposure(self):
        result = self.run_risk(REFERENCE.replace("aumentase un 10 %", "disminuyese un 10 %"))
        self.assertEqual(result.stress_scenarios[0].demand_gwh, 4.32)
        self.assertEqual(result.deltas[0].exposure_change_eur, -20160)
        self.assertEqual(result.risk_findings[1].direction, "improving")

    def test_no_requested_stress_means_base_only(self):
        result = self.run_risk(REFERENCE.split("Analiza")[0])
        self.assertFalse(result.stress_scenarios)
        self.assertEqual(len(result.tool_executions), 2)

    def test_missing_supply_is_incomplete_not_invented(self):
        model = Mock()
        result = self.run_risk("Demanda de 4,8 GWh. Escenario +10 %.", model=model)
        self.assertEqual(result.status, RiskStatus.NEEDS_INPUT)
        self.assertIsNone(result.base_scenario)
        self.assertFalse(result.tool_executions)
        model.interpret.assert_not_called()

    def test_invalid_scenarios_are_controlled_before_tools(self):
        for text in ("escenario -101 %", "escenario abc %"):
            with self.subTest(text=text):
                result = self.run_risk(REFERENCE.replace("aumentase un 10 %", text))
                self.assertEqual(result.status, RiskStatus.FAILED)
                self.assertFalse(result.tool_executions)

    def test_structured_inputs_reject_nonfinite_and_negative_values(self):
        for demand in (float("nan"), float("inf"), -1, True):
            with self.subTest(demand=demand), self.assertRaises(ValidationError):
                RiskInputs(demand_gwh=demand, supply_gwh=1)

    def test_risk_never_calls_rag(self):
        with patch("rag_service.RAGService.retrieve", side_effect=AssertionError("RAG forbidden")) as retrieve:
            self.run_risk()
        retrieve.assert_not_called()
        self.assertFalse(any(e.stage == "vector_search" for e in self.recorder.snapshot().events))

    def test_invalid_model_response_cannot_invent_risk_policy(self):
        model = Mock()
        model.interpret.return_value = {"priority_finding_ids": ["unacceptable exposure"], "chain_of_thought": "private"}
        result = self.run_risk(model=model)
        self.assertEqual(result.interpretation_mode, "deterministic_fallback")
        self.assertNotIn("unacceptable", result.content)
        self.assertNotIn("private", str(self.recorder.snapshot()))
        self.assertEqual(result.deltas[0].exposure_change_eur, 20160)

    def test_provider_requests_one_generation_without_tool_loop(self):
        provider = Mock()
        provider.generate_response.return_value = LLMResponse('{"priority_finding_ids": ["stress_1"]}')
        result = self.run_risk(model=ProviderRiskModel(provider))
        provider.generate_response.assert_called_once()
        options = provider.generate_response.call_args.kwargs["options"]
        self.assertEqual(options.max_rounds, 1)
        self.assertFalse(options.tool_calling_enabled)
        self.assertEqual(result.interpretation_mode, "llm_prioritized")

    def test_diagnostics_contain_actual_scenarios_and_metrics(self):
        self.run_risk()
        text = build_diagnostics_clipboard_text(self.recorder.snapshot())
        for expected in ("Risk Execution", "Agent: RiskAgent", "Stress scenarios: 1", "Tool calls: 5",
                         "RAG calls: 0", "LLM calls: 0", "41,160.00 EUR", "+20,160.00 EUR",
                         "Deterministic calculation wall time", "Result status: completed"):
            self.assertIn(expected, text)
        metrics = build_operation_metrics(self.recorder.snapshot())
        self.assertEqual(metrics.llm_call_count, 0)
        self.assertGreater(metrics.tool_execution_time_total, 0)

    def test_agent_job_supports_no_provider(self):
        job = AgentJob(RiskAgent(), REFERENCE, 30)
        job._run()
        result = job.poll()
        self.assertEqual(result.status, GenerationStatus.COMPLETED)
        self.assertEqual(result.provider_name, "deterministic")
        self.assertEqual(result.structured_result["deltas"][0]["exposure_change_eur"], 20160)
        cancelled = AgentJob(RiskAgent(), REFERENCE, 30)
        cancelled.cancel()
        cancelled._run()
        self.assertEqual(cancelled.poll().status, GenerationStatus.CANCELLED)

    def test_shared_parser_supports_lists_and_arbitrary_percentages(self):
        self.assertEqual([v for _, v in extract_demand_scenarios("escenarios +10 %, +20 %, -7,5 %", use_defaults=False)], [10, 20, -7.5])
        self.assertEqual([v for _, v in extract_demand_scenarios("demanda - 10 %", use_defaults=False)], [-10])

    def test_ui_and_inspector_expose_risk(self):
        result = self.run_risk()
        def page(data, snapshot):
            from risk_models import RiskAgentResult
            from risk_ui import render_risk_result
            from pipeline_inspector import render_pipeline_inspector
            render_risk_result(RiskAgentResult.model_validate(data))
            render_pipeline_inspector(snapshot)
        app = AppTest.from_function(page, args=(result.model_dump(mode="json"), self.recorder.snapshot()), default_timeout=15).run()
        self.assertFalse(app.exception)
        text = " ".join(item.value for item in app.markdown)
        for expected in ("RiskAgent", "Base Scenario", "Stress Scenario", "Risk Delta", "Structured Risk Result"):
            self.assertIn(expected, text)
        self.assertEqual(len(app.dataframe[0].value), 2)

    def test_app_runs_risk_without_available_model_or_provider(self):
        with patch("llm_client.get_available_models", return_value=[]), patch(
            "llm_client.get_llm_provider", side_effect=AssertionError("Provider not needed")
        ) as provider:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
            app.radio(key="selected_mode").set_value("RiskAgent").run()
            app.text_area(key="risk_request").set_value(REFERENCE).run()
            app.button(key="risk_start").click().run()
            app.run()
            self.assertFalse(app.exception)
            result = app.session_state["risk_result"]
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["deltas"][0]["exposure_change_eur"], 20160)
            provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
