import json
import re
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch

from agent_models import AgentDecision
from diagnostics import PerformanceRecorder, PerformanceStatus
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
from streamlit.testing.v1 import AppTest
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


class ObservedKnowledgeDecisionModel:
    def __init__(self, *, attention_first=False, fabricate=False):
        self.attention_first = attention_first
        self.fabricate = fabricate
        self.calls = []

    def decide(self, question, state, tools, timeout_seconds=None):
        self.calls.append((question, state, tools))
        if self.attention_first and not any(
            observation.get("name") == "get_operational_attention"
            for observation in state["tool_observations"]
        ):
            return _call("get_operational_attention", item_id=state["selected_item_id"])
        observation = next(
            (item for item in state["tool_observations"]
             if item.get("name") == "search_industrial_knowledge"), None,
        )
        sources = (observation or {}).get("result", {}).get("sources", [])
        if self.fabricate:
            answer = "El contrato garantiza una reserva específica. [chunk_id:1]"
        elif sources:
            answer = f"La fuente aplicable recuperada es {sources[0]['document_name']}. [chunk_id:{sources[0]['chunk_id']}]"
        else:
            answer = "No se recuperó documentación aplicable."
        return AgentDecision("finish", "finish", answer=answer)


class TracedFakeProvider:
    provider_name = "fake"
    model = "fake-model"

    def __init__(self, recorder, *decisions):
        self.recorder = recorder
        self.decisions = list(decisions)
        self.call_count = 0

    def generate_response(self, messages, **kwargs):
        self.call_count += 1
        for stage in ("provider_start", "http_request", "model_inference"):
            event = self.recorder.start_stage(stage)
            self.recorder.complete_stage(event, fixture_provider=True)
        call = self.recorder.start_llm_call(
            call_number=self.call_count, purpose="supply_agent_decision", provider_round=1,
            message_count=len(messages), prompt_character_count=sum(
                len(message.get("content", "")) for message in messages
            ), tool_schema_character_count=0,
        )
        self.recorder.complete_llm_call(
            call, request_setup_seconds=0.001, response_stream_seconds=0.002,
            response_character_count=10,
        )
        return LLMResponse(json.dumps(self.decisions.pop(0)))


