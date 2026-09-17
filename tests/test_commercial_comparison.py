import json
import random
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from commercial_agent import CommercialAgent
from commercial_models import CommercialAgentResult
from commercial_comparison import comparison_rows
from business_output import commercial_text
from diagnostics import PerformanceRecorder
from rag_models import DocumentChunk, RetrievedChunk, RetrievalResult
from tests.test_commercial_agent import EvidenceModel, QUESTION, contract_matches


COMPARISON = ('¿Qué diferencias existen entre las condiciones de flexibilidad, take-or-pay '
              'y consumo excedentario del Hospital Costa Sur y de Industrias Mediterráneo?')


def both_contracts():
    rows = json.loads((Path(__file__).parent / 'fixtures/industrias_mediterraneo.json').read_text(encoding='utf-8'))
    matches = contract_matches() + [RetrievedChunk(DocumentChunk(**row), .9) for row in rows]
    random.Random(42).shuffle(matches)
    return matches


class CommercialComparisonTests(unittest.TestCase):
    def run_case(self, query=COMPARISON, matches=None, mode='deterministic'):
        self.rag = Mock()
        self.rag.retrieve.return_value = RetrievalResult(both_contracts() if matches is None else matches, 'fixture')
        self.model = EvidenceModel()
        self.recorder = PerformanceRecorder('comparison', 'test', 'test', 'commercial_agent')
        result = CommercialAgent(self.model, self.rag, self.recorder, interpretation_mode=mode).run(query, 30)
        return result

    def test_reference_comparison_values_and_provenance(self):
        result = self.run_case()
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(self.model.calls, 0)
        self.rag.retrieve.assert_called_once()
        expected = {'Hospital Costa Sur': [4, 15, 3.4, 4.6, 48, 85, 40.8, 4],
                    'Industrias Mediterráneo': [6, 20, 4.8, 7.2, 72, 80, 57.6, 6]}
        keys = ['reference_volume_gwh', 'flexibility_percent', 'monthly_min_gwh', 'monthly_max_gwh',
                'annual_volume_gwh', 'take_or_pay_percent', 'minimum_annual_gwh', 'excess_surcharge_eur_mwh']
        for contract in result.comparison.contracts:
            facts = {f.name: f.value for f in contract.facts}
            self.assertEqual([facts[k] for k in keys], expected[contract.customer])
            for fact in contract.facts:
                self.assertEqual(fact.source.document_name, contract.document_name)
        self.assertFalse(result.calculations)
        self.assertEqual(CommercialAgentResult.model_validate_json(result.model_dump_json()), result)

    def test_skip_is_expected_and_not_degradation(self):
        result = self.run_case(QUESTION, contract_matches())
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(self.model.calls, 0)
        event = next(e for e in self.recorder.snapshot().events if e.stage == 'commercial_interpretation')
        self.assertEqual(event.status.value, 'skipped')
        self.assertEqual(event.metadata['skip_kind'], 'expected_optional')
        self.assertEqual(result.calculations[0].result['contractual_excess_gwh'], .2)

    def test_standalone_llm_preserves_warning_status(self):
        result = self.run_case(QUESTION, contract_matches(), mode='llm')
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(result.status.value, 'partial')

    def test_required_margin_failure_remains_partial(self):
        result = self.run_case(QUESTION + ' Calcula el margen.', contract_matches())
        self.assertEqual(result.status.value, 'partial')

    def test_requested_absolute_price_is_required(self):
        result = self.run_case(QUESTION + ' Calcula el precio absoluto del exceso.', contract_matches())
        self.assertEqual(result.status.value, 'partial')

    def test_missing_cell_is_partial_without_borrowing_other_contract(self):
        matches = [m for m in both_contracts() if not (m.chunk.document_id == 'mediterraneo-2026'
                   and 'take-or-pay' in m.chunk.text.lower())]
        result = self.run_case(matches=matches)
        self.assertEqual(result.status.value, 'partial')
        row = next(r for r in comparison_rows(result) if r['Condición'] == 'Take-or-pay')
        self.assertEqual(row['Hospital Costa Sur'], '85 %')
        self.assertIn('No encontrado', row['Industrias Mediterráneo'])

    def test_values_come_from_evidence(self):
        matches = [replace(m, chunk=replace(m.chunk, text=m.chunk.text.replace('20%', '25%')))
                   if m.chunk.document_id == 'mediterraneo-2026' else m for m in both_contracts()]
        result = self.run_case(matches=matches)
        industry = next(c for c in result.comparison.contracts if c.customer == 'Industrias Mediterráneo')
        self.assertEqual(next(f.value for f in industry.facts if f.name == 'flexibility_percent'), 25)

    def test_unnamed_comparison_preserves_ambiguity(self):
        result = self.run_case('Compara estos contratos')
        self.assertEqual(result.status.value, 'needs_input')
        self.assertIsNone(result.comparison)

    def test_multiple_names_without_comparison_are_not_merged(self):
        result = self.run_case('Analiza Hospital Costa Sur e Industrias Mediterráneo')
        self.assertEqual(result.status.value, 'needs_input')

    def test_unresolved_second_contract_does_not_become_single_analysis(self):
        result = self.run_case(matches=contract_matches())
        self.assertEqual(result.status.value, 'needs_input')
        self.assertIsNone(result.comparison)

    def test_single_customer_still_uses_analytical_path(self):
        result = self.run_case(QUESTION)
        self.assertIsNone(result.comparison)
        self.assertEqual(result.calculations[0].result['contractual_excess_gwh'], .2)

    def test_business_text_and_ui_share_comparison_and_sources(self):
        from streamlit.testing.v1 import AppTest
        result = self.run_case()
        text = commercial_text(result)
        for name in ['Hospital Costa Sur', 'Industrias Mediterráneo', 'contrato_hospital_costa_sur', 'contrato_industrias_mediterraneo']:
            self.assertIn(name, text)
        self.assertNotIn('document_id', text)
        from execution_view import ExecutionView, clipboard_payloads
        self.recorder.finish(result.status.value)
        view = ExecutionView.capture('CommercialAgent', result, {'interpretation': 'deterministic'},
                                     self.recorder.snapshot(), text, result.tool_executions)
        payloads = clipboard_payloads(view)
        self.assertIn(text, payloads['all'])
        self.assertIn('ID: comparison', payloads['diagnostics'])
        def page(data):
            from commercial_models import CommercialAgentResult
            from commercial_ui import render_commercial_result
            render_commercial_result(CommercialAgentResult.model_validate(data))
        app = AppTest.from_function(page, args=(result.model_dump(mode='json'),)).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 8)
