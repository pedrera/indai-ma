import unittest

from agent_models import AgentRunResult, AgentStatus
from generation import AgentJob, GenerationStatus


class FakeProvider:
    provider_name = "test"
    model = "test-model"

    def cancel(self):
        pass


class FailedAgent:
    def run(self, request, timeout_seconds):
        return AgentRunResult(
            status=AgentStatus.FAILED,
            content="Controlled plan validation error.",
            tool_executions=(),
            observations=(),
            decision_count=1,
            termination_reason="plan_validation_failed",
        )


class AgentJobTests(unittest.TestCase):
    def test_controlled_agent_failure_preserves_content_and_execution_status(self):
        job = AgentJob(FailedAgent(), "request", 10, FakeProvider())
        job._run()
        result = job.poll()
        self.assertEqual(result.status, GenerationStatus.COMPLETED)
        self.assertEqual(result.execution_status, AgentStatus.FAILED.value)
        self.assertEqual(result.content, "Controlled plan validation error.")


if __name__ == "__main__":
    unittest.main()
