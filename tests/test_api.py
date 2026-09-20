import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.app import create_app
from application_service import AnalysisService, AnalysisServiceError
from runtime_config import LLMRuntimeConfig
from business_recommendation import BusinessAction, BusinessRecommendation
from decision_plan import DecisionPlan, DecisionStep
from rag_models import RetrievalResult
from tests.test_commercial_agent import contract_matches
from clipboard_text import build_business_copy_payload
from api_client_models import ApiAnalysisResult
from business_output import ExecutiveResultProjection


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

    def test_recommendation_crosses_http_from_executive_projection(self):
        recommendation = BusinessRecommendation(
            action='Cubrir el SHORT operativo.', is_complete=True,
            rationale=('Exceso y SHORT son magnitudes distintas.',),
            contractual_implication='Exceso contractual: 0.2 GWh.',
            operational_implication='SHORT: 0.5 GWh; exposición: 21000 EUR.',
            risk_implication='Con un aumento del spot del 20%, la exposición pasa de 21000 a 25200 EUR (+4200 EUR).',
            supporting_metrics=(('Exceso contractual', '0.2 GWh'), ('SHORT', '0.5 GWh'),
                                ('Exposición spot', '21000 EUR')),
            actions=(
                BusinessAction('operational', 'Revisar la cobertura del SHORT operativo.',
                               supporting_metrics=(('SHORT operativo', '0.5 GWh'),)),
                BusinessAction('contractual', 'Revisar la implicación contractual del exceso mensual.',
                               supporting_metrics=(('Exceso contractual', '0.2 GWh'),)),
                BusinessAction('contractual', 'Revisar la previsión TOP.',
                               supporting_metrics=(('Déficit TOP', '2.8 GWh'),)),
                BusinessAction('risk', 'Considerar la sensibilidad del precio spot.',
                               supporting_metrics=(('Delta de exposición', '+4,200 EUR'),)),
            ),
        )
        service = Mock()
        service.analyze.return_value = SimpleNamespace(
            operation_id='op-hospital',
            executive=ExecutiveResultProjection('Resumen', recommendation=recommendation),
            supervisor_result=SimpleNamespace(
                status=SimpleNamespace(value='completed'),
                routing=SimpleNamespace(selected_agents=['CommercialAgent', 'ProcurementAgent', 'RiskAgent'],
                                        skipped_agents=[], routing_method='deterministic', routing_reasons=[]),
                specialist_results=[], total_llm_calls=0, total_rag_calls=1, total_tool_calls=6,
            ),
        )
        response = self.client(service).post('/api/v1/analysis', json={'text': 'hospital'})
        self.assertEqual(response.status_code, 200)
        recommendation_json = response.json()['recommendation']
        self.assertEqual(recommendation_json['contractual_implication'], 'Exceso contractual: 0.2 GWh.')
        self.assertEqual(recommendation_json['operational_implication'], 'SHORT: 0.5 GWh; exposición: 21000 EUR.')
        self.assertIn('25200', recommendation_json['risk_implication'])
        self.assertEqual(recommendation_json['supporting_metrics'][0][1], '0.2 GWh')
        self.assertEqual(recommendation_json['supporting_metrics'][1][1], '0.5 GWh')
        self.assertEqual([item['category'] for item in recommendation_json['actions']],
                         ['operational', 'contractual', 'contractual', 'risk'])
        self.assertEqual(recommendation_json['actions'][3]['supporting_metrics'][0][1], '+4,200 EUR')

    def test_decision_plan_crosses_http_without_recalculation(self):
        step = DecisionStep('operational-short', 'operational', 'Cubrir.',
                            'Estructurado.', (('SHORT', '999 GWh'),),
                            'current_period', 'review_required', ('synthetic-source',),
                            ('dato futuro',), 'operational-short', 'ProcurementAgent')
        recommendation = BusinessRecommendation(
            action='Cubrir.', is_complete=True,
            actions=(BusinessAction('operational', 'Cubrir.', id='operational-short'),),
            decision_plan=DecisionPlan((step,), True, ('seguimiento',)),
        )
        service = Mock()
        service.analyze.return_value = SimpleNamespace(
            operation_id='op-plan',
            executive=ExecutiveResultProjection('Resumen', recommendation=recommendation),
            supervisor_result=SimpleNamespace(
                status=SimpleNamespace(value='completed'),
                routing=SimpleNamespace(selected_agents=['ProcurementAgent'], skipped_agents=[],
                                        routing_method='deterministic', routing_reasons=[]),
                specialist_results=[], total_llm_calls=0, total_rag_calls=0, total_tool_calls=2,
            ),
        )
        data = self.client(service).post('/api/v1/analysis', json={'text': 'x'}).json()
        plan = data['recommendation']['decision_plan']
        self.assertEqual(plan['steps'][0]['id'], 'operational-short')
        self.assertEqual(plan['steps'][0]['supporting_metrics'], [['SHORT', '999 GWh']])
        self.assertEqual(plan['steps'][0]['depends_on'], ['synthetic-source'])
        self.assertEqual(data['recommendation']['actions'][0]['id'], 'operational-short')

    def test_canonical_real_service_decision_plan_crosses_http(self):
        query = ("Analiza la situación completa de Hospital Costa Sur.\n\n"
                 "El consumo acumulado es de 30 GWh y esperamos consumir otros\n"
                 "8 GWh hasta final de año.\n\nPara el próximo mes esperamos una demanda de 4.8 GWh y tenemos\n"
                 "4.3 GWh de suministro.\n\nEl precio spot actual es 42 EUR/MWh.\n\n"
                 "Analiza también qué ocurriría si el spot sube un 20% y dime qué\n"
                 "deberíamos revisar.")
        rag = Mock()
        rag.retrieve.return_value = RetrievalResult(contract_matches(), 'fixture')
        service = AnalysisService(rag_factory=lambda recorder, runtime: rag)
        response = self.client(service).post('/api/v1/analysis', json={'text': query})
        self.assertEqual(response.status_code, 200)
        recommendation = response.json()['recommendation']
        self.assertTrue(recommendation['decision_plan']['is_complete'])
        steps = recommendation['decision_plan']['steps']
        self.assertEqual([step['id'] for step in steps], [
            'operational-short', 'contractual-take-or-pay',
            'contractual-monthly-excess', 'risk-price-stress-20'])
        self.assertEqual(steps[3]['depends_on'], ['operational-short'])
        self.assertEqual(recommendation['actions'][0]['id'], 'operational-short')
        client_result = ApiAnalysisResult.model_validate(response.json())
        copied = build_business_copy_payload(client_result)
        self.assertLess(copied.index('PLAN DE DECISIÓN'), copied.index('ACCIONES RECOMENDADAS'))
        self.assertIn('Relacionado con: Cobertura del SHORT operativo', copied)


if __name__ == '__main__':
    unittest.main()
