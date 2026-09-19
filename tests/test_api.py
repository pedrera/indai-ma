import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from api.app import create_app
from application_service import AnalysisService, AnalysisServiceError
from runtime_config import LLMRuntimeConfig


RUNTIME = LLMRuntimeConfig(384, 384, 30, False)
PROCUREMENT = ("La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados "
              "y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento.")


class ApiTests(unittest.TestCase):
    def client(self, service=None):
        return TestClient(create_app(service or AnalysisService(), RUNTIME), raise_server_exceptions=False)

    def test_health_and_ready(self):
        client = self.client()
        self.assertEqual(client.get('/health').json(), {'status': 'ok'})
        self.assertEqual(client.get('/ready').json(), {'status': 'ready'})

    def test_procurement_uses_application_service_boundary(self):
        response = self.client().post('/api/v1/analysis', json={'text': PROCUREMENT})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['diagnostics']['llm_calls'], 0)
        self.assertEqual(data['diagnostics']['tool_calls'], 2)
        self.assertIn('25 GWh', data['summary'])
        self.assertIn('1,050,000 EUR', data['summary'])

    def test_blank_and_missing_request_are_rejected(self):
        client = self.client()
        self.assertEqual(client.post('/api/v1/analysis', json={'text': ''}).status_code, 422)
        self.assertEqual(client.post('/api/v1/analysis', json={}).status_code, 422)

    def test_controlled_result_is_not_http_error(self):
        response = self.client().post('/api/v1/analysis', json={'text': '¿Qué debería hacer?'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()['status'], {'needs_input', 'rejected_input'})

    def test_service_error_is_sanitized(self):
        service = Mock()
        service.analyze.side_effect = AnalysisServiceError('internal path C:\\secret')
        response = self.client(service).post('/api/v1/analysis', json={'text': PROCUREMENT})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'service_unavailable')
        self.assertNotIn('C:\\secret', response.text)

    def test_unexpected_error_is_sanitized(self):
        service = Mock()
        service.analyze.side_effect = RuntimeError('secret stack path')
        response = self.client(service).post('/api/v1/analysis', json={'text': PROCUREMENT})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret stack path', response.text)

    def test_openapi_contains_only_intended_application_endpoints(self):
        paths = set(self.client().get('/openapi.json').json()['paths'])
        self.assertEqual(paths, {'/health', '/ready', '/api/v1/analysis'})


if __name__ == '__main__':
    unittest.main()
