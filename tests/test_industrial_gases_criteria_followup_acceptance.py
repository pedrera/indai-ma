"""Regression and end-to-end acceptance through production Workspace paths."""
from contextlib import ExitStack
from dataclasses import replace
import re
from unittest.mock import patch

import pytest

from industrial_gases import conversational_workspace as workspace
from industrial_gases.supply_agent import SupplyAgentStatus
from industrial_gases.portfolio_query import SupplyAgentSessionContext
from tests.test_industrial_gases_workspace_criteria import conversation, SET
from tests.test_industrial_gases_workspace_criteria_ui import rendered
from industrial_gases.conversational_workspace_ui import build_workspace_response_copy_text


SPANISH = "La brecha de stock de seguridad debe ser al menos 0 kg; la capacidad no debe superarse."


def test_exact_spanish_followup_uses_retained_set_and_evaluates_both_criteria():
    chat = conversation()
    initial = chat.ask(SET)
    state = chat.context.scenario_set_state
    assert state is not None and chat.context.focused_item_id == state.item_id
    with patch.object(workspace, "split_criteria_request", wraps=workspace.split_criteria_request) as parser, \
         patch.object(workspace, "evaluate_supply_criteria", wraps=workspace.evaluate_supply_criteria) as evaluator:
        response = chat.ask(SPANISH)
    assert parser.call_count == 1
    assert "deterministic_criteria_evaluation" in response.route.capabilities
    assert response.status is SupplyAgentStatus.COMPLETED, (
        response.status, response.clarification_required, response.unsupported_questions, response.explanation)
    assert not response.clarification_required
    assert evaluator.call_count == 1
    assert response.scenario_set_result is initial.scenario_set_result
    assert response.criteria_evaluation.criteria.source_set_binding is state.source_set_binding
    assert len(response.criteria_evaluation.criteria.constraints) == 2
    assert tuple(s.evaluations[0].outcome.value for s in response.criteria_evaluation.scenario_evaluations) == (
        "VIOLATED", "VIOLATED", "SATISFIED")
    assert tuple(s.evaluations[1].outcome.value for s in response.criteria_evaluation.scenario_evaluations) == (
        "SATISFIED", "SATISFIED", "SATISFIED")


@pytest.mark.parametrize("partial", [False, True])
def test_spanish_followup_end_to_end_matrix_copy_cache_and_zero_dependency_calls(partial):
    chat = conversation()
    initial = chat.ask(SET)
    if partial:
        chat.ask("Compare A and B.")
    displayed = chat.context.scenario_set_state.comparison
    targets = (
        "industrial_gases.service.SupplyAssuranceService.assess",
        "industrial_gases.service.build_supply_projection",
        "industrial_gases.calculations.build_supply_projection",
        "llm_client.LMStudioProvider.generate_response",
        "llm_client.OpenAIProvider.generate_response",
        "embeddings.LMStudioEmbeddingProvider.embed_query",
        "embeddings.LMStudioEmbeddingProvider.embed_documents",
        "rag_service.RAGService.retrieve",
    )
    with ExitStack() as stack:
        forbidden = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        evaluator = stack.enter_context(patch.object(workspace, "evaluate_supply_criteria",
                                                    wraps=workspace.evaluate_supply_criteria))
        response = chat.ask(SPANISH)
        again = chat.ask(SPANISH)
        assert evaluator.call_count == 1
        assert again.criteria_evaluation is response.criteria_evaluation
        assert response.scenario_set_result is initial.scenario_set_result
        assert response.scenario_comparison is displayed
        app = rendered(response, SPANISH)
        copied = build_workspace_response_copy_text(response, SPANISH)
        assert all(mock.call_count == 0 for mock in forbidden)
    assert chat.model.calls == chat.knowledge.calls == []
    matrix, aggregate = app.table[-2].value, app.table[-1].value
    labels = tuple(branch.alternative.label for branch in response.scenario_set_result.scenario_results)
    assert tuple(matrix.columns[3:]) == ("Base", *labels)
    assert tuple(matrix["Tipo"]) == ("Restricción obligatoria",) * 2
    assert "-1100.0 kg" in matrix["Base"][0]
    assert "300.0 kg" in matrix[labels[1]][0]
    assert tuple(aggregate["Cumplimiento de restricciones"]) == (
        "incumple (VIOLATED)", "incumple (VIOLATED)", "cumple (SATISFIED)")
    for table in (matrix, aggregate):
        assert all(str(cell) in copied for cell in table.to_numpy().flat)
    assert not re.search(r"\b(best|winner|ranking|score|recommended|optimized|feasible|mejor|ganador)\b", copied, re.I)
    assert len(app.table[0].value.columns) == (3 if partial else 4)


