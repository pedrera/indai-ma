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
            'risk_implication': 'Con un aumento del spot del 20%, la exposición pasa de 21000 a 25200 EUR (+4200 EUR).',
            'supporting_metrics': [['Exceso contractual', '0.2 GWh'], ['SHORT', '0.5 GWh'],
                                   ['Exposición spot', '21000 EUR']],
            'warnings': [],
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        self.assertTrue(result.recommendation.is_complete)
        self.assertEqual(result.recommendation.supporting_metrics[0][1], '0.2 GWh')
        self.assertEqual(result.recommendation.supporting_metrics[1][1], '0.5 GWh')
        self.assertEqual(result.recommendation.operational_implication, payload['recommendation']['operational_implication'])
        self.assertEqual(result.recommendation.risk_implication, 'Con un aumento del spot del 20%, la exposición pasa de 21000 a 25200 EUR (+4200 EUR).')
        self.assertEqual(result.recommendation.actions, [])

    def test_decision_plan_round_trip_preserves_order_and_all_transport_fields(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {
            'action': 'Cubrir SHORT.', 'is_complete': True,
            'actions': [{'id': 'operational-short', 'category': 'operational', 'action': 'Cubrir.',
                         'supporting_metrics': [['SHORT', '0.5 GWh']]}],
            'decision_plan': {
                'is_complete': True, 'warnings': ['seguimiento'],
                'steps': [{
                    'id': 'operational-short', 'category': 'operational', 'action': 'Cubrir.',
                    'rationale': 'SHORT estructurado.', 'supporting_metrics': [['SHORT', '999 GWh']],
                    'horizon': 'current_period', 'decision_state': 'review_required',
                    'depends_on': ['synthetic-source'], 'missing_information': ['dato futuro'],
                    'source_action_id': 'operational-short', 'source_agent': 'ProcurementAgent',
                }],
            },
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        recommendation = result.recommendation
        self.assertEqual(recommendation.actions[0].id, 'operational-short')
        step = recommendation.decision_plan.steps[0]
        self.assertEqual(step.id, 'operational-short')
        self.assertEqual(step.supporting_metrics, [('SHORT', '999 GWh')])
        self.assertEqual(step.depends_on, ['synthetic-source'])
        self.assertEqual(step.missing_information, ['dato futuro'])
        self.assertEqual(step.source_agent, 'ProcurementAgent')
        self.assertEqual(recommendation.decision_plan.warnings, ['seguimiento'])

    def test_decision_plan_and_action_id_are_optional_for_legacy_payloads(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {'action': 'Mantener.', 'is_complete': True,
                                     'actions': [{'category': 'operational', 'action': 'Revisar.'}]}
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        self.assertIsNone(result.recommendation.decision_plan)
        self.assertIsNone(result.recommendation.actions[0].id)

    def test_api_client_reconstructs_business_actions_in_order(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {
            'action': 'Cubrir el SHORT operativo.', 'is_complete': True,
            'actions': [
                {'category': 'operational', 'action': 'Revisar cobertura.',
                 'rationale': 'SHORT estructurado.', 'supporting_metrics': [['SHORT', '0.5 GWh']]},
                {'category': 'contractual', 'action': 'Revisar exceso.',
                 'rationale': None, 'supporting_metrics': [['Exceso', '0.2 GWh']]},
                {'category': 'contractual', 'action': 'Revisar TOP.',
                 'supporting_metrics': [['Déficit TOP', '2.8 GWh']]},
                {'category': 'risk', 'action': 'Revisar sensibilidad.',
                 'supporting_metrics': [['Delta', '+4,200 EUR']]},
            ],
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        self.assertEqual([item.category for item in result.recommendation.actions],
                         ['operational', 'contractual', 'contractual', 'risk'])
        self.assertEqual(result.recommendation.actions[0].supporting_metrics[0][1], '0.5 GWh')
        self.assertEqual(result.recommendation.actions[3].supporting_metrics[0][1], '+4,200 EUR')

    def test_copy_all_preserves_business_actions_without_aggregation(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {
            'action': 'Cubrir el SHORT operativo.', 'is_complete': True,
            'actions': [
                {'category': 'operational', 'action': 'Revisar cobertura.',
                 'rationale': 'SHORT estructurado.', 'supporting_metrics': [['SHORT', '0.5 GWh']]},
                {'category': 'contractual', 'action': 'Revisar exceso.',
                 'rationale': None, 'supporting_metrics': [['Exceso', '0.2 GWh']]},
                {'category': 'contractual', 'action': 'Revisar TOP.',
                 'rationale': 'Déficit separado.', 'supporting_metrics': [['Déficit TOP', '2.8 GWh']]},
                {'category': 'risk', 'action': 'Revisar sensibilidad.',
                 'rationale': None, 'supporting_metrics': [['Delta', '+4,200 EUR']]},
            ],
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        text = build_business_copy_payload(result, operation_id='abc123')
        self.assertIn('ACCIONES RECOMENDADAS', text)
        for expected in ('[OPERATIVA]', '[CONTRACTUAL]', '[RIESGO]', '0.5 GWh', '0.2 GWh', '2.8 GWh', '+4,200 EUR'):
            self.assertIn(expected, text)
        self.assertNotIn('3.3 GWh', text)
        self.assertNotIn('3.5 GWh', text)

    def test_old_recommendation_payload_without_actions_is_backward_compatible(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {'action': 'Mantener.', 'is_complete': True}
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        self.assertEqual(result.recommendation.actions, [])

    def test_old_payload_without_recommendation_remains_valid(self):
        result = self.client(lambda request: httpx.Response(200, json=PAYLOAD)).analyze('x')
        self.assertIsNone(result.recommendation)

    def test_business_copy_includes_recommendation_without_rebuilding_metrics(self):
        payload = dict(PAYLOAD)
        payload['recommendation'] = {
            'action': 'Cubrir el SHORT operativo.', 'is_complete': True,
            'contractual_implication': 'Exceso contractual independiente: 0.2 GWh.',
            'operational_implication': 'SHORT operativo: 0.5 GWh; exposición spot: 21000 EUR.',
            'risk_implication': 'Con un aumento del spot del 20%, la exposición pasa de 21000 a 25200 EUR (+4200 EUR).',
            'rationale': ['Son magnitudes diferentes.'], 'warnings': [],
            'supporting_metrics': [['Exceso', '0.2 GWh'], ['SHORT', '0.5 GWh']],
        }
        result = self.client(lambda request: httpx.Response(200, json=payload)).analyze('x')
        text = build_business_copy_payload(result, operation_id='abc123')
        for expected in ('RECOMENDACIÓN', 'Cubrir el SHORT operativo', '0.2 GWh', '0.5 GWh',
                         '21000 EUR', '25200 EUR', 'Con un aumento del spot'):
            self.assertIn(expected, text)


if __name__ == '__main__':
    unittest.main()