def _render_supply_pipeline(snapshot):
    from pipeline_inspector import render_pipeline_inspector
    render_pipeline_inspector(snapshot)


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

    def agent(self, question, model, portfolio=None, attention=None, service=None, knowledge=None, recorder=None):
        return SupplyAgent(
            SupplyAgentRequest(question, self.item_id),
            portfolio or self.portfolio,
            attention or self.attention,
            knowledge if knowledge is not None else self.knowledge,
            model,
            recorder=recorder,
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

    def test_domain_only_direct_answer_needs_one_generation_and_no_retrieval(self):
        model = QueueDecisionModel(AgentDecision(
            "finish", "finish", answer="El inventario proyectado antes de entrega es 400 kg.",
        ))
        knowledge_factory = Mock(side_effect=AssertionError("domain-only query must not initialize RAG"))
        result = self.agent(
            "¿Cuál será el inventario antes de entrega?", model, knowledge=knowledge_factory,
        ).run()
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(result.knowledge_status, "not_requested")
        self.assertEqual(result.tool_executions, ())
        knowledge_factory.assert_not_called()

    def test_domain_only_unretrieved_document_claim_and_chunk_id_are_blocked(self):
        model = QueueDecisionModel(
            _call("get_operational_attention", item_id=self.item_id),
            AgentDecision("finish", "finish", answer="El contrato garantiza el nivel requerido. [chunk_id:1]"),
        )
        knowledge_factory = Mock(side_effect=AssertionError("domain-only query must not initialize RAG"))
        result = self.agent("¿Por qué requiere atención?", model, knowledge=knowledge_factory).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(result.knowledge_status, "not_requested")
        self.assertIn("No se recuperó documentación", result.answer)
        self.assertNotIn("contrato garantiza", result.answer)
        self.assertNotIn("chunk_id:1", result.answer)
        self.assertEqual(result.attention_item.facts[0].source_finding.code, "safety_stock_breach")
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
        model = ObservedKnowledgeDecisionModel()
        result = self.agent(
            "What procedure applies to Hospital Costa Sur medical oxygen?", model,
        ).run()
        self.assertEqual(result.knowledge_status, "retrieved")
        self.assertTrue(result.knowledge_sources)
        self.assertTrue(all(source.chunk.metadata.get("scope") == "global"
                            or source.chunk.metadata.get("gas_product_id") == "medical-oxygen"
                            for source in result.knowledge_sources))
        self.assertTrue(any(reference.evidence_type == "knowledge" for reference in result.evidence_references))
        self.assertEqual([entry["name"] for entry in result.tool_executions],
                         ["search_industrial_knowledge"])
        cited_id = result.answer.split("[chunk_id:", 1)[1].split("]", 1)[0]
        self.assertIn(cited_id, {source.chunk.chunk_id for source in result.knowledge_sources})

    def test_combined_query_uses_domain_attention_and_knowledge_without_fusing_sources(self):
        question = "¿Por qué requiere atención y qué información contractual es relevante?"
        identity = {
            "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
            "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen",
            "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        }
        knowledge_status, matching_sources = self.knowledge.search(identity=identity, query=question)
        self.assertEqual(knowledge_status, "retrieved")
        model = QueueDecisionModel(
            AgentDecision(
                "finish", "finish",
                answer=("La proyección registra una brecha de stock; la fuente demo aplicable indica "
                        f"revisar los datos de la entrega. [chunk_id:{matching_sources[0].chunk.chunk_id}]"),
            )
        )
        result = self.agent(question, model).run()
        self.assertEqual([entry["name"] for entry in result.tool_executions],
                         ["search_industrial_knowledge", "get_supply_position", "get_operational_attention"])
        self.assertEqual({reference.evidence_type for reference in result.evidence_references}, {"domain", "knowledge"})
        self.assertEqual(result.position.result.findings[0].code, "safety_stock_breach")
        self.assertTrue(result.knowledge_sources)
        self.assertTrue(all(
            source.chunk.metadata.get("scope") == "global"
            or (source.chunk.metadata.get("customer_id") == "hospital-costa-sur"
                and source.chunk.metadata.get("gas_product_id") == "medical-oxygen")
            for source in result.knowledge_sources
        ))
        self.assertFalse(any(
            source.chunk.metadata.get("gas_product_id") in {"co2", "n2"}
            for source in result.knowledge_sources
        ))
        self.assertEqual(len(model.calls), 1)
        observations = model.calls[0][1]["tool_observations"]
        self.assertEqual([observation["name"] for observation in observations],
                         ["search_industrial_knowledge", "get_supply_position",
                          "get_operational_attention"])
        self.assertNotIn("get_supply_position", {tool["name"] for tool in model.calls[0][2]})

    def test_grounded_generation_receives_scoped_sources_and_exact_citation_after_retrieval(self):
        question = "¿Por qué esta posición requiere atención y qué información contractual es relevante?"
        sequence = []
        real_knowledge = self.knowledge

        class RecordingKnowledge:
            def search(inner_self, **kwargs):
                sequence.append("retrieve")
                return real_knowledge.search(**kwargs)

        class ContextAwareProvider:
            provider_name = "fake"
            model = "fake-model"

            def __init__(inner_self):
                inner_self.calls = 0
                inner_self.cited = None

            def generate_response(inner_self, messages, **kwargs):
                inner_self.calls += 1
                sequence.append("generate")
                prompt = messages[0]["content"]
                self.assertIn('"available_citations"', prompt)
                self.assertIn('"inventory_immediately_before_delivery"', prompt)
                self.assertIn('"safety_stock_breach"', prompt)
                self.assertIn("hospital_o2_supply_contract.txt", prompt)
                match = re.search(r'\[chunk_id:([^\]\r\n]+)\]', prompt)
                self.assertIsNotNone(match)
                inner_self.cited = match.group(1)
                answer = (
                    "La proyección registra una brecha de stock; la fuente contractual aplicable "
                    f"describe las condiciones pertinentes. [chunk_id:{inner_self.cited}]"
                )
                return LLMResponse(json.dumps({
                    "action": "finish", "answer": answer, "decision_summary": "grounded answer",
                }))

        provider = ContextAwareProvider()
        result = self.agent(
            question, ProviderSupplyDecisionModel(provider), knowledge=RecordingKnowledge(),
        ).run()

        self.assertEqual(sequence, ["retrieve", "generate"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertIn(f"[chunk_id:{provider.cited}]", result.answer)
        self.assertIn(provider.cited, {source.chunk.chunk_id for source in result.knowledge_sources})
        self.assertEqual({reference.evidence_type for reference in result.evidence_references},
                         {"domain", "knowledge"})
        self.assertEqual(result.position.result.findings[0].code, "safety_stock_breach")
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertTrue(result.attention_item.facts)

    def test_documentary_claim_without_citation_is_rejected_even_with_retrieval(self):
        model = QueueDecisionModel(AgentDecision(
            "finish", "finish", answer="El contrato establece condiciones de suministro aplicables.",
        ))
        result = self.agent("What does the contract say for Hospital Costa Sur medical oxygen?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertIn("could not be verified", result.answer.lower(),
                      f"answer={result.answer!r}, sources={len(result.knowledge_sources)}")
        self.assertTrue(result.knowledge_sources)
        self.assertNotIn("establece condiciones", result.answer)

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

    def test_explicit_delivery_horizons_and_relative_language_use_deterministic_scenario(self):
        cases = (
            ("delivery in 3 days instead of 4", 3),
            ("delivery in 3 days", -1),
            ("one day earlier", -1),
            ("en 3 días en lugar de 4", 3),
            ("adelanta la entrega un día", -1),
        )
        for question, tool_value in cases:
            with self.subTest(question=question):
                model = QueueDecisionModel(
                    _call("evaluate_supply_what_if", item_id=self.item_id,
                          change_type="delivery_offset_days", value=tool_value),
                    AgentDecision("finish", "finish", answer="La hipótesis se ha evaluado con la proyección determinista."),
                )
                knowledge_factory = Mock(side_effect=AssertionError("pure what-if must not initialize RAG"))
                baseline_request = self.baseline_item.request
                baseline_result = self.baseline_item.result
                result = self.agent(
                    question, model, knowledge=knowledge_factory,
                ).run()

                self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
                self.assertIsNotNone(result.scenario_analysis)
                scenario = result.scenario_analysis
                self.assertEqual(scenario.baseline_result, baseline_result)
                alt_request = scenario.alternative.alternative_request
                self.assertEqual(alt_request.reference_time, baseline_request.reference_time)
                self.assertEqual(
                    alt_request.delivery_plan.planned_delivery_at,
                    baseline_request.reference_time + timedelta(days=3),
                )
                self.assertEqual(alt_request.installation, baseline_request.installation)
                self.assertEqual(alt_request.gas_product, baseline_request.gas_product)
                self.assertEqual(alt_request.delivery_plan.planned_quantity,
                                 baseline_request.delivery_plan.planned_quantity)
                projection = scenario.alternative_result.projection
                self.assertEqual(projection.consumption_until_delivery.value, 2100)
                self.assertEqual(projection.inventory_immediately_before_delivery.value, 1100)
                self.assertEqual(projection.safety_stock_gap_before_delivery.value, -400)
                self.assertFalse(projection.stockout_before_delivery)
                self.assertEqual(projection.inventory_immediately_after_delivery.value, 5100)
                self.assertEqual(projection.days_of_supply, baseline_result.projection.days_of_supply)
                self.assertEqual(scenario.baseline_result.projection.inventory_immediately_before_delivery.value, 400)
                self.assertEqual(self.baseline_item.request, baseline_request)
                self.assertEqual(self.baseline_item.result, baseline_result)
                self.assertEqual(result.knowledge_status, "not_requested")
                knowledge_factory.assert_not_called()

    def test_explicit_delivery_baseline_conflict_does_not_evaluate_or_reinterpret(self):
        question = "¿Qué ocurriría si la entrega llegara en 3 días en lugar de 5?"
        model = QueueDecisionModel(
            _call("evaluate_supply_what_if", item_id=self.item_id,
                  change_type="delivery_offset_days", value=3),
            AgentDecision("request_information", "request_information",
                          question="La fecha de referencia indicada no coincide con la entrega estructurada. ¿Qué horizonte debo usar?",
                          missing_fields=["delivery_baseline"]),
        )
        service = Mock(spec=SupplyAssuranceService)
        knowledge_factory = Mock(side_effect=AssertionError("pure what-if must not initialize RAG"))
        result = self.agent(question, model, service=service, knowledge=knowledge_factory).run()
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(result.tool_executions[0]["result"]["status"], "operational_error")
        self.assertIn("conflicts with the structured baseline", result.tool_executions[0]["result"]["message"])
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertIn("delivery_baseline", result.unsupported_questions)
        service.assess.assert_not_called()
        knowledge_factory.assert_not_called()

    def test_vague_delivery_change_is_not_normalized_or_evaluated(self):
        question = "Please deliver earlier."
        model = QueueDecisionModel(
            _call("evaluate_supply_what_if", item_id=self.item_id,
                  change_type="delivery_offset_days", value=-1),
            AgentDecision("finish", "finish", answer="A specific number of days is needed."),
        )
        result = self.agent(question, model, knowledge=Mock(
            side_effect=AssertionError("pure what-if must not initialize RAG"),
        )).run()
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(result.tool_executions[0]["result"]["status"], "operational_error")
        self.assertTrue(any("explicit what-if" in error for error in result.operational_errors))

    def test_delivery_what_if_preserves_quantity_and_consumption_rate_paths(self):
        cases = (
            ("What if the planned delivery were 5000 kg?", "planned_delivery_quantity", 5000),
            ("What if the consumption rate were 600 kg/day?", "consumption_rate", 600),
        )
        for question, change_type, value in cases:
            with self.subTest(change_type=change_type):
                model = QueueDecisionModel(
                    _call("evaluate_supply_what_if", item_id=self.item_id,
                          change_type=change_type, value=value),
                    AgentDecision("finish", "finish", answer="The explicit scenario was evaluated."),
                )
                result = self.agent(
                    question, model,
                    knowledge=Mock(side_effect=AssertionError("what-if must not initialize RAG")),
                ).run()
                self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
                self.assertIsNotNone(result.scenario_analysis)
                self.assertEqual(result.scenario_analysis.alternative.id,
                                 "planned-delivery-quantity" if change_type == "planned_delivery_quantity"
                                 else "consumption-rate")

    def test_invalid_or_missing_baseline_cannot_be_used_for_delivery_what_if(self):
        for status, changes in (
            ("INVALID", {"projection": None, "validation_errors": ("invalid_identity",), "missing_inputs": ()}),
            ("MISSING_INPUTS", {"projection": None, "validation_errors": (), "missing_inputs": ("safety_stock",)}),
        ):
            with self.subTest(status=status):
                changed = replace(self.baseline_item.result, status=status, findings=(), **changes)
                portfolio = SupplyPortfolioResult((replace(self.baseline_item, result=changed), *self.portfolio.items[1:]))
                attention = OperationalAttentionService().project(portfolio)
                model = QueueDecisionModel(
                    _call("evaluate_supply_what_if", item_id=self.item_id,
                          change_type="delivery_offset_days", value=3),
                    AgentDecision("finish", "finish", answer="No complete baseline is available."),
                )
                service = Mock(spec=SupplyAssuranceService)
                result = self.agent(
                    "delivery in 3 days", model, portfolio, attention, service,
                ).run()
                self.assertIsNone(result.scenario_analysis)
                self.assertEqual(result.tool_executions[0]["result"]["status"], "operational_error")
                service.assess.assert_not_called()

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
            _call("get_operational_attention", item_id=self.item_id),
            AgentDecision("finish", "finish", answer="El contrato contiene una cláusula de reserva."),
        )
        with patch.object(self.knowledge, "search", side_effect=OSError("offline")):
            result = self.agent("¿Qué documentos aplican y cuál es la posición?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(result.knowledge_status, "operational_error")
        self.assertEqual(result.position.result.status, "COMPLETED")
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(result.attention_item.facts[0].source_finding.code, "safety_stock_breach")
        self.assertTrue(any("search_industrial_knowledge" in error
                            for error in result.operational_errors))
        self.assertEqual([entry["name"] for entry in result.tool_executions],
                         ["search_industrial_knowledge", "get_supply_position",
                          "get_operational_attention"])
        self.assertEqual(result.tool_executions[0]["result"]["status"], "operational_error")
        self.assertIn("No se pudo completar la búsqueda documental", result.answer)
        self.assertNotIn("cláusula de reserva", result.answer)
        self.assertEqual(model.calls, [])

    def test_provider_failure_after_successful_retrieval_preserves_sources_and_domain_evidence(self):
        class FailedProvider:
            provider_name = "fake"
            model = "fake-model"

            def generate_response(inner_self, messages, **kwargs):
                raise RuntimeError("provider unavailable")

        question = "¿Por qué requiere atención y qué información contractual aplica?"
        result = self.agent(
            question, ProviderSupplyDecisionModel(FailedProvider()),
        ).run()
        self.assertEqual(result.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertEqual(result.knowledge_status, "retrieved")
        self.assertTrue(result.knowledge_sources)
        self.assertEqual(result.position.result.status, "COMPLETED")
        self.assertEqual(result.position.result.findings[0].code, "safety_stock_breach")
        self.assertEqual(result.attention_item.facts[0].source_finding.code, "safety_stock_breach")
        self.assertTrue(any(reference.evidence_type == "knowledge"
                            for reference in result.evidence_references))
        self.assertTrue(any("SupplyAgentProviderError" in error
                            for error in result.operational_errors))

    def test_no_applicable_knowledge_is_not_confused_with_missing_domain_inputs(self):
        model = ObservedKnowledgeDecisionModel(fabricate=True)
        with patch.object(self.knowledge, "search", return_value=("no_applicable_knowledge", ())):
            result = self.agent("¿Qué información contractual hay sobre quasars?", model).run()
        self.assertEqual(result.knowledge_status, "no_applicable_knowledge")
        self.assertEqual(result.knowledge_sources, ())
        self.assertEqual(result.domain_status, "COMPLETED")
        self.assertEqual(result.position.result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertIn("No se recuperó ninguna fuente documental aplicable", result.answer)
        self.assertNotIn("contrato garantiza", result.answer)
        self.assertNotIn("chunk_id:1", result.answer)
        self.assertEqual(model.calls, [])
        self.assertEqual(model.calls, [])

    def test_unsupported_request_is_reported_without_inventing_evidence_or_scenarios(self):
        model = QueueDecisionModel(
            AgentDecision("request_information", "request_information",
                          question="No hay datos estructurados ni documentos aplicables para responder.",
                          missing_fields=["unavailable_fact"]),
        )
        result = self.agent("¿Cuál será el inventario el próximo año?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(result.unsupported_questions,
                         ("unavailable_fact", "applicable_documentary_source"))
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
            AgentDecision("finish", "finish", answer="The contract provides a reserve. [chunk_id:1]"),
        )
        result = self.agent(
            "What procedure applies to Hospital Costa Sur medical oxygen?", model,
        ).run()
        self.assertIn("could not be verified", result.answer)
        self.assertNotIn("[chunk_id:", result.answer)
        self.assertTrue(result.knowledge_sources)

    def test_pipeline_recorder_tracks_domain_only_work_and_marks_unused_rag(self):
        recorder = PerformanceRecorder("domain-only", "fake", "fake-model", "supply_agent")
        provider = TracedFakeProvider(
            recorder,
            {"action": "call_tool", "tool_name": "get_operational_attention",
             "arguments": {"item_id": self.item_id}, "decision_summary": "attention"},
            {"action": "finish", "answer": "La posición tiene un hecho de atención operativo.",
             "decision_summary": "complete"},
        )
        result = self.agent(
            "¿Por qué requiere atención?", ProviderSupplyDecisionModel(provider),
            recorder=recorder,
            knowledge=Mock(side_effect=AssertionError("RAG stays lazy")),
        ).run()
        recorder.finish(result.status.value)
        stages = {event.stage: event.status for event in recorder.snapshot().events}
        for stage in ("agent_start", "prompt_build", "provider_start", "http_request",
                      "model_inference", "llm_call", "parse_validation", "agent_decision",
                      "tool_execution", "final_response", "agent_final"):
            self.assertEqual(stages[stage], PerformanceStatus.COMPLETED, stage)
        for stage in ("document_parsing", "chunking", "embedding", "index_persistence",
                      "query_embedding", "vector_search", "retrieved_context"):
            self.assertEqual(stages[stage], PerformanceStatus.SKIPPED, stage)
        self.assertEqual(result.knowledge_status, "not_requested")
        inspector = AppTest.from_function(_render_supply_pipeline, args=(recorder.snapshot(),)).run()
        self.assertFalse(inspector.exception)
        rendered = "\n".join(item.value for item in inspector.markdown)
        self.assertIn("SupplyAgent", rendered)
        self.assertIn("Prompt Build", rendered)
        self.assertIn("Query Embedding", rendered)
        self.assertIn("pi-stage-skipped", rendered)
        self.assertNotIn("pi-stage-pending", rendered)
        self.assertEqual(provider.call_count, 2)
        self.assertLess(rendered.index("<strong>LLM Call #1</strong>"),
                        rendered.index("<strong>get_operational_attention</strong><time>"))
        self.assertLess(rendered.index("<strong>get_operational_attention</strong><time>"),
                        rendered.index("<strong>LLM Call #2</strong>"))

    def test_pipeline_inspector_records_explicit_what_if_tool_execution(self):
        recorder = PerformanceRecorder("what-if", "fake", "fake-model", "supply_agent")
        provider = TracedFakeProvider(
            recorder,
            {"action": "call_tool", "tool_name": "evaluate_supply_what_if",
             "arguments": {"item_id": self.item_id, "change_type": "delivery_offset_days", "value": 3},
             "decision_summary": "explicit delivery horizon"},
            {"action": "finish", "answer": "The +3 day delivery scenario was evaluated deterministically.",
             "decision_summary": "scenario complete"},
        )
        result = self.agent(
            "delivery in 3 days",
            ProviderSupplyDecisionModel(provider), recorder=recorder,
            knowledge=Mock(side_effect=AssertionError("what-if must not initialize RAG")),
        ).run()
        recorder.finish(result.status.value)
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual([event["name"] for event in result.tool_executions], ["evaluate_supply_what_if"])
        inspector = AppTest.from_function(_render_supply_pipeline, args=(recorder.snapshot(),)).run()
        self.assertFalse(inspector.exception)
        rendered = "\n".join(item.value for item in inspector.markdown)
        self.assertIn("evaluate_supply_what_if", rendered)
        self.assertLess(rendered.index("<strong>LLM Call #1</strong>"),
                        rendered.index("<strong>evaluate_supply_what_if</strong><time>"))
        self.assertLess(rendered.index("<strong>evaluate_supply_what_if</strong><time>"),
                        rendered.index("<strong>LLM Call #2</strong>"))
        self.assertEqual(result.knowledge_status, "not_requested")
        self.assertEqual(sum(event.stage == "llm_call" for event in recorder.snapshot().events), 2)

    def test_pipeline_recorder_tracks_combined_query_rag_and_final_stages(self):
        recorder = PerformanceRecorder("combined", "fake", "fake-model", "supply_agent")
        question = "¿Por qué requiere atención y qué información contractual aplica?"
        identity = {
            "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
            "application_id": "hospital-costa-sur-medical-oxygen",
            "gas_product_id": "medical-oxygen",
            "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
        }
        _, matching_sources = self.knowledge.search(identity=identity, query=question)
        self.assertTrue(matching_sources)
        rag = RAGService(
            self.embeddings,
            LocalVectorStore(self.temp.name, self.embeddings.model),
            recorder,
        )
        knowledge = IndustrialKnowledgeService(rag)
        provider = TracedFakeProvider(
            recorder,
            {"action": "finish", "answer": (
                "The retrieved contract source describes its applicable terms. "
                f"[chunk_id:{matching_sources[0].chunk.chunk_id}]"
            ),
             "decision_summary": "complete"},
        )
        result = self.agent(
            question,
            ProviderSupplyDecisionModel(provider), recorder=recorder, knowledge=knowledge,
        ).run()
        recorder.finish(result.status.value)
        stage_events = {}
        for event in recorder.snapshot().events:
            stage_events.setdefault(event.stage, []).append(event)
        for stage in ("provider_start", "http_request", "model_inference", "llm_call",
                      "parse_validation", "tool_execution", "query_embedding",
                      "vector_search", "retrieved_context", "final_response", "agent_final"):
            self.assertTrue(stage_events.get(stage), stage)
            self.assertTrue(all(event.status == PerformanceStatus.COMPLETED
                                for event in stage_events[stage]), stage)
        self.assertEqual(result.attention_item.facts[0].source_finding.code, "safety_stock_breach")
        self.assertTrue(result.knowledge_sources)
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED)
        inspector = AppTest.from_function(_render_supply_pipeline, args=(recorder.snapshot(),)).run()
        self.assertFalse(inspector.exception)
        rendered = "\n".join(item.value for item in inspector.markdown)
        self.assertIn("Query Embedding", rendered)
        self.assertIn("Retrieved Context", rendered)
        self.assertNotIn("pi-stage-pending", rendered)
        self.assertLess(rendered.index("Retrieved Context"), rendered.index("LLM Call #1"))
        self.assertEqual(sum(event.stage == "llm_call" for event in recorder.snapshot().events), 1)

    def test_provider_failure_is_visible_in_pipeline_and_keeps_domain_result(self):
        recorder = PerformanceRecorder("provider-failure", "fake", "fake-model", "supply_agent")

        class FailedProvider:
            provider_name = "fake"
            model = "fake-model"

            def __init__(self):
                self.calls = 0

            def generate_response(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(json.dumps({
                        "action": "call_tool", "tool_name": "get_supply_position",
                        "arguments": {"item_id": "hospital-costa-sur-o2"},
                        "decision_summary": "read position",
                    }))
                raise RuntimeError("fixture provider unavailable")

        result = self.agent(
            "¿Cuál es el inventario actual?", ProviderSupplyDecisionModel(FailedProvider()),
            recorder=recorder,
        ).run()
        recorder.finish(result.status.value)
        self.assertEqual(result.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertEqual(result.position.result.status, "COMPLETED")
        self.assertEqual(len(result.tool_executions), 1)
        self.assertEqual(result.evidence_references[0].source_id, "supply:hospital-costa-sur-o2")
        events = recorder.snapshot().events
        self.assertTrue(any(event.stage == "agent_decision" and event.status == PerformanceStatus.FAILED
                            for event in events))
        self.assertTrue(any(event.stage == "provider_start" and event.status == PerformanceStatus.SKIPPED
                            for event in events))
        self.assertTrue(result.operational_errors)
        inspector = AppTest.from_function(_render_supply_pipeline, args=(recorder.snapshot(),)).run()
        self.assertFalse(inspector.exception)
        rendered = "\n".join(item.value for item in inspector.markdown)
        self.assertIn("Error del proveedor", rendered)

    def test_empty_retrieval_blocks_documentary_prose_and_invented_chunk_id(self):
        model = ObservedKnowledgeDecisionModel(fabricate=True)
        with patch.object(self.knowledge, "search", return_value=("no_applicable_knowledge", ())):
            result = self.agent("¿Qué dice el contrato aplicable?", model).run()
        self.assertEqual(result.knowledge_status, "no_applicable_knowledge")
        self.assertEqual(result.knowledge_sources, ())
        self.assertIn("No se recuperó ninguna fuente documental aplicable", result.answer)
        self.assertNotIn("reserva específica", result.answer)
        self.assertNotIn("chunk_id:1", result.answer)

    def test_request_information_text_cannot_bypass_documentary_grounding(self):
        model = QueueDecisionModel(AgentDecision(
            "request_information", "request_information",
            question="El contrato garantiza una reserva. [chunk_id:invented]",
            missing_fields=["documentary_source"],
        ))
        with patch.object(self.knowledge, "search", return_value=("no_applicable_knowledge", ())):
            result = self.agent("¿Qué dice el contrato aplicable?", model).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertIn("No se recuperó ninguna fuente documental aplicable", result.answer)
        self.assertNotIn("garantiza una reserva", result.answer)
        self.assertNotIn("chunk_id:invented", result.answer)
        self.assertIn("applicable_documentary_source", result.unsupported_questions)


if __name__ == "__main__":
    unittest.main()
