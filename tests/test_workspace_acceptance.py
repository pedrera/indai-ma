import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from diagnostics import PerformanceRecorder, PerformanceStatus
from generation import GenerationCancelledError
from llm_client import LLMTimeoutError
from scripts.run_workspace_acceptance import (
    AcceptanceResult,
    FailureCategory,
    TurnResult,
    classify_failure,
    render_safe_report,
    run_workspace_acceptance,
    write_safe_report,
    _semantic_guard_status,
)
from tests.test_industrial_gases_conversational_workspace import (
    CanonicalContinuityDecisionModel,
    ScopedFixtureKnowledge,
)
from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio


class _RecordingKnowledge:
    def __init__(self, recorder, *, fail=False):
        self.recorder = recorder
        self.fixture = ScopedFixtureKnowledge(fail=fail)

    def search(self, *, identity, query, top_k=4):
        event = self.recorder.start_stage(
            "portfolio_knowledge_retrieval", item_id=identity.get("gas_product_id"),
        )
        try:
            status, chunks = self.fixture.search(identity=identity, query=query, top_k=top_k)
        except Exception:
            self.recorder.fail_stage(event, status="operational_error")
            raise
        self.recorder.complete_stage(event, status=status, retrieved_chunk_count=len(chunks))
        return status, chunks


class _OfflineDocumentaryModel:
    def __init__(self, recorder, delegate, *, failure=None, answer_override=None):
        self.recorder = recorder
        self.delegate = delegate
        self.failure = failure
        self.answer_override = answer_override

    def decide(self, question, state, tools, timeout_seconds=None):
        if "sobre la entrega" in question.casefold():
            if self.failure is not None:
                raise self.failure
            event = self.recorder.start_llm_call(
                call_number=1, purpose="supply_agent_decision", provider_round=1,
                message_count=1, prompt_character_count=500,
                tool_schema_character_count=0,
            )
            try:
                result = self.delegate.decide(question, state, tools, timeout_seconds)
            finally:
                if event:
                    self.recorder.complete_llm_call(
                        event, request_setup_seconds=0.01, response_stream_seconds=0.02,
                        response_character_count=40,
                    )
            if self.answer_override is not None:
                from agent_models import AgentDecision
                return AgentDecision("finish", "finish", answer=self.answer_override)
            return result
        return self.delegate.decide(question, state, tools, timeout_seconds)


