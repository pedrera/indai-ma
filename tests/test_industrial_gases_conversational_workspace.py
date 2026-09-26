import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from agent_models import AgentDecision
from diagnostics import PerformanceRecorder, PerformanceStatus
from industrial_gases.supply_agent import ProviderSupplyDecisionModel
from industrial_gases.conversational_workspace import (
    ConversationalWorkspaceOrchestrator,
    WorkspaceIntent,
    _guard_physical_event_language,
    route_workspace_intent,
)
from industrial_gases.industrial_knowledge import IndustrialKnowledgeOperationalError, IndustrialKnowledgeService
from industrial_gases.portfolio_query import PortfolioEvidenceBundle, SupplyAgentSessionContext
from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio
from industrial_gases.supply_agent import SupplyAgentProviderError, SupplyAgentStatus
from llm_client import LLMResponse
from rag_models import DocumentChunk, RetrievedChunk
from streamlit.testing.v1 import AppTest


class WorkspaceDecisionModel:
    def __init__(self):
        self.calls = []

    def decide(self, question, state, tools, timeout_seconds=None):
        self.calls.append((question, state, tools))
        folded = question.casefold()
        if "un día antes" in folded or "one day earlier" in folded:
            if not any(item.get("name") == "evaluate_supply_what_if" for item in state["tool_observations"]):
                return AgentDecision(
                    "call_tool", "tool", "evaluate_supply_what_if",
                    {"item_id": state["selected_item_id"], "change_type": "delivery_offset_days", "value": -1},
                )
        if "contractual" in folded or "contrato" in folded:
            source = next(
                citation["citation"] for citation in state.get("available_citations", ())
                if citation["item_ids"] == (state["selected_item_id"],)
                and not citation["global_scope"]
            )
            return AgentDecision(
                "finish", "finish",
                answer=f"The Hospital Costa Sur oxygen contract contains the relevant supply terms. {source}",
            )
        if "un día antes" in folded or "one day earlier" in folded:
            answer = "The explicit earlier-delivery scenario has been evaluated against the existing projection."
        else:
            answer = (
                "Hospital Costa Sur projected inventory before delivery is 400 kg, below configured safety stock; "
                "a physical stockout is not projected before delivery."
            )
        return AgentDecision("finish", "finish", answer=answer)


class CanonicalContinuityDecisionModel:
    """Fake generation is used only for a cited document answer and explicit scenarios."""

    def __init__(self):
        self.calls = []

    def decide(self, question, state, tools, timeout_seconds=None):
        self.calls.append((question, state, tools))
        folded = question.casefold()
        if any(term in folded for term in (
            "sobre la entrega", "about delivery", "sobre la instalación", "sobre la instalacion",
            "about the installation",
        )):
            source = next(
                citation["citation"] for citation in state["available_citations"]
                if state["selected_item_id"] in citation["item_ids"] and not citation["global_scope"]
            )
            return AgentDecision(
                "finish", "finish",
                answer=f"The retrieved supply contract source for this position covers delivery. {source}",
            )
        if "un día antes" in folded or "one day before" in folded:
            offset = -1
        elif "dos días antes" in folded or "two days earlier" in folded:
            offset = -2
        else:
            return AgentDecision("finish", "finish", answer="The structured position facts are shown below.")
        if not any(item.get("name") == "evaluate_supply_what_if" for item in state["tool_observations"]):
            return AgentDecision(
                "call_tool", "tool", "evaluate_supply_what_if",
                {"item_id": state["selected_item_id"], "change_type": "delivery_offset_days", "value": offset},
            )
        return AgentDecision("finish", "finish", answer="The explicit delivery-time alternative was evaluated.")


class ScopedFixtureKnowledge:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def search(self, *, identity, query, top_k=4):
        self.calls.append((dict(identity), query))
        if self.fail:
            raise IndustrialKnowledgeOperationalError("fixture retrieval failure")
        gas_id = identity.get("gas_product_id")
        chunks = []
        if gas_id == "medical-oxygen":
            chunks.append(_chunk("hospital-contract", "hospital_o2_supply_contract.txt", "Hospital oxygen supply contract terms.", {
                "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
                "application_id": "hospital-costa-sur-medical-oxygen", "gas_product_id": "medical-oxygen",
                "installation_id": "hospital-costa-sur-bulk-cryogenic-o2", "document_type": "supply_contract",
            }))
        elif gas_id == "co2":
            chunks.append(_chunk("co2-contract", "alimentos_co2_supply_contract.txt", "CO2 carbonation supply contract terms.", {
                "customer_id": "alimentos-del-sur", "site_id": "malaga-production-plant",
                "application_id": "beverage-carbonation", "gas_product_id": "co2",
                "installation_id": "co2-bulk-installation", "document_type": "supply_contract",
            }))
        chunks.append(_chunk("global-policy", "global_demo_supply_policy.txt", "General industrial gas policy.", {
            "scope": "global", "applicable_domain": "industrial_gases", "document_type": "supply_policy",
        }))
        return "retrieved", tuple(RetrievedChunk(chunk, 1.0) for chunk in chunks[:top_k])


def _chunk(chunk_id, name, text, metadata):
    return DocumentChunk(chunk_id, chunk_id, name, "Supply", 1, 1, 0, text, metadata)


class _WorkspaceConversation:
    def __init__(self, model, knowledge, recorder=None):
        self.portfolio, self.attention = evaluate_demo_supply_portfolio()
        self.model = model
        self.knowledge = knowledge
        self.recorder = recorder
        self.context = SupplyAgentSessionContext()

    def ask(self, question):
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, self.knowledge, self.model, self.context,
            recorder=self.recorder,
        ).run(question)
        self.context = response.session_context
        return response


class ConversationalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.portfolio, self.attention = evaluate_demo_supply_portfolio()

    def test_route_classifies_operational_documentary_scenario_combined_and_followup(self):
        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")
        self.assertEqual(route_workspace_intent("¿Qué posiciones requieren atención?").intent, WorkspaceIntent.OPERATIONAL)
        self.assertEqual(route_workspace_intent("¿Qué dice el contrato?").intent, WorkspaceIntent.DOCUMENTARY)
        self.assertEqual(route_workspace_intent("¿Y si adelantamos la entrega un día?", context).intent, WorkspaceIntent.SCENARIO)
        self.assertEqual(route_workspace_intent("¿Por qué requiere atención y qué dice el contrato?").intent, WorkspaceIntent.COMBINED)
        self.assertEqual(route_workspace_intent("¿Por qué la del hospital?", context).intent, WorkspaceIntent.FOLLOW_UP)

    def test_direct_documentary_content_does_not_require_existing_document_scope(self):
        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")
        for question in (
            "¿Qué dice sobre la entrega?",
            "¿Qué dice la documentación sobre la entrega?",
            "¿Qué establece el contrato sobre la entrega?",
            "¿Qué indica el procedimiento sobre la entrega?",
            "What does it say about delivery?",
            "What does the documentation say about delivery?",
            "What does the contract say about delivery?",
        ):
            with self.subTest(question=question):
                route = route_workspace_intent(question, context)
                self.assertEqual(route.intent, WorkspaceIntent.DOCUMENTARY)
                self.assertIn("industrial_knowledge", route.capabilities)

        context_with_stale_scope = SupplyAgentSessionContext(
            context.selected_item_ids, context.focused_item_id,
            last_document_scope_item_ids=("hospital-costa-sur-o2",),
        )
        for question in (
            "¿Cuál es el inventario antes de la entrega?",
            "¿Hay agotamiento antes de la entrega?",
            "¿Cuál es la brecha frente al stock de seguridad?",
            "¿Cuánto se consume hasta la entrega?",
            "¿Cuál es el volumen requerido?",
            "What is the inventory before delivery?",
            "Is there a stockout before delivery?",
        ):
            with self.subTest(question=question):
                route = route_workspace_intent(question, context_with_stale_scope)
                self.assertNotIn("industrial_knowledge", route.capabilities)


    def test_canonical_four_turn_conversation_keeps_scope_and_uses_existing_capabilities(self):
        model = WorkspaceDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        conversation = _WorkspaceConversation(model, knowledge)

        q1 = conversation.ask("¿Qué posiciones requieren atención?")
        self.assertEqual(q1.selected_item_ids, ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertNotIn("alimentos-sur-malaga-n2", q1.selected_item_ids)
        self.assertEqual(
            tuple(item_id for item_id, _ in q1.attention_facts), q1.selected_item_ids,
        )
        self.assertEqual(
            tuple(item_id for item_id, _ in q1.provenance), q1.selected_item_ids,
        )
        self.assertEqual(model.calls, [])
        self.assertEqual(knowledge.calls, [])

        q2 = conversation.ask("¿Por qué la del hospital?")
        self.assertEqual(q2.selected_item_ids, ("hospital-costa-sur-o2",))
        hospital = q2.positions[0]
        self.assertEqual(tuple(f.code for f in hospital.result.findings), ("safety_stock_breach",))
        self.assertFalse(hospital.result.projection.stockout_before_delivery)
        self.assertIn("no se prevé agotamiento físico", q2.explanation)
        self.assertEqual(knowledge.calls, [])

        q3 = conversation.ask("¿Qué información contractual es relevante?")
        self.assertEqual(q3.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(len(knowledge.calls), 1)
        self.assertEqual(knowledge.calls[0][0]["gas_product_id"], "medical-oxygen")
        source_ids = {source.source.chunk.chunk_id for source in q3.documentary_sources}
        self.assertIn("hospital-contract", source_ids)
        self.assertNotIn("co2-contract", source_ids)
        self.assertIn("[chunk_id:hospital-contract]", q3.explanation)
        q3_call = model.calls[0]
        self.assertEqual(len(q3_call[1]["portfolio_items"]), 1)
        self.assertFalse({"get_supply_position", "get_operational_attention", "search_industrial_knowledge", "query_supply_portfolio"}
                         & {tool["name"] for tool in q3_call[2]})
        source_text = q3.positions[0].knowledge_sources[0].chunk.text
        self.assertEqual(q3_call[1]["portfolio_items"][0]["knowledge_sources"][0]["text"], source_text)
        self.assertEqual(q3_call[1]["tool_observations"][0]["result"]["source_ids"],
                         ("hospital-contract", "global-policy"))
        self.assertNotIn("sources", q3_call[1]["tool_observations"][0]["result"])

        q4 = conversation.ask("¿Y si la entrega llegara un día antes?")
        self.assertEqual(q4.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(q4.positions[0].scenario)
        self.assertEqual(q4.scenario_analyses[0][0], "hospital-costa-sur-o2")
        self.assertEqual(q4.positions[0].scenario.alternative.id, "planned-delivery-time")
        self.assertEqual(len(knowledge.calls), 1, "a prior RAG turn must not force RAG on scenario follow-up")
        self.assertEqual(len(model.calls), 1, "explicit day-offset scenarios are evaluated without generation")

    def test_canonical_seven_turn_conversation_preserves_focus_documents_and_scenario_baseline(self):
        model = CanonicalContinuityDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        recorder = PerformanceRecorder("direct-documentary-followup", "fixture", "none", "conversational_workspace")
        conversation = _WorkspaceConversation(model, knowledge, recorder)

        q1 = conversation.ask("¿Qué posiciones requieren atención?")
        self.assertEqual(q1.selected_item_ids, ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertNotIn("alimentos-sur-malaga-n2", q1.selected_item_ids)
        self.assertEqual(model.calls, [])
        self.assertEqual(knowledge.calls, [])
        self.assertIsNone(q1.session_context.focused_item_id)

        q2 = conversation.ask("¿Por qué la del hospital?")
        self.assertEqual(q2.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(q2.session_context.selected_item_ids, q1.selected_item_ids)
        self.assertEqual(q2.session_context.focused_item_id, "hospital-costa-sur-o2")
        self.assertEqual(tuple(f.code for f in q2.positions[0].result.findings), ("safety_stock_breach",))
        self.assertFalse(q2.positions[0].result.projection.stockout_before_delivery)
        self.assertEqual(model.calls, [])

        q3 = conversation.ask("¿Y la otra?")
        self.assertEqual(q3.selected_item_ids, ("alimentos-sur-malaga-co2",))
        self.assertEqual(q3.session_context.selected_item_ids, q1.selected_item_ids)
        self.assertEqual(q3.session_context.focused_item_id, "alimentos-sur-malaga-co2")
        self.assertEqual(model.calls, [])

        q4 = conversation.ask("¿Cuál tiene mayor brecha frente al stock de seguridad?")
        self.assertEqual(q4.selected_item_ids, q1.selected_item_ids)
        self.assertEqual(q4.comparison.field, "safety_stock_gap_before_delivery")
        self.assertEqual(tuple(value.value for value in q4.comparison.values), (-1100, -50))
        self.assertIn("déficit en valor absoluto es mayor", q4.explanation)
        self.assertNotIn("crítico", q4.explanation.casefold())
        self.assertEqual(model.calls, [])

        def render_comparison(response):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(response, "¿Cuál tiene mayor brecha frente al stock de seguridad?")

        comparison_app = AppTest.from_function(render_comparison, args=(q4,)).run()
        self.assertFalse(comparison_app.exception)
        comparison_text = "\n".join(str(element.value) for element in comparison_app.markdown)
        self.assertIn("-1,100 kg", comparison_text)
        self.assertIn("-50 kg", comparison_text)
        self.assertNotIn("-1, 100 kg", comparison_text)

        q5 = conversation.ask("Háblame solo de la del hospital.")
        self.assertEqual(q5.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(q5.session_context.focused_item_id, "hospital-costa-sur-o2")
        self.assertEqual(q5.session_context.last_document_scope_item_ids, ())
        self.assertEqual(model.calls, [])

        q6 = conversation.ask("¿Qué dice sobre la entrega?")
        self.assertEqual(q6.route.intent, WorkspaceIntent.DOCUMENTARY)
        self.assertIn("industrial_knowledge", q6.route.capabilities)
        self.assertNotIn("scenario_evaluation", q6.route.capabilities)
        self.assertEqual(q6.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(q6.session_context.focused_item_id, "hospital-costa-sur-o2")
        self.assertEqual(q6.session_context.last_document_scope_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(len(knowledge.calls), 1)
        self.assertEqual(knowledge.calls[0][0]["gas_product_id"], "medical-oxygen")
        self.assertIn("[chunk_id:hospital-contract]", q6.explanation)
        self.assertNotIn("co2-contract", q6.explanation)
        self.assertNotIn("n2-contract", q6.explanation)
        self.assertEqual(tuple(source.source.chunk.chunk_id for source in q6.documentary_sources), ("hospital-contract",))
        self.assertEqual(q6.documentary_sources[0].item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(
            tuple(source.source.chunk.chunk_id for source in q6.evidence.global_sources),
            ("global-policy",),
        )
        self.assertNotIn("400 kg", q6.explanation)
        self.assertEqual(q6.positions[0].result.projection.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[-1][1]["selected_item_id"], "hospital-costa-sur-o2")
        self.assertIn("[chunk_id:hospital-contract]", q6.explanation)
        self.assertEqual(q6.status, SupplyAgentStatus.COMPLETED)

        q6_events = recorder.snapshot().events
        q6_stages = [event.stage for event in q6_events]
        for stage in (
            "workspace_intent_routing", "session_reference_resolution",
            "portfolio_knowledge_retrieval", "agent_decision", "citation_validation",
            "workspace_structured_evidence", "workspace_response_projection",
        ):
            self.assertIn(stage, q6_stages)
        self.assertLess(q6_stages.index("workspace_intent_routing"), q6_stages.index("session_reference_resolution"))
        self.assertLess(q6_stages.index("session_reference_resolution"), q6_stages.index("portfolio_knowledge_retrieval"))
        self.assertLess(q6_stages.index("portfolio_knowledge_retrieval"), q6_stages.index("agent_decision"))
        self.assertLess(q6_stages.index("agent_decision"), q6_stages.index("citation_validation"))
        citation_events = [event for event in q6_events if event.stage == "citation_validation"]
        self.assertEqual(len(citation_events), 1)
        self.assertEqual(citation_events[0].status, PerformanceStatus.COMPLETED)

        baseline_delivery = q6.positions[0].item.request.delivery_plan.planned_delivery_at
        q7 = conversation.ask("¿Y dos días antes?")
        self.assertEqual(q7.route.intent, WorkspaceIntent.SCENARIO)
        self.assertIn("scenario_evaluation", q7.route.capabilities)
        self.assertNotIn("industrial_knowledge", q7.route.capabilities)
        self.assertEqual(q7.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(len(q7.positions), 1)
        scenario = q7.positions[0].scenario
        self.assertIsNotNone(scenario, "an explicit day offset must execute the existing evaluator")
        self.assertEqual(q7.session_context.last_scenario_target_id, "hospital-costa-sur-o2")
        self.assertEqual(scenario.alternative.alternative_request.delivery_plan.planned_delivery_at,
                         baseline_delivery - timedelta(days=2))
        baseline = scenario.baseline_result.projection
        alternative = scenario.alternative_result.projection
        self.assertEqual(baseline.consumption_until_delivery.value, 2800)
        self.assertEqual(baseline.inventory_immediately_before_delivery.value, 400)
        self.assertEqual(baseline.safety_stock_gap_before_delivery.value, -1100)
        self.assertFalse(baseline.stockout_before_delivery)
        self.assertEqual(baseline.inventory_immediately_after_delivery.value, 4400)
        self.assertEqual(baseline.required_delivery_volume.value, 1100)
        self.assertFalse(baseline.capacity_exceeded)
        self.assertEqual(alternative.consumption_until_delivery.value, 1400)
        self.assertEqual(alternative.inventory_immediately_before_delivery.value, 1800)
        self.assertEqual(alternative.safety_stock_gap_before_delivery.value, 300)
        self.assertFalse(alternative.stockout_before_delivery)
        self.assertEqual(alternative.inventory_immediately_after_delivery.value, 5800)
        self.assertEqual(alternative.required_delivery_volume.value, 0)
        self.assertFalse(alternative.capacity_exceeded)
        self.assertEqual(len(q7.scenario_analyses), 1)
        self.assertEqual(len(knowledge.calls), 1, "explicit scenario after documentary retrieval must not run RAG again")
        self.assertEqual(len(model.calls), 1, "explicit scenario evaluation must not invoke generation")
        q7_stages = [event.stage for event in recorder.snapshot().events[len(q6_events):]]
        self.assertIn("session_reference_resolution", q7_stages)
        self.assertIn("scenario_execution", q7_stages)
        self.assertNotIn("portfolio_knowledge_retrieval", q7_stages)
        self.assertNotIn("agent_decision", q7_stages)
        self.assertLess(q7_stages.index("session_reference_resolution"), q7_stages.index("scenario_execution"))
        self.assertLess(q7_stages.index("scenario_execution"), q7_stages.index("workspace_response_projection"))

        def render_scenario(item):
            from industrial_gases.conversational_workspace_ui import _render_scenario_card
            _render_scenario_card(item, "es")

        app = AppTest.from_function(render_scenario, args=(q7.positions[0],)).run()
        self.assertFalse(app.exception)
        table_text = str(app.table[0].value)
        self.assertIn("1,800 kg", table_text)
        self.assertIn("5,800 kg", table_text)
        self.assertIn("Actual", table_text)
        self.assertIn("Alternativa", table_text)

    def test_two_day_scenario_after_one_day_scenario_uses_original_baseline(self):
        model = CanonicalContinuityDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")

        first = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, model, context,
        ).run("¿Y si llega un día antes?")
        second = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, model, first.session_context,
        ).run("¿Y dos días antes?")
        baseline_delivery = second.positions[0].item.request.delivery_plan.planned_delivery_at
        self.assertEqual(first.positions[0].scenario.alternative.alternative_request.delivery_plan.planned_delivery_at,
                         baseline_delivery - timedelta(days=1))
        self.assertEqual(second.positions[0].scenario.alternative.alternative_request.delivery_plan.planned_delivery_at,
                         baseline_delivery - timedelta(days=2))
        self.assertNotEqual(
            second.positions[0].scenario.alternative.alternative_request.delivery_plan.planned_delivery_at,
            first.positions[0].scenario.alternative.alternative_request.delivery_plan.planned_delivery_at
            - timedelta(days=2),
        )
        self.assertEqual(knowledge.calls, [])

    def test_documentary_followup_then_two_day_scenario_keeps_document_scope_but_runs_no_rag(self):
        class DocumentaryModel:
            def __init__(self):
                self.calls = 0

            def decide(self, question, state, tools, timeout_seconds=None):
                self.calls += 1
                citation = next(
                    value["citation"] for value in state["available_citations"]
                    if state["selected_item_id"] in value["item_ids"] and not value["global_scope"]
                )
                answer = (
                    f"The Hospital Costa Sur oxygen contract contains the relevant supply terms. {citation}"
                    if "contrato tiene" in question.casefold()
                    else f"The retrieved supply contract source for this position covers delivery. {citation}"
                )
                return AgentDecision(
                    "finish", "finish",
                    answer=answer,
                )

        model = DocumentaryModel()
        knowledge = ScopedFixtureKnowledge()
        conversation = _WorkspaceConversation(model, knowledge)
        conversation.context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        q6 = conversation.ask("¿Qué contrato tiene?")
        q7 = conversation.ask("¿Qué dice sobre la entrega?")
        self.assertEqual(q7.session_context.last_document_scope_item_ids, ("hospital-costa-sur-o2",))
        self.assertIn("[chunk_id:hospital-contract]", q7.explanation)
        calls_before_scenario = len(knowledge.calls)
        model_calls_before_scenario = model.calls

        q8 = conversation.ask("¿Y dos días antes?")
        self.assertEqual(q8.route.intent, WorkspaceIntent.SCENARIO)
        self.assertEqual(q8.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(len(q8.positions), 1)
        self.assertIsNotNone(q8.positions[0].scenario)
        self.assertEqual(q8.positions[0].scenario.baseline_result, q7.positions[0].result)
        self.assertEqual(len(knowledge.calls), calls_before_scenario)
        self.assertEqual(q8.session_context.last_document_scope_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(model.calls, model_calls_before_scenario,
                         "the scenario turn must not invoke generation")

    def test_direct_documentary_question_without_focus_requests_clarification_before_rag(self):
        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("ambiguous documentary scope must be clarified before generation")

        selected = ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2")
        context = SupplyAgentSessionContext(selected, None)
        knowledge = ScopedFixtureKnowledge()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, NeverModel(), context,
        ).run("¿Qué dice sobre la entrega?")

        self.assertTrue(response.clarification_required)
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(response.session_context.selected_item_ids, selected)
        self.assertIsNone(response.session_context.focused_item_id)
        self.assertEqual(knowledge.calls, [])
        self.assertEqual(response.documentary_sources, ())
        self.assertEqual(response.session_context.last_document_scope_item_ids, ())

    def test_direct_documentary_question_with_stale_focus_does_not_guess_or_retrieve(self):
        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("stale position references must be clarified before generation")

        selected = ("removed-position", "hospital-costa-sur-o2")
        context = SupplyAgentSessionContext(selected, "removed-position")
        knowledge = ScopedFixtureKnowledge()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, NeverModel(), context,
        ).run("¿Qué dice sobre la entrega?")

        self.assertTrue(response.clarification_required)
        self.assertEqual(knowledge.calls, [])
        self.assertEqual(response.session_context.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNone(response.session_context.focused_item_id)

    def test_direct_documentary_question_without_any_position_selection_requests_identity(self):
        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("an unscoped documentary request must clarify before generation")

        knowledge = ScopedFixtureKnowledge()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, NeverModel(),
        ).run("¿Qué dice sobre la entrega?")
        self.assertTrue(response.clarification_required)
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(response.selected_item_ids, ())
        self.assertEqual(knowledge.calls, [])

    def test_failed_direct_retrieval_does_not_create_document_scope(self):
        knowledge = ScopedFixtureKnowledge(fail=True)
        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, CanonicalContinuityDecisionModel(), context,
        ).run("¿Qué dice sobre la entrega?")
        self.assertEqual(response.session_context.last_document_scope_item_ids, ())
        self.assertTrue(response.operational_errors)
        self.assertEqual(len(knowledge.calls), 1)

    def test_operational_projection_questions_stay_deterministic_without_rag_after_document_scope(self):
        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("structured operational facts must not invoke generation")

        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
            last_document_scope_item_ids=("hospital-costa-sur-o2",),
        )
        expectations = {
            "¿Cuál es el inventario antes de la entrega?": ("400 kg", "Inventario antes de la entrega"),
            "¿Hay agotamiento antes de la entrega?": ("No", "Agotamiento antes de la entrega"),
            "¿Cuál es la brecha frente al stock de seguridad?": ("-1,100 kg", "Brecha frente al stock de seguridad"),
            "¿Cuánto se consume hasta la entrega?": ("2,800 kg", "Consumo hasta la entrega"),
            "¿Cuál es el volumen requerido?": ("1,100 kg", "Volumen requerido"),
        }
        for question, (value, label) in expectations.items():
            with self.subTest(question=question):
                knowledge = ScopedFixtureKnowledge()
                response = ConversationalWorkspaceOrchestrator(
                    self.portfolio, self.attention, knowledge, NeverModel(), context,
                ).run(question)
                self.assertEqual(response.status, SupplyAgentStatus.COMPLETED)
                self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2",))
                self.assertIn(label, response.explanation)
                self.assertIn(value, response.explanation)
                self.assertNotIn("industrial_knowledge", response.route.capabilities)
                self.assertEqual(knowledge.calls, [])

    def test_reference_adversarial_cases_are_clarified_without_model_calls(self):
        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("reference resolution must be deterministic")

        selected = ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2", "alimentos-sur-malaga-n2")
        three = SupplyAgentSessionContext(selected, "hospital-costa-sur-o2")
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NeverModel(), three,
        ).run("¿Y la otra?")
        self.assertTrue(response.clarification_required)
        self.assertEqual(response.session_context.selected_item_ids, selected)
        self.assertEqual(response.session_context.focused_item_id, "hospital-costa-sur-o2")

        no_focus = SupplyAgentSessionContext((selected[0], selected[1]), None)
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NeverModel(), no_focus,
        ).run("¿Qué ocurre con esa?")
        self.assertTrue(response.clarification_required)

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NeverModel(),
        ).run("¿Qué ocurre con esa posición?")
        self.assertTrue(response.clarification_required)

        both_context = SupplyAgentSessionContext((selected[0], selected[1]), selected[0])
        both = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NeverModel(), both_context,
        ).run("Háblame de ambas posiciones.")
        self.assertEqual(both.selected_item_ids, (selected[0], selected[1]))

    def test_ambiguous_co2_identity_does_not_choose_first_matching_position(self):
        from industrial_gases.operational_attention import OperationalAttentionResult
        from industrial_gases.portfolio import SupplyPortfolioResult

        co2 = next(item for item in self.portfolio.items if item.item_id == "alimentos-sur-malaga-co2")
        extra = replace(co2, item_id="another-site-co2")
        portfolio = SupplyPortfolioResult(self.portfolio.items + (extra,))
        attention = OperationalAttentionResult(self.attention.items + (
            replace(next(item for item in self.attention.items if item.item_id == co2.item_id), item_id=extra.item_id),
        ))
        context = SupplyAgentSessionContext((co2.item_id, extra.item_id), co2.item_id)

        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("ambiguous identity must be clarified before generation")

        response = ConversationalWorkspaceOrchestrator(
            portfolio, attention, ScopedFixtureKnowledge(), NeverModel(), context,
        ).run("¿Qué ocurre con la de CO2?")
        self.assertTrue(response.clarification_required)
        self.assertEqual(response.session_context.focused_item_id, co2.item_id)
        self.assertEqual(response.session_context.selected_item_ids, context.selected_item_ids)

    def test_focus_switch_changes_documentary_scope_and_explicit_hospital_identity_wins(self):
        conversation = _WorkspaceConversation(CanonicalContinuityDecisionModel(), ScopedFixtureKnowledge())
        conversation.ask("¿Qué posiciones requieren atención?")
        conversation.ask("¿Por qué la del hospital?")
        other = conversation.ask("¿Y la otra?")
        self.assertEqual(other.selected_item_ids, ("alimentos-sur-malaga-co2",))

        contract = conversation.ask("¿Qué contrato tiene?")
        self.assertEqual(contract.selected_item_ids, ("alimentos-sur-malaga-co2",))
        answer = conversation.ask("¿Qué dice sobre la instalación?")
        self.assertEqual(answer.selected_item_ids, ("alimentos-sur-malaga-co2",))
        self.assertIn("[chunk_id:co2-contract]", answer.explanation)
        self.assertNotIn("hospital-contract", answer.explanation)

        hospital = conversation.ask("¿Y el hospital?")
        self.assertEqual(hospital.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(hospital.session_context.focused_item_id, "hospital-costa-sur-o2")
        self.assertNotIn("alimentos-sur-malaga-co2", hospital.explanation)
        self.assertEqual(hospital.session_context.last_document_scope_item_ids, ("hospital-costa-sur-o2",))
        hospital_document = conversation.ask("¿Qué dice sobre la instalación?")
        self.assertEqual(hospital_document.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIn("[chunk_id:hospital-contract]", hospital_document.explanation)

    def test_other_position_capacity_question_is_a_deterministic_single_position_fact(self):
        model = CanonicalContinuityDecisionModel()
        conversation = _WorkspaceConversation(model, ScopedFixtureKnowledge())
        conversation.ask("¿Qué posiciones requieren atención?")
        conversation.ask("¿Por qué la del hospital?")
        response = conversation.ask("¿La otra supera capacidad?")
        self.assertEqual(response.selected_item_ids, ("alimentos-sur-malaga-co2",))
        self.assertIn("no se supera la capacidad después de la entrega", response.explanation)
        self.assertIsNone(response.comparison)
        self.assertEqual(model.calls, [])

    def test_explicit_identity_switch_retargets_followup_scenario(self):
        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"),
            "alimentos-sur-malaga-co2",
            last_scenario_target_id="alimentos-sur-malaga-co2",
            last_intent="scenario",
            last_document_scope_item_ids=("alimentos-sur-malaga-co2",),
            last_scenario_change=("delivery_plan.planned_delivery_at", "2030-01-03T00:00:00+00:00"),
        )
        model = CanonicalContinuityDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        switched = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, model, context,
        ).run("¿Qué ocurre con Hospital Costa Sur?")
        self.assertEqual(switched.session_context.focused_item_id, "hospital-costa-sur-o2")
        self.assertIsNone(switched.session_context.last_scenario_target_id)
        self.assertEqual(switched.session_context.last_document_scope_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNone(switched.session_context.last_scenario_change)

        scenario = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, model, switched.session_context,
        ).run("¿Y si la entrega llegara un día antes?")
        self.assertEqual(scenario.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(scenario.session_context.last_scenario_target_id, "hospital-costa-sur-o2")
        self.assertEqual(scenario.scenario_analyses[0][0], "hospital-costa-sur-o2")

    def test_deterministic_attention_list_needs_no_model_or_knowledge_initialization(self):
        class Never:
            def __call__(self):
                raise AssertionError("deterministic query must not initialize generation or RAG")

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, Never(), None,
        ).run("¿Qué posiciones requieren atención?")
        self.assertEqual(response.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(len(response.selected_item_ids), 2)

    def test_ambiguous_followup_requests_clarification_instead_of_guessing(self):
        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"), None,
        )
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), None, context,
        ).run("¿Por qué esa posición?")
        self.assertTrue(response.clarification_required)
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertIn("Which of the previously selected positions", response.explanation)

    def test_explicit_identity_overrides_previous_focus_and_stale_context_is_not_guessed(self):
        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), WorkspaceDecisionModel(), context,
        ).run("¿Qué ocurre en Alimentos del Sur N2?")
        self.assertEqual(response.selected_item_ids, ("alimentos-sur-malaga-n2",))

        stale = SupplyAgentSessionContext(("removed-position",), "removed-position")
        stale_response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), None, stale,
        ).run("¿Y esa posición?")
        self.assertTrue(stale_response.clarification_required)
        self.assertEqual(stale_response.selected_item_ids, ())

    def test_model_cannot_target_nonselected_position_or_expose_cross_position_total(self):
        class NonmatchingScenarioModel:
            def __init__(self):
                self.calls = 0

            def decide(self, question, state, tools, timeout_seconds=None):
                self.calls += 1
                if self.calls == 1:
                    return AgentDecision(
                        "call_tool", "tool", "evaluate_supply_what_if",
                        {"item_id": "alimentos-sur-malaga-co2", "change_type": "delivery_offset_days", "value": -1},
                    )
                return AgentDecision("finish", "finish", answer="The selected hospital position is under its configured safety stock.")

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NonmatchingScenarioModel(),
        ).run("¿Qué ocurre en Hospital Costa Sur? ¿Y si la entrega llegara un día antes?")
        self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(response.positions[0].scenario)
        self.assertEqual(response.positions[0].scenario.alternative.alternative_request.delivery_plan.planned_delivery_at,
                         response.positions[0].item.request.delivery_plan.planned_delivery_at - timedelta(days=1))
        self.assertEqual(response.operational_errors, ())

        class AggregateClaimModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                citation = state["available_citations"][0]["citation"]
                return AgentDecision(
                    "finish", "finish", answer=f"The portfolio total gap is -1,150 kg. {citation}",
                )

        aggregate = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), AggregateClaimModel(),
        ).run("¿Qué información contractual aplica a las posiciones con brecha de stock de seguridad?")
        self.assertEqual(aggregate.selected_item_ids, ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertEqual(aggregate.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertNotIn("-1,150", aggregate.explanation)

    def test_wrong_position_and_invented_document_citations_are_blocked(self):
        class WrongScopeCitationModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision(
                    "finish", "finish",
                    answer="The CO2 contract sets the applicable supply terms. [chunk_id:hospital-contract]",
                )

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), WrongScopeCitationModel(),
        ).run("¿Qué dice el contrato de CO2 para Alimentos del Sur?")
        self.assertEqual(response.selected_item_ids, ("alimentos-sur-malaga-co2",))
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertNotIn("[chunk_id:hospital-contract]", response.explanation)
        self.assertNotIn("sets the applicable supply terms", response.explanation)

        class InventedCitationModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision(
                    "finish", "finish", answer="The contract confirms delivery terms. [chunk_id:invented]",
                )

        invented = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), InventedCitationModel(),
        ).run("¿Qué dice el contrato del Hospital Costa Sur?")
        self.assertEqual(invented.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertNotIn("[chunk_id:invented]", invented.explanation)

    def test_documentary_claim_without_citation_is_blocked(self):
        class UncitedDocumentaryClaimModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision(
                    "finish", "finish", answer="The contract confirms the next delivery terms."
                )

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), UncitedDocumentaryClaimModel(),
        ).run("¿Qué dice el contrato del Hospital Costa Sur?")
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertNotIn("confirms the next delivery terms", response.explanation)
        self.assertTrue(response.evidence.items[0].knowledge_sources)

    def test_documentary_citations_cannot_cross_position_in_either_direction(self):
        class CrossPositionCitationModel:
            def __init__(self, source_chunk_id, identity):
                self.source_chunk_id = source_chunk_id
                self.identity = identity

            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision(
                    "finish", "finish",
                    answer=f"The {self.identity} contract confirms supply terms. "
                           f"[chunk_id:{self.source_chunk_id}]",
                )

        cases = (
            ("¿Qué dice el contrato de CO2 para Alimentos del Sur?", "hospital-contract", "CO2"),
            ("¿Qué dice el contrato del Hospital Costa Sur?", "co2-contract", "oxygen"),
        )
        for question, wrong_source, identity in cases:
            with self.subTest(wrong_source=wrong_source):
                response = ConversationalWorkspaceOrchestrator(
                    self.portfolio, self.attention, ScopedFixtureKnowledge(),
                    CrossPositionCitationModel(wrong_source, identity),
                ).run(question)
                self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
                self.assertNotIn(f"[chunk_id:{wrong_source}]", response.explanation)
                self.assertNotIn("confirms supply terms", response.explanation)

    def test_vague_scenario_does_not_run_what_if(self):
        class ClarifyScenarioModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision("finish", "finish", answer="Please specify the change to evaluate.")

        context = SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2")
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), ClarifyScenarioModel(), context,
        ).run("¿Y si cambia algo?")
        self.assertIsNone(response.scenario_analyses[0][1] if response.scenario_analyses else None)
        self.assertFalse(any(execution["name"] == "evaluate_supply_what_if" for execution in response.tool_executions))

    def test_multi_position_scenario_requires_one_explicit_target(self):
        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"), None,
        )
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), WorkspaceDecisionModel(), context,
        ).run("¿Y si la entrega llegara un día antes?")
        self.assertTrue(response.clarification_required)
        self.assertIsNone(response.scenario_analyses[0][1] if response.scenario_analyses else None)
        self.assertEqual(len(response.selected_item_ids), 2)

    def test_pure_scenario_never_initializes_knowledge_even_after_documentary_turn(self):
        model = WorkspaceDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        conversation = _WorkspaceConversation(model, knowledge)
        conversation.ask("¿Qué posiciones requieren atención?")
        conversation.ask("¿Por qué la del hospital?")
        conversation.ask("¿Qué información contractual es relevante?")
        before = len(knowledge.calls)
        scenario = conversation.ask("¿Y si la entrega llegara un día antes?")
        self.assertEqual(len(knowledge.calls), before)
        self.assertIsNotNone(scenario.positions[0].scenario)

    def test_model_physical_event_claim_is_replaced_using_projection_evidence(self):
        model = type("Model", (), {"decide": lambda *args, **kwargs: AgentDecision(
            "finish", "finish", answer="A supply shortage is expected for Hospital Costa Sur."
        )})()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), model,
        ).run("¿Qué ocurre en Hospital Costa Sur?")
        self.assertTrue(response.semantic_guard_applied)
        self.assertIn("below configured safety stock", response.explanation)
        self.assertIn("no physical stockout is projected", response.explanation)

    def test_semantically_negated_stockout_is_not_rejected_and_conflation_is(self):
        evidence = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué posiciones requieren atención?"
        ).evidence
        self.assertFalse(_guard_physical_event_language(
            "A physical stockout is not projected before delivery.", evidence,
        ))
        self.assertFalse(_guard_physical_event_language(
            "A safety-stock breach does not mean a physical stockout.", evidence,
        ))
        self.assertTrue(_guard_physical_event_language(
            "There is no evidence of a shortage, but a supply shortage is expected.", evidence,
        ))
        self.assertTrue(_guard_physical_event_language(
            "A safety-stock breach means a stockout.", evidence,
        ))
        self.assertTrue(_guard_physical_event_language(
            "The safety stock is equivalent to tank capacity.", evidence,
        ))

    def test_documentary_delivery_answer_with_supported_safety_stock_shortfall_survives_projection(self):
        from diagnostics import PerformanceRecorder
        from pipeline_inspector import _render_supply_agent_timeline

        class RecordingDocumentaryProvider:
            def __init__(self, answer_template):
                self.answer_template = answer_template
                self.recorder = None
                self.calls = 0

            def generate_response(self, messages, *, timeout_seconds=None, options=None):
                self.calls += 1
                llm_event = self.recorder.start_stage(
                    "llm_call", call_number=1, purpose="supply_agent_decision", provider_round=1,
                    message_count=len(messages), prompt_character_count=len(messages[0]["content"]),
                )
                self.recorder.complete_stage(llm_event, input_tokens=10, output_tokens=20, total_tokens=30)
                answer = self.answer_template.replace(
                    "{citation}", "[chunk_id:hospital-contract]",
                )
                return LLMResponse(content=json.dumps({"action": "finish", "answer": answer}))

        class HospitalDeliveryKnowledge(ScopedFixtureKnowledge):
            def search(self, *, identity, query, top_k=4):
                self.calls.append((dict(identity), query))
                source = _chunk(
                    "hospital-contract", "hospital_o2_supply_contract.txt",
                    "The Hospital Costa Sur oxygen supply contract describes its planned delivery.",
                    {
                        "customer_id": "hospital-costa-sur",
                        "site_id": "hospital-costa-sur-site",
                        "application_id": "hospital-costa-sur-medical-oxygen",
                        "gas_product_id": "medical-oxygen",
                        "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
                        "document_type": "supply_contract",
                    },
                )
                return "retrieved", (RetrievedChunk(source, 1.0),)

        answer_template = (
            "The Hospital Costa Sur contract describes the planned delivery. "
            "The installation specification describes installation capacity and lists capacity overflow conditions. "
            "Capacity is not exceeded; inventory remains within configured capacity, and there is no capacity overflow. "
            "The structured position shows a safety-stock shortfall of 1,100 kg "
            "(400 kg inventory before delivery against 1,500 kg safety stock); "
            "no physical stockout is projected before delivery. {citation}"
        )
        recorder = PerformanceRecorder(
            "documentary-delivery-guard", "fixture", "fixture-model", "conversational_workspace",
        )
        provider = RecordingDocumentaryProvider(answer_template)
        provider.recorder = recorder
        knowledge = HospitalDeliveryKnowledge()

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, ProviderSupplyDecisionModel(provider),
            SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2"),
            recorder=recorder,
        ).run("¿Qué dice sobre la entrega?")

        self.assertEqual(provider.calls, 1)
        self.assertEqual(response.route.intent, WorkspaceIntent.DOCUMENTARY)
        self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(response.status, SupplyAgentStatus.COMPLETED, response.operational_errors)
        self.assertIn("planned delivery", response.explanation)
        self.assertIn("capacity overflow conditions", response.explanation)
        self.assertIn("Capacity is not exceeded", response.explanation)
        self.assertIn("there is no capacity overflow", response.explanation)
        self.assertIn("safety-stock shortfall of 1,100 kg", response.explanation)
        self.assertIn("[chunk_id:hospital-contract]", response.explanation)
        self.assertFalse(response.semantic_guard_applied)
        self.assertEqual(
            tuple(source.source.chunk.chunk_id for source in response.documentary_sources),
            ("hospital-contract",),
        )
        self.assertNotIn("co2-contract", response.explanation)
        self.assertNotIn("n2-contract", response.explanation)

        events = recorder.snapshot().events
        citation_event = next(event for event in events if event.stage == "citation_validation")
        projection_event = next(event for event in events if event.stage == "workspace_response_projection")
        self.assertTrue(citation_event.metadata["valid"])
        self.assertFalse(projection_event.metadata["semantic_guard_applied"])
        self.assertIsNone(projection_event.metadata["semantic_guard_reason"])
        self.assertIsNone(projection_event.metadata["semantic_guard_rule"])
        self.assertIsNone(projection_event.metadata["semantic_guard_pattern_id"])
        self.assertIsNone(projection_event.metadata["semantic_guard_matched_phrase"])
        timeline = "".join(_render_supply_agent_timeline(recorder.snapshot()))
        for expected in (
            "Workspace Intent / Routing", "Workspace Reference Resolution",
            "Scoped Portfolio Retrieval", "LLM Call #1", "Citation / Scope Validation",
            "Valid: True", "Structured Operational Evidence", "Workspace Response Projection",
            "Semantic guard applied: False",
        ):
            self.assertIn(expected, timeline)
        self.assertNotIn("Rule:", timeline)
        self.assertNotIn("Pattern:", timeline)
        self.assertNotIn("Matched phrase:", timeline)

        rejected_recorder = PerformanceRecorder(
            "documentary-unsupported-claim", "fixture", "fixture-model", "conversational_workspace",
        )

        rejected_provider = RecordingDocumentaryProvider(
            "A stockout before delivery is expected for Hospital Costa Sur. {citation}",
        )
        rejected_provider.recorder = rejected_recorder
        rejected_knowledge = HospitalDeliveryKnowledge()

        rejected = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, rejected_knowledge,
            ProviderSupplyDecisionModel(rejected_provider),
            SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2"),
            recorder=rejected_recorder,
        ).run("¿Qué dice sobre la entrega?")
        self.assertTrue(rejected.semantic_guard_applied)
        rejected_projection = next(
            event for event in rejected_recorder.snapshot().events
            if event.stage == "workspace_response_projection"
        )
        self.assertEqual(
            rejected_projection.metadata["semantic_guard_reason"], "unsupported_stockout_claim",
        )
        self.assertEqual(rejected_projection.metadata["semantic_guard_rule"], "physical_stockout_claim")
        self.assertEqual(
            rejected_projection.metadata["semantic_guard_pattern_id"],
            "physical_shortage_claim_en_es_1",
        )
        self.assertEqual(rejected_projection.metadata["semantic_guard_matched_phrase"], "stockout")
        rejected_timeline = "".join(_render_supply_agent_timeline(rejected_recorder.snapshot()))
        self.assertIn("Semantic guard applied: True", rejected_timeline)
        self.assertIn("Semantic guard reason: unsupported_stockout_claim", rejected_timeline)
        self.assertIn("Rule: physical_stockout_claim", rejected_timeline)
        self.assertIn("Pattern: physical_shortage_claim_en_es_1", rejected_timeline)
        self.assertIn("Matched phrase: stockout", rejected_timeline)

        capacity_recorder = PerformanceRecorder(
            "documentary-capacity-claim", "fixture", "fixture-model", "conversational_workspace",
        )
        capacity_provider = RecordingDocumentaryProvider(
            "The Hospital Costa Sur delivery exceeds the installation capacity after delivery. {citation}",
        )
        capacity_provider.recorder = capacity_recorder
        capacity_rejected = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, HospitalDeliveryKnowledge(),
            ProviderSupplyDecisionModel(capacity_provider),
            SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2"),
            recorder=capacity_recorder,
        ).run("¿Qué dice sobre la entrega?")
        self.assertFalse(capacity_rejected.positions[0].result.projection.capacity_exceeded)
        self.assertTrue(capacity_rejected.semantic_guard_applied)
        capacity_projection_event = next(
            event for event in capacity_recorder.snapshot().events
            if event.stage == "workspace_response_projection"
        )
        self.assertEqual(
            capacity_projection_event.metadata["semantic_guard_reason"], "unsupported_capacity_claim",
        )
        self.assertEqual(
            capacity_projection_event.metadata["semantic_guard_rule"], "capacity_exceedance",
        )
        self.assertEqual(
            capacity_projection_event.metadata["semantic_guard_pattern_id"],
            "capacity_exceedance_en_1",
        )
        matched_phrase = capacity_projection_event.metadata["semantic_guard_matched_phrase"]
        self.assertEqual(matched_phrase, "exceeds the installation capacity")
        self.assertLessEqual(len(matched_phrase), 120)
        self.assertNotRegex(matched_phrase, r"[\r\n]")
        capacity_timeline = "".join(_render_supply_agent_timeline(capacity_recorder.snapshot()))
        self.assertIn("Semantic guard applied: True", capacity_timeline)
        self.assertIn("Semantic guard reason: unsupported_capacity_claim", capacity_timeline)
        self.assertIn("Rule: capacity_exceedance", capacity_timeline)
        self.assertIn("Pattern: capacity_exceedance_en_1", capacity_timeline)
        self.assertIn("Matched phrase: exceeds the installation capacity", capacity_timeline)

    def test_semantic_guard_keeps_rejecting_unsupported_physical_claim_categories(self):
        evidence = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué posiciones requieren atención?"
        ).evidence
        cases = (
            ("Hospital Costa Sur will have a stockout before delivery.", "unsupported_stockout_claim"),
            ("A safety-stock breach means a stockout for Hospital Costa Sur.",
             "safety_stock_stockout_conflation"),
            ("The delivery exceeds the installation capacity.", "unsupported_capacity_claim"),
            ("Capacity will be exceeded after delivery.", "unsupported_capacity_claim"),
            ("There will be a capacity overflow.", "unsupported_capacity_claim"),
            ("La entrega supera la capacidad instalada.", "unsupported_capacity_claim"),
            ("Existe un exceso de capacidad.", "unsupported_capacity_claim"),
            ("Hospital Costa Sur capacity will be exceeded after delivery.",
             "unsupported_capacity_claim"),
            ("Alimentos del Sur CO2 will have a stockout before delivery.",
             "unsupported_stockout_claim"),
        )
        from industrial_gases.conversational_workspace import (
            _physical_event_guard_match, _physical_event_guard_reason,
        )
        for answer, expected_reason in cases:
            with self.subTest(answer=answer):
                self.assertEqual(
                    _physical_event_guard_reason(answer, evidence), expected_reason,
                )
                diagnostic = _physical_event_guard_match(answer, evidence)
                self.assertIsNotNone(diagnostic)
                self.assertEqual(diagnostic.reason, expected_reason)
                self.assertTrue(diagnostic.rule)
                self.assertTrue(diagnostic.pattern_id)
                self.assertTrue(diagnostic.matched_phrase)
                self.assertLessEqual(len(diagnostic.matched_phrase), 120)
                self.assertNotRegex(diagnostic.matched_phrase, r"[\r\n]")
        neutral_capacity_statements = (
            "The delivery does not exceed the installation capacity.",
            "Capacity will not be exceeded.",
            "There is no capacity overflow.",
            "Inventory remains within configured capacity.",
            "The installation specification discusses installation capacity.",
            "The specification lists capacity overflow conditions.",
            "The installation capacity is 10,000 kg.",
        )
        for answer in neutral_capacity_statements:
            with self.subTest(answer=answer):
                self.assertIsNone(_physical_event_guard_reason(answer, evidence))
                self.assertIsNone(_physical_event_guard_match(answer, evidence))

    def test_provider_failure_preserves_structured_evidence(self):
        class BrokenModel:
            def decide(self, *args, **kwargs):
                raise SupplyAgentProviderError("provider disconnected")

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), BrokenModel(),
        ).run("¿Qué ocurre en Hospital Costa Sur?")
        self.assertEqual(response.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(response.positions[0].result.projection)
        self.assertEqual(response.session_context.focused_item_id, "hospital-costa-sur-o2")

    def test_documentary_provider_failure_keeps_sources_and_exposes_failure_category_in_trace(self):
        class FailSecondDocumentaryCall:
            def __init__(self):
                self.calls = 0

            def decide(self, question, state, tools, timeout_seconds=None):
                self.calls += 1
                if "entrega" in question.casefold():
                    raise SupplyAgentProviderError("transient provider failure detail")
                citation = next(value["citation"] for value in state["available_citations"]
                                if value["chunk_id"] == "hospital-contract")
                answer = (
                    f"The Hospital Costa Sur oxygen contract contains the relevant supply terms. {citation}"
                    if "contrato tiene" in question.casefold()
                    else f"The retrieved supply contract source for this position covers delivery. {citation}"
                )
                return AgentDecision(
                    "finish", "finish", answer=answer,
                )

        model = FailSecondDocumentaryCall()
        conversation = _WorkspaceConversation(model, ScopedFixtureKnowledge())
        conversation.context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        conversation.ask("¿Qué contrato tiene?")
        failed = conversation.ask("¿Qué dice sobre la entrega?")
        self.assertEqual(failed.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertTrue(failed.operational_errors[0].startswith("SupplyAgentProviderError:"))
        self.assertEqual(failed.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(failed.positions[0].result.projection)
        self.assertEqual(len(failed.documentary_sources), 1)

        def render_failure(value):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(value, "¿Qué dice sobre la entrega?")

        app = AppTest.from_function(render_failure, args=(failed,)).run()
        self.assertFalse(app.exception)
        trace = next(expander for expander in app.expander if expander.label == "Evidencia y trazabilidad")
        self.assertIn("SupplyAgentProviderError", " ".join(str(value.value) for value in trace.caption))

    def test_unsupported_delivery_timing_does_not_replace_previous_scenario_state(self):
        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
            last_scenario_target_id="hospital-costa-sur-o2",
            last_intent="scenario",
            last_scenario_change=("delivery_plan.planned_delivery_at", "2030-01-03T00:00:00+00:00"),
        )

        class NeverModel:
            def decide(self, *args, **kwargs):
                raise AssertionError("unsupported vague dates must be clarified deterministically")

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), NeverModel(), context,
        ).run("¿Y si la entrega llega mañana?")
        self.assertTrue(response.clarification_required)
        self.assertEqual(response.scenario_analyses, ())
        self.assertEqual(response.session_context, context)

    def test_missing_generation_provider_preserves_deterministic_and_retrieved_evidence(self):
        knowledge = ScopedFixtureKnowledge()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, None,
            SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2"),
        ).run("¿Qué dice el contrato del Hospital Costa Sur?")
        self.assertEqual(response.status, SupplyAgentStatus.PROVIDER_ERROR)
        self.assertIsNotNone(response.positions[0].result.projection)
        self.assertEqual(len(response.positions[0].knowledge_sources), 1)
        self.assertEqual(len(knowledge.calls), 1)

    def test_retrieval_failure_keeps_operational_evidence(self):
        model = WorkspaceDecisionModel()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(fail=True), model,
            SupplyAgentSessionContext(("hospital-costa-sur-o2",), "hospital-costa-sur-o2"),
        ).run("¿Qué dice el contrato del Hospital Costa Sur?")
        self.assertEqual(response.status, SupplyAgentStatus.NEEDS_INPUT)
        self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertIsNotNone(response.positions[0].result.projection)
        self.assertEqual(len(response.evidence.retrieval_failures), 1)

    def test_multi_position_documentary_retrieval_keeps_sources_scoped(self):
        model = WorkspaceDecisionModel()
        knowledge = ScopedFixtureKnowledge()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, knowledge, model,
        ).run("¿Qué información documental es relevante para posiciones con brecha de stock de seguridad?")
        self.assertEqual(response.selected_item_ids, ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"))
        self.assertNotIn("alimentos-sur-malaga-n2", response.selected_item_ids)
        item_sources = {
            item.item.item_id: {source.chunk.chunk_id for source in item.knowledge_sources}
            for item in response.evidence.items
        }
        self.assertEqual(item_sources["hospital-costa-sur-o2"], {"hospital-contract"})
        self.assertEqual(item_sources["alimentos-sur-malaga-co2"], {"co2-contract"})
        self.assertEqual({source.source.chunk.chunk_id for source in response.evidence.global_sources}, {"global-policy"})
        self.assertEqual(len(knowledge.calls), 2)

    def test_multi_position_documentary_generation_keeps_claims_and_citations_scoped(self):
        question = "¿Qué condiciones contractuales aplican a las posiciones con brecha de stock de seguridad?"

        class FakeProvider:
            def __init__(self, answer):
                self.answer = answer
                self.prompt = None

            def generate_response(self, messages, **kwargs):
                self.prompt = messages[0]["content"]
                return LLMResponse(json.dumps({
                    "action": "finish", "answer": self.answer, "decision_summary": "Summarize sources",
                }))

        def run(answer):
            provider = FakeProvider(answer)
            model = ProviderSupplyDecisionModel(provider)
            response = ConversationalWorkspaceOrchestrator(
                self.portfolio, self.attention, ScopedFixtureKnowledge(), model,
            ).run(question)
            return response, provider

        hospital_citation = "[chunk_id:hospital-contract]"
        co2_citation = "[chunk_id:co2-contract]"
        global_citation = "[chunk_id:global-policy]"

        valid, provider = run(
            f"Hospital Costa Sur: the medical oxygen contract contains relevant supply terms. {hospital_citation} "
            f"Málaga Production Plant: the CO2 contract contains relevant supply terms. {co2_citation}"
        )
        self.assertEqual(valid.status, SupplyAgentStatus.COMPLETED, (valid.unsupported_questions, valid.explanation))
        self.assertEqual(valid.selected_item_ids, (
            "hospital-costa-sur-o2", "alimentos-sur-malaga-co2",
        ))
        self.assertNotIn("alimentos-sur-malaga-n2", valid.selected_item_ids)
        self.assertEqual(
            {source.source.chunk.chunk_id for source in valid.evidence.global_sources},
            {"global-policy"},
        )
        self.assertIn(hospital_citation, valid.explanation)
        self.assertIn(co2_citation, valid.explanation)
        self.assertNotIn(global_citation, valid.explanation)
        self.assertEqual(provider.prompt.count("MULTI-POSITION DOCUMENTARY OUTPUT"), 1)
        state_json = provider.prompt.split(
            "STATE (domain status is source data; observations are authoritative):\n", 1,
        )[1].split("\n\nAVAILABLE TOOLS:", 1)[0]
        state = json.loads(state_json)
        by_id = {item["item_id"]: item for item in state["portfolio_items"]}
        hospital = by_id["hospital-costa-sur-o2"]
        co2 = by_id["alimentos-sur-malaga-co2"]
        self.assertIn("Hospital Costa Sur", hospital["display_identity"])
        self.assertIn("O2 / Medicinal oxygen", hospital["display_identity"])
        self.assertIn(hospital_citation, [s["citation"] for s in hospital["knowledge_sources"]])
        self.assertIn(co2_citation, [s["citation"] for s in co2["knowledge_sources"]])
        self.assertNotIn(co2_citation, [s["citation"] for s in hospital["knowledge_sources"]])
        self.assertNotIn(hospital_citation, [s["citation"] for s in co2["knowledge_sources"]])
        self.assertIn(global_citation, [s["citation"] for s in state["global_knowledge_sources"]])
        self.assertIn("Do not mention internal item selection", provider.prompt)
        self.assertIn("Do not restate every metric already shown in the position cards", provider.prompt)

        invalid_answers = {
            "ambiguous plural claim":
                f"The Hospital Costa Sur and Málaga Production Plant contracts contain supply terms. {hospital_citation}",
            "cross-position citation":
                f"Málaga Production Plant contract contains supply terms. {hospital_citation}",
            "global source used for a position":
                f"Hospital Costa Sur contract contains supply terms. {global_citation}",
            "invented citation":
                "Hospital Costa Sur contract contains supply terms. [chunk_id:invented]",
        }
        for label, answer in invalid_answers.items():
            with self.subTest(label=label):
                rejected, _ = run(answer)
                self.assertEqual(rejected.status, SupplyAgentStatus.NEEDS_INPUT)
                self.assertNotIn("contains supply terms", rejected.explanation)

    def test_document_list_uses_scoped_metadata_and_avoids_generation(self):
        class MultiDocumentKnowledge:
            def search(inner, *, identity, query, top_k=4):
                gas_id = identity["gas_product_id"]
                if gas_id == "medical-oxygen":
                    types = (
                        ("hospital-contract", "supply_contract"),
                        ("hospital-procedure", "operating_procedure"),
                        ("hospital-spec", "installation_specification"),
                    )
                else:
                    types = (
                        ("co2-contract", "supply_contract"),
                        ("co2-spec", "installation_specification"),
                    )
                chunks = [
                    RetrievedChunk(_chunk(
                        chunk_id, f"{chunk_id}.txt", "Retrieved source text.",
                        {**identity, "document_type": document_type},
                    ), 1.0)
                    for chunk_id, document_type in types
                ]
                chunks.append(RetrievedChunk(_chunk(
                    "global-policy", "global-policy.txt", "Global policy source.", {
                        "scope": "global", "applicable_domain": "industrial_gases",
                        "document_type": "supply_policy",
                    },
                ), 0.5))
                return "retrieved", tuple(chunks[:top_k])

        class GenerationMustNotRun:
            def __init__(inner):
                inner.calls = 0

            def decide(inner, *args, **kwargs):
                inner.calls += 1
                raise AssertionError("Document inventory must not require generation")

        question = "¿Qué información documental es relevante para las posiciones con brecha de stock de seguridad?"
        model = GenerationMustNotRun()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, MultiDocumentKnowledge(), model,
        ).run(question)
        self.assertEqual(response.status, SupplyAgentStatus.COMPLETED)
        self.assertEqual(model.calls, 0)
        self.assertEqual(response.selected_item_ids, (
            "hospital-costa-sur-o2", "alimentos-sur-malaga-co2",
        ))
        by_id = {item.item.item_id: item for item in response.evidence.items}
        self.assertEqual(
            {source.chunk.chunk_id for source in by_id["hospital-costa-sur-o2"].knowledge_sources},
            {"hospital-contract", "hospital-procedure", "hospital-spec"},
        )
        self.assertEqual(
            {source.chunk.chunk_id for source in by_id["alimentos-sur-malaga-co2"].knowledge_sources},
            {"co2-contract", "co2-spec"},
        )
        self.assertEqual(
            {source.source.chunk.chunk_id for source in response.evidence.global_sources},
            {"global-policy"},
        )

        # Reproduce the real-provider failure class: citations are the only
        # document references, so removing them leaves punctuation fragments.
        bad_generated = replace(response, explanation=(
            "Para la posición Hospital Costa Sur / Oxígeno medicinal (O₂), los documentos relevantes son: "
            "[chunk_id:hospital-contract], [chunk_id:hospital-procedure], [chunk_id:hospital-spec]. "
            "Para la posición Málaga Production Plant / CO₂, los documentos relevantes son: "
            "[chunk_id:co2-contract], [chunk_id:co2-spec]."
        ))
        from industrial_gases.conversational_workspace_ui import (
            _primary_answer, _strip_technical_references,
        )
        old_reference_cleanup = _strip_technical_references(bad_generated.explanation)
        self.assertRegex(old_reference_cleanup, r":\s*,\s*,\s*\.")
        repaired_presentation = _primary_answer(bad_generated, question, "es")
        self.assertIn("Contrato de suministro", repaired_presentation)
        self.assertIn("Procedimiento operativo", repaired_presentation)
        self.assertIn("Especificación de instalación", repaired_presentation)
        self.assertIn("Política de gases industriales", repaired_presentation)
        self.assertNotRegex(repaired_presentation, r",\s*,\s*\.|:\s*,|\(\)|[ \t]{2,}")
        generic_cleanup = _primary_answer(
            bad_generated, "¿Qué dice el contrato aplicable?", "es",
        )
        self.assertNotIn("[chunk_id:", generic_cleanup)
        self.assertNotRegex(generic_cleanup, r",\s*,\s*\.|:\s*,|\(\)|[ \t]{2,}")

        def page(response, question):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(response, question)

        app = AppTest.from_function(page, args=(response, question), default_timeout=15).run()
        self.assertFalse(app.exception)
        trace = next(expander for expander in app.expander if expander.label == "Evidencia y trazabilidad")
        trace_nodes = {id(node) for node in trace}
        primary = "\n".join(
            str(element.value)
            for collection in (app.markdown, app.caption, app.text, app.metric, app.info)
            for element in collection if id(element) not in trace_nodes
        )
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂)", primary)
        self.assertIn("Málaga Production Plant · CO₂", primary)
        self.assertIn("Contrato de suministro", primary)
        self.assertIn("Procedimiento operativo", primary)
        self.assertIn("Especificación de instalación", primary)
        self.assertIn("Política de gases industriales", primary)
        self.assertLess(primary.index("Hospital Costa Sur"), primary.index("Málaga Production Plant"))
        self.assertLess(primary.index("Documentación general aplicable"), primary.index("Política de gases industriales"))
        self.assertNotRegex(primary, r",\s*,\s*\.|:\s*,|\(\)|[ \t]{2,}")
        self.assertNotIn("[chunk_id:", primary)
        for internal_id in (
            "hospital-costa-sur-o2", "alimentos-sur-malaga-co2", "hospital-contract",
            "hospital-procedure", "hospital-spec", "co2-contract", "co2-spec", "global-policy",
            "hospital_o2_supply_contract.txt", "co2-contract.txt",
        ):
            self.assertNotIn(internal_id, primary)
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂) · Contrato de suministro", primary)

    def test_workspace_apptest_keeps_one_conversation_and_renders_structured_cards(self):
        conversation = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())

        def page(conversation):
            from industrial_gases.conversational_workspace_ui import render_conversational_workspace
            render_conversational_workspace(lambda question, context: conversation.ask(question))

        app = AppTest.from_function(page, args=(conversation,), default_timeout=15).run()
        self.assertFalse(app.exception)
        for question in (
            "¿Qué posiciones requieren atención?",
            "¿Por qué la del hospital?",
            "¿Qué información contractual es relevante?",
            "¿Y si la entrega llegara un día antes?",
        ):
            app.chat_input[0].set_value(question).run()
            self.assertFalse(app.exception)
        self.assertEqual(
            [message["role"] for message in app.session_state["workspace_messages"]],
            ["user", "assistant"] * 4,
        )
        self.assertEqual(
            [message.get("content") for message in app.session_state["workspace_messages"] if message["role"] == "user"],
            ["¿Qué posiciones requieren atención?", "¿Por qué la del hospital?",
             "¿Qué información contractual es relevante?", "¿Y si la entrega llegara un día antes?"],
        )
        self.assertIsNotNone(
            app.session_state["workspace_messages"][-1]["response"].positions[0].scenario,
            repr(app.session_state["workspace_messages"][-1]),
        )
        rendered = "\n".join(
            str(element.value)
            for name in ("header", "subheader", "markdown", "caption", "text", "metric", "table")
            for element in getattr(app, name, ())
        )
        self.assertIn("Hospital Costa Sur", rendered)
        self.assertIn("Fuentes", rendered)
        self.assertNotIn("General industrial gas policy.", rendered)
        self.assertIn("Fecha de entrega", rendered)
        self.assertIn("1 día antes", rendered)
        self.assertIn("Actual", rendered)
        self.assertIn("Alternativa", rendered)
        self.assertNotIn("alimentos-sur-malaga-n2", rendered)

    def test_workspace_copy_contains_visible_answer_and_facts_but_not_trace(self):
        conversation = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())
        conversation.context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        response = conversation.ask("¿Por qué la del hospital?")
        copied = []

        def page(response):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(response, "¿Por qué la del hospital?", copy_key="workspace-copy-op")

        with patch(
            "industrial_gases.conversational_workspace_ui.render_clipboard_button",
            side_effect=lambda content, label, **kwargs: copied.append((content, label, kwargs)),
        ):
            app = AppTest.from_function(page, args=(response,), default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(copied), 1)
        payload, label, kwargs = copied[0]
        self.assertEqual(label, "Copiar")
        self.assertEqual(kwargs["key"], "workspace-response-workspace-copy-op")
        self.assertIn("no se prevé agotamiento físico", payload)
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂)", payload)
        self.assertIn("Inventario antes de la entrega: 400 kg", payload)
        self.assertIn("Diferencia frente al stock de seguridad: -1,100 kg", payload)
        self.assertNotIn("Evidencia y trazabilidad", payload)
        self.assertNotIn("Structured comparison", payload)
        self.assertNotIn("hospital-costa-sur-o2", payload)
        self.assertNotIn("private", payload.casefold())

    def test_workspace_copy_includes_visible_scenario_comparison(self):
        conversation = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())
        conversation.context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        response = conversation.ask("¿Y si la entrega llegara un día antes?")
        from industrial_gases.conversational_workspace_ui import build_workspace_response_copy_text
        payload = build_workspace_response_copy_text(response, "¿Y si la entrega llegara un día antes?")
        self.assertIn("Fecha de entrega · Hospital Costa Sur", payload)
        self.assertIn("Inventario antes de la entrega | 400 kg | 1,100 kg", payload)
        self.assertNotIn("Evidence & trace", payload)

    def test_primary_position_card_uses_human_identity_and_hides_technical_status(self):
        evidence = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué posiciones requieren atención?"
        ).positions[0]
        def page(item):
            from industrial_gases.conversational_workspace_ui import _render_position_card
            _render_position_card(item, "es")

        app = AppTest.from_function(page, args=(evidence,)).run()
        self.assertFalse(app.exception)
        primary = "\n".join(
            str(element.value)
            for collection in (app.markdown, app.caption, app.metric, app.text, app.info, app.warning)
            for element in collection
        )
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂)", primary)
        self.assertIn("Por debajo del stock de seguridad configurado", primary)
        self.assertIn("400 kg", primary)
        self.assertIn("-1,100 kg", primary)
        self.assertIn("No", primary)
        self.assertNotIn("COMPLETED", primary)
        for internal_id in (
            "hospital-costa-sur-o2", "hospital-costa-sur-site",
            "hospital-costa-sur-medical-oxygen", "hospital-costa-sur-bulk-cryogenic-o2",
        ):
            self.assertNotIn(internal_id, primary)

    def test_answer_sanitizer_uses_customer_name_for_customer_identifier(self):
        from industrial_gases.conversational_workspace_ui import _primary_answer

        class CustomerReferenceModel:
            def decide(self, question, state, tools, timeout_seconds=None):
                return AgentDecision("finish", "finish", answer="alimentos-del-sur supply position was reviewed.")

        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), CustomerReferenceModel(),
        ).run("¿Qué ocurre en Alimentos del Sur CO2?")
        self.assertEqual(response.selected_item_ids, ("alimentos-sur-malaga-co2",))
        answer = _primary_answer(response, "¿Qué ocurre en Alimentos del Sur CO2?", "es")
        self.assertIn("Alimentos del Sur", answer)
        self.assertNotIn("alimentos-del-sur", answer)
        self.assertNotIn("Málaga Production Plant supply position", answer)

    def test_primary_document_card_is_compact_while_exact_source_stays_traceable(self):
        response = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué dice el contrato del Hospital Costa Sur?"
        )
        source = response.evidence.items[0].knowledge_sources[0]

        def page(response, source):
            from industrial_gases.conversational_workspace_ui import _render_source
            _render_source(source, ("hospital-costa-sur-o2",), response, "es")

        app = AppTest.from_function(page, args=(response, source)).run()
        self.assertFalse(app.exception)
        primary = "\n".join(
            str(element.value)
            for collection in (app.markdown, app.caption, app.code, app.text)
            for element in collection
        )
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂) · Contrato de suministro · página 1", primary)
        self.assertNotIn("hospital_o2_supply_contract.txt", primary)
        self.assertNotIn("[chunk_id:hospital-contract]", primary)
        self.assertNotIn(source.chunk.text, primary)
        self.assertIn("[chunk_id:hospital-contract]", response.explanation)
        self.assertEqual(response.evidence.items[0].knowledge_sources[0].chunk.chunk_id, "hospital-contract")

    def test_additional_document_types_have_business_facing_labels_in_english_and_spanish(self):
        response = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué ocurre en Hospital Costa Sur?"
        )
        cases = (
            ("operating_procedure", "Operating procedure", "Procedimiento operativo"),
            ("installation_specification", "Installation specification", "Especificación de instalación"),
        )
        for document_type, english, spanish in cases:
            with self.subTest(document_type=document_type):
                source = RetrievedChunk(_chunk(
                    f"{document_type}-test", f"{document_type}.txt", "Source text", {
                        "customer_id": "hospital-costa-sur", "site_id": "hospital-costa-sur-site",
                        "application_id": "hospital-costa-sur-medical-oxygen",
                        "gas_product_id": "medical-oxygen",
                        "installation_id": "hospital-costa-sur-bulk-cryogenic-o2",
                        "document_type": document_type,
                    },
                ), 1.0)

                def page(source, lang, response):
                    from industrial_gases.conversational_workspace_ui import _render_source
                    _render_source(source, ("hospital-costa-sur-o2",), response, lang)

                for lang, label in (("en", english), ("es", spanish)):
                    app = AppTest.from_function(page, args=(source, lang, response)).run()
                    self.assertFalse(app.exception)
                    self.assertIn(label, " ".join(str(element.value) for element in app.caption))

    def test_documentary_response_hides_full_chunk_from_primary_view_but_keeps_it_in_trace(self):
        conversation = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())
        conversation.ask("¿Qué posiciones requieren atención?")
        conversation.ask("¿Por qué la del hospital?")
        response = conversation.ask("¿Qué información contractual es relevante?")
        chunk = response.evidence.items[0].knowledge_sources[0].chunk

        def page(value):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(value, "¿Qué información contractual es relevante?")

        app = AppTest.from_function(page, args=(response,)).run()
        self.assertFalse(app.exception)
        trace = next(expander for expander in app.expander if expander.label == "Evidencia y trazabilidad")
        trace_nodes = {id(node) for node in trace}
        primary = "\n".join(
            str(element.value)
            for collection in (app.markdown, app.caption, app.text, app.metric)
            for element in collection
            if id(element) not in trace_nodes
        )
        self.assertIn("Hospital Costa Sur · Oxígeno medicinal (O₂)", primary)
        self.assertNotIn(chunk.text, primary)
        self.assertNotIn(chunk.chunk_id, primary)
        self.assertNotIn(chunk.document_name, primary)

        self.assertIn(chunk.text, "\n".join(str(element.value) for element in trace.code))
        trace_content = "\n".join(
            str(element.value)
            for collection in (trace.markdown, trace.caption, trace.code, trace.json, trace.text)
            for element in collection
        )
        self.assertIn(chunk.chunk_id, trace_content)
        self.assertIn(chunk.document_name, trace_content)

    def test_scenario_card_is_a_compact_comparison_without_winner_language(self):
        conversation = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())
        conversation.ask("¿Qué posiciones requieren atención?")
        conversation.ask("¿Por qué la del hospital?")
        conversation.ask("¿Qué información contractual es relevante?")
        response = conversation.ask("¿Y si la entrega llegara un día antes?")
        item = response.positions[0]
        def page(value):
            from industrial_gases.conversational_workspace_ui import _render_scenario_card
            _render_scenario_card(value, "es")

        app = AppTest.from_function(page, args=(item,)).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.table), 1)
        table_text = str(app.table[0].value)
        for value in ("400 kg", "1,100 kg", "-1,100 kg", "-400 kg", "4,400 kg", "5,100 kg"):
            self.assertIn(value, table_text)
        self.assertIn("No", table_text)
        self.assertNotIn("better", table_text.casefold())
        self.assertNotIn("recommended", table_text.casefold())
        self.assertNotIn("winner", table_text.casefold())
        self.assertNotIn("recommendation", table_text.casefold())

    def test_guardrail_safe_text_is_user_facing_and_intervention_is_in_trace(self):
        model = type("Model", (), {"decide": lambda *args, **kwargs: AgentDecision(
            "finish", "finish", answer="A supply shortage is expected for Hospital Costa Sur."
        )})()
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), model,
        ).run("¿Qué ocurre en Hospital Costa Sur?")
        self.assertTrue(response.semantic_guard_applied)

        def page(value):
            from industrial_gases.conversational_workspace_ui import _render_workspace_response
            _render_workspace_response(value, "¿Qué ocurre en Hospital Costa Sur?")

        app = AppTest.from_function(page, args=(response,)).run()
        self.assertFalse(app.exception)
        primary = "\n".join(str(element.value) for element in app.markdown)
        self.assertIn("no se prevé agotamiento físico", primary)
        self.assertNotIn("Operational facts remain separate", primary)
        self.assertNotIn("unsupported physical-event statement was replaced", primary)
        trace = next(expander for expander in app.expander if expander.label == "Evidencia y trazabilidad")
        trace_warnings = "\n".join(str(element.value) for element in trace.warning)
        self.assertIn("Se sustituyó la explicación", trace_warnings)

    def test_missing_and_invalid_evaluation_issues_remain_visible_without_raw_codes(self):
        from dataclasses import replace

        evidence = _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge()).ask(
            "¿Qué posiciones requieren atención?"
        ).positions[0]
        cases = (
            (replace(evidence.result, status="MISSING_INPUTS", projection=None,
                     missing_inputs=("safety_stock",), findings=()), "Stock de seguridad"),
            (replace(evidence.result, status="INVALID", projection=None,
                     validation_errors=("inventory_unit_mismatch",), findings=()), "datos de evaluación son incoherentes"),
        )
        for result, expected in cases:
            with self.subTest(status=result.status):
                portfolio_result = replace(evidence.item, result=result)
                item = replace(evidence, item=portfolio_result)
                def page(value):
                    from industrial_gases.conversational_workspace_ui import _render_position_card
                    _render_position_card(value, "es")

                app = AppTest.from_function(page, args=(item,)).run()
                self.assertFalse(app.exception)
                text = "\n".join(
                    str(element.value)
                    for collection in (app.markdown, app.text, app.info, app.warning, app.caption)
                    for element in collection
                )
                self.assertIn(expected, text)
                self.assertNotIn("COMPLETED", text)
                self.assertNotIn("inventory_unit_mismatch", text)

    def test_new_conversation_clears_presentation_and_structured_context_only(self):
        from industrial_gases.conversational_workspace_ui import render_conversational_workspace

        def page():
            import streamlit as st
            from industrial_gases.portfolio_query import SupplyAgentSessionContext
            from industrial_gases.conversational_workspace_ui import render_conversational_workspace
            if "workspace_context" not in st.session_state:
                from industrial_gases.portfolio_query import PortfolioQuery
                st.session_state.workspace_context = SupplyAgentSessionContext(
                    ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
                    last_query=PortfolioQuery(item_id="hospital-costa-sur-o2"),
                    last_scenario_target_id="hospital-costa-sur-o2",
                    last_intent="scenario",
                    last_document_scope_item_ids=("hospital-costa-sur-o2",),
                    last_scenario_change=("delivery_plan.planned_delivery_at", "2030-01-03T00:00:00+00:00"),
                )
                st.session_state.workspace_messages = [{"role": "user", "content": "old turn"}]
            render_conversational_workspace(lambda *_: None)

        app = AppTest.from_function(page, default_timeout=15).run()
        self.assertFalse(app.exception)
        app.button(key="workspace_new_conversation").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["workspace_messages"], [])
        self.assertEqual(app.session_state["workspace_context"], SupplyAgentSessionContext())

    def test_pipeline_inspector_shows_actual_workspace_stages_without_synthetic_skips(self):
        from pipeline_inspector import render_pipeline_inspector

        recorder = PerformanceRecorder("workspace-pipeline", "fixture", "none", "conversational_workspace")
        ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), None, recorder=recorder,
        ).run("¿Qué posiciones requieren atención?")
        stages = recorder.snapshot().events
        names = {event.stage for event in stages}
        self.assertTrue({
            "workspace_intent_routing", "portfolio_query", "workspace_structured_evidence",
            "workspace_response_projection", "agent_final",
        } <= names)
        self.assertFalse(any(event.status == PerformanceStatus.SKIPPED for event in stages))

        def page(snapshot):
            from pipeline_inspector import render_pipeline_inspector
            render_pipeline_inspector(snapshot)

        app = AppTest.from_function(page, args=(recorder.snapshot(),)).run()
        self.assertFalse(app.exception)
        text = "\n".join(
            str(item.value)
            for collection in (app.markdown, app.caption, app.text, app.info)
            for item in collection
        )
        self.assertIn("Workspace Intent / Routing", text)
        self.assertIn("Workspace Response Projection", text)

        comparison_recorder = PerformanceRecorder(
            "workspace-comparison-pipeline", "fixture", "none", "conversational_workspace",
        )
        context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"),
            "hospital-costa-sur-o2",
        )
        comparison_response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, ScopedFixtureKnowledge(), None,
            context, recorder=comparison_recorder,
        ).run("¿Cuál tiene mayor brecha frente al stock de seguridad?")
        self.assertIsNotNone(comparison_response.comparison)
        from pipeline_inspector import _render_supply_agent_timeline
        timeline = "".join(_render_supply_agent_timeline(comparison_recorder.snapshot()))
        self.assertIn("Workspace Reference Resolution", timeline)
        self.assertIn("Deterministic Factual Comparison", timeline)
        self.assertIn("safety_stock_gap_before_delivery", timeline)
        self.assertIn("Comparable: True", timeline)

    def test_workspace_inspector_only_shows_stages_observed_for_each_turn_type(self):
        from pipeline_inspector import render_pipeline_inspector
        from industrial_gases.supply_agent import ProviderSupplyDecisionModel
        import tempfile
        from embeddings import EmbeddingProvider
        from industrial_gases.industrial_knowledge import IndustrialKnowledgeService
        from rag_service import RAGService
        from vector_store import LocalVectorStore

        def run_turn(operation_id, question, context=None, *, knowledge=None, model=None):
            recorder = PerformanceRecorder(
                operation_id, "fixture", "fixture-model" if model else "none",
                "conversational_workspace",
            )
            response = ConversationalWorkspaceOrchestrator(
                self.portfolio, self.attention, knowledge, model, context, recorder=recorder,
            ).run(question)
            recorder.finish("completed")
            return response, recorder.snapshot()

        operational, operational_snapshot = run_turn(
            "workspace-operational", "¿Qué posiciones requieren atención?",
        )
        self.assertEqual(operational.status, SupplyAgentStatus.COMPLETED)
        operational_stages = {event.stage for event in operational_snapshot.events}
        self.assertIn("workspace_structured_evidence", operational_stages)
        self.assertNotIn("portfolio_knowledge_retrieval", operational_stages)
        self.assertNotIn("query_embedding", operational_stages)
        self.assertNotIn("vector_search", operational_stages)
        self.assertNotIn("retrieved_context", operational_stages)
        self.assertNotIn("llm_call", operational_stages)

        class RecordingFakeProvider:
            def __init__(self):
                self.recorder = None
                self.calls = 0

            def generate_response(self, messages, *, timeout_seconds=None, options=None):
                self.calls += 1
                retrieval = next(
                    event for event in reversed(self.recorder.snapshot().events)
                    if event.stage == "retrieved_context"
                )
                source = next(
                    item for item in retrieval.metadata["sources"]
                    if item.get("metadata", {}).get("gas_product_id") == "medical-oxygen"
                )
                call = self.recorder.start_llm_call(
                    call_number=1, purpose="supply_agent_decision", provider_round=1,
                    message_count=len(messages), prompt_character_count=len(messages[0]["content"]),
                    tool_schema_character_count=0,
                )
                self.recorder.complete_llm_call(
                    call, request_setup_seconds=0.001, response_stream_seconds=0.001,
                    input_tokens=10, output_tokens=12, total_tokens=22,
                )
                return LLMResponse(content=json.dumps({
                    "action": "finish",
                    "answer": (
                        "The supply contract contains the delivery terms. "
                        f"[chunk_id:{source['chunk_id']}]"
                    ),
                }))

        documentary_recorder = PerformanceRecorder(
            "workspace-documentary", "fixture", "fixture-model", "conversational_workspace",
        )
        fake_provider = RecordingFakeProvider()
        fake_provider.recorder = documentary_recorder
        documentary_context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        class MatchingEmbeddings(EmbeddingProvider):
            model = "workspace-inspector-fixture"

            def embed_documents(self, texts):
                return [[1.0, 0.0] for _ in texts]

            def embed_query(self, text):
                return [1.0, 0.0]

        with tempfile.TemporaryDirectory() as directory:
            embeddings = MatchingEmbeddings()
            knowledge = IndustrialKnowledgeService(RAGService(
                embeddings, LocalVectorStore(directory, embeddings.model), documentary_recorder,
            ))
            documentary_response = ConversationalWorkspaceOrchestrator(
                self.portfolio, self.attention, knowledge,
                ProviderSupplyDecisionModel(fake_provider), documentary_context,
                recorder=documentary_recorder,
            ).run("¿Qué dice sobre la entrega?")
            documentary_recorder.finish("completed")
            documentary_snapshot = documentary_recorder.snapshot()
        self.assertEqual(fake_provider.calls, 1)
        self.assertEqual(documentary_response.status, SupplyAgentStatus.COMPLETED)
        documentary_stages = {event.stage for event in documentary_snapshot.events}
        self.assertTrue({
            "portfolio_knowledge_retrieval", "query_embedding", "vector_search",
            "retrieved_context", "llm_call", "agent_decision", "citation_validation",
        } <= documentary_stages)

        scenario_recorder = PerformanceRecorder(
            "workspace-scenario", "fixture", "none", "conversational_workspace",
        )
        scenario_context = SupplyAgentSessionContext(
            ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
        )
        scenario_response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention,
            lambda: (_ for _ in ()).throw(AssertionError("scenario turn must not retrieve")),
            None, scenario_context, recorder=scenario_recorder,
        ).run("¿Y si la entrega llegara un día antes?")
        scenario_recorder.finish("completed")
        scenario_snapshot = scenario_recorder.snapshot()
        self.assertEqual(scenario_response.status, SupplyAgentStatus.COMPLETED)
        scenario_stages = {event.stage for event in scenario_snapshot.events}
        self.assertIn("scenario_execution", scenario_stages)
        for absent in (
            "portfolio_knowledge_retrieval", "query_embedding", "vector_search",
            "retrieved_context", "llm_call", "agent_decision", "citation_validation",
        ):
            self.assertNotIn(absent, scenario_stages)

        def inspect(snapshot):
            from pipeline_inspector import render_pipeline_inspector
            render_pipeline_inspector(snapshot)

        def displayed_text(snapshot):
            app = AppTest.from_function(inspect, args=(snapshot,), default_timeout=15).run()
            self.assertFalse(app.exception)
            return "\n".join(
                str(item.value)
                for collection in (app.markdown, app.caption, app.text, app.info)
                for item in collection
            )

        operational_text = displayed_text(operational_snapshot)
        self.assertIn("Structured Operational Evidence", operational_text)
        self.assertIn("safety_stock_breach", operational_text)
        self.assertIn("hospital-costa-sur-o2", operational_text)
        for absent in ("Query Embedding", "Vector Search", "Retrieved Context", "LLM Call"):
            self.assertNotIn(absent, operational_text)

        documentary_text = displayed_text(documentary_snapshot)
        for expected in (
            "Scoped Portfolio Retrieval", "Query Embedding", "Vector Search",
            "Retrieved Context", "LLM Call #1", "Citation / Scope Validation",
        ):
            self.assertIn(expected, documentary_text)

        scenario_text = displayed_text(scenario_snapshot)
        self.assertIn("Explicit Scenario Execution", scenario_text)
        for absent in ("Query Embedding", "Vector Search", "Retrieved Context", "LLM Call"):
            self.assertNotIn(absent, scenario_text)


if __name__ == "__main__":
    unittest.main()
