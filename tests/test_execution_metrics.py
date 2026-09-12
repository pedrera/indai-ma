import unittest
from unittest.mock import patch

from diagnostics import (
    PerformanceEvent,
    PerformanceRecorder,
    PerformanceSnapshot,
    PerformanceStatus,
)
from execution_metrics import (
    build_execution_comparison_record,
    build_operation_metrics,
)


def event(stage, duration, metadata=None, status=PerformanceStatus.COMPLETED):
    return PerformanceEvent(
        event_id=f"{stage}-{duration}",
        operation_id="metrics",
        stage=stage,
        status=status,
        duration_seconds=duration,
        metadata=metadata or {},
    )


def snapshot(mode="procurement_agent", events=(), elapsed=12.0, status="completed"):
    return PerformanceSnapshot(
        operation_id="metrics",
        provider="lmstudio",
        model="qwen/qwen3-8b",
        mode=mode,
        status=status,
        started_at=0,
        elapsed_seconds=elapsed,
        events=tuple(events),
    )


class ExecutionMetricsTests(unittest.TestCase):
    def test_recorder_measures_operation_wall_time_with_monotonic_clock(self):
        with patch("diagnostics.perf_counter", side_effect=[10.0, 20.0, 30.0]):
            recorder = PerformanceRecorder("op", "test", "model", "chat")
            recorder.finish("completed")
            measured = recorder.snapshot()
        self.assertEqual(measured.elapsed_seconds, 10.0)

    def test_operation_wall_time_is_independent_and_nested_time_not_added(self):
        metrics = build_operation_metrics(
            snapshot(
                events=[
                    event("llm_call", 10, {"call_number": 1, "response_stream_seconds": 8}),
                    event("model_inference", 8, {"inference_seconds": 8}),
                ],
                elapsed=12,
            )
        )
        self.assertEqual(metrics.total_operation_wall_time, 12)
        self.assertEqual(metrics.llm_request_wall_time_total, 10)
        self.assertIsNone(metrics.llm_inference_time_total)

    def test_multiple_calls_and_tools_aggregate_independently(self):
        metrics = build_operation_metrics(
            snapshot(
                events=[
                    event("llm_call", 3, {"call_number": 1, "server_inference_seconds": 2}),
                    event("tool_execution", 0.2, {"tool_seconds": 0.1, "tool_call_count": 1}),
                    event("llm_call", 4, {"call_number": 2, "server_inference_seconds": 3}),
                    event("tool_execution", 0.3, {"tool_seconds": 0.2, "tool_call_count": 1}),
                ]
            )
        )
        self.assertEqual(metrics.llm_call_count, 2)
        self.assertEqual(metrics.llm_request_wall_time_total, 7)
        self.assertEqual(metrics.llm_inference_time_total, 5)
        self.assertEqual(metrics.llm_overhead_time_total, 2)
        self.assertAlmostEqual(metrics.tool_execution_time_total, 0.3)

    def test_missing_and_failed_call_metrics_are_safe(self):
        metrics = build_operation_metrics(
            snapshot(
                events=[
                    event(
                        "llm_call",
                        1.5,
                        {"call_number": 1, "purpose": "agent_decision"},
                        PerformanceStatus.FAILED,
                    )
                ],
                status="failed",
            )
        )
        self.assertEqual(metrics.llm_calls[0].status, "failed")
        self.assertEqual(metrics.llm_calls[0].request_wall_time, 1.5)
        self.assertIsNone(metrics.llm_calls[0].inference_time)

    def test_comparison_records_represent_agent_and_deterministic_modes(self):
        events = [
            event("llm_call", 2, {"call_number": 1}),
            event("tool_execution", 0.1, {"tool_name": "calculate_supply_position", "tool_call_count": 1, "tool_seconds": 0.1}),
        ]
        agent = build_execution_comparison_record(snapshot(events=events), "SHORT")
        deterministic = build_execution_comparison_record(
            snapshot(mode="gas_analysis", events=events), "SHORT"
        )
        planner = build_execution_comparison_record(
            snapshot(mode="procurement_planner", events=events), "SHORT"
        )
        self.assertEqual(agent.mode, "react_agent")
        self.assertEqual(deterministic.mode, "deterministic")
        self.assertEqual(planner.mode, "planner_agent")
        self.assertEqual(agent.tool_names, ("calculate_supply_position",))
        self.assertEqual(agent.llm_call_count, 1)

    def test_prompt_and_response_character_metrics_are_aggregated(self):
        metrics = build_operation_metrics(snapshot(events=[
            event("llm_call", 2, {
                "call_number": 1,
                "message_count": 3,
                "prompt_character_count": 4820,
                "tool_schema_character_count": 2100,
                "response_character_count": 250,
            })
        ]))
        self.assertEqual(metrics.llm_calls[0].message_count, 3)
        self.assertEqual(metrics.llm_calls[0].tool_schema_character_count, 2100)
        self.assertEqual(metrics.prompt_character_count_total, 4820)
        self.assertEqual(metrics.response_character_count_total, 250)


if __name__ == "__main__":
    unittest.main()
