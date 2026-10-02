from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from diagnostics import PerformanceRecorder
from industrial_gases.industrial_knowledge import lazy_demo_knowledge_service
from industrial_gases.conversational_workspace import ConversationalWorkspaceOrchestrator
from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio
from llm_client import LLMResponse
from llm_client import GenerationOptions
from rag_models import DocumentChunk, RetrievedChunk
from scripts import benchmark_workspace_generation as benchmark


def _source():
    portfolio, _ = evaluate_demo_supply_portfolio()
    item = next(item for item in portfolio.items if item.item_id == benchmark.CASE_ITEM_ID)
    result = item.result
    metadata = {
        "customer_id": result.customer_id,
        "site_id": result.site_id,
        "application_id": result.application_id,
        "gas_product_id": result.gas_product_id,
        "installation_id": result.installation_id,
        "document_type": "supply_contract",
    }
    return RetrievedChunk(DocumentChunk(
        chunk_id="benchmark-hospital-contract",
        document_id="benchmark-hospital-contract-document",
        document_name="hospital_o2_supply_contract.txt",
        section="Delivery", page_start=1, page_end=1, ordinal=0,
        text=("The planned delivery record is 4,000 kg, scheduled four days after "
              "the reference time supplied to the analysis."),
        metadata=metadata,
    ), score=0.99)


class _FixedKnowledge:
    def __init__(self, source=None):
        self.source = source or _source()
        self.calls = []

    def search(self, *, identity, query, top_k=4):
        self.calls.append((identity, query, top_k))
        assert identity["customer_id"] == "hospital-costa-sur"
        assert identity["installation_id"] == "hospital-costa-sur-bulk-cryogenic-o2"
        return "retrieved", (self.source,)


class _FixtureEmbeddings:
    """Cheap deterministic embeddings for exercising the real local RAG stack."""
    model = "offline-benchmark-fixture"

    @staticmethod
    def _vector(_text):
        return [1.0, 0.0, 0.0]

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


class _FixtureEmbeddingPool:
    provider_count = 1

    def __init__(self):
        self.provider = _FixtureEmbeddings()

    def get_or_create_with_status(self, **_configuration):
        return self.provider, {
            "provider_pool_hit": False,
            "provider_pool_size": 1,
            "provider_pool_key_fingerprint": "offline-fixture",
            "embedding_provider_instance_id": "fixture-provider",
            "embedding_client_instance_id": "fixture-client",
        }


class _Provider:
    provider_name = "lmstudio"
    model = "qwen/qwen3-8b"
    max_tokens = 384
    max_output_tokens = 384
    thinking_enabled = False

    def __init__(self, recorder, *, answer=None, max_tokens=384):
        self.recorder = recorder
        self.answer = answer or (
            "El contrato indica: ‘The planned delivery record is 4,000 kg, scheduled four days after "
            "the reference time supplied to the analysis.’ "
            "[chunk_id:benchmark-hospital-contract] El inventario antes de la entrega es 400 kg."
        )
        self.max_tokens = max_tokens
        self.runtime_config = SimpleNamespace(
            max_tokens=max_tokens, max_output_tokens=384, timeout_seconds=640,
        )
        self.thinking_enabled = False

    def generate_response(self, messages, timeout_seconds=None, options=None):
        call = self.recorder.start_llm_call(
            call_number=1, purpose="supply_agent_decision", provider_round=1,
            message_count=len(messages),
            prompt_character_count=sum(len(message.get("content", "")) for message in messages),
            tool_schema_character_count=0,
        )
        self.recorder.complete_llm_call(
            call, request_setup_seconds=0.01, response_stream_seconds=0.03,
            request_to_stream_seconds=0.01, time_to_first_token_seconds=0.02,
            stream_initial_wait_seconds=0.01, stream_generation_seconds=0.02,
            tokens_per_second=None, input_tokens=None, output_tokens=None,
            total_tokens=None, response_character_count=len(self.answer),
        )
        return LLMResponse(json.dumps({"action": "finish", "answer": self.answer}))


