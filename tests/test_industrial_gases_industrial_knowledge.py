import tempfile
import unittest

from embeddings import EmbeddingProvider
from industrial_gases.industrial_knowledge import IndustrialKnowledgeService
from industrial_gases.portfolio import SupplyPortfolioService
from industrial_gases.portfolio_ui import _canonical_portfolio_request
from rag_service import RAGService
from vector_store import LocalVectorStore


class KnowledgeEmbeddings(EmbeddingProvider):
    model = "industrial-knowledge-tests"
    terms = ("oxygen", "medical", "hospital", "supply", "contract", "procedure",
             "installation", "co2", "nitrogen", "n2", "policy", "inventory",
             "delivery", "stock", "nm3", "beverage", "carbonation", "inerting",
             "specification", "each", "structured", "projection", "source", "data")

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        lowered = text.casefold()
        vector = [float(lowered.count(term)) for term in self.terms]
        # An unmatched query is orthogonal to all demo documents, allowing the
        # existing retrieval boundary to represent no applicable knowledge.
        return vector if any(vector) else [0.0] * (len(self.terms) - 1) + [-1.0]


class IndustrialKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.embeddings = KnowledgeEmbeddings()
        store = LocalVectorStore(self.temp.name, self.embeddings.model)
        self.knowledge = IndustrialKnowledgeService(RAGService(self.embeddings, store))
        self.portfolio = SupplyPortfolioService().evaluate(_canonical_portfolio_request())
        self.items = {item.item_id: item for item in self.portfolio.items}

    def test_demo_corpus_contains_required_document_types_and_global_policy(self):
        self.knowledge.ensure_demo_corpus()
        docs = self.knowledge.rag_service.store.documents
        types = {document.metadata.get("document_type") for document in docs}
        self.assertEqual(len(docs), 8)
        self.assertTrue({"supply_contract", "operating_procedure",
                         "installation_specification", "supply_policy"} <= types)
        self.assertEqual(sum(document.metadata.get("scope") == "global" for document in docs), 1)
        self.assertTrue(all("DEMO DOCUMENT" in chunk.chunk.text
                            for chunk in self.knowledge.rag_service.store._items))

    def test_o2_query_cannot_retrieve_adversarial_co2_or_n2_even_when_query_matches_them(self):
        identity = {
            "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
            "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen",
            "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        }
        status, sources = self.knowledge.search(
            identity=identity,
            query="N2 nitrogen Nm3 Alimentos del Sur CO2 installation specification contract supply",
            top_k=50,
        )
        self.assertEqual(status, "retrieved")
        self.assertTrue(sources)
        self.assertTrue(all(
            source.chunk.metadata.get("scope") == "global"
            or source.chunk.metadata.get("gas_product_id") == "medical-oxygen"
            for source in sources
        ))
        self.assertFalse(any(source.chunk.metadata.get("customer_id") == "alimentos-del-sur"
                             for source in sources))

    def test_co2_and_n2_are_scoped_separately_and_n2_preserves_nm3(self):
        co2_status, co2_sources = self.knowledge.search(
            identity={"customer_id": "alimentos-del-sur", "site_id": "malaga-production-plant",
                      "application_id": "beverage-carbonation", "gas_product_id": "co2",
                      "installation_id": "co2-bulk-installation"},
            query="CO2 beverage carbonation supply contract N2 nitrogen Nm3 inerting operating procedure installation specification",
            top_k=50,
        )
        n2_status, n2_sources = self.knowledge.search(
            identity={"customer_id": "alimentos-del-sur", "site_id": "malaga-production-plant",
                      "application_id": "modified-atmosphere-inerting", "gas_product_id": "n2",
                      "installation_id": "n2-bulk-installation"},
            query="CO2 beverage carbonation supply contract N2 nitrogen Nm3 inerting operating procedure installation specification",
            top_k=50,
        )
        self.assertEqual((co2_status, n2_status), ("retrieved", "retrieved"))
        self.assertTrue(all(source.chunk.metadata.get("gas_product_id") in {"co2", None}
                            for source in co2_sources))
        self.assertTrue(all(source.chunk.metadata.get("gas_product_id") in {"n2", None}
                            for source in n2_sources))
        n2_spec = next(source for source in n2_sources
                        if source.chunk.metadata.get("document_type") == "installation_specification")
        self.assertIn("Nm3", n2_spec.chunk.text)
        self.assertFalse(any(source.chunk.metadata.get("installation_id") == "n2-bulk-installation"
                             for source in co2_sources))

    def test_explicit_global_policy_is_eligible_for_each_complete_identity(self):
        for item_id in ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2",
                        "alimentos-sur-malaga-n2"):
            item = self.items[item_id]
            request = item.request
            result = item.result
            identity = {"customer_id": result.customer_id, "site_id": result.site_id,
                        "application_id": result.application_id,
                        "gas_product_id": result.gas_product_id,
                        "installation_id": result.installation_id}
            status, sources = self.knowledge.search(identity=identity, query="global supply policy inventory stock")
            self.assertEqual(status, "retrieved")
            self.assertTrue(any(source.chunk.metadata.get("scope") == "global" for source in sources))

    def test_unknown_topic_can_be_reported_as_no_applicable_knowledge(self):
        identity = {"customer_id": "unknown", "site_id": "unknown-site",
                    "application_id": "unknown-app", "gas_product_id": "unknown-gas",
                    "installation_id": "unknown-installation"}
        status, sources = self.knowledge.search(identity=identity, query="astrophysics quasars")
        self.assertEqual(status, "no_applicable_knowledge")
        self.assertEqual(sources, ())

    def test_incomplete_identity_only_allows_explicit_global_document(self):
        status, sources = self.knowledge.search(
            identity={"customer_id": "hospital-costa-sur", "site_id": None,
                      "application_id": None, "gas_product_id": None,
                      "installation_id": None},
            query="installation structured projection source data safety stock policy",
            top_k=50,
        )
        self.assertEqual(status, "retrieved")
        self.assertTrue(sources)
        self.assertTrue(all(source.chunk.metadata.get("scope") == "global" for source in sources))


if __name__ == "__main__":
    unittest.main()
