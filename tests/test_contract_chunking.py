import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from chunking import chunk_document, _looks_like_heading
from document_ingestion import parse_document
from tests.test_excess_price_formula import HOSPITAL_CLAUSE, INDUSTRIAS_CLAUSE


class ContractChunkingTests(unittest.TestCase):
    def test_fresh_persisted_index_to_final_comparison(self):
        from rag_service import RAGService
        from vector_store import LocalVectorStore
        from commercial_agent import CommercialAgent
        from commercial_models import CommercialAgentResult
        from tests.test_commercial_comparison import COMPARISON
        class Embeddings:
            model = 'deterministic-test-embedding'
            def embed_documents(self, texts):
                return [self.embed_query(text) for text in texts]
            def embed_query(self, text):
                # Offline semantic ranking; no document-specific identities or values.
                import re
                return [float(bool(re.search(p,text,re.I))) for p in
                        ('flexibilidad', 'take-or-pay', 'exce', 'volumen', 'precio')] + [.1]
        with TemporaryDirectory() as directory:
            embeddings = Embeddings()
            service = RAGService(embeddings, LocalVectorStore(directory, embeddings.model))
            fixtures = Path(__file__).parent/'fixtures'
            service.ingest([('contrato_hospital_costa_sur.txt',(fixtures/'hospital_source.txt').read_bytes()),
                            ('contrato_industrias_mediterraneo_2026.txt',(fixtures/'industrias_source.txt').read_bytes())])
            reloaded = RAGService(embeddings, LocalVectorStore(directory, embeddings.model))
            model = Mock()
            result = CommercialAgent(model, reloaded, interpretation_mode='deterministic').run(COMPARISON)
            model.interpret.assert_not_called()
            result = CommercialAgentResult.model_validate_json(result.model_dump_json())
            self.assertEqual(result.status.value,'completed',result.warnings)
            keys = ['reference_volume_gwh','flexibility_percent','monthly_min_gwh','monthly_max_gwh',
                    'annual_volume_gwh','take_or_pay_percent','minimum_annual_gwh','excess_surcharge_eur_mwh']
            for contract in result.comparison.contracts:
                expected = [4,15,3.4,4.6,48,85,40.8,4] if 'hospital' in contract.document_name else [6,20,4.8,7.2,72,80,57.6,6]
                facts = {f.name:f for f in contract.facts}
                self.assertEqual([facts[k].value for k in keys],expected)
                self.assertTrue(all(f.source.document_name==contract.document_name for f in facts.values()))

    def test_previous_chunker_index_is_rejected(self):
        from vector_store import LocalVectorStore, VectorStoreError
        with TemporaryDirectory() as directory:
            store = LocalVectorStore(directory,'test')
            store.save()
            manifest_path = Path(directory)/'manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['chunker_version'] = 1
            manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
            with self.assertRaises(VectorStoreError):
                LocalVectorStore(directory,'test')

    def test_real_clauses_retain_formula_under_numbered_section(self):
        for clause in (HOSPITAL_CLAUSE, INDUSTRIAS_CLAUSE):
            with self.subTest(clause=clause.splitlines()[0]):
                chunks = chunk_document(parse_document('contract.txt', clause.encode('utf-8')))
                section = next(c for c in chunks if c.section == clause.splitlines()[0])
                self.assertIn('Precio Spot +', section.text)
                self.assertIn('El precio aplicable', section.text)
                self.assertEqual(len(chunks), 1)

    def test_structural_headings_and_prose(self):
        for heading in ('6. EXCESOS DE CONSUMO', '6. CONSUMO POR ENCIMA DE LA FLEXIBILIDAD',
                        '2.1 Condiciones generales', 'ANEXO:', 'CONDICIONES GENERALES'):
            self.assertTrue(_looks_like_heading(heading), heading)
        for prose in ('El resultado aplicable será:', 'Los siguientes conceptos se incluyen:',
                      'El precio aplicable a este volumen será:'):
            self.assertFalse(_looks_like_heading(prose), prose)


