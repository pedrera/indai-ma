"""Production Streamlit and Copy acceptance using real Workspace decisions."""
from contextlib import ExitStack
from dataclasses import replace
import re
from unittest.mock import patch

import pytest

from industrial_gases.conversational_workspace_ui import (
    _decision_presentation, _DECISION_BOUNDARY, build_workspace_response_copy_text,
)
from industrial_gases.decision_engine_models import SupplyDecisionReason as R
from tests.test_industrial_gases_workspace_decision import ready, ES, EN, ALL
from tests.test_industrial_gases_workspace_criteria import conversation, SET, GAP
from tests.test_industrial_gases_workspace_criteria_ui import rendered


def response_for(kind="decided", command=ES):
    criteria = {
        "decided": GAP + "; Capacity must not be exceeded.",
        "multiple": "Capacity must not be exceeded.",
        "none": "Safety-stock gap must be at least 10000 kg.",
        "objectives": "Objective: " + GAP,
        "unknown": "Safety-stock gap must be at least 0 t.",
    }[kind]
    return ready(criteria).ask(command)


def ui_text(app):
    return "\n".join(str(element.value) for group in
                     (app.markdown, app.text, app.caption, app.info, app.warning) for element in group)


@pytest.mark.parametrize("kind,status", [("decided", "DECIDED"), ("multiple", "UNDECIDABLE"),
    ("none", "UNDECIDABLE"), ("objectives", "UNDECIDABLE"), ("unknown", "INSUFFICIENT_EVIDENCE")])
def test_three_states_render_and_copy_parity(kind, status):
    response = response_for(kind)
    app = rendered(response, ES)
    copy = build_workspace_response_copy_text(response, ES)
    lines, rows, references = _decision_presentation(response)
    assert f"Estado: {status}" in ui_text(app)
    for line in (*lines, _DECISION_BOUNDARY):
        assert line in ui_text(app) and line in copy
    for line in references:
        assert line in ui_text(app) and line in copy
    table = app.table[-1].value
    assert len(table) == len(rows) == 2
    for column in table.columns:
        assert column in copy
    for cell in table.to_numpy().flat:
        assert str(cell) in copy
    selected = "Identificador seleccionado:"
    assert (selected in ui_text(app)) == (status == "DECIDED")
    assert (selected in copy) == (status == "DECIDED")


def test_selected_and_other_candidate_explanations():
    response = response_for()
    decision = response.decision_evaluation
    app = rendered(response, ES)
    text = ui_text(app)
    assert "Alternativa seleccionada bajo la política de cumplimiento único de restricciones explícitas." in text
    assert f"Identificador seleccionado: {decision.selected_scenario_id}" in text
    assert "delivery-horizon-days-3 · INELIGIBLE" in text
    assert "violadas: criterion-1" in text
    assert "delivery-horizon-days-2 · ELIGIBLE · restricciones satisfechas: criterion-1, criterion-2" in text
    assert not re.search(r"mejor alternativa|alternativa óptima|recomendación empresarial|más rentable", text, re.I)


@pytest.mark.parametrize("kind,reason,message", [
    ("multiple", R.MULTIPLE_ELIGIBLE_CANDIDATES, "Varias candidatas cumplen todas las restricciones."),
    ("none", R.NO_ELIGIBLE_CANDIDATE, "Ninguna candidata cumple todas las restricciones."),
    ("objectives", R.NO_HARD_CONSTRAINTS, "No se definieron restricciones obligatorias."),
])
def test_specific_undecidable_reasons(kind, reason, message):
    response = response_for(kind)
    copied = build_workspace_response_copy_text(response, ES)
    assert reason.value in copied and message in copied
    assert "Identificador seleccionado:" not in copied


def test_unsupported_preference_record_has_specific_explanation_without_selection():
    response = response_for("multiple")
    decision = replace(response.decision_evaluation, reasons=(R.UNSUPPORTED_PREFERENCE,))
    response = replace(response, decision_evaluation=decision)
    app = rendered(response, ES)
    copied = build_workspace_response_copy_text(response, ES)
    assert "Existe una preferencia no soportada." in ui_text(app)
    assert "UNSUPPORTED_PREFERENCE" in copied
    assert "Identificador seleccionado:" not in copied


def test_unknown_candidates_constraints_and_exact_units():
    response = response_for("unknown")
    app = rendered(response, ES)
    copy = build_workspace_response_copy_text(response, ES)
    matrix = app.table[-1].value
    assert tuple(matrix["Elegibilidad"]) == ("UNRESOLVED", "UNRESOLVED")
    assert "≥ 0 t" in matrix.columns[2]
    assert "-400.0 kg" in matrix.iloc[0, 2]
    assert "300.0 kg" in matrix.iloc[1, 2]
    assert "No evaluable (UNKNOWN)" in matrix.iloc[0, 2]
    assert "incompatible_operational_units" in matrix.iloc[0, 2]
    assert "UNKNOWN: criterion-1" in ui_text(app)
    assert "UNKNOWN no implica incumplimiento" in copy
    assert "estos hechos impiden establecer una selección única" in copy


