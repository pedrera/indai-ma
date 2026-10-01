from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import PerformanceRecorder
from embeddings import LMStudioEmbeddingProvider, LMStudioEmbeddingProviderPool
from industrial_gases.industrial_knowledge import lazy_demo_knowledge_service


class _RecordingProvider:
    def __init__(self, **configuration):
        self.configuration = configuration
        self.provider_instance_id = f"provider-{len(configuration)}"
        self.client_instance_id = f"client-{len(configuration)}"


class _CorpusEmbeddings:
    model = "pooled-corpus-fixture"
    terms = ("oxygen", "medical", "hospital", "supply", "contract", "delivery",
             "procedure", "installation", "co2", "nitrogen", "policy", "inventory",
             "stock", "structured", "projection", "source", "data")

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        lowered = text.casefold()
        vector = [float(lowered.count(term)) for term in self.terms]
        return vector if any(vector) else [0.0] * (len(self.terms) - 1) + [-1.0]


class EmbeddingProviderPoolTests(unittest.TestCase):
    def test_pool_is_lazy_and_reuses_only_exact_compatible_configuration(self):
        created = []

        def make_provider(**configuration):
            provider = _RecordingProvider(**configuration)
            created.append(provider)
            return provider

        pool = LMStudioEmbeddingProviderPool(provider_factory=make_provider)
        self.assertEqual(pool.provider_count, 0)

        first = pool.get_or_create(
            model="embed-a", base_url="http://localhost:1234/v1", api_key="key-a",
        )
        same = pool.get_or_create(
            model="embed-a", base_url="http://localhost:1234/v1", api_key="key-a",
        )
        self.assertIs(first, same)
        self.assertEqual(pool.provider_count, 1)
        self.assertEqual(len(created), 1)
        _, miss_metadata = pool.get_or_create_with_status(
            model="embed-status", base_url="http://localhost:1234/v1", api_key="key-a",
        )
        _, hit_metadata = pool.get_or_create_with_status(
            model="embed-status", base_url="http://localhost:1234/v1", api_key="key-a",
        )
        self.assertFalse(miss_metadata["provider_pool_hit"])
        self.assertTrue(hit_metadata["provider_pool_hit"])
        self.assertEqual(miss_metadata["provider_pool_key_fingerprint"],
                         hit_metadata["provider_pool_key_fingerprint"])
        self.assertEqual(miss_metadata["embedding_provider_instance_id"],
                         hit_metadata["embedding_provider_instance_id"])
        self.assertEqual(miss_metadata["embedding_client_instance_id"],
                         hit_metadata["embedding_client_instance_id"])

        incompatible = (
            {"model": "embed-b", "base_url": "http://localhost:1234/v1", "api_key": "key-a"},
            {"model": "embed-a", "base_url": "http://other-host:1234/v1", "api_key": "key-a"},
            {"model": "embed-a", "base_url": "http://localhost:1234/v1", "api_key": "key-b"},
        )
        for configuration in incompatible:
            self.assertIsNot(pool.get_or_create(**configuration), first)
        self.assertEqual(pool.provider_count, 5)
        self.assertNotIn("key-a", repr(pool._providers.keys()))

    def test_concurrent_first_access_constructs_exactly_one_compatible_provider(self):
        created = []
        create_lock = threading.Lock()

        def make_provider(**configuration):
            time.sleep(0.01)
            with create_lock:
                created.append(configuration)
            return _RecordingProvider(**configuration)

        pool = LMStudioEmbeddingProviderPool(provider_factory=make_provider)
        args = {"model": "embed", "base_url": "http://localhost:1234/v1", "api_key": "secret"}
        with ThreadPoolExecutor(max_workers=12) as executor:
            providers = list(executor.map(lambda _: pool.get_or_create(**args), range(24)))
        self.assertTrue(all(provider is providers[0] for provider in providers))
        self.assertEqual(len(created), 1)
        self.assertEqual(pool.provider_count, 1)

    def test_lazy_workspace_factory_reuses_provider_but_keeps_rag_services_operation_scoped(self):
        embeddings = _CorpusEmbeddings()

        class FakePool:
            def __init__(self):
                self.calls = 0

            def get_or_create(self, **_configuration):
                self.calls += 1
                return embeddings

            def get_or_create_with_status(self, **_configuration):
                provider = self.get_or_create(**_configuration)
                return provider, {
                    "provider_pool_hit": self.calls > 1,
                    "provider_pool_size": 1,
                    "provider_pool_key_fingerprint": "safe-pool-fingerprint",
                    "embedding_provider_instance_id": "provider-session-a",
                    "embedding_client_instance_id": "client-session-a",
                }

        pool = FakePool()
        with tempfile.TemporaryDirectory() as directory:
            recorder_one = PerformanceRecorder("workspace-op-1", "fixture", "model", "workspace")
            recorder_two = PerformanceRecorder("workspace-op-2", "fixture", "model", "workspace")
            create_first = lazy_demo_knowledge_service(
                pool, model=embeddings.model, base_url="http://unused", api_key="unused",
                recorder=recorder_one, root=directory,
            )
            create_second = lazy_demo_knowledge_service(
                pool, model=embeddings.model, base_url="http://unused", api_key="unused",
                recorder=recorder_two, root=directory,
            )
            self.assertEqual(pool.calls, 0, "constructing the operation must stay lazy")
            first, second = create_first(), create_second()
            self.assertEqual(pool.calls, 2)
            self.assertIsNot(first, second)
            self.assertIs(first.rag_service.embeddings, second.rag_service.embeddings)
            self.assertIs(first.rag_service.recorder, recorder_one)
            self.assertIs(second.rag_service.recorder, recorder_two)
            pool_events_one = [event for event in recorder_one.snapshot().events
                               if event.stage == "embedding_provider_pool_lookup"]
            pool_events_two = [event for event in recorder_two.snapshot().events
                               if event.stage == "embedding_provider_pool_lookup"]
            self.assertEqual(pool_events_one[0].metadata["provider_pool_hit"], False)
            self.assertEqual(pool_events_two[0].metadata["provider_pool_hit"], True)

            identity = {
                "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
                "application_id": "hospital-costa-sur-medical-oxygen",
                "gas_product_id": "medical-oxygen",
                "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
            }
            result_one = first.search(identity=identity, query="oxygen supply contract delivery")
            result_two = second.search(identity=identity, query="oxygen supply contract delivery")
            self.assertEqual(result_one[0], result_two[0])
            self.assertEqual(
                tuple(source.chunk.chunk_id for source in result_one[1]),
                tuple(source.chunk.chunk_id for source in result_two[1]),
            )
            self.assertEqual(
                tuple(source.chunk.text for source in result_one[1]),
                tuple(source.chunk.text for source in result_two[1]),
            )


if __name__ == "__main__":
    unittest.main()
