import unittest
import tempfile
import re
from unittest.mock import Mock

from agent_models import AgentDecision
from industrial_gases.operational_attention import OperationalAttentionService
from industrial_gases.portfolio import SupplyPortfolioService
from industrial_gases.portfolio_query import (
    PortfolioQuery, SupplyAgentSessionContext, SupplyPortfolioQueryService,
)
from industrial_gases.supply_agent import (
    SupplyAgent, SupplyAgentRequest, SupplyAgentStatus,
    _enforce_portfolio_answer_boundary,
)
from industrial_gases.industrial_knowledge import IndustrialKnowledgeOperationalError, IndustrialKnowledgeService
from rag_models import DocumentChunk, RetrievedChunk
from rag_service import RAGService
from vector_store import LocalVectorStore
from tests.test_industrial_gases_supply_agent import (
    AgentTestEmbeddings, ObservedKnowledgeDecisionModel, QueueDecisionModel,
    _call,
)
from industrial_gases.portfolio_ui import _canonical_portfolio_request


class PortfolioSupplyAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.embeddings = AgentTestEmbeddings()
        self.knowledge = IndustrialKnowledgeService(
            RAGService(self.embeddings, LocalVectorStore(self.temp.name, self.embeddings.model)),
        )
        self.portfolio = SupplyPortfolioService().evaluate(_canonical_portfolio_request())
        self.attention = OperationalAttentionService().project(self.portfolio)

    def test_selection_query_is_deterministic_even_if_model_would_omit_or_invent_items(self):
        model = QueueDecisionModel(AgentDecision(
            "finish", "finish", answer="N2 has the safety-stock breach; O2 is omitted.",
        ))
        knowledge = Mock(side_effect=AssertionError("portfolio query must not use RAG"))
        result = SupplyAgent(
            SupplyAgentRequest("¿Qué posiciones requieren atención?"), self.portfolio,
            self.attention, knowledge, model,
        ).run()
        expected = ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2")
        self.assertEqual(result.portfolio_query.item_ids, expected)
        self.assertEqual(tuple(item.item.item_id for item in result.evidence_bundle.items), expected)
        self.assertIn("Hospital Costa Sur", result.answer)
        self.assertIn("CO2", result.answer)
        self.assertNotIn("N2", result.answer)
        self.assertEqual(model.calls, [])
        knowledge.assert_not_called()

    def test_hospital_followup_then_documentary_question_then_single_target_what_if(self):
        q1 = SupplyAgent(
            SupplyAgentRequest("¿Qué posiciones requieren atención?"), self.portfolio,
            self.attention, self.knowledge, QueueDecisionModel(),
        ).run()
        q2_model = QueueDecisionModel(AgentDecision(
            "finish", "finish", answer="El inventario O2 proyectado queda bajo el stock de seguridad.",
        ))
        q2 = SupplyAgent(
            SupplyAgentRequest("¿Por qué la del hospital?", session_context=q1.session_context),
            self.portfolio, self.attention, self.knowledge, q2_model,
        ).run()
        self.assertEqual(q2.portfolio_query.item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(q2.session_context.focused_item_id, "hospital-costa-sur-o2")

        q3_model = ObservedKnowledgeDecisionModel()
        q3 = SupplyAgent(
            SupplyAgentRequest("¿Qué información contractual es relevante?", session_context=q2.session_context),
            self.portfolio, self.attention, self.knowledge, q3_model,
        ).run()
        self.assertEqual(q3.portfolio_query.item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(q3.status, SupplyAgentStatus.COMPLETED, q3.answer)
        self.assertTrue(q3.evidence_bundle.items[0].knowledge_sources)
        self.assertTrue(all(
            source.chunk.metadata.get("scope") == "global"
            or source.chunk.metadata.get("gas_product_id") == "medical-oxygen"
            for source in q3.evidence_bundle.items[0].knowledge_sources
        ))
        cited_ids = re.findall(r"\[chunk_id:([^\]]+)\]", q3.answer or "")
        eligible_ids = {
            source.chunk.chunk_id for source in q3.knowledge_sources
        }
        eligible_ids.update(
            scoped.source.chunk.chunk_id for scoped in q3.evidence_bundle.global_sources
        )
        self.assertTrue(cited_ids)
        self.assertTrue(set(cited_ids).issubset(eligible_ids))

        q4_model = QueueDecisionModel(
            _call("evaluate_supply_what_if", item_id="hospital-costa-sur-o2",
                  change_type="delivery_offset_days", value=-1),
            AgentDecision("finish", "finish", answer="The explicit earlier delivery scenario was evaluated."),
        )
        q4_knowledge = Mock(side_effect=AssertionError("what-if must not use RAG"))
        q4 = SupplyAgent(
            SupplyAgentRequest("¿Y si la entrega llegara un día antes?", session_context=q3.session_context),
            self.portfolio, self.attention, q4_knowledge, q4_model,
        ).run()
        self.assertEqual(q4.portfolio_query.item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(q4.scenario_analysis)
        self.assertEqual(len([item for item in q4.evidence_bundle.items if item.scenario]), 1)
        q4_knowledge.assert_not_called()

    def test_ambiguous_multi_item_followup_and_stale_reference_fail_closed(self):
        multiple = SupplyAgentSessionContext(
            selected_item_ids=("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"),
        )
        model = QueueDecisionModel()
        result = SupplyAgent(
            SupplyAgentRequest("What if its delivery arrived earlier?", session_context=multiple),
            self.portfolio, self.attention, self.knowledge, model,
        ).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertTrue(result.clarification_required)
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(model.calls, [])

        stale = SupplyAgentSessionContext(selected_item_ids=("removed-position",))
        stale_result = SupplyAgent(
            SupplyAgentRequest("Why is it listed?", session_context=stale),
            self.portfolio, self.attention, self.knowledge, QueueDecisionModel(),
        ).run()
        self.assertTrue(stale_result.clarification_required)
        self.assertEqual(stale_result.status, SupplyAgentStatus.NEEDS_INPUT)

    def test_explicit_current_identity_overrides_context(self):
        context = SupplyAgentSessionContext(
            selected_item_ids=("hospital-costa-sur-o2",), focused_item_id="hospital-costa-sur-o2",
        )
        result = SupplyAgent(
            SupplyAgentRequest("Show the CO2 position for Alimentos del Sur.", session_context=context),
            self.portfolio, self.attention, self.knowledge,
            QueueDecisionModel(AgentDecision("finish", "finish", answer="CO2 position.")),
        ).run()
        self.assertEqual(result.portfolio_query.item_ids, ("alimentos-sur-malaga-co2",))

    def test_pipeline_records_resolved_portfolio_and_actual_context_reference(self):
        from diagnostics import PerformanceRecorder, PerformanceStatus
        from streamlit.testing.v1 import AppTest

        def render_inspector(snapshot):
            from pipeline_inspector import render_pipeline_inspector
            render_pipeline_inspector(snapshot)

        recorder = PerformanceRecorder("portfolio-query", "fake", "fixture", "supply_agent")
        first = SupplyAgent(
            SupplyAgentRequest("¿Qué posiciones requieren atención?"), self.portfolio,
            self.attention, self.knowledge, QueueDecisionModel(), recorder=recorder,
        ).run()
        query_events = [event for event in recorder.snapshot().events if event.stage == "portfolio_query"]
        self.assertEqual(len(query_events), 1)
        self.assertEqual(query_events[0].status, PerformanceStatus.COMPLETED)
        self.assertEqual(query_events[0].metadata["resolved_item_ids"], first.portfolio_query.item_ids)

        followup_recorder = PerformanceRecorder("portfolio-followup", "fake", "fixture", "supply_agent")
        followup = SupplyAgent(
            SupplyAgentRequest("¿Por qué la del hospital?", session_context=first.session_context),
            self.portfolio, self.attention, self.knowledge,
            QueueDecisionModel(AgentDecision("finish", "finish", answer="Structured O2 finding.")),
            recorder=followup_recorder,
        ).run()
        context_events = [event for event in followup_recorder.snapshot().events
                          if event.stage == "session_reference_resolution"]
        self.assertEqual(len(context_events), 1)
        self.assertEqual(context_events[0].metadata["focused_item_id"], followup.portfolio_item_id)
        inspector = AppTest.from_function(render_inspector, args=(followup_recorder.snapshot(),)).run()
        self.assertFalse(inspector.exception)
        rendered = "\n".join(element.value for element in inspector.markdown)
        self.assertIn("Portfolio Query / Selection", rendered)
        self.assertIn("Session Reference Resolution", rendered)

    def test_multi_position_what_if_without_unique_target_requests_clarification(self):
        model = QueueDecisionModel()
        knowledge = Mock(side_effect=AssertionError("what-if must not use RAG"))
        result = SupplyAgent(
            SupplyAgentRequest("What if delivery arrived one day earlier?"), self.portfolio,
            self.attention, knowledge, model,
        ).run()
        self.assertEqual(result.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertTrue(result.clarification_required)
        self.assertIsNone(result.scenario_analysis)
        self.assertEqual(model.calls, [])
        knowledge.assert_not_called()

    def test_cross_position_citations_and_aggregate_claims_are_rejected(self):
        selected = SupplyPortfolioQueryService().select(
            self.portfolio, self.attention, PortfolioQuery(finding_code="safety_stock_breach"),
        ).matches
        o2_source = RetrievedChunk(DocumentChunk(
            "o2-source", "doc", "Hospital contract", None, 1, 1, 0,
            "O2 source content", {"customer_id": "hospital-costa-sur", "gas_product_id": "medical-oxygen"},
        ), 1.0)
        answer, rejected = _enforce_portfolio_answer_boundary(
            "Alimentos del Sur CO2 terms are supported by the Hospital source. [chunk_id:o2-source]",
            selected, {selected[0].item_id: (o2_source,)}, self.portfolio.items,
        )
        self.assertTrue(rejected)
        self.assertNotIn("Hospital source", answer)
        co2_source = RetrievedChunk(DocumentChunk(
            "co2-source", "doc-2", "CO2 contract", None, 1, 1, 0,
            "CO2 source content", {"customer_id": "alimentos-del-sur", "gas_product_id": "co2"},
        ), 1.0)
        answer, rejected = _enforce_portfolio_answer_boundary(
            "Hospital Costa Sur O2 terms are supported by the CO2 source. [chunk_id:co2-source]",
            selected, {selected[1].item_id: (co2_source,)}, self.portfolio.items,
        )
        self.assertTrue(rejected)
        self.assertNotIn("CO2 source", answer)
        answer, rejected = _enforce_portfolio_answer_boundary(
            "The portfolio total gap is -1150 kg.", selected, {}, self.portfolio.items,
        )
        self.assertTrue(rejected)
        self.assertNotIn("-1150", answer)
        answer, rejected = _enforce_portfolio_answer_boundary(
            "1100 kg + 50 kg = 1150 kg.", selected, {}, self.portfolio.items,
        )
        self.assertTrue(rejected)
        self.assertNotIn("1150 kg", answer)

    def test_applicable_global_source_is_for_global_claims_only(self):
        selected = SupplyPortfolioQueryService().select(
            self.portfolio, self.attention, PortfolioQuery(finding_code="safety_stock_breach"),
        ).matches
        global_source = RetrievedChunk(DocumentChunk(
            "global-policy", "global-doc", "Industrial Gases Policy", None, 1, 1, 0,
            "General industrial gases statement.",
            {"scope": "global", "applicable_domain": "industrial_gases"},
        ), 1.0)
        sources = {match.item_id: (global_source,) for match in selected}
        answer, rejected = _enforce_portfolio_answer_boundary(
            "The global policy applies across Industrial Gases. [chunk_id:global-policy]",
            selected, sources, self.portfolio.items,
        )
        self.assertFalse(rejected)
        self.assertIn("global policy", answer)
        answer, rejected = _enforce_portfolio_answer_boundary(
            "The global policy specifically applies to Hospital Costa Sur O2. [chunk_id:global-policy]",
            selected, sources, self.portfolio.items,
        )
        self.assertTrue(rejected)

    def test_multi_position_documentary_query_keeps_each_source_in_its_item_scope(self):
        class ScopedKnowledge:
            def search(inner, *, identity, query, top_k=4):
                gas_id = identity["gas_product_id"]
                chunk = DocumentChunk(
                    f"{gas_id}-doc", f"{gas_id}-document", f"{gas_id.upper()} contract",
                    None, 1, 1, 0, f"Approved source for {gas_id}.",
                    {key: value for key, value in identity.items() if value is not None},
                )
                global_chunk = DocumentChunk(
                    "global-policy", "global-document", "Industrial Gases policy",
                    None, 1, 1, 0, "Global industrial gases policy.",
                    {"scope": "global", "applicable_domain": "industrial_gases"},
                )
                return "retrieved", (RetrievedChunk(chunk, 1.0), RetrievedChunk(global_chunk, 0.5))

        class ScopedSummaryModel:
            def decide(inner, question, state, tools, timeout_seconds=None):
                items = state["portfolio_items"]
                sources = {
                    item["identity"]["gas_product_id"]: item["knowledge_sources"]
                    for item in items
                }
                o2 = next(source for source in sources["medical-oxygen"]
                          if source["metadata"].get("scope") != "global")
                co2 = next(source for source in sources["co2"]
                           if source["metadata"].get("scope") != "global")
                return AgentDecision(
                    "finish", "finish",
                    answer=(
                        f"Hospital Costa Sur O2: {o2['document_name']} [chunk_id:{o2['chunk_id']}]. "
                        f"Alimentos del Sur CO2: {co2['document_name']} [chunk_id:{co2['chunk_id']}]"
                    ),
                )

        question = "¿Qué información documental es relevante para las posiciones con brecha de stock de seguridad?"
        result = SupplyAgent(
            SupplyAgentRequest(question), self.portfolio, self.attention,
            ScopedKnowledge(), ScopedSummaryModel(),
        ).run()
        self.assertEqual(result.portfolio_query.item_ids,
                         ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertEqual(result.status, SupplyAgentStatus.COMPLETED, result.answer)
        by_id = {item.item.item_id: item for item in result.evidence_bundle.items}
        self.assertTrue(by_id["hospital-costa-sur-o2"].knowledge_sources)
        self.assertTrue(by_id["alimentos-sur-malaga-co2"].knowledge_sources)
        self.assertFalse(any(
            source.chunk.metadata.get("gas_product_id") == "n2"
            for item in result.evidence_bundle.items for source in item.knowledge_sources
        ))
        self.assertEqual(len(result.evidence_bundle.global_sources), 1)
        self.assertNotIn("N2", result.answer)

    def test_invented_and_uncited_multi_position_documentary_claims_are_rejected(self):
        result = SupplyPortfolioQueryService().select(
            self.portfolio, self.attention, PortfolioQuery(finding_code="safety_stock_breach"),
        )
        knowledge = self.knowledge
        sources_by_item = {}
        for match in result.matches:
            _, sources_by_item[match.item_id] = knowledge.search(
                identity={
                    "customer_id": match.item.result.customer_id,
                    "site_id": match.item.result.site_id,
                    "application_id": match.item.result.application_id,
                    "gas_product_id": match.item.result.gas_product_id,
                    "installation_id": match.item.result.installation_id,
                }, query="contract terms",
            )
        all_sources = tuple(source for sources in sources_by_item.values() for source in sources)
        from industrial_gases.supply_agent import _enforce_supply_answer_boundary
        for bad_answer in (
            "The contract reserves capacity without a source.",
            "The contract reserves capacity. [chunk_id:invented-chunk]",
        ):
            answer, rejected = _enforce_supply_answer_boundary(
                "What contract information applies?", bad_answer, "retrieved", all_sources, True,
            )
            self.assertTrue(rejected)
            self.assertNotIn("reserves capacity", answer)

    def test_scoped_retrieval_failure_is_per_item_and_other_results_survive(self):
        class PerItemKnowledge:
            def search(inner, *, identity, query, top_k=4):
                if identity["gas_product_id"] == "medical-oxygen":
                    raise IndustrialKnowledgeOperationalError("fixture retrieval failure")
                source = RetrievedChunk(DocumentChunk(
                    "co2-scoped-source", "co2-doc", "CO2 scoped contract", None, 1, 1, 0,
                    "Scope-valid CO2 document.", dict(identity),
                ), 1.0)
                return "retrieved", (source,)

        result = SupplyAgent(
            SupplyAgentRequest(
                "¿Qué información documental es relevante para las posiciones con brecha de stock de seguridad?",
            ), self.portfolio, self.attention, PerItemKnowledge(), ObservedKnowledgeDecisionModel(),
        ).run()
        self.assertEqual(result.portfolio_query.item_ids,
                         ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertEqual(tuple(failure.item_id for failure in result.evidence_bundle.retrieval_failures),
                         ("hospital-costa-sur-o2",))
        self.assertTrue(result.evidence_bundle.items[0].attention.facts)
        self.assertTrue(result.evidence_bundle.items[1].knowledge_sources)
        self.assertEqual(tuple(item.knowledge_status for item in result.evidence_bundle.items),
                         ("operational_error", "retrieved"))


if __name__ == "__main__":
    unittest.main()