def test_objective_prefix_lifecycle_focus_and_reset():
    chat = conversation()
    initial = chat.ask(SET + "; Objetivo: " + SPANISH)
    assert initial.status is SupplyAgentStatus.COMPLETED
    criteria = initial.criteria_evaluation.criteria
    assert len(criteria.objectives) == len(criteria.constraints) == 1
    assert all(s.hard_constraint_outcome.value == "SATISFIED"
               for s in initial.criteria_evaluation.scenario_evaluations)
    old_binding = criteria.source_set_binding
    replaced = chat.ask(SET)
    assert replaced.criteria_evaluation is None
    assert chat.context.scenario_set_state.criteria is None
    assert chat.context.scenario_set_state.source_set_binding != old_binding
    assert chat.ask(SPANISH).status is SupplyAgentStatus.COMPLETED
    chat.ask("¿Por qué la de CO2?")
    assert chat.context.scenario_set_state is None
    assert chat.ask(SPANISH).status is SupplyAgentStatus.NEEDS_INPUT
    chat.context = SupplyAgentSessionContext()
    assert chat.ask(SPANISH).criteria_evaluation is None


@pytest.mark.parametrize("unavailable", [False, True])
def test_unknown_and_unsupported_sentences_never_partial_evaluate(unavailable):
    chat = conversation()
    if unavailable:
        chat.portfolio = replace(chat.portfolio, items=tuple(
            replace(item, request=replace(item.request, consumption_forecast=None))
            if item.item_id == "hospital-costa-sur-o2" else item for item in chat.portfolio.items))
    chat.ask(SET)
    response = chat.ask(SPANISH)
    expected = "UNKNOWN" if unavailable else "VIOLATED"
    assert response.criteria_evaluation.scenario_evaluations[0].evaluations[0].outcome.value == expected
    if unavailable:
        copied = build_workspace_response_copy_text(response, SPANISH)
        assert "No evaluable (UNKNOWN)" in copied and "source_missing_inputs" in copied
    with patch.object(workspace, "evaluate_supply_criteria", side_effect=AssertionError("partial evaluation")):
        bad = chat.ask(SPANISH + "; El coste debe ser al menos 0 EUR.")
    assert bad.status is SupplyAgentStatus.NEEDS_INPUT
    assert bad.criteria_evaluation is None
    assert bad.unsupported_questions == ("unsupported_or_incomplete_criteria",)


@pytest.mark.parametrize("stale", ["revision", "portfolio", "identity", "missing_attention", "ambiguous"])
def test_stale_or_ambiguous_scope_still_requires_clarification(stale):
    chat = conversation()
    chat.ask(SET)
    state = chat.context.scenario_set_state
    if stale == "revision":
        chat.context = replace(chat.context, scenario_set_state=replace(state,
            source_set_binding=replace(state.source_set_binding, set_revision="stale")))
    elif stale == "portfolio":
        chat.portfolio = replace(chat.portfolio, items=tuple(
            replace(item, request=replace(item.request, safety_stock=None))
            if item.item_id == state.item_id else item for item in chat.portfolio.items))
    elif stale == "identity":
        binding = replace(state.source_set_binding,
                          position_identity=replace(state.source_set_binding.position_identity, site_id="other"))
        chat.context = replace(chat.context, scenario_set_state=replace(state, source_set_binding=binding))
    elif stale == "missing_attention":
        chat.attention = replace(chat.attention, items=tuple(item for item in chat.attention.items
                                                          if item.item_id != state.item_id))
    else:
        chat.context = SupplyAgentSessionContext((state.item_id, "alimentos-sur-malaga-co2"))
    response = chat.ask(SPANISH)
    assert response.status is SupplyAgentStatus.NEEDS_INPUT
    assert response.clarification_required and response.criteria_evaluation is None


def test_documentary_and_portfolio_routes_stay_independent_of_criteria_handler():
    chat = conversation()
    chat.ask(SET)
    chat.ask(SPANISH)
    with patch.object(workspace.ConversationalWorkspaceOrchestrator, "_run_explicit_criteria",
                      side_effect=AssertionError("wrong routing branch")):
        documentary = chat.ask("¿Qué información contractual es relevante?")
        assert documentary.documentary_sources
        assert "[chunk_id:hospital-contract]" in documentary.explanation
        assert "Contrato de suministro" in build_workspace_response_copy_text(
            documentary, "¿Qué información contractual es relevante?")
        portfolio = chat.ask("¿Qué posiciones requieren atención?")
        assert portfolio.selected_item_ids == ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2")
        assert portfolio.criteria_evaluation is None
