"""Production Workspace criteria integration with fixture-backed dependencies."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from industrial_gases.conversational_workspace import ConversationalWorkspaceOrchestrator
from industrial_gases.objective_constraint_evaluation import evaluate_supply_criteria
from industrial_gases.objective_constraint_models import CriterionOutcome as O, HardConstraintOutcome as H
from industrial_gases.portfolio_query import SupplyAgentSessionContext
from industrial_gases.supply_agent import SupplyAgentStatus
from tests.test_industrial_gases_conversational_workspace import (
    _WorkspaceConversation, WorkspaceDecisionModel, ScopedFixtureKnowledge,
)


SET = "For Hospital Costa Sur compare delivery in 3 days and 2 days."
GAP = "Safety-stock gap must be at least 0 kg."


def conversation():
    return _WorkspaceConversation(WorkspaceDecisionModel(), ScopedFixtureKnowledge())


def outcomes(response, index=0):
    return tuple(s.evaluations[index].outcome for s in response.criteria_evaluation.scenario_evaluations)


@pytest.mark.parametrize("sentence,expected", [
    (GAP, (O.VIOLATED, O.VIOLATED, O.SATISFIED)),
    ("La brecha de stock de seguridad debe ser al menos 0 kg.", (O.VIOLATED, O.VIOLATED, O.SATISFIED)),
    ("Inventory before delivery must be at least 1000 kg.", (O.VIOLATED, O.SATISFIED, O.SATISFIED)),
    ("El inventario antes de la entrega debe ser al menos 1000 kg.", (O.VIOLATED, O.SATISFIED, O.SATISFIED)),
    ("There must be no stockout before delivery.", (O.SATISFIED,) * 3),
    ("No debe haber agotamiento antes de la entrega.", (O.SATISFIED,) * 3),
    ("Capacity must not be exceeded.", (O.SATISFIED,) * 3),
    ("La capacidad no debe superarse.", (O.SATISFIED,) * 3),
])
def test_supported_english_spanish_numeric_boolean_followups(sentence, expected):
    chat = conversation()
    initial = chat.ask(SET)
    response = chat.ask(sentence)
    assert response.status is SupplyAgentStatus.COMPLETED
    assert outcomes(response) == expected
    assert response.scenario_set_result is initial.scenario_set_result
    assert response.criteria_evaluation.criteria is response.session_context.scenario_set_state.criteria
    assert not chat.model.calls and not chat.knowledge.calls


def test_combined_creation_objective_and_constraints():
    chat = conversation()
    response = chat.ask(SET + "; Objective: " + GAP + "; There must be no stockout before delivery.")
    assert response.status is SupplyAgentStatus.COMPLETED
    assert outcomes(response) == (O.VIOLATED, O.VIOLATED, O.SATISFIED)
    assert outcomes(response, 1) == (O.SATISFIED,) * 3
    state = chat.context.scenario_set_state
    assert len(state.criteria.objectives) == len(state.criteria.constraints) == 1
    assert all(item.hard_constraint_outcome is H.SATISFIED for item in response.criteria_evaluation.scenario_evaluations)
    assert tuple(s.source.scenario_id for s in response.criteria_evaluation.scenario_evaluations) == (
        "baseline", *(a.id for a in state.scenario_set.alternatives))
    for cell in response.criteria_evaluation.scenario_evaluations[0].evaluations:
        assert cell.source.source_set_binding == state.source_set_binding
        assert cell.source.projection_reference and cell.definition_source.reference
        assert cell.definition_source.revision
    assert not chat.model.calls and not chat.knowledge.calls


def test_spanish_combined_creation_and_objective_only():
    chat = conversation()
    response = chat.ask("Para Hospital Costa Sur compara entrega en 3 días y 2 días; Objetivo: "
                        "La brecha de stock de seguridad debe ser al menos 0 kg.")
    assert response.status is SupplyAgentStatus.COMPLETED
    assert all(s.hard_constraint_outcome is H.NOT_APPLICABLE for s in response.criteria_evaluation.scenario_evaluations)


def test_partial_display_rebuilt_without_replacing_display_or_physical_work():
    chat = conversation()
    chat.ask(SET)
    partial = chat.ask("Compare A and B.").scenario_comparison
    with patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=AssertionError("physical work")), \
         patch("industrial_gases.conversational_workspace.evaluate_supply_criteria", wraps=evaluate_supply_criteria) as evaluator:
        response = chat.ask(GAP)
    assert len(evaluator.call_args.args[1].metrics[0].values) == 3
    assert response.scenario_comparison is partial
    assert chat.context.scenario_set_state.comparison is partial
    assert len(response.criteria_evaluation.scenario_evaluations) == 3


def test_repeated_criteria_and_factual_followups_reuse_evaluation():
    chat = conversation()
    chat.ask(SET)
    original = chat.ask(GAP).criteria_evaluation
    with patch("industrial_gases.conversational_workspace.evaluate_supply_criteria", side_effect=AssertionError("unnecessary evaluation")), \
         patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=AssertionError("physical work")):
        assert chat.ask(GAP).criteria_evaluation is original
        assert chat.ask("Compare A and B.").criteria_evaluation is original
        assert chat.ask("Compare A and B.").criteria_evaluation is original


def test_set_replacement_invalidates_and_advances_revision():
    chat = conversation()
    chat.ask(SET + "; " + GAP)
    old = chat.context.scenario_set_state
    response = chat.ask(SET)
    new = chat.context.scenario_set_state
    assert new.criteria is new.criteria_evaluation is response.criteria_evaluation is None
    assert new.source_set_binding.set_revision != old.source_set_binding.set_revision
    assert chat.context.scenario_set_generation == 2
    assert chat.ask(GAP).criteria_evaluation.criteria.source_set_binding == new.source_set_binding


def test_focus_and_conversation_reset_clear_criteria():
    chat = conversation()
    chat.ask(SET + "; " + GAP)
    response = chat.ask("¿Por qué la de CO2?")
    assert response.session_context.scenario_set_state is None
    assert response.criteria_evaluation is None
    assert chat.ask(GAP).status is SupplyAgentStatus.NEEDS_INPUT
    chat.context = SupplyAgentSessionContext()
    response = chat.ask(GAP)
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert response.criteria_evaluation is None


@pytest.mark.parametrize("text", [
    "Inventory before delivery must be at least 1000.", "Safety-stock gap must be at least kg.",
    "Safety-stock gap must be at least 0 lb.", "Cost must be at least 0 EUR.",
    "Safety-stock gap must be at least 0 KG.", "Make the operation safer.",
    "Optimize delivery.", "Minimize risk.", "Objective: improve supply.", "Choose the best alternative.",
    "Inventory before delivery must be at least -1 kg.",
])
def test_unsupported_or_ambiguous_input_never_partially_evaluates(text):
    chat = conversation()
    chat.ask(SET)
    with patch("industrial_gases.conversational_workspace.evaluate_supply_criteria", side_effect=AssertionError("partial evaluation")):
        response = chat.ask(GAP + "; " + text)
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert chat.context.scenario_set_state.criteria is None
    assert not chat.model.calls and not chat.knowledge.calls


def test_invalid_combined_criteria_never_creates_scenarios():
    chat = conversation()
    with patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=AssertionError("partial creation")):
        response = chat.ask(SET + "; " + GAP + "; Cost must be at least 0 EUR.")
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert chat.context.scenario_set_state is None


def test_duplicates_rejected_contradictions_retained_independently():
    chat = conversation()
    chat.ask(SET)
    assert chat.ask(GAP + "; " + GAP).status is SupplyAgentStatus.NEEDS_INPUT
    response = chat.ask("There must be no stockout before delivery; There must be stockout before delivery.")
    assert outcomes(response) == (O.SATISFIED,) * 3
    assert outcomes(response, 1) == (O.VIOLATED,) * 3
    assert all(s.hard_constraint_outcome is H.VIOLATED for s in response.criteria_evaluation.scenario_evaluations)


def test_unknown_units_and_unavailable_projection_propagate():
    chat = conversation()
    chat.ask(SET)
    response = chat.ask("Safety-stock gap must be at least 0 t.")
    assert outcomes(response) == (O.UNKNOWN,) * 3
    assert all(s.evaluations[0].reason == "incompatible_operational_units"
               for s in response.criteria_evaluation.scenario_evaluations)
    # A valid scenario set with an incomplete original operational request.
    chat = conversation()
    items = tuple(replace(item, request=replace(item.request, consumption_forecast=None))
                  if item.item_id == "hospital-costa-sur-o2" else item for item in chat.portfolio.items)
    chat.portfolio = replace(chat.portfolio, items=items)
    response = chat.ask(SET + "; " + GAP)
    assert response.status is SupplyAgentStatus.COMPLETED
    assert outcomes(response) == (O.UNKNOWN,) * 3


def test_stale_revision_or_changed_portfolio_requires_clarification():
    chat = conversation()
    chat.ask(SET)
    state = chat.context.scenario_set_state
    chat.context = replace(chat.context, scenario_set_state=replace(state,
        source_set_binding=replace(state.source_set_binding, set_revision="old")))
    assert chat.ask(GAP).unsupported_questions == ("stale_scenario_set_revision",)
    chat = conversation()
    chat.ask(SET)
    chat.portfolio = replace(chat.portfolio, items=tuple(
        replace(item, request=replace(item.request, safety_stock=None))
        if item.item_id == "hospital-costa-sur-o2" else item for item in chat.portfolio.items))
    assert chat.ask(GAP).status is SupplyAgentStatus.NEEDS_INPUT


def test_documentary_mix_and_ranking_stay_guarded():
    chat = conversation()
    chat.ask(SET)
    response = chat.ask(GAP + "; tell me what the contract says.")
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert not chat.knowledge.calls
    assert chat.ask("Which is best?").unsupported_questions == ("scenario_ranking_not_supported",)


def test_supported_criteria_paths_do_not_call_providers_or_calculators():
    chat = conversation()
    chat.ask(SET)
    forbidden = AssertionError("forbidden dependency")
    with patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=forbidden), \
         patch("industrial_gases.service.build_supply_projection", side_effect=forbidden), \
         patch("llm_client.LMStudioProvider.generate_response", side_effect=forbidden), \
         patch("llm_client.OpenAIProvider.generate_response", side_effect=forbidden), \
         patch("embeddings.LMStudioEmbeddingProvider.embed_query", side_effect=forbidden), \
         patch("rag_service.RAGService.retrieve", side_effect=forbidden):
        response = chat.ask(GAP)
    assert response.status is SupplyAgentStatus.COMPLETED
    assert not chat.model.calls and not chat.knowledge.calls
    assert not any(word in response.explanation.lower() for word in ("winner", "best", "optimal", "safe", "feasible"))


def test_invalid_new_submission_does_not_display_previous_evaluation():
    chat = conversation()
    chat.ask(SET + "; " + GAP)
    old = chat.context.scenario_set_state
    response = chat.ask("Inventory before delivery must be at least.")
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert response.criteria_evaluation is None
    assert chat.context.scenario_set_state is old


def test_same_focus_operational_fact_retains_criteria_then_single_scenario_clears():
    chat = conversation()
    initial = chat.ask(SET + "; " + GAP)
    response = chat.ask("¿Cuál es el inventario antes de la entrega?")
    assert response.criteria_evaluation is initial.criteria_evaluation
    response = chat.ask("¿Y si la entrega llegara un día antes?")
    assert response.status is SupplyAgentStatus.COMPLETED
    assert response.session_context.scenario_set_state is None
    assert response.criteria_evaluation is None


def test_documentary_only_mandatory_language_keeps_documentary_route():
    chat = conversation()
    chat.ask(SET)
    response = chat.ask("What does the contract say we must do?")
    assert "industrial_knowledge" in response.route.capabilities
    assert "deterministic_criteria_evaluation" not in response.route.capabilities
    assert chat.knowledge.calls


def test_focus_switch_keeps_generation_counter_without_carrying_criteria():
    chat = conversation()
    old = chat.ask(SET + "; " + GAP).session_context.scenario_set_state.source_set_binding
    chat.ask("¿Por qué la de CO2?")
    chat.ask(SET)
    state = chat.context.scenario_set_state
    assert chat.context.scenario_set_generation == 2
    assert state.source_set_binding.set_revision != old.set_revision
    assert state.criteria is None
