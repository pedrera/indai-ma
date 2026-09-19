import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from diagnostics import PerformanceRecorder
from execution_metrics import build_operation_metrics
from clipboard_text import build_diagnostics_clipboard_text
from llm_client import LLMResponse, GenerationCancelledError
from rag_service import RAGService
from vector_store import LocalVectorStore
from supervisor import Supervisor
from supervisor_models import AGENT_ORDER, SupervisorResult
from supervisor_routing import route_deterministically, resolve_routing
from procurement_common import extract_procurement_context
from tests.test_commercial_agent import contract_matches

REFERENCE = """El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
Tenemos 4,3 GWh aprovisionados y el precio spot actual es de 42 EUR/MWh.

Analiza:
- las condiciones relevantes de su contrato;
- nuestra posición de aprovisionamiento;
- el coste de cubrir cualquier déficit;
- y qué ocurriría si la demanda aumentase un 10 %."""
FACTS = "Tenemos 4,8 GWh de demanda, 4,3 GWh aprovisionados y precio spot 42 EUR/MWh. "
CANONICAL_BUSINESS_QUERY = """Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
Tenemos 4,3 GWh aprovisionados y el precio spot es 42 EUR/MWh.
Analiza la situación contractual, de aprovisionamiento y de riesgo."""


class Embeddings:
    model = "test-supervisor"
    def embed_documents(self, texts):
        return [[1., 1.] for _ in texts]
    def embed_query(self, text):
        return [1., 1.]


class RecordedProvider:
    def __init__(self, recorder, content='{"source_numbers":[1,2,3]}'):
        self.recorder, self.content = recorder, content
        self.calls = 0

    def generate_response(self, messages, timeout_seconds=None, options=None):
        self.calls += 1
        event = self.recorder.start_llm_call(call_number=1, purpose=options.trace_purpose,
            provider_round=1, message_count=1, prompt_character_count=len(messages[0]['content']),
            tool_schema_character_count=0)
        self.recorder.complete_stage(event)
        return LLMResponse(self.content)

    def cancel(self):
        pass


