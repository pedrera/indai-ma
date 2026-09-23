import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from agent_models import AgentDecision
from embeddings import EmbeddingProvider
from industrial_gases.industrial_knowledge import IndustrialKnowledgeService
from industrial_gases.operational_attention import OperationalAttentionService
from industrial_gases.portfolio import SupplyPortfolioResult, SupplyPortfolioService
from industrial_gases.portfolio_ui import _canonical_portfolio_request
from industrial_gases.service import SupplyAssuranceService
from industrial_gases.supply_agent import (
    ProviderSupplyDecisionModel,
    SupplyAgent,
    SupplyAgentRequest,
    SupplyAgentStatus,
    SupplyAgentTools,
)
from llm_client import LLMResponse
from rag_service import RAGService
from vector_store import LocalVectorStore


class AgentTestEmbeddings(EmbeddingProvider):
    model = "supply-agent-tests"
    terms = ("oxygen", "medical", "hospital", "supply", "contract", "procedure",
             "installation", "co2", "nitrogen", "n2", "policy", "inventory",
             "delivery", "stock", "nm3")

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        lowered = text.casefold()
        vector = [float(lowered.count(term)) for term in self.terms]
        return vector if any(vector) else [0.0] * (len(self.terms) - 1) + [-1.0]


def _call(name, **arguments):
    return AgentDecision("call_tool", "tool", name, arguments)


class QueueDecisionModel:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = []

    def decide(self, question, state, tools, timeout_seconds=None):
        self.calls.append((question, state, tools))
        return self.decisions.pop(0)


class SupplyAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.embeddings = AgentTestEmbeddings()
        rag = RAGService(self.embeddings, LocalVectorStore(self.temp.name, self.embeddings.model))
        self.knowledge = IndustrialKnowledgeService(rag)
        self.portfolio = SupplyPortfolioService().evaluate(_canonical_portfolio_request())
        self.attention = OperationalAttentionService().project(self.portfolio)
        self.item_id = "hospital-costa-sur-o2"
        self.baseline_item = self.portfolio.items[0]

    def agent(self, question, model, portfolio=None, attention=None, service=None, knowledge=None):
        return SupplyAgent(
            SupplyAgentRequest(question, self.item_id),
            portfolio or self.portfolio,
            attention or self.attention,
            knowledge if knowledge is not None else self.knowledge,
            model,
            service=service,
        )

    def test_domain_only_answer_uses_original_projection_without_rag_or_recalculation(self):
        decisions = QueueDecisionModel(
            _call("get_supply_position", item_id=self.item_id),
            AgentDecision("finish", "finish", answer="El inventario será 400 kg antes de la entrega."),
        )
        service = Mock(spec=SupplyAssuranceService)
        knowledge_factory = Mock(side_effect=AssertionError("RAG must stay lazy"))
        result = self.agent("¿Cuál será el inventario antes de la próxima entrega?", decisions,
                            service=service, knowledge=knowledge_factory).run()
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(result.answer, "El inventario será 400 kg antes de la entrega.")
        self.assertEqual(result.knowledge_status, "not_requested")
        self.assertEqual([call["name"] for call in result.tool_executions], ["get_supply_position"])
        self.assertEqual(result.evidence_references[0].evidence_type, "domain")
        service.assess.assert_not_called()
        self.assertFalse(hasattr(result, "recommendation"))
        knowledge_factory.assert_not_called()

    def test_attention_query_preserves_finding_and_distinguishes_stockout(self):
        model = QueueDecisionModel(
            _call("get_operational_attention", item_id=self.item_id),
            AgentDecision("finish", "finish", answer="La posición está por debajo del stock configurado, pero no tiene stockout proyectado."),
        )
        result = self.agent("¿Por qué aparece esta posición en atención?", model).run()
        finding = result.attention_item.facts[0].source_finding
        self.assertEqual(finding.code, "safety_stock_breach")
        self.assertEqual(result.position.result.projection.stockout_before_delivery, False)
        self.assertIn("stockout", result.answer)
        self.assertEqual(result.evidence_references[0].source_id, "finding:safety_stock_breach")

    def test_knowledge_only_query_returns_scoped_chunk_provenance(self):
        model = QueueDecisionModel(
            _call("search_industrial_knowledge", item_id=self.item_id,
                  query="Hospital Costa Sur medical oxygen operating procedure"),
            AgentDecision("finish", "finish", answer="El procedimiento separa la brecha de stock de un stockout."),
        )
        result = self.agent("¿Qué dice el procedimiento aplicable a esta instalación?", model).run()
        self.assertEqual(result.knowledge_status, "retrieved")
        self.assertTrue(result.knowledge_sources)
        self.assertTrue(all(source.chunk.metadata.get("scope") == "global"
                            or source.chunk.metadata.get("gas_product_id") == "medical-oxygen"
                            for source in result.knowledge_sources))
        self.assertTrue(any(reference.evidence_type == "knowledge" for reference in result.evidence_references))

    def test_combined_query_uses_domain_attention_and_knowledge_without_fusing_sources(self):
        model = QueueDecisionModel(
            _call("get_supply_position", item_id=self.item_id),
            _call("get_operational_attention", item_id=self.item_id),
            _call("search_industrial_knowledge", item_id=self.item_id, query="medical oxygen supply contract"),
            AgentDecision("finish", "finish", answer="La proyección registra una brecha de stock; el documento demo indica revisar los datos de la entrega."),
        )
        result = self.agent("¿Por qué requiere atención y qué información contractual es relevante?", model).run()
        self.assertEqual([entry["name"] for entry in result.tool_executions],
                         ["get_supply_position", "get_operational_attention", "search_industrial_knowledge"])
        self.assertEqual({reference.evidence_type for reference in result.evidence_references}, {"domain", "knowledge"})
        self.assertEqual(result.position.result.findings[0].code, "safety_stock_breach")
        self.assertTrue(result.knowledge_sources)

    def test_explicit_what_if_uses_existing_evaluator_and_keeps_baseline_immutable(self):
        original = self.baseline_item.result
        model = QueueDecisionModel(
            _call("get_supply_position", item_id=self.item_id),
            _call("evaluate_supply_what_if", item_id=self.item_id,
                  change_type="delivery_offset_days", value=-1),
            AgentDecision("finish", "finish", answer="Con la entrega un día antes, la proyección alternativa muestra 1,100 kg antes de entrega."),
        )
        result = self.agent("¿Qué ocurriría si la entrega llegase un día antes?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(result.scenario_analysis.baseline_result, original)
        self.assertEqual(result.scenario_analysis.baseline_result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(result.scenario_analysis.alternative_result.projection.inventory_immediately_before_delivery.value, 1100)
        self.assertEqual(result.scenario_analysis.alternative.alternative_request.installation.installation_id,
                         self.baseline_item.request.installation.installation_id)
        self.assertEqual(self.baseline_item.result, original)

    def test_unrequested_what_if_is_rejected_and_not_evaluated_automatically(self):
        model = QueueDecisionModel(
            _call("evaluate_supply_what_if", item_id=self.item_id,
                  change_type="delivery_offset_days", value=-1),
            AgentDecision("finish", "finish", answer="Puedo evaluar una hipótesis explícita si indicas el cambio."),
        )
        service = Mock(spec=SupplyAssuranceService)
        result = self.agent("¿Qué podemos hacer?", model, service=service).run()
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(result.tool_executions[0]["name"], "evaluate_supply_what_if")
        self.assertEqual(result.tool_executions[0]["result"]["status"], "operational_error")
        service.assess.assert_not_called()
        self.assertTrue(any("explicit what-if" in error for error in result.operational_errors))

    def test_explicit_what_if_rejects_physical_unit_mismatch_without_conversion(self):
        for question, amount in (
            ("¿Qué ocurriría si la entrega fuera 1000 Nm3?", 1000),
            ("¿Qué ocurriría si fueran 1000 kg o 2000 Nm3?", 2000),
        ):
            with self.subTest(question=question):
                model = QueueDecisionModel(
                    _call("evaluate_supply_what_if", item_id=self.item_id,
                          change_type="planned_delivery_quantity", value=amount),
                    AgentDecision("finish", "finish", answer="La unidad indicada no coincide con la unidad operativa."),
                )
                result = self.agent(question, model).run()
                self.assertIsNone(result.scenario_analysis)
                self.assertTrue(any("does not match operational unit" in error
                                    for error in result.operational_errors))
                self.assertEqual(result.position.request.delivery_plan.planned_quantity.unit, "kg")

    def test_explicit_rate_what_if_rejects_time_unit_mismatch_without_conversion(self):
        model = QueueDecisionModel(
            _call("evaluate_supply_what_if", item_id=self.item_id,
                  change_type="consumption_rate", value=700),
            AgentDecision("finish", "finish", answer="La unidad temporal indicada no coincide con la previsión."),
        )
        service = Mock(spec=SupplyAssuranceService)
        result = self.agent(
            "¿Qué ocurriría si el consumo fuera 700 kg/hour?", model, service=service,
        ).run()
        self.assertIsNone(result.scenario_analysis)
        self.assertTrue(any("Explicit time unit hour" in error
                            for error in result.operational_errors))
        service.assess.assert_not_called()

    def test_knowledge_search_failure_is_distinct_and_preserves_domain_evidence(self):
        model = QueueDecisionModel(
            _call("get_supply_position", item_id=self.item_id),
            _call("search_industrial_knowledge", item_id=self.item_id,
                  query="medical oxygen supply contract"),
            AgentDecision("finish", "finish", answer="La proyección estructurada continúa disponible."),
        )
        with patch.object(self.knowledge, "search", side_effect=OSError("offline")):
            result = self.agent("¿Qué documentos aplican y cuál es la posición?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(result.knowledge_status, "operational_error")
        self.assertEqual(result.position.result.status, "COMPLETED")
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertTrue(any("search_industrial_knowledge" in error
                            for error in result.operational_errors))
        self.assertEqual([entry["name"] for entry in result.tool_executions],
                         ["get_supply_position", "search_industrial_knowledge"])
        self.assertEqual(result.tool_executions[-1]["result"]["status"], "operational_error")

    def test_no_applicable_knowledge_is_not_confused_with_missing_domain_inputs(self):
        model = QueueDecisionModel(
            _call("search_industrial_knowledge", item_id=self.item_id, query="astrophysics quasars"),
            AgentDecision("finish", "finish", answer="No se recuperó documentación aplicable."),
        )
        result = self.agent("¿Qué evidencia hay sobre quasars?", model).run()
        self.assertEqual(result.knowledge_status, "no_applicable_knowledge")
        self.assertEqual(result.knowledge_sources, ())
        self.assertEqual(result.domain_status, "COMPLETED")

    def test_unsupported_request_is_reported_without_inventing_evidence_or_scenarios(self):
        model = QueueDecisionModel(
            AgentDecision("request_information", "request_information",
                          question="No hay datos estructurados ni documentos aplicables para responder.",
                          missing_fields=["unavailable_fact"]),
        )
        result = self.agent("¿Cuál será el inventario el próximo año?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(result.unsupported_questions, ("unavailable_fact",))
        self.assertEqual(result.tool_executions, ())
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(result.position.result.status, "COMPLETED")

    def test_provider_failure_after_tool_preserves_domain_evidence_and_error_status(self):
        class Provider:
            provider_name = "fake"
            model = "fake-model"

            def __init__(self):
                self.calls = 0

            def generate_response(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(json.dumps({"action": "call_tool", "tool_name": "get_supply_position",
                                                  "arguments": {"item_id": "hospital-costa-sur-o2"},
                                                  "decision_summary": "position"}))
                raise RuntimeError("provider unavailable")

        provider = Provider()
        result = self.agent(
            "¿Cuál será el inventario antes de entrega?", ProviderSupplyDecisionModel(provider),
        ).run()
        self.assertEqual(result.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertIsNone(result.answer)
        self.assertEqual(result.position.result.status, "COMPLETED")
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertTrue(result.operational_errors)
        self.assertEqual(len(result.tool_executions), 1)

    def test_invalid_and_missing_domain_states_are_preserved(self):
        for status, changes in (
            ("INVALID", {"projection": None, "validation_errors": ("invalid_identity",), "missing_inputs": ()}),
            ("MISSING_INPUTS", {"projection": None, "validation_errors": (), "missing_inputs": ("safety_stock",)}),
        ):
            with self.subTest(status=status):
                changed = replace(self.baseline_item.result, status=status, findings=(), **changes)
                portfolio = SupplyPortfolioResult((replace(self.baseline_item, result=changed), *self.portfolio.items[1:]))
                attention = OperationalAttentionService().project(portfolio)
                model = QueueDecisionModel(
                    _call("get_supply_position", item_id=self.item_id),
                    AgentDecision("finish", "finish", answer=f"Domain result is {status}."),
                )
                result = self.agent("¿Cuál es el estado?", model, portfolio, attention).run()
                self.assertEqual(result.domain_status, status)
                self.assertIsNone(result.position.result.projection)
                self.assertEqual(result.position.result.missing_inputs, changed.missing_inputs)
                self.assertEqual(result.position.result.validation_errors, changed.validation_errors)

    def test_cross_position_tool_selection_and_unknown_identity_fail_closed(self):
        tools = SupplyAgentTools(self.portfolio, self.attention, self.knowledge)
        with self.assertRaisesRegex(ValueError, "selected portfolio position"):
            tools.execute("get_supply_position", {"item_id": "alimentos-sur-malaga-co2"},
                          request=SupplyAgentRequest("question", self.item_id))
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            SupplyAgent(SupplyAgentRequest("question", "not-a-position"), self.portfolio,
                        self.attention, self.knowledge, QueueDecisionModel())

    def test_retrieved_chunk_citations_are_validated_against_available_sources(self):
        model = QueueDecisionModel(
            _call("search_industrial_knowledge", item_id=self.item_id, query="hospital oxygen procedure"),
            AgentDecision("finish", "finish", answer="Valid [not-a-real-chunk-id] and [0000000000000000:000000000000]."),
        )
        result = self.agent("¿Qué dice el procedimiento?", model).run()
        self.assertIn("[fuente no verificada]", result.answer)
        self.assertTrue(result.knowledge_sources)


if __name__ == "__main__":
    unittest.main()
