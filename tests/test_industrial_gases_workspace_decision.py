"""Explicit selection through the production conversational Workspace."""
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import patch

import pytest

from industrial_gases import conversational_workspace as workspace
from industrial_gases.portfolio_query import SupplyAgentSessionContext
from industrial_gases.supply_agent import SupplyAgentStatus as S
from industrial_gases.workspace_decision import selection_scope
from tests.test_industrial_gases_workspace_criteria import conversation, SET, GAP


ES = "Aplica la política de selección estricta a las alternativas."
EN = "Apply strict selection to the alternatives."
ALL = "Aplica la política de selección estricta incluyendo el escenario base."
CRITERIA = GAP + "; Capacity must not be exceeded."
ALTERNATIVES = ("delivery-horizon-days-3", "delivery-horizon-days-2")


def ready(criteria=CRITERIA):
    chat = conversation()
    created = chat.ask(SET)
    assert created.decision_evaluation is None
    evaluated = chat.ask(criteria)
    assert evaluated.decision_evaluation is None
    return chat


@pytest.mark.parametrize("command", [ES, EN,
    "Selecciona la única alternativa que cumpla todas las restricciones.",
    "Select the unique alternative satisfying all hard constraints."])
def test_explicit_authorization_precedes_criteria_and_selects_b(command):
    chat = ready()
    previous = chat.context.scenario_set_state
    with patch.object(workspace, "evaluate_supply_decision", wraps=workspace.evaluate_supply_decision) as engine:
        response = chat.ask(command)
    assert response.status is S.COMPLETED
    assert engine.call_count == 1
    assert response.route.capabilities == ("deterministic_decision_evaluation",)
    result = response.decision_evaluation
    assert result.selected_scenario_id == ALTERNATIVES[1]
    assert result.candidate_ids == ALTERNATIVES
    assert result.criteria_evaluation is previous.criteria_evaluation
    assert result.source_set_binding == previous.source_set_binding
    assert chat.context.scenario_set_state.decision_evaluation is result
    assert "UNIQUE_HARD_CONSTRAINT_COMPLIANT" in response.explanation
    assert all(word not in response.explanation.lower() for word in ("mejor", "óptima", "recomendamos"))


def test_cache_reuses_only_same_authorization_and_full_scope():
    chat = ready()
    with patch.object(workspace, "evaluate_supply_decision", wraps=workspace.evaluate_supply_decision) as engine:
        first = chat.ask(ES).decision_evaluation
        assert chat.ask(ES).decision_evaluation is first
        chat.ask("Compare A and B.")
        assert chat.ask(ES).decision_evaluation is first
        changed = chat.ask(ALL).decision_evaluation
        assert changed.candidate_ids == ("baseline", *ALTERNATIVES)
        assert changed is not first
        assert chat.ask(ALL).decision_evaluation is changed
        assert engine.call_count == 2


@pytest.mark.parametrize("command", [ALL, "Apply strict selection including the baseline scenario."])
def test_baseline_explicit_inclusion(command):
    chat = ready()
    response = chat.ask(command)
    assert response.decision_evaluation.candidate_ids == ("baseline", *ALTERNATIVES)


@pytest.mark.parametrize("criteria,reason", [
    ("Capacity must not be exceeded.", "MULTIPLE_ELIGIBLE_CANDIDATES"),
    ("Safety-stock gap must be at least 10000 kg.", "NO_ELIGIBLE_CANDIDATE"),
    ("Objective: " + GAP, "NO_HARD_CONSTRAINTS"),
    ("Objective: " + GAP + "; Capacity must not be exceeded.", "MULTIPLE_ELIGIBLE_CANDIDATES"),
])
def test_engine_owns_no_selection_outcomes(criteria, reason):
    response = ready(criteria).ask(EN)
    assert response.status is S.COMPLETED
    assert response.decision_evaluation.reasons[0].value == reason
    assert response.decision_evaluation.selected_scenario_id is None


@pytest.mark.parametrize("criteria_present", [False, True])
def test_missing_set_or_criteria(criteria_present):
    chat = conversation()
    if criteria_present:
        chat.ask(SET)
    response = chat.ask(ES)
    assert response.status is S.NEEDS_INPUT and response.decision_evaluation is None
    assert response.unsupported_questions == (("evaluated_criteria_required" if criteria_present
                                             else "retained_scenario_set_required"),)