class SupervisorTests(unittest.TestCase):
    def test_supervisor_explicitly_configures_deterministic_commercial(self):
        from supervisor import ScopedRecorder
        runner = Supervisor(rag_factory=lambda recorder: Mock())
        with patch('supervisor.CommercialAgent') as agent:
            runner._specialist('CommercialAgent', ScopedRecorder(runner.recorder, 'CommercialAgent'), {})
        self.assertEqual(agent.call_args.kwargs['interpretation_mode'], 'deterministic')
        self.assertIsNone(agent.call_args.args[0])

    def test_standalone_commercial_keeps_explicit_llm_mode(self):
        from commercial_agent import CommercialAgent
        from tests.test_commercial_agent import EvidenceModel
        from rag_models import RetrievalResult
        model, rag = EvidenceModel(), Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'test')
        result = CommercialAgent(model, rag, interpretation_mode='llm').run(REFERENCE, 30)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.interpretation_mode, 'llm')
        self.assertEqual(result.status.value, 'completed')

    def test_shared_parser_value_first_demand_does_not_read_supply(self):
        context = extract_procurement_context(FACTS)
        self.assertEqual((context.demand_gwh, context.supply_gwh), (4.8, 4.3))

    def test_ambiguous_provider_records_one_router_call(self):
        runner = Supervisor(provider_factory=lambda r: RecordedProvider(r, '{"selected_agents":["RiskAgent"]}'))
        result = runner.run('Analiza el riesgo del Hospital Costa Sur')
        self.assertEqual(result.router_llm_calls, 1)
        self.assertEqual(result.total_llm_calls, 1)
        self.assertEqual(result.total_tool_calls, 0)
        self.assertEqual(result.specialist_results[0].status, 'needs_input')

    def test_cancellation_between_agents_prevents_next_agent(self):
        later = Mock(side_effect=AssertionError('Must not execute'))
        runner = Supervisor(specialist_factories={'RiskAgent': later})
        def cancel(recorder):
            runner.cancel()
            raise GenerationCancelledError('cancelled')
        runner.specialist_factories['ProcurementAgent'] = cancel
        with self.assertRaises(GenerationCancelledError):
            runner.run(FACTS + 'Posición y escenario +10%')
        later.assert_not_called()

    def test_tool_cache_is_not_shared_between_operations(self):
        runner = Supervisor()
        for _ in range(2):
            result = runner.run(FACTS + 'Posición y escenario +10%')
            self.assertEqual(result.total_tool_calls, 5)
            self.assertEqual(result.specialist_results[1].reused_tool_results, 2)

    def test_routing_combinations(self):
        cases = [
            ("¿Cuál es la flexibilidad contractual del Hospital Costa Sur?", ["CommercialAgent"]),
            ("Tenemos 120 GWh de demanda y 95 GWh aprovisionados. ¿Cuál es nuestra posición?", ["ProcurementAgent"]),
            (FACTS + "¿Qué ocurre si la demanda sube un 10%?", ["RiskAgent"]),
            (FACTS + "Analiza el contrato y nuestra posición.", ["CommercialAgent", "ProcurementAgent"]),
            (FACTS + "Analiza nuestra posición y escenario +10%.", ["ProcurementAgent", "RiskAgent"]),
            (FACTS + "Analiza el contrato y escenario +10%.", ["CommercialAgent", "RiskAgent"]),
            (REFERENCE, list(AGENT_ORDER)),
            ("Demanda 4,8 GWh. Suministro contratado 4,3 GWh. Posición actual.", ["ProcurementAgent"]),
            ("Analiza la posición contractual del Hospital Costa Sur.", ["CommercialAgent"]),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                decision = route_deterministically(query)
                self.assertEqual(decision.selected_agents, expected)
                self.assertFalse(decision.ambiguity_detected)

    def test_canonical_business_query_routes_all_relevant_specialists(self):
        decision = route_deterministically(CANONICAL_BUSINESS_QUERY)
        self.assertEqual(decision.selected_agents, list(AGENT_ORDER))
        self.assertFalse(decision.ambiguity_detected)

    def test_ambiguous_router_only_once(self):
        router = Mock()
        router.select.return_value = {"selected_agents": ["RiskAgent"]}
        decision = resolve_routing("Analiza el riesgo de nuestra cartera", route_deterministically("Analiza el riesgo de nuestra cartera"), router, 30)
        self.assertEqual(decision.routing_method, "hybrid")
        self.assertEqual(decision.selected_agents, ["RiskAgent"])
        router.select.assert_called_once()

    def test_invalid_ambiguous_routing_executes_nobody(self):
        router = Mock()
        router.select.return_value = {"selected_agents": ["OtherAgent"], "chain_of_thought": "private"}
        result = Supervisor(router=router).run("Analiza nuestra cartera de gas")
        self.assertFalse(result.specialist_results)
        self.assertEqual(result.status.value, "needs_input")
        self.assertNotIn("private", result.model_dump_json())

    def test_unsupported_never_uses_router_or_agents(self):
        factory = Mock(side_effect=AssertionError("Must not execute"))
        router = Mock()
        result = Supervisor(router=router, specialist_factories={n: factory for n in AGENT_ORDER}).run("Escribe una receta de tortilla")
        factory.assert_not_called()
        router.select.assert_not_called()
        self.assertFalse(result.specialist_results)

    def test_clear_risk_does_not_instantiate_provider_or_other_specialists(self):
        factory = Mock(side_effect=AssertionError("Unselected"))
        result = Supervisor(provider_factory=factory, specialist_factories={"CommercialAgent": factory, "ProcurementAgent": factory}).run(FACTS + "Escenario +10%")
        factory.assert_not_called()
        self.assertEqual(result.total_llm_calls, 0)
        self.assertEqual(result.total_tool_calls, 5)

    def test_full_reference_real_specialists_and_rag(self):
        with TemporaryDirectory() as directory:
            recorder = PerformanceRecorder("reference", "test", "test", "supervisor")
            store = LocalVectorStore(directory, Embeddings.model)
            service = RAGService(Embeddings(), store)
            service.ingest([("contrato_hospital_costa_sur.txt", "\n\n".join(m.chunk.text for m in contract_matches()).encode())])
            provider_factory = Mock(side_effect=AssertionError("Supervisor must not create an LLM provider"))
            runner = Supervisor(recorder, provider_factory, lambda r: RAGService(Embeddings(), store, r))
            result = runner.run(REFERENCE, 30)
            recorder.finish(result.status.value)
            self.assertEqual(result.routing.selected_agents, list(AGENT_ORDER))
            self.assertEqual(result.status.value, "completed")
            commercial, procurement, risk = [item.result for item in result.specialist_results]
            provider_factory.assert_not_called()
            self.assertEqual(commercial.interpretation_mode, "deterministic")
            stages = [e for e in recorder.snapshot().events if e.stage == "commercial_interpretation"]
            self.assertEqual([e.status.value for e in stages], ["skipped"])
            facts = {f.name: f.value for f in commercial.contract_facts}
            self.assertEqual(facts['reference_volume_gwh'], 4)
            self.assertEqual(facts['flexibility_percent'], 15)
            self.assertEqual(facts['excess_surcharge_eur_mwh'], 4)
            calc = commercial.calculations[0].result
            self.assertEqual((calc['contractual_min_gwh'], calc['contractual_max_gwh'], calc['contractual_excess_gwh']), (3.4, 4.6, .2))
            position, exposure = procurement.tool_executions
            self.assertEqual(position['arguments'], {'expected_demand_gwh': 4.8, 'contracted_supply_gwh': 4.3})
            self.assertEqual(position['result'], {'position_gwh': -.5, 'interpretation': 'SHORT'})
            self.assertEqual(exposure['result']['exposure_eur'], 21000)
            self.assertEqual(exposure['result']['short_position_gwh'], .5)
            self.assertNotEqual(calc['contractual_excess_gwh'], exposure['result']['short_position_gwh'])
            self.assertIn('Spot + 4', commercial.content)
            self.assertIn('contrato_hospital_costa_sur.txt', result.content)
            self.assertEqual(risk.stress_scenarios[0].demand_gwh, 5.28)
            self.assertEqual(risk.stress_scenarios[0].position_gwh, -.98)
            self.assertEqual(risk.stress_scenarios[0].spot_exposure_eur, 41160)
            self.assertEqual(risk.deltas[0].exposure_change_eur, 20160)
            self.assertEqual((result.total_llm_calls, result.total_rag_calls, result.total_tool_calls), (0, 1, 6))
            self.assertEqual(result.specialist_results[2].reused_tool_results, 2)
            self.assertEqual([i.tool_calls for i in result.specialist_results], [1, 2, 3])
            self.assertEqual(SupervisorResult.model_validate_json(result.model_dump_json()).content, result.content)
            text = build_diagnostics_clipboard_text(recorder.snapshot())
            for expected in ('Supervisor Execution', 'Total LLM calls: 0', 'Total RAG calls: 1', 'Total tool calls: 6', 'reused tool results: 2'):
                self.assertIn(expected, text)
            self.assertEqual(build_operation_metrics(recorder.snapshot()).llm_call_count, 0)
            self.assertLessEqual(sum(i.duration_seconds for i in result.specialist_results), result.total_operation_wall_time)
            starts = [e.metadata['agent_name'] for e in recorder.snapshot().events if e.stage == 'specialist_execution']
            self.assertEqual(starts, list(AGENT_ORDER))

    def test_partial_failure_preserves_success(self):
        failed = Mock(side_effect=RuntimeError("private internal failure"))
        result = Supervisor(specialist_factories={"CommercialAgent": failed}).run(REFERENCE)
        self.assertEqual(result.status.value, 'partial')
        self.assertEqual(result.specialist_results[0].status, 'failed')
        self.assertEqual(result.specialist_results[2].result.deltas[0].exposure_change_eur, 20160)
        self.assertNotIn('private internal failure', result.content)

    def test_risk_failure_preserves_procurement(self):
        result = Supervisor(specialist_factories={'RiskAgent': Mock(side_effect=RuntimeError())}).run(FACTS + 'Posición y escenario +10%')
        self.assertEqual(result.status.value, 'partial')
        self.assertEqual(result.specialist_results[0].result.tool_executions[1]['result']['exposure_eur'], 21000)

    def test_cancel_stops_before_execution(self):
        runner = Supervisor()
        runner.cancel()
        with self.assertRaises(GenerationCancelledError):
            runner.run(REFERENCE)

    def test_optional_synthesis_one_call_preserves_values(self):
        recorder = PerformanceRecorder('synthesis', 'test', 'test', 'supervisor')
        result = Supervisor(recorder, lambda r: RecordedProvider(r, '{"priority_sections":["RiskAgent"]}'), use_llm_synthesis=True).run(FACTS + 'Escenario +10%')
        self.assertEqual(result.total_llm_calls, 1)
        self.assertEqual(result.synthesis_llm_calls, 1)
        self.assertEqual(result.synthesis_status, 'llm_prioritized')
        self.assertEqual(result.specialist_results[0].result.deltas[0].exposure_change_eur, 20160)

    def test_invalid_synthesis_falls_back(self):
        result = Supervisor(provider_factory=lambda r: RecordedProvider(r, '{"priority_sections":["InventedAgent"]}'), use_llm_synthesis=True).run(FACTS + 'Escenario +10%')
        self.assertEqual(result.synthesis_status, 'deterministic_fallback')
        self.assertNotIn('InventedAgent', result.content)

    def test_specialists_do_not_import_supervisor_or_each_other(self):
        import ast
        root = Path(__file__).resolve().parents[1]
        for file in ('procurement_agent.py', 'procurement_deterministic.py', 'commercial_agent.py', 'risk_agent.py'):
            tree = ast.parse((root / file).read_text(encoding='utf-8'))
            modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            for forbidden in ('supervisor', 'commercial_agent', 'risk_agent', 'procurement_agent'):
                self.assertNotIn(forbidden, modules)

    def test_app_supervisor_without_model_and_inspector(self):
        with patch('llm_client.get_available_models', return_value=[]), patch('llm_client.get_llm_provider', side_effect=AssertionError('Unnecessary provider')):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20).run()
            app.radio(key='selected_mode').set_value('Multi-Agent Supervisor').run()
            app.text_area(key='supervisor_request').set_value(FACTS + 'Escenario +10%').run()
            app.button(key='supervisor_start').click().run()
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['supervisor_result']['total_tool_calls'], 5)
            self.assertTrue(any('Supervisor Execution' in t.value for t in app.text))


if __name__ == '__main__':
    unittest.main()
