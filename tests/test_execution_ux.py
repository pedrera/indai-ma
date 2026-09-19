import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest
from diagnostics import PerformanceRecorder
from execution_view import ExecutionView, find_execution, clipboard_payloads
from business_output import commercial_text, supervisor_text
from commercial_agent import CommercialAgent
from rag_models import RetrievalResult
from risk_agent import RiskAgent
from supervisor import Supervisor
from tests.test_commercial_agent import contract_matches, EvidenceModel
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
        self.assertIn(commercial_text(result.specialist_results[0].result), text)
        self.assertIn('0.2', text)
        self.assertIn('0.5', text)
        self.assertIn('21,000', text)
        self.assertIn('41,160', text)
        self.assertNotIn('contractual_excess_gwh', text)
        self.assertNotIn('{', text)
        self.assertEqual(result.total_llm_calls, 0)

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