class WorkspaceAcceptanceTests(unittest.TestCase):
    def test_documented_script_invocation_bootstraps_repo_imports_without_running_acceptance(self):
        repository_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "scripts/run_workspace_acceptance.py", "--help"],
            cwd=repository_root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage:", completed.stdout.casefold())
        self.assertIn("Run the v1.15 Workspace real-provider acceptance conversation", completed.stdout)
        self.assertNotIn("Provider:", completed.stdout)

    def _run(self, *, documentary_failure=None, answer_override=None, retrieval_failure=False):
        delegate = CanonicalContinuityDecisionModel()
        provider_calls = []
        def provider_factory(recorder):
            provider_calls.append(recorder.operation_id)
            return SimpleNamespace(provider_name="fixture", model="fixture-model")
        result = run_workspace_acceptance(
            provider_name="fixture", model_name="fixture-model", timeout_seconds=640,
            provider_factory=provider_factory,
            decision_model_factory=lambda provider, recorder: _OfflineDocumentaryModel(
                recorder, delegate, failure=documentary_failure, answer_override=answer_override,
            ),
            knowledge_factory=lambda recorder: _RecordingKnowledge(recorder, fail=retrieval_failure),
            portfolio_factory=evaluate_demo_supply_portfolio,
        )
        return result, provider_calls, delegate

    def test_successful_canonical_q1_q9_flow_and_historical_inspector_views(self):
        result, provider_calls, model = self._run()
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.turns), 9)
        self.assertEqual(len(provider_calls), 9)
        self.assertGreaterEqual(len(model.calls), 1)
        self.assertTrue(all(turn.category is FailureCategory.PASS for turn in result.turns))

        q3 = result.turns[2].response
        q4 = result.turns[3].response
        self.assertEqual(q3.session_context.scenario_history[-1].scenario_id, "delivery-offset:-1")
        self.assertEqual(q4.session_context.scenario_history[-1].scenario_id, "delivery-offset:-2")
        self.assertEqual(q4.session_context.scenario_history[-1].offset_days, -2)
        q5 = result.turns[4].response
        self.assertEqual(tuple(item.value for item in q5.scenario_comparison.metrics[1].values), (400, 1100, 1800))
        q8 = result.turns[7]
        q9 = result.turns[8]
        q8_view = result.execution_views[q8.operation_id]
        q9_view = result.execution_views[q9.operation_id]
        self.assertIn("portfolio_knowledge_retrieval", tuple(event.stage for event in q8_view.snapshot.events))
        self.assertIn("llm_call", tuple(event.stage for event in q8_view.snapshot.events))
        self.assertIn("deterministic_scenario_comparison", tuple(event.stage for event in q9_view.snapshot.events))
        self.assertFalse(any(event.stage in {"llm_call", "vector_search", "query_embedding"}
                             for event in q9_view.snapshot.events))
        self.assertEqual(q8_view.snapshot.operation_id, q8.operation_id)
        self.assertNotEqual(q8_view.snapshot.operation_id, q9_view.snapshot.operation_id)
        self.assertTrue(all(passed for _, passed in result.summary_checks))

    def test_timeout_is_classified_and_safe_report_omits_error_payload(self):
        secret_error = "PRIVATE_PROMPT raw provider response api_key=acceptance-secret"
        result, _, _ = self._run(documentary_failure=LLMTimeoutError(secret_error))
        self.assertEqual(result.turns[7].category, FailureCategory.TIMEOUT)
        self.assertEqual(result.exit_code, 1)
        report = render_safe_report(
            result, provider="fixture", model="fixture-model", timeout=640,
        )
        for forbidden in ("PRIVATE_PROMPT", "raw provider response", "acceptance-secret"):
            self.assertNotIn(forbidden, report)
        self.assertIn("category=TIMEOUT", report)
        self.assertIn("retrieval=completed", report)

    def test_invalid_citation_event_is_classified_without_exposing_payload(self):
        recorder = PerformanceRecorder("q8-invalid", "fixture", "fixture-model", "conversational_workspace")
        recorder.record_stage(
            "citation_validation", status=PerformanceStatus.FAILED,
            valid=False, reason="private citation payload",
        )
        recorder.finish("failed")
        snapshot = recorder.snapshot()
        self.assertEqual(classify_failure(snapshot=snapshot), FailureCategory.CITATION_VALIDATION_FAILURE)
        turn = TurnResult(
            8, "Contract delivery", "¿Qué dice el contrato sobre la entrega?", "q8-invalid",
            "failed", 1.2, FailureCategory.CITATION_VALIDATION_FAILURE,
            (), 1, 0.4, "completed", "fail", "clear", snapshot=snapshot,
        )
        result = AcceptanceResult((turn,), {}, None, (), stopped_early=True)
        report = render_safe_report(result, provider="fixture", model="fixture-model", timeout=640)
        self.assertIn("category=CITATION_VALIDATION_FAILURE", report)
        self.assertNotIn("private citation payload", report)

    def test_retrieval_failure_is_distinguished(self):
        result, _, _ = self._run(retrieval_failure=True)
        self.assertEqual(result.turns[7].category, FailureCategory.RETRIEVAL_FAILURE)
        self.assertNotEqual(result.exit_code, 0)

    def test_q8_citation_failure_preserves_history_and_q9_deterministic_comparison(self):
        result, _, _ = self._run(
            answer_override="El contrato prevé una entrega de 4.000 kg cuatro días después de la referencia.",
        )
        q8, q9 = result.turns[7], result.turns[8]
        self.assertEqual(q8.category, FailureCategory.CITATION_VALIDATION_FAILURE)
        citation = next(event for event in q8.snapshot.events if event.stage == "citation_validation")
        self.assertEqual(citation.metadata["failure_category"], "missing_citation")
        self.assertIn(q8.operation_id, result.execution_views)
        self.assertIn("deterministic_scenario_comparison", tuple(
            event.stage for event in q9.snapshot.events
        ))
        self.assertEqual(q9.llm_calls, 0)
        self.assertEqual(q9.retrieval_status, "not_used")
        self.assertEqual(
            q9.response.session_context.scenario_history,
            result.turns[3].response.session_context.scenario_history,
        )

    def test_classifies_provider_cancel_and_unexpected_errors_without_messages(self):
        self.assertEqual(classify_failure(error=GenerationCancelledError("private detail")),
                         FailureCategory.PROVIDER_FAILURE)
        self.assertEqual(classify_failure(error=RuntimeError("private detail")),
                         FailureCategory.UNEXPECTED_EXCEPTION)
        recorder = PerformanceRecorder("retrieve", "fixture", "model", "conversational_workspace")
        event = recorder.start_stage("portfolio_knowledge_retrieval")
        recorder.fail_stage(event)
        recorder.finish("failed")
        self.assertEqual(classify_failure(snapshot=recorder.snapshot()), FailureCategory.RETRIEVAL_FAILURE)

    def test_semantic_guard_report_uses_only_safe_reason_categories(self):
        recorder = PerformanceRecorder("guard", "fixture", "model", "conversational_workspace")
        recorder.record_stage(
            "workspace_response_projection", semantic_guard_applied=True,
            semantic_guard_reason="unsupported_stockout_claim",
        )
        self.assertEqual(_semantic_guard_status(recorder.snapshot()), "applied:unsupported_stockout_claim")
        recorder.record_stage(
            "workspace_response_projection", semantic_guard_applied=True,
            semantic_guard_reason="private raw explanation",
        )
        self.assertEqual(_semantic_guard_status(recorder.snapshot()), "applied:unsupported_stockout_claim,other")

    def test_safe_report_writer_creates_report_without_acceptance_pass(self):
        turn = TurnResult(
            8, "Contract delivery", "¿Qué dice el contrato sobre la entrega?", "op-8",
            "failed", 1.2, FailureCategory.CITATION_VALIDATION_FAILURE,
            ("q8_citation_scope_validation_passed",), 1, 0.4, "completed", "fail", "clear",
        )
        result = AcceptanceResult((turn,), {}, None, (), stopped_early=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_safe_report(
                Path(tmp) / "workspace_acceptance.txt",
                render_safe_report(result, provider="fixture", model="fixture-model", timeout=640),
            )
            self.assertTrue(path.exists())
            self.assertIn("RESULT: FAIL", path.read_text(encoding="utf-8"))
            self.assertIn("Q8 Contract delivery: FAIL", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
