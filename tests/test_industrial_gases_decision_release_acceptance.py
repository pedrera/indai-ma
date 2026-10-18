"""v1.19 acceptance: production projections, Workspace, engine, Streamlit and Copy."""
from contextlib import ExitStack
from dataclasses import replace
from hashlib import sha256
from unittest.mock import patch

import pytest

from industrial_gases.conversational_workspace_ui import build_workspace_response_copy_text
from industrial_gases.factual_comparison import compare_supply_assurance_scenario_set
from industrial_gases.objective_constraint_models import ScenarioSetBinding
from industrial_gases.portfolio_query import WorkspaceScenarioSetState
from industrial_gases.service import SupplyAssuranceService
from industrial_gases.supply_scenarios import evaluate_supply_assurance_scenario_set
from tests.test_industrial_gases_workspace_criteria import conversation, SET
from tests.test_industrial_gases_workspace_decision import ES, ALL
from tests.test_industrial_gases_workspace_criteria_ui import rendered


def unresolved_source_conversation():
    """Construct incomplete input BEFORE evaluating; never forge evaluation cells.

    The Workspace sentence grammar cannot express a missing forecast. Seed its
    retained-source fixture with a real scenario set containing that request.
    All subsequent criteria, authorization, verification and rendering are real.
    """
    chat = conversation()
    chat.ask(SET)
    state = chat.context.scenario_set_state
    first, second = state.scenario_set.alternatives
    second = replace(second, alternative_request=replace(second.alternative_request,
                                                         consumption_forecast=None))
    scenario_set = replace(state.scenario_set, alternatives=(first, second))
    result = evaluate_supply_assurance_scenario_set(scenario_set, SupplyAssuranceService())
    comparison = compare_supply_assurance_scenario_set(result)
    revision = f"{chat.context.scenario_set_generation}:" + sha256(
        repr((scenario_set, result)).encode("utf-8")).hexdigest()
    binding = ScenarioSetBinding(f"workspace:{state.item_id}", revision, comparison.position_identity)
    chat.context = replace(chat.context, scenario_set_state=WorkspaceScenarioSetState(
        state.item_id, scenario_set, result, comparison, binding))
    assert result.scenario_results[0].alternative_result.status == "COMPLETED"
    assert result.scenario_results[1].alternative_result.status == "MISSING_INPUTS"
    return chat


@pytest.mark.parametrize("case,criteria,status,reason,eligibility", [
    ("A", "La brecha de stock de seguridad debe ser al menos 0 kg; la capacidad no debe superarse.",
     "DECIDED", "UNIQUE_ELIGIBLE_CANDIDATE", ("INELIGIBLE", "ELIGIBLE")),
    ("B", "No debe haber agotamiento antes de la entrega; la capacidad no debe superarse.",
     "UNDECIDABLE", "MULTIPLE_ELIGIBLE_CANDIDATES", ("ELIGIBLE", "ELIGIBLE")),
    ("C", "No debe haber agotamiento antes de la entrega; la capacidad no debe superarse.",
     "INSUFFICIENT_EVIDENCE", "ELIGIBILITY_UNRESOLVED", ("ELIGIBLE", "UNRESOLVED")),
])
def test_hospital_end_to_end_no_selection_or_presentation_dependencies(case, criteria, status, reason, eligibility):
    chat = unresolved_source_conversation() if case == "C" else conversation()
    if case != "C":
        created = chat.ask(SET)
        assert created.decision_evaluation is None
    targets = (
        "industrial_gases.service.SupplyAssuranceService.assess",
        "industrial_gases.service.build_supply_projection",
        "industrial_gases.calculations.build_supply_projection",
        "industrial_gases.calculations.calculate_days_of_supply",
        "industrial_gases.calculations.project_inventory",
        "industrial_gases.calculations.calculate_required_delivery_volume",
        "llm_client.LMStudioProvider.generate_response",
        "llm_client.OpenAIProvider.generate_response",
        "embeddings.LMStudioEmbeddingProvider.embed_query",
        "embeddings.LMStudioEmbeddingProvider.embed_documents",
        "rag_service.RAGService.retrieve", "socket.socket",
    )
    with ExitStack() as stack:
        forbidden = [stack.enter_context(patch(target, side_effect=AssertionError(target))) for target in targets]
        evaluated = chat.ask(criteria)
        assert evaluated.decision_evaluation is None
        chat.ask("Compare A and B.")
        response = chat.ask(ES)
        decision = response.decision_evaluation
        assert decision.status.value == status
        assert decision.reasons[0].value == reason
        assert tuple(item.eligibility.value for item in decision.candidate_assessments) == eligibility
        assert chat.ask(ES).decision_evaluation is decision
        app = rendered(response, ES)
        copied = build_workspace_response_copy_text(response, ES)
        matrix = app.table[-1].value
        assert tuple(matrix["ID"]) == decision.candidate_ids
        for value in matrix.to_numpy().flat:
            assert str(value) in copied
        assert reason in copied
        if case == "A":
            assert decision.selected_scenario_id == "delivery-horizon-days-2"
            assert tuple(row.evaluations[0].outcome.value for row in decision.criteria_evaluation.scenario_evaluations) == (
                "VIOLATED", "VIOLATED", "SATISFIED")
        else:
            assert decision.selected_scenario_id is None
            assert "Identificador seleccionado:" not in copied
        if case == "C":
            pending = decision.candidate_assessments[1]
            assert not pending.violation_references
            assert pending.unknown_references == ("criterion-1", "criterion-2")
            assert "source_missing_inputs" in copied
            assert "UNKNOWN no implica incumplimiento" in copied
        assert all(mock.call_count == 0 for mock in forbidden)
    assert chat.model.calls == chat.knowledge.calls == []


def test_documentary_decision_roundtrip_reuses_only_current_authorization():
    chat = conversation()
    chat.ask(SET)
    chat.ask("La brecha de stock de seguridad debe ser al menos 0 kg.")
    original = chat.ask(ES).decision_evaluation
    documentary = chat.ask("¿Qué dice el contrato?")
    assert documentary.decision_evaluation is None
    assert "Decisión determinista" not in build_workspace_response_copy_text(documentary, "¿Qué dice el contrato?")
    assert chat.ask(ES).decision_evaluation is original
    assert chat.ask(ALL).decision_evaluation.scope.mode.value == "ALL_SCENARIOS"
    chat.ask(SET)
    assert chat.context.scenario_set_state.decision_evaluation is None
    assert chat.ask(ES).decision_evaluation is None
