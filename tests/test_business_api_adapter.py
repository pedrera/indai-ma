import unittest
from unittest.mock import Mock

from business_api_adapter import ApiJobAdapter, create_business_api_job


class BusinessApiAdapterTests(unittest.TestCase):
    def test_job_adapter_delegates_only_to_api_client(self):
        client = Mock()
        expected = object()
        client.analyze.return_value = expected
        result = ApiJobAdapter(client).run("pregunta", 300)
        self.assertIs(result, expected)
        client.analyze.assert_called_once_with("pregunta")

    def test_create_job_uses_client_timeout(self):
        client = Mock()
        client.config.analysis_timeout = 17
        job = create_business_api_job("pregunta", client)
        self.assertEqual(job.timeout_seconds, 17)
        self.assertIsInstance(job.agent, ApiJobAdapter)
        self.assertIs(job.agent.client, client)


if __name__ == "__main__":
    unittest.main()
