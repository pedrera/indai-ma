import unittest
from unittest.mock import Mock

from business_api_adapter import ApiJobAdapter, create_business_api_job, BusinessApiRequest


class BusinessApiAdapterTests(unittest.TestCase):
    def test_job_adapter_delegates_only_to_api_client(self):
        client = Mock()
        expected = object()
        client.analyze.return_value = expected
        request = BusinessApiRequest("pregunta", access_token="adapter-test-token", tenant_id="adapter-test-tenant")
        result = ApiJobAdapter(client).run(request, 300)
        self.assertIs(result, expected)
        client.analyze.assert_called_once_with("pregunta", None, access_token="adapter-test-token", tenant_id="adapter-test-tenant")

    def test_create_job_uses_client_timeout(self):
        client = Mock()
        client.config.analysis_timeout = 17
        job = create_business_api_job(BusinessApiRequest("pregunta", access_token="adapter-test-token", tenant_id="adapter-test-tenant"), client)
        self.assertEqual(job.timeout_seconds, 17)
        self.assertIsInstance(job.agent, ApiJobAdapter)
        self.assertIs(job.agent.client, client)


if __name__ == "__main__":
    unittest.main()
