import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from diagnostics import PerformanceRecorder, PerformanceStatus
from embeddings import LMStudioEmbeddingProvider
from clipboard_text import build_diagnostics_clipboard_text
from pipeline_inspector import _render_supply_agent_timeline
from rag_service import RAGService


class _FakeEmbeddingsEndpoint:
    def __init__(self, provider, responses):
        self.provider = provider
        self.responses = iter(responses)
        self.inputs = []

    def create(self, *, model, input):
        self.inputs.append((model, tuple(input)))
        request = SimpleNamespace(extensions={})
        self.provider._on_http_request(request)
        time.sleep(0.001)
        response = SimpleNamespace(
            request=request,
            headers={"server-timing": "inference;dur=12.5", "x-request-id": "never-record-this"},
        )
        self.provider._on_http_response(response)
        return next(self.responses)


class EmbeddingTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.recorder = PerformanceRecorder("embedding-telemetry", "fixture", "fixture-model", "rag")
        self.provider = LMStudioEmbeddingProvider(
            model="fixture-embedding-model", base_url="http://127.0.0.1:1234/v1",
        )
        self.addCleanup(self.provider.client.close)

    def _use_responses(self, responses):
        endpoint = _FakeEmbeddingsEndpoint(self.provider, responses)
        self.provider.client = SimpleNamespace(embeddings=endpoint)
        self.provider.set_recorder(self.recorder)
        return endpoint

    def _events(self):
        return [event for event in self.recorder.snapshot().events if event.stage == "embedding_request"]

    def test_query_telemetry_records_usage_dimensions_and_safe_timing(self):
        endpoint = self._use_responses([SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3])],
            usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
        )])
        vector = self.provider.embed_query("¿Qué dice el contrato?")
        self.assertEqual(len(vector), 3)
        event = self._events()[0]
        self.assertEqual(event.status, PerformanceStatus.COMPLETED)
        self.assertEqual(event.metadata["embedding_model"], "fixture-embedding-model")
        self.assertEqual(event.metadata["embedding_dimensions"], 3)
        self.assertEqual(event.metadata["query_character_length"], len("¿Qué dice el contrato?"))
        self.assertEqual(event.metadata["query_byte_length"], len("¿Qué dice el contrato?".encode("utf-8")))
        self.assertEqual(event.metadata["provider_prompt_tokens"], 7)
        self.assertEqual(event.metadata["provider_total_tokens"], 7)
        self.assertGreaterEqual(event.metadata["embedding_preparation_seconds"], 0)
        self.assertGreaterEqual(event.metadata["http_request_to_response_headers_seconds"], 0)
        self.assertGreaterEqual(event.metadata["response_body_and_sdk_parse_seconds"], 0)
        self.assertEqual(event.metadata["server_timing_headers"], {"server-timing": "inference;dur=12.5"})
        self.assertFalse(any("¿Qué dice" in repr(value) for value in event.metadata.values()))
        self.assertEqual(endpoint.inputs[0][1], ("search_query: ¿Qué dice el contrato?",))
        self.assertTrue(event.metadata["embedding_provider_instance_id"])
        self.assertTrue(event.metadata["embedding_client_instance_id"])
        self.assertNotIn("0x", event.metadata["embedding_provider_instance_id"])
        self.assertNotIn("0x", event.metadata["embedding_client_instance_id"])

    def test_missing_or_malformed_optional_usage_never_breaks_embedding(self):
        endpoint = self._use_responses([
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 2.0])]),
            SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[3.0, 4.0])],
                usage=SimpleNamespace(prompt_tokens="unknown", total_tokens=-1),
            ),
        ])
        self.assertEqual(self.provider.embed_query("first"), [1.0, 2.0])
        self.assertEqual(self.provider.embed_query("first"), [3.0, 4.0])
        first, second = self._events()
        self.assertEqual(first.metadata["provider_prompt_tokens"], None)
        self.assertEqual(first.metadata["provider_total_tokens"], None)
        self.assertEqual(second.metadata["provider_prompt_tokens"], None)
        self.assertEqual(second.metadata["provider_total_tokens"], None)
        self.assertFalse(first.metadata["client_reused"])
        self.assertTrue(second.metadata["client_reused"])
        self.assertIsNotNone(first.metadata["client_initialization_seconds"])
        self.assertIsNone(second.metadata["client_initialization_seconds"])
        self.assertEqual(endpoint.inputs[0][1], endpoint.inputs[1][1])

    def test_unavailable_http_timings_are_null_and_server_headers_are_filtered(self):
        response = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.4, 0.5])],
            usage=SimpleNamespace(prompt_tokens=2, total_tokens=2),
        )

        class NoHookEndpoint:
            def create(inner, **_kwargs):
                return response

        self.provider.client = SimpleNamespace(embeddings=NoHookEndpoint())
        self.provider.set_recorder(self.recorder)
        self.assertEqual(self.provider.embed_query("short"), [0.4, 0.5])
        event = self._events()[0]
        self.assertIsNone(event.metadata["client_pre_http_seconds"])
        self.assertIsNone(event.metadata["http_request_to_response_headers_seconds"])
        self.assertIsNone(event.metadata["response_body_and_sdk_parse_seconds"])
        self.assertEqual(event.metadata["server_timing_headers"], {})

    def test_first_second_and_different_query_reuse_the_same_provider_client(self):
        endpoint = self._use_responses([
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])], usage=None),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])], usage=None),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0, 1.0])], usage=None),
        ])
        self.provider.embed_query("identical diagnostic query")
        self.provider.embed_query("identical diagnostic query")
        self.provider.embed_query("short different query")
        events = self._events()
        self.assertEqual(len(events), 3)
        self.assertEqual([event.metadata["client_reused"] for event in events], [False, True, True])
        self.assertEqual(endpoint.inputs[0][1], endpoint.inputs[1][1])
        self.assertNotEqual(endpoint.inputs[1][1], endpoint.inputs[2][1])
        self.assertTrue(all(event.metadata["embedding_dimensions"] == 2 for event in events))
        self.assertTrue(all(event.metadata["total_embedding_request_seconds"] >= 0 for event in events))

    def test_rag_operations_route_telemetry_to_their_own_recorders_without_mutating_provider(self):
        self.provider.recorder = None
        self.provider.client = SimpleNamespace(embeddings=_FakeEmbeddingsEndpoint(self.provider, [
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])], usage=None),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0, 1.0])], usage=None),
        ]))

        class EmptyStore:
            document_count = 0
            chunk_count = 0

            def search(self, *_args, **_kwargs):
                return []

        first = PerformanceRecorder("operation-one", "fixture", "model", "workspace")
        second = PerformanceRecorder("operation-two", "fixture", "model", "workspace")
        RAGService(self.provider, EmptyStore(), first).retrieve("first query")
        RAGService(self.provider, EmptyStore(), second).retrieve("second query")

        def events(recorder):
            return [event for event in recorder.snapshot().events if event.stage == "embedding_request"]

        first_events, second_events = events(first), events(second)
        self.assertEqual(len(first_events), 1)
        self.assertEqual(len(second_events), 1)
        self.assertFalse(first_events[0].metadata["client_reused"])
        self.assertTrue(second_events[0].metadata["client_reused"])
        self.assertIsNone(self.provider.recorder)
        self.assertNotEqual(first.operation_id, second.operation_id)

    def test_concurrent_rag_operations_keep_embedding_telemetry_on_the_matching_recorder(self):
        barrier = threading.Barrier(2)

        class ConcurrentEndpoint:
            def create(inner, **_kwargs):
                request = SimpleNamespace(extensions={})
                self.provider._on_http_request(request)
                barrier.wait(timeout=2)
                self.provider._on_http_response(SimpleNamespace(
                    request=request, headers={"server-timing": "inference;dur=1"},
                ))
                return SimpleNamespace(
                    data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])], usage=None,
                )

        self.provider.recorder = None
        self.provider.client = SimpleNamespace(embeddings=ConcurrentEndpoint())

        class EmptyStore:
            document_count = 0
            chunk_count = 0

            def search(self, *_args, **_kwargs):
                return []

        recorders = [
            PerformanceRecorder("concurrent-one", "fixture", "model", "workspace"),
            PerformanceRecorder("concurrent-two", "fixture", "model", "workspace"),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(RAGService(self.provider, EmptyStore(), recorder).retrieve, f"query-{index}")
                for index, recorder in enumerate(recorders)
            ]
            for future in futures:
                future.result(timeout=3)

        for recorder in recorders:
            events = [event for event in recorder.snapshot().events if event.stage == "embedding_request"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, PerformanceStatus.COMPLETED)
        self.assertEqual(
            sum(event.metadata["client_reused"] for recorder in recorders
                for event in recorder.snapshot().events if event.stage == "embedding_request"),
            1,
        )
        self.assertIsNone(self.provider.recorder)

    def test_embedding_lifecycle_diagnostics_are_visible_in_inspector_and_copy_output(self):
        recorder = PerformanceRecorder(
            "embedding-diagnostics", "lmstudio", "embedding-model", "conversational_workspace",
        )
        recorder.record_stage(
            "embedding_provider_pool_lookup", status=PerformanceStatus.COMPLETED,
            duration_seconds=0.0002, provider_pool_lookup_occurred=True,
            provider_pool_hit=True, provider_pool_size=1,
            provider_pool_key_fingerprint="a1b2c3d4e5f60718",
            embedding_provider_instance_id="provider-0123456789abcdef",
            embedding_client_instance_id="client-0123456789abcdef",
        )
        recorder.record_stage(
            "embedding_request", status=PerformanceStatus.COMPLETED,
            duration_seconds=2.2, client_reused=True,
            client_initialization_seconds=None, client_pre_http_seconds=0.001,
            http_request_to_response_headers_seconds=2.18,
            response_body_and_sdk_parse_seconds=0.009,
            total_embedding_request_seconds=2.19,
            embedding_provider_instance_id="provider-0123456789abcdef",
            embedding_client_instance_id="client-0123456789abcdef",
            provider_pool_hit=True, provider_pool_size=1,
            provider_pool_key_fingerprint="a1b2c3d4e5f60718",
        )
        recorder.finish("completed")

        snapshot = recorder.snapshot()
        inspector = "".join(_render_supply_agent_timeline(snapshot))
        copied = build_diagnostics_clipboard_text(snapshot)
        for output in (inspector, copied):
            for visible in (
                "Pool lookup", "Pool hit", "Pool size", "Pool key fingerprint",
                "Provider instance", "Client instance", "Client reused",
                "Client initialization", "Client pre-HTTP",
                "HTTP request to response headers", "Response body and SDK parse",
                "Total embedding request",
            ):
                self.assertIn(visible, output)
            self.assertNotIn("api_key", output.casefold())
            self.assertNotIn("authorization", output.casefold())
            self.assertNotIn("0x", output)


if __name__ == "__main__":
    unittest.main()
