import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from diagnostics import PerformanceRecorder
from execution_view import ExecutionView, find_execution, clipboard_payloads
from business_output import commercial_text, supervisor_text
from business_output import build_supervisor_executive_sections
from api_client_models import ApiAnalysisResult
from demo_scenarios import DEMO_SCENARIOS
from commercial_agent import CommercialAgent
from rag_models import RetrievalResult
from risk_agent import RiskAgent
from supervisor import Supervisor
from tests.test_commercial_agent import contract_matches, EvidenceModel
from tests.test_commercial_comparison import both_contracts, COMPARISON
from tests.test_supervisor import REFERENCE

APP = str(Path(__file__).resolve().parents[1] / 'app.py')
RISK_QUERY = 'Demanda 4,8 GWh. Tenemos 4,3 GWh aprovisionados. Precio spot 42 EUR/MWh. Escenario +10%.'


class ExecutionUXTests(unittest.TestCase):
    def make_view(self, operation='A', status='completed'):
        recorder = PerformanceRecorder(operation, 'deterministic', 'none', 'risk_agent')
        result = RiskAgent(recorder=recorder).run(RISK_QUERY)
        recorder.finish(status)
        return ExecutionView.capture('RiskAgent', result, {'interpretation':'deterministic'},
                                     recorder.snapshot(), result.content, result.tool_executions)

    def test_snapshots_and_domain_results_are_copied(self):
        recorder = PerformanceRecorder('A', 'test', 'test', 'risk_agent')
        result = RiskAgent(recorder=recorder).run(RISK_QUERY)
        config = {'nested': {'model':'original'}}
        view = ExecutionView.capture('RiskAgent', result, config, recorder.snapshot())
        result.warnings.append('later mutation')
        config['nested']['model'] = 'changed'
        recorder.record_stage('unrelated')
        self.assertFalse(view.result.warnings)
        self.assertEqual(view.configuration['nested']['model'], 'original')
        self.assertNotIn('unrelated', [e.stage for e in view.snapshot.events])

    def test_no_fallback_to_unrelated_operation(self):
        a, b = self.make_view('A'), self.make_view('B')
        executions = {'A': a, 'B': b}
        self.assertIs(find_execution(executions, 'A'), a)
        self.assertIsNone(find_execution(executions, 'missing'))
        self.assertIn('ID: A', clipboard_payloads(a)['diagnostics'])
        self.assertNotIn('ID: B', clipboard_payloads(a)['all'])

    def test_partial_and_needs_input_clipboards_and_redaction(self):
        for status in ('partial','needs_input'):
            view = replace(self.make_view(status=status), business_text='Respuesta. api_key=sk-123456789abcdef')
            payloads = clipboard_payloads(view)
            for text in payloads.values():
                self.assertNotIn('sk-123456789abcdef', text)
            self.assertIn('ID: A', payloads['all'])

    def test_commercial_business_text_has_values_and_friendly_sources(self):
        rag = Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'fixture')
        result = CommercialAgent(None, rag, interpretation_mode='deterministic').run(REFERENCE)
        text = commercial_text(result)
        for expected in ('0.2', 'Spot + 4', 'contrato_hospital_costa_sur.txt', 'Flexibilidad'):
            self.assertIn(expected, text)
        for forbidden in ('contractual_excess_gwh', 'tool_executions', 'source_number', 'chunk_id', '{', '3cd65c406a8a3ef5'):
            self.assertNotIn(forbidden, text)
        self.assertNotIn(contract_matches()[0].chunk.text, text)

    def test_supervisor_reuses_business_text_and_preserves_distinction(self):
        rag = Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'fixture')
        result = Supervisor(rag_factory=lambda r: rag).run(REFERENCE)
        text = supervisor_text(result)
        self.assertIn('Resumen ejecutivo', text)
        self.assertIn('Métricas clave', text)
        self.assertIn('Implicación integrada', text)
        self.assertIn('0.2', text)
        self.assertIn('0.5', text)
        self.assertIn('21,000', text)
        self.assertIn('41,160', text)
        self.assertNotIn('contractual_excess_gwh', text)
        self.assertNotIn('{', text)

    def test_executive_projection_summary_and_provenance_are_business_meaningful(self):
        rag = Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'fixture')
        result = Supervisor(rag_factory=lambda r: rag).run(REFERENCE)
        projection = build_supervisor_executive_sections(result)
        self.assertIn('Exceso contractual de 0.2 GWh', projection.summary)
        self.assertIn('SHORT de 0.5 GWh', projection.summary)
        self.assertIn('21,000 EUR', projection.summary)
        self.assertIn('magnitudes distintas', projection.summary)
        categories = {(item.category, item.label, item.value) for item in projection.provenance}
        self.assertIn(('ENTRADA OPERATIVA', 'Previsión mensual', '4.8 GWh'), categories)
        self.assertIn(('ENTRADA OPERATIVA', 'Suministro aprovisionado', '4.3 GWh'), categories)
        self.assertIn(('ENTRADA OPERATIVA', 'Precio spot', '42 EUR/MWh'), categories)
        self.assertIn(('VALOR CALCULADO', 'Exposición spot', '21,000 EUR'), categories)
        self.assertNotIn(('VALOR CALCULADO', 'Previsión mensual', '4.8 GWh'), categories)
        self.assertEqual(sum(1 for item in projection.provenance if item.category == 'ENTRADA OPERATIVA' and item.value == '42 EUR/MWh'), 1)
        self.assertEqual(result.total_llm_calls, 0)

    def test_comparison_projection_exposes_document_scoped_facts(self):
        rag = Mock()
        rag.retrieve.return_value = RetrievalResult(both_contracts(), 'fixture')
        result = Supervisor(rag_factory=lambda r: rag).run(COMPARISON)
        projection = build_supervisor_executive_sections(result)
        self.assertNotEqual(projection.summary, 'Resultados de los especialistas seleccionados.')
        text = supervisor_text(result)
        for expected in ('Hospital Costa Sur', 'Industrias Mediterráneo', 'Spot + 4 EUR/MWh', 'Spot + 6 EUR/MWh'):
            self.assertIn(expected, text)
        hospital = next(c for c in result.specialist_results[0].result.comparison.contracts if c.customer == 'Hospital Costa Sur')
        industrias = next(c for c in result.specialist_results[0].result.comparison.contracts if c.customer == 'Industrias Mediterráneo')
        h = {fact.name: fact.value for fact in hospital.facts}
        i = {fact.name: fact.value for fact in industrias.facts}
        self.assertEqual(h['flexibility_percent'], 15)
        self.assertEqual(i['flexibility_percent'], 20)
        self.assertEqual(h['excess_surcharge_eur_mwh'], 4)
        self.assertEqual(i['excess_surcharge_eur_mwh'], 6)
        self.assertEqual(result.total_llm_calls, 0)
        self.assertEqual(rag.retrieve.call_count, 1)

    def test_business_entry_and_advanced_modes_load(self):
        with patch('llm_client.get_available_models', return_value=[]):
            app = AppTest.from_file(APP, default_timeout=20).run()
            modes = app.radio(key='selected_mode').options
            self.assertEqual(len(modes), 8)
            self.assertEqual(app.radio(key='selected_mode').value, 'Business')
            self.assertTrue(any('¿Qué quieres analizar?' in item.label for item in app.text_area))
            for mode in modes:
                app.radio(key='selected_mode').set_value(mode).run()
                self.assertFalse(app.exception, mode)
                if mode == 'ProcurementAgent':
                    self.assertEqual(app.radio(key='procurement_orchestration_mode').options,
                                     ['Deterministic','Planner Agent','ReAct Agent'])
                    self.assertTrue(any('Historial del benchmark' in item.value for item in app.subheader))
                if mode == 'CommercialAgent':
                    self.assertEqual(app.radio(key='commercial_interpretation').value, 'deterministic')
                if mode == 'RiskAgent':
                    self.assertFalse(app.checkbox(key='risk_use_llm').value)

    def test_business_demos_populate_editable_request_without_execution(self):
        with patch('llm_client.get_available_models', return_value=[]):
            app = AppTest.from_file(APP, default_timeout=20).run()
            self.assertEqual(len(DEMO_SCENARIOS), 4)
            self.assertEqual(len({demo.key for demo in DEMO_SCENARIOS}), 4)
            for demo in DEMO_SCENARIOS:
                self.assertTrue(demo.title and demo.description and demo.request)
                app.button(key=f'demo_{demo.key}').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.text_area(key='business_request').value, demo.request)
                self.assertIsNone(app.session_state['supervisor_operation_id'])

    def test_business_api_render_path_wires_copy_all(self):
        copied = []
        with patch('llm_client.get_available_models', return_value=[]), patch(
            'clipboard_ui.render_clipboard_button', side_effect=lambda text, label, **kw: copied.append((label, text))):
            app = AppTest.from_file(APP, default_timeout=20).run()
            payload = {
                'operation_id': 'api-op', 'status': 'completed', 'summary': 'SHORT 25 GWh',
                'metrics': [], 'explanations': [], 'evidence': [], 'provenance': [], 'warnings': [],
                'routing': {'selected_agents': ['ProcurementAgent'], 'skipped_agents': ['CommercialAgent', 'RiskAgent'],
                            'method': 'deterministic', 'reasons': []}, 'specialists': [],
                'diagnostics': {'llm_calls': 0, 'rag_calls': 0, 'tool_calls': 2},
            }
            result = ApiAnalysisResult.model_validate(payload)
            recorder = PerformanceRecorder('api-op', 'api', 'remote', 'business_api')
            recorder.finish('completed')
            view = ExecutionView.capture('Business', result, {}, recorder.snapshot(), result.summary)
            app.session_state['business_api_result'] = payload
            app.session_state['execution_views'] = {'api-op': view}
            app.session_state['execution_mode_ids']['Multi-Agent Supervisor'] = 'api-op'
            app.run()
            self.assertTrue(any(label == 'Copiar todo' for label, _ in copied))

    def test_business_api_renders_recommendation(self):
        with patch('llm_client.get_available_models', return_value=[]):
            app = AppTest.from_file(APP, default_timeout=20).run()
            payload = {
                'operation_id': 'api-recommendation', 'status': 'completed', 'summary': 'Resumen',
                'metrics': [], 'explanations': [], 'evidence': [], 'provenance': [], 'warnings': [],
                'routing': {'selected_agents': ['CommercialAgent', 'ProcurementAgent'], 'skipped_agents': ['RiskAgent'],
                            'method': 'deterministic', 'reasons': []}, 'specialists': [],
                'diagnostics': {'llm_calls': 0, 'rag_calls': 1, 'tool_calls': 3},
                'recommendation': {
                    'action': 'Cubrir el SHORT operativo.', 'is_complete': True,
                    'rationale': ['Son magnitudes diferentes.'],
                    'contractual_implication': 'Exceso contractual: 0.2 GWh.',
                    'operational_implication': 'SHORT: 0.5 GWh; exposición: 21000 EUR.',
                    'risk_implication': 'Con un aumento del spot del 20%, la exposición pasa de 21000 a 25200 EUR (+4200 EUR).', 'supporting_metrics': [], 'warnings': [],
                    'actions': [
                        {'category': 'operational', 'action': 'Revisar la cobertura del SHORT operativo.',
                         'rationale': 'SHORT estructurado.', 'supporting_metrics': [['SHORT operativo', '0.5 GWh']]},
                        {'category': 'contractual', 'action': 'Revisar la implicación contractual del exceso.',
                         'rationale': None, 'supporting_metrics': [['Exceso contractual', '0.2 GWh']]},
                    ],
                    'decision_plan': {
                        'is_complete': True, 'warnings': [], 'steps': [{
                            'id': 'operational-short', 'category': 'operational',
                            'action': 'Cubrir el SHORT operativo.', 'rationale': 'No duplicar',
                            'supporting_metrics': [['SHORT', '0.5 GWh']],
                            'horizon': 'current_period', 'decision_state': 'review_required',
                            'depends_on': [], 'missing_information': [],
                            'source_action_id': 'operational-short', 'source_agent': 'ProcurementAgent',
                        }],
                    },
                },
            }
            recorder = PerformanceRecorder('api-recommendation', 'api', 'remote', 'business_api')
            recorder.finish('completed')
            view = ExecutionView.capture('Business', ApiAnalysisResult.model_validate(payload), {}, recorder.snapshot(), 'Resumen')
            app.session_state['business_api_result'] = payload
            app.session_state['execution_views'] = {'api-recommendation': view}
            app.session_state['execution_mode_ids']['Multi-Agent Supervisor'] = 'api-recommendation'
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any('Cubrir el SHORT operativo' in item.value for item in app.markdown))
            self.assertTrue(any('25200' in item.value for item in app.markdown))
            self.assertTrue(any('ACCIONES RECOMENDADAS' in item.value for item in app.markdown))
            self.assertTrue(any('0.5 GWh' in item.value for item in app.markdown))
            self.assertTrue(any('PLAN DE DECISIÓN' in item.value for item in app.markdown))
            self.assertTrue(any('Periodo actual · Operativa' in item.value for item in app.markdown))

    def test_mode_switch_indexing_and_clipboard_keep_visible_operation(self):
        copied = []
        with patch('llm_client.get_available_models', return_value=[]), patch(
            'clipboard_ui.render_clipboard_button', side_effect=lambda text, label, **kw: copied.append((label,text))):
            app = AppTest.from_file(APP, default_timeout=20).run()
            app.radio(key='selected_mode').set_value('RiskAgent').run()
            app.text_area(key='risk_request').set_value(RISK_QUERY).run()
            app.button(key='risk_start').click().run()
            app.run()
            operation = app.session_state['risk_operation_id']
            app.radio(key='selected_mode').set_value('Multi-Agent Supervisor').run()
            app.text_area(key='supervisor_request').set_value('Demanda 100 GWh. Tenemos 100 GWh aprovisionados. Posición.').run()
            app.button(key='supervisor_start').click().run()
            app.run()
            self.assertNotEqual(operation, app.session_state['supervisor_operation_id'])
            app.radio(key='selected_mode').set_value('RiskAgent').run()
            index = PerformanceRecorder('INDEX', 'fixture', 'fixture', 'rag_index')
            index.finish('completed')
            app.session_state['pipeline_recorder'] = index
            copied.clear()
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == 'Operación: '+operation for item in app.caption))
            response_diagnostics = [text for label,text in copied if label == 'Copiar todo']
            self.assertTrue(response_diagnostics)
            self.assertTrue(all('ID: '+operation in text and 'ID: INDEX' not in text for text in response_diagnostics))

    def test_commercial_ui_deterministic_never_creates_provider(self):
        store = SimpleNamespace(document_count=1, chunk_count=1)
        with patch('vector_store.LocalVectorStore.from_environment', return_value=store), \
             patch('embeddings.LMStudioEmbeddingProvider'), \
             patch('rag_service.RAGService.retrieve', return_value=RetrievalResult(contract_matches(),'fixture')), \
             patch('llm_client.get_available_models', return_value=[]), \
             patch('llm_client.get_llm_provider', side_effect=AssertionError('LLM not allowed')) as provider:
            app = AppTest.from_file(APP, default_timeout=20).run()
            app.radio(key='selected_mode').set_value('CommercialAgent').run()
            app.text_area(key='commercial_request').set_value(REFERENCE).run()
            app.button(key='commercial_start').click().run()
            app.run()
            self.assertFalse(app.exception)
            result = app.session_state['commercial_result']
            self.assertEqual(result['interpretation_mode'], 'deterministic')
            self.assertEqual(result['status'], 'completed')
            provider.assert_not_called()
            view = app.session_state['execution_views'][app.session_state['commercial_operation_id']]
            self.assertEqual(view.configuration['interpretation'], 'deterministic')
            self.assertNotIn('contractual_excess_gwh', clipboard_payloads(view)['response'])

    def test_standalone_llm_interpretation_still_works(self):
        model, rag = EvidenceModel(), Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'fixture')
        result = CommercialAgent(model, rag, interpretation_mode='llm').run(REFERENCE)
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.interpretation_mode, 'llm')


if __name__ == '__main__':
    unittest.main()