def test_changed_criteria_and_new_set_remove_authorization():
    chat = ready()
    first = chat.ask(ES).decision_evaluation
    chat.ask("Capacity must not be exceeded.")
    assert chat.context.scenario_set_state.decision_evaluation is None
    assert chat.context.scenario_set_state.decision_reuse_key is None
    assert chat.ask(ES).decision_evaluation != first
    chat.ask(SET)
    assert chat.context.scenario_set_state.decision_evaluation is None
    assert chat.ask(ES).unsupported_questions == ("evaluated_criteria_required",)


def test_focus_change_and_reset_remove_decision():
    chat = ready()
    chat.ask(ES)
    chat.ask("¿Por qué la de CO2?")
    assert chat.context.scenario_set_state is None
    assert chat.ask(ES).decision_evaluation is None
    chat = ready()
    chat.ask(ES)
    chat.context = SupplyAgentSessionContext()
    assert chat.ask(EN).unsupported_questions == ("retained_scenario_set_required",)


@pytest.mark.parametrize("suffix", ["; choose the cheapest", " only A", " and optimize",
                                     "; Capacity must not be exceeded."])
def test_unsupported_combination_or_subset_never_partially_selects(suffix):
    chat = ready()
    chat.ask(ES)
    with patch.object(workspace, "evaluate_supply_decision", side_effect=AssertionError("selection")):
        response = chat.ask(EN + suffix)
    assert response.status is S.NEEDS_INPUT
    assert response.decision_evaluation is None
    assert chat.context.scenario_set_state.decision_evaluation is None
    assert response.unsupported_questions == ("ambiguous_or_unsupported_decision_scope",)


@pytest.mark.parametrize("question", ["¿Cuál es mejor?", "Optimiza el suministro.",
    "Recomiéndame una alternativa.", "¿Qué harías?", "¿Cuál cumple?", "Compara A y B.",
    "¿Qué escenario tiene más inventario?"])
def test_ambiguous_or_factual_requests_never_authorize(question):
    assert selection_scope(question) is None
    chat = ready()
    with patch.object(workspace, "evaluate_supply_decision", side_effect=AssertionError("selection")), \
         patch.object(workspace, "explicit_selection_policy", side_effect=AssertionError("policy")):
        response = chat.ask(question)
    assert response.decision_evaluation is None


def test_partial_comparison_remains_partial_but_decision_uses_full_evidence():
    chat = ready()
    partial = chat.ask("Compare A and B.").scenario_comparison
    response = chat.ask(ES)
    assert response.scenario_comparison is partial
    assert len(partial.metrics[0].values) == 2
    assert len(response.decision_evaluation.criteria_evaluation.scenario_evaluations) == 3


def test_invalid_revision_or_portfolio_focus_never_reuses_cache():
    chat = ready()
    chat.ask(ES)
    chat.context = replace(chat.context, scenario_set_generation=chat.context.scenario_set_generation + 1)
    response = chat.ask(ES)
    assert response.unsupported_questions == ("stale_scenario_set_revision",)
    assert chat.context.scenario_set_state.decision_evaluation is None
    chat = ready()
    chat.ask(ES)
    state = chat.context.scenario_set_state
    chat.portfolio = replace(chat.portfolio, items=tuple(
        replace(item, request=replace(item.request, safety_stock=None))
        if item.item_id == state.item_id else item for item in chat.portfolio.items))
    assert chat.ask(ES).unsupported_questions == ("stale_or_different_scenario_focus",)


def test_tampered_criteria_rejected_by_engine_not_workspace_rules():
    chat = ready()
    state = chat.context.scenario_set_state
    evaluation = state.criteria_evaluation
    rows = evaluation.scenario_evaluations
    rows = (replace(rows[0], evaluations=rows[0].evaluations[:1]),) + rows[1:]
    chat.context = replace(chat.context, scenario_set_state=replace(state,
        criteria_evaluation=replace(evaluation, scenario_evaluations=rows)))
    response = chat.ask(ES)
    assert response.status is S.NEEDS_INPUT
    assert response.unsupported_questions == ("INCOMPLETE_EVIDENCE",)
    assert response.decision_evaluation is None


def test_zero_provider_rag_physical_calls_and_identical_reuse():
    chat = ready()
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
        mocks = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        first = chat.ask(ES)
        second = chat.ask(ES)
        assert first.decision_evaluation is second.decision_evaluation
        assert all(mock.call_count == 0 for mock in mocks)
    assert not chat.model.calls and not chat.knowledge.calls