def test_missing_facts_keep_original_reason_and_no_projection_reference():
    chat = conversation()
    chat.portfolio = replace(chat.portfolio, items=tuple(
        replace(item, request=replace(item.request, consumption_forecast=None))
        if item.item_id == "hospital-costa-sur-o2" else item for item in chat.portfolio.items))
    chat.ask(SET)
    chat.ask(GAP)
    response = chat.ask(ES)
    app = rendered(response, ES)
    copy = build_workspace_response_copy_text(response, ES)
    assert "source_missing_inputs" in app.table[-1].value.iloc[0, 2]
    assert "Proyección: No disponible" in copy
    assert "INSUFFICIENT_EVIDENCE" in copy


@pytest.mark.parametrize("command,included", [(ES, False), (ALL, True)])
def test_baseline_scope_order_and_policy(command, included):
    response = response_for(command=command)
    decision = response.decision_evaluation
    app = rendered(response, command)
    matrix = app.table[-1].value
    assert tuple(matrix["ID"]) == decision.candidate_ids
    assert ("baseline" in tuple(matrix["ID"])) is included
    assert decision.policy.policy_id in ui_text(app)
    assert f"versión: {decision.policy.policy_version}" in ui_text(app)
    assert decision.scope.mode.value in ui_text(app)
    assert ("Baseline participa explícitamente" if included else "Baseline excluido") in ui_text(app)


def test_partial_display_does_not_change_decision_matrix_scope():
    chat = ready()
    chat.ask("Compare A and B.")
    response = chat.ask(ALL)
    app = rendered(response, ALL)
    assert "Base" not in app.table[0].value.columns
    assert tuple(app.table[-1].value["ID"]) == ("baseline", "delivery-horizon-days-3", "delivery-horizon-days-2")


def test_complete_provenance_is_visible_and_copied():
    response = response_for()
    decision = response.decision_evaluation
    app = rendered(response, ES)
    copy = build_workspace_response_copy_text(response, ES)
    _, _, references = _decision_presentation(response)
    assert any(element.value == "\n".join(references) for element in app.text)
    expected = [*vars(decision.position_identity).values(), decision.source_set_binding.set_reference,
        decision.source_set_binding.set_revision, decision.engine_version,
        decision.criteria_evaluation_reference, decision.policy.definition_source.reference,
        decision.policy.definition_source.revision, *decision.provenance_references]
    for value in expected:
        assert value in ui_text(app) and value in copy
    assert "no constituyen auditoría persistente, autenticación ni almacenamiento histórico" in copy


def test_current_response_does_not_copy_retained_old_decision():
    chat = ready()
    original = chat.ask(ES).decision_evaluation
    response = chat.ask("Compare A and B.")
    assert response.decision_evaluation is None
    assert response.session_context.scenario_set_state.decision_evaluation is original
    app = rendered(response, "Compare A and B.")
    copied = build_workspace_response_copy_text(response, "Compare A and B.")
    assert "Decisión determinista" not in ui_text(app) and "Decisión determinista" not in copied
    assert "Identificador seleccionado:" not in copied
    assert len(app.table) == 3  # v1.18 factual comparison, criteria, aggregate


def test_no_decision_block_after_new_criteria_or_error():
    chat = ready()
    chat.ask(ES)
    response = chat.ask("Capacity must not be exceeded.")
    assert "Decisión determinista" not in build_workspace_response_copy_text(response, ES)
    chat.ask(ES)
    response = chat.ask(ES + " only A")
    app = rendered(response, ES)
    assert "Decisión determinista" not in ui_text(app)
    assert "Decisión determinista" not in build_workspace_response_copy_text(response, ES)


def test_english_command_still_has_spanish_decision_explanation():
    response = response_for(command=EN)
    app = rendered(response, EN)
    assert "Alternativa seleccionada bajo la política" in ui_text(app)
    assert "Alternativa seleccionada bajo la política" in build_workspace_response_copy_text(response, EN)


def test_no_providers_calculations_or_evaluation_from_render_or_copy():
    response = response_for()
    targets = (
        "industrial_gases.decision_engine.evaluate_supply_decision",
        "industrial_gases.objective_constraint_evaluation.evaluate_supply_criteria",
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
        rendered(response, ES)
        build_workspace_response_copy_text(response, ES)
        assert all(mock.call_count == 0 for mock in mocks)
