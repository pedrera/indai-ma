import unittest
from dataclasses import replace

from contractual_analysis import extract_contractual_volume_terms
from tests import test_commercial_comparison as comparison


HOSPITAL_CLAUSE = """6. EXCESOS DE CONSUMO

Cuando el consumo mensual supere el límite superior de flexibilidad
establecido en este contrato, el volumen excedentario será considerado
consumo adicional.

El proveedor podrá adquirir dicho volumen en el mercado spot.

El precio aplicable al volumen excedentario será:

Precio Spot + 4 €/MWh.

El recargo de 4 €/MWh corresponde a costes de gestión, balance y riesgo
asumidos por el proveedor."""

INDUSTRIAS_CLAUSE = """6. CONSUMO POR ENCIMA DE LA FLEXIBILIDAD

Cuando el consumo mensual supere el límite superior de flexibilidad de
7,2 GWh, el proveedor podrá suministrar el volumen adicional sujeto a
disponibilidad.

El volumen consumido por encima de dicho límite será considerado volumen
excedentario contractual.

El precio aplicable a este volumen será:

Precio Spot + 6 EUR/MWh.

El precio Spot utilizado será el precio de referencia del mercado definido
para el día de suministro."""


class ExcessPriceFormulaTests(unittest.TestCase):
    def test_streamlit_multicontract_final_result_with_complete_clauses(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from rag_models import RetrievalResult
        clauses = {'contrato_hospital_costa_sur.txt': HOSPITAL_CLAUSE,
                   'contrato_industrias_mediterraneo_2026.txt': INDUSTRIAS_CLAUSE}
        matches = [replace(m, chunk=replace(m.chunk, text=clauses[m.chunk.document_name]))
                   if m.chunk.section.startswith('6.') else m
                   for m in comparison.both_contracts() if 'Spot +' not in m.chunk.text]
        with patch('vector_store.LocalVectorStore.from_environment', return_value=SimpleNamespace(document_count=2, chunk_count=len(matches))), \
             patch('embeddings.LMStudioEmbeddingProvider'), \
             patch('rag_service.RAGService.retrieve', return_value=RetrievalResult(matches, 'complete clause evidence')), \
             patch('llm_client.get_available_models', return_value=[]), \
             patch('llm_client.get_llm_provider', side_effect=AssertionError('Unexpected LLM generation')) as provider:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=20).run()
            app.radio(key='selected_mode').set_value('CommercialAgent').run()
            app.text_area(key='commercial_request').set_value(comparison.COMPARISON).run()
            app.button(key='commercial_start').click().run()
            app.run()
            self.assertFalse(app.exception)
            result = app.session_state['commercial_result']
            self.assertEqual(result['status'], 'completed')
            for contract in result['comparison']['contracts']:
                expected = 4 if contract['document_name'] == 'contrato_hospital_costa_sur.txt' else 6
                fact = next(f for f in contract['facts'] if f['name'] == 'excess_surcharge_eur_mwh')
                self.assertEqual(fact['value'], expected)
                self.assertEqual(fact['source']['document_name'], contract['document_name'])
            provider.assert_not_called()

    def test_exact_user_clauses_through_structured_comparison_and_renderer(self):
        from unittest.mock import patch
        from commercial_models import CommercialAgentResult
        from business_output import commercial_text
        from clipboard_text import build_diagnostics_clipboard_text
        clauses = {'contrato_hospital_costa_sur.txt': HOSPITAL_CLAUSE,
                   'contrato_industrias_mediterraneo_2026.txt': INDUSTRIAS_CLAUSE}
        matches = []
        for match in comparison.both_contracts():
            # Remove original pricing chunks so ONLY the supplied wording can supply the formula.
            if 'Spot +' in match.chunk.text:
                continue
            if match.chunk.section.startswith('6.'):
                match = replace(match, chunk=replace(match.chunk, text=clauses[match.chunk.document_name]))
            matches.append(match)
        runner = comparison.CommercialComparisonTests()
        with patch('commercial_comparison.extract_contractual_volume_terms',
                   wraps=extract_contractual_volume_terms) as extractor:
            result = runner.run_case(matches=matches)
        self.assertEqual(extractor.call_count, 2)
        inputs = [c['text'] for call in extractor.call_args_list for c in call.args[0]]
        self.assertIn(HOSPITAL_CLAUSE, inputs)
        self.assertIn(INDUSTRIAS_CLAUSE, inputs)
        result = CommercialAgentResult.model_validate_json(result.model_dump_json())
        text = commercial_text(result)
        for contract in result.comparison.contracts:
            expected = 4 if contract.document_name == 'contrato_hospital_costa_sur.txt' else 6
            fact = next(f for f in contract.facts if f.name == 'excess_surcharge_eur_mwh')
            self.assertEqual(fact.value, expected)
            self.assertEqual(fact.source.document_name, contract.document_name)
            self.assertEqual(fact.evidence, clauses[contract.document_name])
            self.assertIn(f'Spot + {expected} EUR/MWh', text)
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(runner.model.calls, 0)
        runner.recorder.finish('completed')
        event = next(e for e in runner.recorder.snapshot().events if e.stage == 'commercial_interpretation')
        self.assertEqual(event.status.value, 'skipped')
        self.assertEqual(event.metadata['skip_kind'], 'expected_optional')
        self.assertNotIn('Errors / warnings', build_diagnostics_clipboard_text(runner.recorder.snapshot()))

    def test_requested_formula_variants(self):
        for text, expected in [
            ('Spot + 4 EUR/MWh', 4), ('Precio Spot + 4 EUR/MWh', 4),
            ('Spot + 6 EUR/MWh', 6), ('Precio Spot + 6 EUR/MWh', 6),
            ('Spot + 4,5 EUR/MWh', 4.5),
            ('precio\n SPOT\t+ 7.25 eur / mwh', 7.25),
            ('Spot\u00a0+\u00a08,75\u00a0€/MWh', 8.75),
        ]:
            with self.subTest(text=text):
                terms = extract_contractual_volume_terms([{'chunk_id': 'evidence', 'text': text}])
                self.assertEqual(terms.excess_surcharge_eur_mwh, expected)
                self.assertEqual(terms.field_sources['excess_surcharge_eur_mwh'], 'evidence')

    def test_complete_comparison_without_current_spot_price(self):
        runner = comparison.CommercialComparisonTests()
        result = runner.run_case()
        self.assertEqual(result.status.value, 'completed')
        self.assertEqual(runner.model.calls, 0)
        runner.rag.retrieve.assert_called_once()
        for contract in result.comparison.contracts:
            fact = next(f for f in contract.facts if f.name == 'excess_surcharge_eur_mwh')
            self.assertEqual(fact.source.document_name, contract.document_name)
            expected = 4 if contract.customer == 'Hospital Costa Sur' else 6
            self.assertEqual(fact.value, expected)
            self.assertIn(f'Spot + {expected}', result.content)

    def test_section_heading_does_not_supply_missing_formula(self):
        runner = comparison.CommercialComparisonTests()
        matches = [replace(m, chunk=replace(m.chunk, text='6. EXCESOS DE CONSUMO\nEl proveedor podrá adquirir volumen en el mercado spot.'))
                   if 'Spot +' in m.chunk.text else m for m in comparison.both_contracts()]
        result = runner.run_case(matches=matches)
        self.assertEqual(result.status.value, 'partial')
        for contract in result.comparison.contracts:
            self.assertFalse(any(f.name == 'excess_surcharge_eur_mwh' for f in contract.facts))