def test_unknown_source_is_not_undecidable_or_satisfied():
    chat = conversation()
    chat.portfolio = replace(chat.portfolio, items=tuple(
        replace(item, request=replace(item.request, consumption_forecast=None))
        if item.item_id == "hospital-costa-sur-o2" else item for item in chat.portfolio.items))
    chat.ask(SET)
    chat.ask(CRITERIA)
    response = chat.ask(ES)
    decision = response.decision_evaluation
    assert decision.status.value == "INSUFFICIENT_EVIDENCE"
    assert decision.reasons[0].value == "ELIGIBILITY_UNRESOLVED"
    assert decision.selected_scenario_id is None
    assert all(item.eligibility.value == "UNRESOLVED" for item in decision.candidate_assessments)
    assert "UNKNOWN" in response.explanation


def test_creation_and_criteria_never_implicitly_invoke_engine():
    with patch.object(workspace, "evaluate_supply_decision", side_effect=AssertionError("implicit selection")), \
         patch.object(workspace, "explicit_selection_policy", side_effect=AssertionError("implicit policy")):
        chat = ready()
    assert chat.context.scenario_set_state.decision_evaluation is None


@pytest.mark.parametrize("dependency", ["ENGINE_VERSION", "EVALUATOR_VERSION"])
def test_version_changes_invalidate_reuse(dependency):
    chat = ready()
    first = chat.ask(ES).decision_evaluation
    with patch("industrial_gases.workspace_decision." + dependency, "new-version"), \
         patch.object(workspace, "evaluate_supply_decision", wraps=workspace.evaluate_supply_decision) as engine:
        assert chat.ask(ES).decision_evaluation is not first
        assert engine.call_count == 1


def test_policy_version_change_recomputes():
    chat = ready()
    chat.ask(ES)
    original = workspace.explicit_selection_policy
    with patch.object(workspace, "explicit_selection_policy",
                      side_effect=lambda *args: replace(original(*args), policy_version="2")), \
         patch.object(workspace, "evaluate_supply_decision", wraps=workspace.evaluate_supply_decision) as engine:
        response = chat.ask(ES)
        assert engine.call_count == 1
        assert response.decision_evaluation.policy.policy_version == "2"


def test_tampered_criteria_after_cached_selection_is_reverified():
    chat = ready()
    chat.ask(ES)
    state = chat.context.scenario_set_state
    evaluation = state.criteria_evaluation
    row = evaluation.scenario_evaluations[1]
    cell = replace(row.evaluations[0], reason="tampered")
    row = replace(row, evaluations=(cell,) + row.evaluations[1:])
    evaluation = replace(evaluation, scenario_evaluations=(evaluation.scenario_evaluations[0], row,
                                                          evaluation.scenario_evaluations[2]))
    chat.context = replace(chat.context, scenario_set_state=replace(state, criteria_evaluation=evaluation))
    assert chat.context.scenario_set_state.decision_evaluation is None
    response = chat.ask(ES)
    assert response.unsupported_questions == ("SOURCE_FACT_MISMATCH",)


def test_equal_valued_replacement_does_not_bypass_reference_validation():
    chat = ready()
    chat.ask(ES)
    state = chat.context.scenario_set_state
    first, *others = state.result.scenario_results
    # v1.18 requires the original baseline object, not just equal field values.
    first = replace(first, baseline_result=replace(first.baseline_result))
    result = replace(state.result, scenario_results=(first, *others))
    chat.context = replace(chat.context, scenario_set_state=replace(state, result=result))
    assert chat.context.scenario_set_state.decision_evaluation is None
    response = chat.ask(ES)
    assert response.unsupported_questions == ("SOURCE_BINDING_MISMATCH",)


@pytest.mark.parametrize("question", ["¿Qué dice el contrato?", "¿Qué posiciones requieren atención?"])
def test_documentary_and_portfolio_routes_do_not_apply_selection(question):
    chat = ready()
    with patch.object(workspace, "evaluate_supply_decision", side_effect=AssertionError("selection")), \
         patch.object(workspace, "explicit_selection_policy", side_effect=AssertionError("policy")):
        response = chat.ask(question)
    assert response.decision_evaluation is None


def test_unsupported_policy_has_distinct_reason():
    chat = ready()
    with patch.object(workspace, "explicit_selection_policy", side_effect=AssertionError("unsupported policy")):
        response = chat.ask("Aplica la política de selección por coste a las alternativas.")
    assert response.status is S.NEEDS_INPUT
    assert response.unsupported_questions == ("unsupported_decision_policy",)
    assert response.decision_evaluation is None
