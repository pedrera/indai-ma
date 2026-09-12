import sqlite3
from contextlib import closing
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_models import AgentStatus
from comparison_store import ComparisonStore
from procurement_benchmark import main, run_benchmark, summarize
from runtime_config import LLMRuntimeConfig


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "runs.sqlite3"
        self.store = ComparisonStore(self.path)
        self.config = LLMRuntimeConfig(100, 100, 30, False)

    def run_batch(self, runner_factory, **kwargs):
        return run_benchmark(
            self.store, provider_name="fake", model="fake-model", config=self.config,
            provider_factory=Mock(), runner_factory=runner_factory,
            progress=lambda _: None, **kwargs,
        )

    @staticmethod
    def successful_runner(mode, provider, recorder):
        recorder.record_stage("agent_observation", observation_type="tool_skipped")
        recorder.record_stage("final_response_validation", deterministic_fallback=True,
                              validation_reasons=["unsupported_economic_amount"])
        return SimpleNamespace(run=lambda *_: SimpleNamespace(
            status=AgentStatus.COMPLETED, content="SHORT", termination_reason="final_answer"
        ))

    def test_runs_survive_reopen_and_include_configuration_and_quality(self):
        batch = self.run_batch(self.successful_runner, repetitions=2,
                               modes=("deterministic",), cases={"short": "request"})
        rows = ComparisonStore(self.path).read(batch)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["config"]["timeout_seconds"], 30)
        self.assertEqual(rows[0]["record"]["result_summary"], "SHORT")
        self.assertEqual(rows[0]["validation_reasons"], ["unsupported_economic_amount"])
        summary = summarize(rows)[0]
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["fallbacks"], 2)
        self.assertEqual(summary["skipped_actions"], 2)
        self.assertEqual(self.store.read("unknown"), [])

    def test_failed_attempt_is_persisted_and_next_attempt_runs(self):
        factory = Mock(side_effect=[RuntimeError("private endpoint"),
                                   self.successful_runner("", None, Mock())])
        batch = self.run_batch(factory, repetitions=2, modes=("deterministic",),
                               cases={"short": "request"})
        rows = self.store.read(batch)
        self.assertEqual([r["record"]["status"] for r in rows], ["failed", "completed"])
        self.assertEqual(rows[0]["record"]["termination_reason"], "RuntimeError")
        self.assertNotIn("private endpoint", str(rows))
        self.assertIsNone(rows[0]["validation_reasons"])

    def test_legacy_records_have_unknown_reasons_without_rewriting_database(self):
        import json
        batch = self.run_batch(self.successful_runner, repetitions=1,
                               modes=("deterministic",), cases={"short": "request"})
        with closing(sqlite3.connect(self.path)) as connection:
            payload = json.loads(connection.execute(
                "SELECT payload FROM comparison_runs"
            ).fetchone()[0])
            del payload["validation_reasons"]
            connection.execute("UPDATE comparison_runs SET payload = ?", (json.dumps(payload),))
            connection.commit()
        self.assertIsNone(self.store.read(batch)[0]["validation_reasons"])
        with closing(sqlite3.connect(self.path)) as connection:
            stored = json.loads(connection.execute(
                "SELECT payload FROM comparison_runs"
            ).fetchone()[0])
        self.assertNotIn("validation_reasons", stored)

    def test_accepted_validation_persists_empty_reasons(self):
        def factory(mode, provider, recorder):
            recorder.record_stage("final_response_validation", deterministic_fallback=False,
                                  validation_reasons=[])
            return SimpleNamespace(run=lambda *_: SimpleNamespace(
                status=AgentStatus.COMPLETED, content="SHORT", termination_reason="final_answer"
            ))
        batch = self.run_batch(factory, repetitions=1, modes=("deterministic",),
                               cases={"short": "request"})
        row = self.store.read(batch)[0]
        self.assertEqual(row["validation_reasons"], [])
        self.assertFalse(row["fallback"])

    def test_percentiles_exclude_failures_and_use_nearest_rank(self):
        batch = self.run_batch(self.successful_runner, repetitions=1,
                               modes=("deterministic",), cases={"short": "request"})
        row = self.store.read(batch)[0]
        rows = [dict(row, record=dict(row["record"], total_wall_time=t)) for t in (1, 2, 3, 4, 100)]
        rows[-1]["record"]["status"] = "failed"
        result = summarize(rows)[0]
        self.assertEqual(result["median_seconds_completed"], 2.5)
        self.assertEqual(result["p95_seconds_completed"], 4)
        self.assertEqual(result["not_completed"], 1)
        rows = [rows[-1]]
        self.assertIsNone(summarize(rows)[0]["p95_seconds_completed"])

    def test_modes_rotate_between_repetitions(self):
        order = []
        def factory(mode, provider, recorder):
            order.append(mode)
            return self.successful_runner(mode, provider, recorder)
        self.run_batch(factory, repetitions=2, cases={"short": "request"})
        self.assertEqual(order, ["deterministic", "planner_agent", "react_agent",
                                 "planner_agent", "react_agent", "deterministic"])

    def test_duplicate_operation_cannot_overwrite_saved_run(self):
        batch = self.run_batch(self.successful_runner, repetitions=1,
                               modes=("deterministic",), cases={"short": "request"})
        row = self.store.read(batch)[0]
        from execution_metrics import ExecutionComparisonRecord
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append(ExecutionComparisonRecord(**row["record"]),
                              batch_id=batch, case_id="other", repetition=1,
                              provider="fake", model="fake", config={})
        self.assertEqual(len(self.store.read(batch)), 1)

    def test_default_cli_does_not_call_provider_or_create_store(self):
        with patch("sys.argv", ["benchmark"]), patch("builtins.print"), \
             patch("procurement_benchmark.run_benchmark") as run, \
             patch("procurement_benchmark.ComparisonStore") as store:
            main()
        run.assert_not_called()
        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
