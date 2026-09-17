import json
import unittest
from pathlib import Path
from dataclasses import replace
from tests import test_commercial_comparison as comparison_tests
from tests.test_commercial_comparison import both_contracts
from rag_models import DocumentChunk, RetrievedChunk
from clipboard_text import build_diagnostics_clipboard_text


class ExtractionTests(unittest.TestCase):
    def run_matches(self, matches):
        runner = comparison_tests.CommercialComparisonTests()
        result = runner.run_case(matches=matches)
        self.assertEqual(runner.model.calls, 0)
        runner.rag.retrieve.assert_called_once()
        self.runner = runner
        return result

    def test_real_retrieved_evidence_and_warnings(self):
        rows = json.loads((Path(__file__).parent/'fixtures/commercial_retrieved_comparison.json').read_text(encoding='utf-8'))
        result = self.run_matches([RetrievedChunk(DocumentChunk(**r), .9) for r in rows])
        industry = next(c for c in result.comparison.contracts if 'industrias' in c.document_name)
        facts = {f.name:f for f in industry.facts}
        for key,value in [('monthly_min_gwh',4.8),('monthly_max_gwh',7.2),('annual_volume_gwh',72),('minimum_annual_gwh',57.6)]:
            self.assertEqual(facts[key].value,value)
            self.assertEqual(facts[key].origin,'extracted')
        self.assertEqual(result.status.value,'partial')
        self.assertEqual(len(result.warnings),2)
        self.assertTrue(all('Precio del exceso' in w for w in result.warnings))
        self.runner.recorder.finish('partial')
        text = build_diagnostics_clipboard_text(self.runner.recorder.snapshot())
        errors = text.split('Errors / warnings')[-1]
        self.assertNotIn('commercial_interpretation', errors)
        self.assertIn('Precio del exceso',errors)

    def test_decimal_variants(self):
        for punctuation in (',','.'):
            matches = [replace(m,chunk=replace(m.chunk,text=m.chunk.text.replace('entre ', 'entre:\n').replace(',',punctuation))) for m in both_contracts()]
            result = self.run_matches(matches)
            for c in result.comparison.contracts:
                facts={f.name:f for f in c.facts}
                self.assertEqual(facts['monthly_min_gwh'].origin,'extracted')
                self.assertEqual(facts['monthly_max_gwh'].origin,'extracted')

    def test_derived_values_and_no_cross_document_inputs(self):
        matches = both_contracts()
        # Use only supported inputs, with arbitrary values unrelated to reference customers.
        matches = [replace(m,chunk=replace(m.chunk,text=('Cliente: Alpha\nVolumen mensual de referencia de 10 GWh. Flexibilidad de ±10%. '
                   'Volumen anual contratado de 120 GWh. Take-or-pay 75%.' if i==0 else 'Cliente: Beta\nFlexibilidad de ±20%.')))
                   for i,m in enumerate([next(m for m in matches if 'hospital' in m.chunk.document_name),next(m for m in matches if 'industrias' in m.chunk.document_name)])]
        result = self.run_matches(matches)
        a,b=result.comparison.contracts
        facts={f.name:f for f in a.facts}
        for key,value in [('monthly_min_gwh',9),('monthly_max_gwh',11),('minimum_annual_gwh',90)]:
            self.assertEqual(facts[key].value,value)
            self.assertEqual(facts[key].origin,'deterministic_calculation')
            self.assertEqual(len(facts[key].input_sources),2)
            self.assertTrue(all(s.document_name==a.document_name for s in facts[key].input_sources))
        self.assertFalse(any(f.name in ('monthly_min_gwh','monthly_max_gwh','minimum_annual_gwh','annual_volume_gwh') for f in b.facts))

    def test_spot_formulas_currency_variants(self):
        for currency in ('EUR/MWh','eur / MWh','€/MWh'):
            result=self.run_matches([replace(m,chunk=replace(m.chunk,text=m.chunk.text.replace('€/MWh',currency))) for m in both_contracts()])
            self.assertEqual(result.status.value,'completed')
            self.assertEqual(sorted(f.value for c in result.comparison.contracts for f in c.facts if f.name=='excess_surcharge_eur_mwh'),[4,6])
