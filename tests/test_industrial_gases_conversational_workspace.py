import json
import unittest
from dataclasses import replace

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
    def __init__(self, model, knowledge):
        self.portfolio, self.attention = evaluate_demo_supply_portfolio()
        self.model = model
        self.knowledge = knowledge
        self.context = SupplyAgentSessionContext()

    def ask(self, question):
        response = ConversationalWorkspaceOrchestrator(
            self.portfolio, self.attention, self.knowledge, self.model, self.context,
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
        self.assertIn("not projected", q2.explanation)
        self.assertEqual(knowledge.calls, [])

        q3 = conversation.ask("¿Qué información contractual es relevante?")
        self.assertEqual(q3.selected_item_ids, ("hospital-costa-sur-o2",))
        self.assertEqual(len(knowledge.calls), 1)
        self.assertEqual(knowledge.calls[0][0]["gas_product_id"], "medical-oxygen")
        source_ids = {source.source.chunk.chunk_id for source in q3.documentary_sources}
        self.assertIn("hospital-contract", source_ids)
        self.assertNotIn("co2-contract", source_ids)
        self.assertIn("[chunk_id:hospital-contract]", q3.explanation)
        q3_call = model.calls[1]
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
        self.assertEqual(len(model.calls), 4)

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
        self.assertIsNone(response.positions[0].scenario)
        self.assertTrue(any("must match the selected portfolio" in error for error in response.operational_errors))

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
                st.session_state.workspace_context = SupplyAgentSessionContext(
                    ("hospital-costa-sur-o2",), "hospital-costa-sur-o2",
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


if __name__ == "__main__":
    unittest.main()
