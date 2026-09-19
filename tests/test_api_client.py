import unittest
import httpx
from types import SimpleNamespace

from api_client import (AnalysisApiClient, AnalysisApiClientError,
                        AnalysisApiConnectionError, AnalysisApiResponseError,
                        AnalysisApiTimeoutError)
from execution_view import execution_header_parts
from clipboard_text import build_business_copy_payload


PAYLOAD = {
    "operation_id": "abc123", "status": "completed", "summary": "SHORT 25 GWh",
    "metrics": [], "explanations": [], "evidence": [], "provenance": [], "warnings": [],
    "routing": {"selected_agents": ["ProcurementAgent"], "skipped_agents": ["CommercialAgent", "RiskAgent"], "method": "deterministic", "reasons": []},
    "specialists": [], "diagnostics": {"llm_calls": 0, "rag_calls": 0, "tool_calls": 2},
}


class ApiClientTests(unittest.TestCase):
    def client(self, handler):
        return AnalysisApiClient("http://test", transport=httpx.MockTransport(handler))

    def test_sends_exact_public_payload_and_endpoint(self):
        seen = {}
        def handler(request):
            seen.update(method=request.method, path=request.url.path, body=request.read())
            return httpx.Response(200, json=PAYLOAD)
        result = self.client(handler).analyze("pregunta")
        self.assertEqual((seen['method'], seen['path']), ('POST', '/api/v1/analysis'))
        self.assertEqual(seen['body'], b'{"text":"pregunta"}')
        self.assertEqual(result.status, 'completed')

    def test_connection_timeout_http_and_schema_errors_are_safe(self):
        def timeout(request):
            raise httpx.ReadTimeout("late")
        with self.assertRaises(AnalysisApiTimeoutError):
            self.client(timeout).analyze('x')
        def status(request):
            return httpx.Response(503, json={"detail": "secret"})
        with self.assertRaises(AnalysisApiResponseError):
            self.client(status).analyze('x')
        def malformed(request):
            return httpx.Response(200, content=b'not-json')
        with self.assertRaises(AnalysisApiResponseError):
            self.client(malformed).analyze('x')

    def test_business_header_uses_public_routing_method(self):
        result = self.client(lambda request: httpx.Response(200, json=PAYLOAD)).analyze('x')
        execution = SimpleNamespace(status='completed', snapshot=SimpleNamespace(elapsed_seconds=0.1),
                                    configuration={}, result=result, operation_id='abc123')
        parts = execution_header_parts(execution)
        self.assertIn('Routing: deterministic', parts)
        self.assertIn('Agentes: ProcurementAgent', parts)

    def test_business_copy_all_uses_public_api_result(self):
        payload = dict(PAYLOAD)
        payload.update({
            "metrics": [{"label": "Posición", "value": "SHORT 25 GWh", "origin": "Calculado"}],
            "provenance": [{"category": "ENTRADA OPERATIVA", "label": "Demanda", "value": "120 GWh", "origin": "Consulta"},
                            {"category": "VALOR CALCULADO", "label": "Exposición", "value": "1,050,000 EUR", "origin": "Herramienta"}],
            "specialists": [{"agent_name": "ProcurementAgent", "status": "completed", "llm_calls": 0, "rag_calls": 0, "tool_calls": 2}],
        })
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        text = build_business_copy_payload(result, operation_id='abc123', status='completed', duration_seconds=0.2)
        for expected in ('SHORT 25 GWh', '1,050,000 EUR', 'ProcurementAgent', 'abc123',
                         'Demanda', 'Exposición', 'LLM calls: 0', 'RAG calls: 0', 'Tool calls: 2'):
            self.assertIn(expected, text)
        self.assertNotIn('SupervisorResult', text)
        self.assertNotIn('secret', text)

    def test_recommendation_is_optional_and_preserves_structured_fields(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {
            'action': 'Cubrir el SHORT operativo.', 'is_complete': True,
            'rationale': ['Exceso y SHORT son magnitudes distintas.'],
            'contractual_implication': 'Exceso contractual: 0.2 GWh.',
            'operational_implication': 'SHORT: 0.5 GWh; exposición: 21000 EUR.',
            'risk_implication': 'Riesgo base SHORT.',
            'supporting_metrics': [['Exceso contractual', '0.2 GWh'], ['SHORT', '0.5 GWh'],
                                   ['Exposición spot', '21000 EUR']],
            'warnings': [],
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        self.assertTrue(result.recommendation.is_complete)
        self.assertEqual(result.recommendation.supporting_metrics[0][1], '0.2 GWh')
        self.assertEqual(result.recommendation.supporting_metrics[1][1], '0.5 GWh')
        self.assertEqual(result.recommendation.operational_implication, payload['recommendation']['operational_implication'])

    def test_old_payload_without_recommendation_remains_valid(self):
        result = self.client(lambda request: httpx.Response(200, json=PAYLOAD)).analyze('x')
        self.assertIsNone(result.recommendation)


if __name__ == '__main__':
    unittest.main()