class _RetrievalAwareProvider(_Provider):
    def generate_response(self, messages, timeout_seconds=None, options=None):
        prompt = "\n".join(str(message.get("content", "")) for message in messages)
        chunk_ids = tuple(dict.fromkeys(re.findall(r'"chunk_id"\s*:\s*"([^"\\]+)"', prompt)))
        if not chunk_ids:
            raise AssertionError("The production RAG retrieval must contribute source chunk IDs")
        citations = " ".join(f"[chunk_id:{chunk_id}]" for chunk_id in chunk_ids)
        self.answer = (
            "The planned delivery record is 4,000 kg, scheduled four days after the reference time "
            f"supplied to the analysis. {citations}"
        )
        return super().generate_response(messages, timeout_seconds, options)


class WorkspaceGenerationBenchmarkTests(unittest.TestCase):
    def run_benchmark(self, *, provider_answers=None, provider_configs=None,
                      knowledge_factory=None, warmup_runs=1, runs=3):
        provider_answers = list(provider_answers or [])
        provider_configs = list(provider_configs or [])
        created_providers = []
        created_knowledge = []

        def provider_factory(recorder):
            index = len(created_providers)
            provider = _Provider(
                recorder,
                answer=provider_answers[index] if index < len(provider_answers) else None,
                max_tokens=provider_configs[index] if index < len(provider_configs) else 384,
            )
            created_providers.append(provider)
            return provider

        def knowledge(recorder):
            service = knowledge_factory(len(created_knowledge)) if knowledge_factory else _FixedKnowledge()
            created_knowledge.append(service)
            return service

        with patch.dict("os.environ", {"LLM_TIMEOUT_SECONDS": "640"}):
            report = benchmark.run_benchmark(
                provider_name="lmstudio", model_name="qwen/qwen3-8b",
                warmup_runs=warmup_runs, runs=runs,
                provider_factory=provider_factory,
                knowledge_factory=knowledge,
            )
        return report, created_providers, created_knowledge

    def test_real_workspace_path_requires_and_performs_generation(self):
        report, providers, knowledge = self.run_benchmark(warmup_runs=0, runs=1)
        run = report["runs"][0]
        self.assertEqual(run["generation_call_count"], 1)
        self.assertEqual(run["operation_status"], "completed")
        self.assertTrue(run["functional_pass"])
        self.assertEqual(run["citation_status"], "passed")
        self.assertEqual(providers[0].recorder.mode, "conversational_workspace")
        self.assertEqual(len(knowledge[0].calls), 1)
        self.assertEqual(knowledge[0].calls[0][0]["gas_product_id"], "medical-oxygen")

    def test_generation_is_required_by_production_deterministic_eligibility(self):
        report, _, _ = self.run_benchmark(warmup_runs=0, runs=1)
        run = report["runs"][0]
        self.assertEqual(run["deterministic_eligibility_reason"], "operational_context_required")
        self.assertFalse(run["deterministic_response_used"])
        self.assertEqual(run["generation_call_count"], 1)

    def test_nested_lazy_factory_failure_is_reported_with_safe_retrieval_diagnostics(self):
        report, _, _ = self.run_benchmark(
            knowledge_factory=lambda _index: (lambda: _FixedKnowledge()),
            warmup_runs=0,
            runs=1,
        )
        run = report["runs"][0]
        self.assertEqual(run["failure_type"], "AttributeError")
        self.assertEqual(run["failure_message"], "function object has no attribute search")
        self.assertEqual(run["failure_phase"], "workspace_execution")
        self.assertEqual(run["failure_pipeline_stage"], "tool_execution")
        self.assertTrue(run["failure_traceback"])
        self.assertTrue(run["failure_traceback"][-1].startswith("supply_agent.py:"))
        self.assertTrue(run["failure_traceback"][-1].endswith("in execute"))
        encoded = json.dumps(run)
        for forbidden in (benchmark.CASE_QUESTION, "reference time supplied", "api_key", "Authorization"):
            self.assertNotIn(forbidden, encoded)

    def test_real_knowledge_service_factory_reaches_rag_retrieval_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = _FixtureEmbeddingPool()
            created_providers = []

            def provider_factory(recorder):
                provider = _RetrievalAwareProvider(recorder)
                created_providers.append(provider)
                return provider

            # This mirrors the corrected production wiring: lazy_demo returns
            # a factory, which must be called to obtain IndustrialKnowledgeService.
            def knowledge_factory(recorder):
                return lazy_demo_knowledge_service(
                    pool,
                    model=pool.provider.model,
                    base_url="http://offline-fixture.invalid",
                    api_key="offline-fixture",
                    recorder=recorder,
                    root=Path(directory),
                )()

            with patch.dict("os.environ", {"LLM_TIMEOUT_SECONDS": "640"}):
                report = benchmark.run_benchmark(
                    provider_name="lmstudio", model_name="qwen/qwen3-8b",
                    warmup_runs=0, runs=1,
                    provider_factory=provider_factory,
                    knowledge_factory=knowledge_factory,
                )

        run = report["runs"][0]
        self.assertEqual(run["operation_status"], "completed")
        self.assertEqual(run["generation_call_count"], 1)
        self.assertEqual(run["citation_status"], "passed")
        self.assertTrue(run["functional_pass"])
        self.assertTrue(run["source_chunk_ids"])
        self.assertEqual(len(created_providers), 2)
        self.assertIsNone(run["failure_type"])
        self.assertEqual(report["successful_comparable_measured_runs"], 1)
        self.assertEqual(pool.provider_count, 1)

    def test_smoke_mode_executes_one_operation_and_checks_citation(self):
        report, providers, knowledge = self.run_smoke_fixture()
        self.assertEqual(report["mode"], "smoke")
        self.assertTrue(report["smoke_passed"])
        self.assertEqual(len(report["runs"]), 1)
        self.assertEqual(report["runs"][0]["classification"], "smoke")
        self.assertEqual(report["runs"][0]["generation_call_count"], 1)
        self.assertEqual(report["runs"][0]["citation_status"], "passed")
        self.assertEqual(len(providers), 1)
        self.assertEqual(len(knowledge), 1)

    def run_smoke_fixture(self):
        providers = []
        knowledge = []

        def provider_factory(recorder):
            provider = _Provider(recorder)
            providers.append(provider)
            return provider

        def knowledge_factory(_recorder):
            service = _FixedKnowledge()
            knowledge.append(service)
            return service

        with patch.dict("os.environ", {"LLM_TIMEOUT_SECONDS": "640"}):
            report = benchmark.run_smoke(
                provider_name="lmstudio", model_name="qwen/qwen3-8b",
                provider_factory=provider_factory,
                knowledge_factory=knowledge_factory,
            )
        return report, providers, knowledge

    def test_run_classification_distinguishes_first_warmup_and_measured(self):
        report, _, _ = self.run_benchmark(warmup_runs=1, runs=3)
        self.assertEqual(
            [run["classification"] for run in report["runs"]],
            ["first_runner_run", "warmup", "measured", "measured", "measured"],
        )

    def test_each_operation_uses_a_fresh_workspace_and_fixed_initial_context(self):
        instances = []
        original = ConversationalWorkspaceOrchestrator

        class TrackingWorkspace(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                instances.append(self)

        with patch.object(benchmark, "ConversationalWorkspaceOrchestrator", TrackingWorkspace):
            self.run_benchmark(warmup_runs=1, runs=2)
        self.assertEqual(len(instances), 4)
        self.assertEqual(len({id(instance) for instance in instances}), 4)
        self.assertTrue(all(instance.session_context.focused_item_id == benchmark.CASE_ITEM_ID
                            for instance in instances))

    def test_prompt_fingerprint_is_stable_across_repetitions(self):
        report, _, _ = self.run_benchmark(warmup_runs=1, runs=3)
        fingerprints = [run["prompt_fingerprints"] for run in report["runs"]]
        self.assertTrue(all(value == fingerprints[0] for value in fingerprints))
        self.assertTrue(all(run["comparable"] for run in report["runs"]))

    def test_evidence_fingerprint_and_source_order_are_stable(self):
        report, _, _ = self.run_benchmark(warmup_runs=1, runs=2)
        self.assertEqual(len({run["evidence_fingerprint"] for run in report["runs"]}), 1)
        self.assertEqual(
            [run["source_chunk_ids"] for run in report["runs"]],
            [("benchmark-hospital-contract",)] * 4,
        )

    def test_generation_configuration_fingerprint_is_stable(self):
        report, _, _ = self.run_benchmark(warmup_runs=1, runs=2)
        configs = [run["generation_configuration_fingerprints"] for run in report["runs"]]
        self.assertTrue(all(value == configs[0] for value in configs))
        self.assertEqual(report["runs"][0]["effective_generation_configuration"][0]["temperature"], 0.0)

    def test_http_observer_fingerprints_final_provider_messages_without_retaining_them(self):
        class Completions:
            def create(inner, **parameters):
                inner.parameters = parameters
                return "stream"

        completions = Completions()
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
            base_url="http://local-provider.example/v1",
        )
        provider = _Provider(PerformanceRecorder("http-observer", "lmstudio", "qwen", "test"))
        provider.client = client
        observer = benchmark._GenerationObserver(provider)
        observer.generate_response(
            [{"role": "user", "content": "internal prompt"}],
            timeout_seconds=640,
            options=GenerationOptions(tool_calling_enabled=False, temperature=0.0),
        )
        final_messages = [
            {"role": "system", "content": "system instructions /no_think"},
            {"role": "user", "content": "internal prompt"},
        ]
        observer_provider_client = provider.client
        observer_provider_client.chat.completions.create(
            model="qwen/qwen3-8b", messages=final_messages, max_tokens=384,
            stream=True, temperature=0.0, timeout=640,
        )
        call = observer.calls[0]
        self.assertEqual(call["messages_fingerprint"], benchmark.stable_fingerprint(final_messages))
        self.assertNotIn("internal prompt", json.dumps(call))
        self.assertNotIn("local-provider.example", json.dumps(call))
        self.assertNotIn("api_key", json.dumps(call).casefold())

    def test_configuration_mismatch_marks_run_non_comparable(self):
        report, _, _ = self.run_benchmark(
            warmup_runs=0, runs=2, provider_configs=[384, 384, 512],
        )
        final = report["runs"][-1]
        self.assertFalse(final["comparable"])
        self.assertIn("generation_configuration_fingerprint_changed", final["non_comparable_reasons"])
        self.assertFalse(final["successful_performance_sample"])

    def test_functional_citation_failure_is_excluded_from_aggregates(self):
        uncited = "El contrato indica que la entrega está prevista."
        report, _, _ = self.run_benchmark(
            warmup_runs=1, runs=3,
            provider_answers=[None, None, None, uncited, None],
        )
        failed_measured = report["runs"][3]
        self.assertFalse(failed_measured["functional_pass"])
        self.assertEqual(failed_measured["citation_status"], "failed")
        self.assertFalse(failed_measured["successful_performance_sample"])
        self.assertEqual(report["successful_comparable_measured_runs"], 2)

    def test_missing_token_usage_and_server_inference_remain_none(self):
        report, _, _ = self.run_benchmark(warmup_runs=0, runs=1)
        run = report["runs"][0]
        self.assertIsNone(run["input_tokens"])
        self.assertIsNone(run["output_tokens"])
        self.assertIsNone(run["total_tokens"])
        self.assertIsNone(run["server_inference_seconds"])

    def test_aggregates_exclude_first_run_warmup_and_invalid_samples(self):
        report, _, _ = self.run_benchmark(warmup_runs=1, runs=3)
        self.assertEqual(report["aggregate_statistics"]["TTFT"]["count"], 3)
        self.assertTrue(all(run["classification"] == "measured"
                            for run in report["runs"] if run["successful_performance_sample"]))

    def test_machine_report_serializes_with_unavailable_metrics_as_null(self):
        report, _, _ = self.run_benchmark(warmup_runs=0, runs=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            decoded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(decoded["case_id"], benchmark.CASE_ID)
        self.assertIsNone(decoded["runs"][0]["server_inference_seconds"])

    def test_cli_rejects_invalid_measurement_counts(self):
        with self.assertRaises(SystemExit):
            benchmark.parse_args(["--model", "qwen/qwen3-8b", "--runs", "0"])
        with self.assertRaises(SystemExit):
            benchmark.parse_args(["--model", "qwen/qwen3-8b", "--warmup-runs", "-1"])

    def test_cli_accepts_exactly_one_operation_smoke_mode(self):
        args = benchmark.parse_args(["--model", "qwen/qwen3-8b", "--smoke"])
        self.assertTrue(args.smoke)


if __name__ == "__main__":
    unittest.main()
