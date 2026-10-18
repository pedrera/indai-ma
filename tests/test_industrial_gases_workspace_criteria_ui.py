"""Functional acceptance of the production Workspace renderer and Copy path."""
from dataclasses import replace
from decimal import Decimal, localcontext
import re
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from industrial_gases.conversational_workspace_ui import (
    _criteria_presentation, _criteria_value, build_workspace_response_copy_text,
)
from industrial_gases.models import Quantity
from tests.test_industrial_gases_workspace_criteria import conversation, SET, GAP


SPANISH_CRITERIA = ("Objetivo: la brecha de stock de seguridad debe ser al menos 0 kg.; "
                    "No debe haber agotamiento antes de la entrega.; La capacidad no debe superarse.")


def render_response(response, question):
    from industrial_gases.conversational_workspace_ui import _render_workspace_response
    _render_workspace_response(response, question)


def rendered(response, question):
    app = AppTest.from_function(render_response, args=(response, question), default_timeout=15).run()
    assert not app.exception
    return app


def spanish_response():
    chat = conversation()
    return chat, chat.ask(SET + "; " + SPANISH_CRITERIA)


def test_spanish_matrix_kinds_labels_order_observations_and_aggregate():
    chat, response = spanish_response()
    app = rendered(response, SPANISH_CRITERIA)
    assert len(app.table) == 3  # existing factual comparison, criteria, aggregate
    matrix, aggregate = app.table[1].value, app.table[2].value
    labels = [branch.alternative.label for branch in response.scenario_set_result.scenario_results]
    assert tuple(matrix.columns) == ("Tipo", "Criterio", "Condición", "Base", *labels)
    assert tuple(matrix["Tipo"]) == ("Objetivo", "Restricción obligatoria", "Restricción obligatoria")
    assert tuple(matrix["Criterio"]) == tuple(
        item.label for item in (*chat.context.scenario_set_state.criteria.objectives,
                                *chat.context.scenario_set_state.criteria.constraints))
    assert matrix["Base"][0] == "No cumple (VIOLATED) · observado: -1100.0 kg"
    assert "Cumple (SATISFIED)" in matrix[labels[1]][0]
    assert "300.0 kg" in matrix[labels[1]][0]
    assert "≥ 0 kg" in matrix["Condición"][0]
    assert "= No" in matrix["Condición"][1]
    assert tuple(aggregate["Escenario"]) == ("Base", *labels)
    assert tuple(aggregate["Cumplimiento de restricciones"]) == ("cumple (SATISFIED)",) * 3
    assert any("únicamente los criterios explícitamente enviados" in caption.value for caption in app.caption)


def test_copy_parity_includes_definitions_observations_and_all_references():
    _, response = spanish_response()
    copied = build_workspace_response_copy_text(response, SPANISH_CRITERIA)
    app = rendered(response, SPANISH_CRITERIA)
    for table in app.table[1:]:
        for column in table.value.columns:
            assert column in copied
        for cell in table.value.to_numpy().flat:
            assert str(cell) in copied
    rows, aggregates, references = _criteria_presentation(response, "es")
    assert rows and aggregates
    for line in references:
        assert line in copied
    assert any("Hospital Costa Sur" in line for line in references)
    binding = response.criteria_evaluation.criteria.source_set_binding
    assert binding.set_reference in copied and binding.set_revision in copied
    for item in response.criteria_evaluation.criteria.objectives + response.criteria_evaluation.criteria.constraints:
        assert item.id in copied and item.definition_source.reference in copied
        assert item.definition_source.revision in copied
    assert "origin: user" in copied
    assert "scenario_id: baseline" in copied and "Evaluator: supply-criteria-v1" in copied
    assert "criterion-1" in copied and " ge 0 kg" in copied
    # The same provenance block is available on screen, not Copy-only hidden data.
    assert any("\n".join(references) == element.value for element in app.text)
    assert "[chunk_id:" not in copied


@pytest.mark.parametrize("criterion,expected", [
    ("Objective: " + GAP, "not applicable (NOT_APPLICABLE)"),
    (GAP, "violated (VIOLATED)"),
    ("Safety-stock gap must be at least 0 t.", "unknown (UNKNOWN)"),
])
def test_english_aggregates_and_unknown_unit_reason(criterion, expected):
    chat = conversation()
    chat.ask(SET)
    response = chat.ask(criterion)
    app = rendered(response, criterion)
    aggregate = app.table[-1].value
    assert aggregate["Hard-constraint compliance"][0] == expected
    copied = build_workspace_response_copy_text(response, criterion)
    assert expected in copied
    if "UNKNOWN" in expected:
        assert "Unknown (UNKNOWN)" in app.table[-2].value["Baseline"][0]
        assert "Incompatible operational units (incompatible_operational_units)" in copied
        assert "-1100.0 kg" in copied and "≥ 0 t" in copied


@pytest.mark.parametrize("issue,reason", [
    ("missing", "Faltan datos de entrada en el origen (source_missing_inputs)"),
    ("invalid", "Los datos de origen no son válidos (source_invalid)"),
])
def test_missing_and_invalid_source_explanations(issue, reason):
    chat = conversation()
    items = []
    for item in chat.portfolio.items:
        if item.item_id == "hospital-costa-sur-o2":
            request = (replace(item.request, consumption_forecast=None) if issue == "missing" else
                       replace(item.request, safety_stock=Quantity(11000, "kg")))
            item = replace(item, request=request)
        items.append(item)
    chat.portfolio = replace(chat.portfolio, items=tuple(items))
    response = chat.ask(SET + "; " + SPANISH_CRITERIA)
    app = rendered(response, SPANISH_CRITERIA)
    copied = build_workspace_response_copy_text(response, SPANISH_CRITERIA)
    assert "No evaluable (UNKNOWN)" in app.table[-2].value["Base"][0]
    assert reason in app.table[-2].value["Base"][0] and reason in copied
    assert "No disponible" in copied
    assert set(app.table[-1].value["Cumplimiento de restricciones"]) == {"desconocido (UNKNOWN)"}


def test_partial_factual_comparison_alongside_full_criteria_matrix():
    chat, initial = spanish_response()
    response = chat.ask("Compare A and B.")
    assert response.criteria_evaluation is initial.criteria_evaluation
    app = rendered(response, "Compare A and B.")
    assert len(app.table[0].value.columns) == 3  # Measure + A + B
    assert "Baseline" not in app.table[0].value.columns
    assert "Baseline" in app.table[1].value.columns
    assert tuple(app.table[2].value["Scenario"])[0] == "Baseline"
    copied = build_workspace_response_copy_text(response, "Compare A and B.")
    assert "Scenario comparison" in copied and "Explicit objectives and hard constraints" in copied


def test_valid_and_invalid_alternatives_stay_in_order_in_same_matrix():
    chat = conversation()
    response = chat.ask("For Hospital Costa Sur compare delivery in 3 days and 5 days; " + GAP)
    app = rendered(response, GAP)
    matrix = app.table[-2].value
    assert "Violated (VIOLATED)" in matrix["Baseline"][0]
    labels = [branch.alternative.label for branch in response.scenario_set_result.scenario_results]
    assert tuple(matrix.columns[3:]) == ("Baseline", *labels)
    assert "Violated (VIOLATED)" in matrix[labels[0]][0]
    assert "Unknown (UNKNOWN)" in matrix[labels[1]][0]
    assert "source_invalid" in matrix[labels[1]][0]
    copied = build_workspace_response_copy_text(response, GAP)
    assert all(value in copied for value in matrix.iloc[0])


@pytest.mark.parametrize("prefix,expected", [("Objetivo: ", "no aplica (NOT_APPLICABLE)"),
                                           ("", "incumple (VIOLATED)")])
def test_spanish_objective_only_and_constraint_aggregate(prefix, expected):
    question = prefix + "La brecha de stock de seguridad debe ser al menos 0 kg."
    response = conversation().ask(SET + "; " + question)
    app = rendered(response, question)
    assert app.table[-1].value["Cumplimiento de restricciones"][0] == expected
    assert expected in build_workspace_response_copy_text(response, question)


def test_documentary_response_after_criteria_keeps_citation_evidence():
    chat, _ = spanish_response()
    response = chat.ask("¿Qué información contractual es relevante?")
    assert response.documentary_sources
    source_ids = tuple(source.source.chunk.chunk_id for source in response.documentary_sources)
    assert "hospital-contract" in source_ids
    assert "[chunk_id:hospital-contract]" in response.explanation
    app = rendered(response, "¿Qué información contractual es relevante?")
    copied = build_workspace_response_copy_text(response, "¿Qué información contractual es relevante?")
    assert not app.exception and "Hospital Costa Sur" in copied
    assert "Contrato de suministro" in copied
    assert response.criteria_evaluation is not None


def test_no_criteria_state_keeps_existing_response_and_copy_sections():
    response = conversation().ask(SET)
    app = rendered(response, SET)
    assert len(app.table) == 1
    copied = build_workspace_response_copy_text(response, SET)
    assert "Comparación de escenarios" in copied
    assert "Explicit objectives and hard constraints" not in copied
    assert not any("criteria" in expander.label.lower() for expander in app.expander)


def test_spanish_capacity_sentence_selects_spanish_presentation():
    chat = conversation()
    chat.ask(SET)
    question = "La capacidad no debe superarse."
    response = chat.ask(question)
    assert "Restricción obligatoria" in build_workspace_response_copy_text(response, question)
    assert "Tipo" in rendered(response, question).table[-2].value.columns


def test_presentation_preserves_decimal_precision_and_boolean_false():
    with localcontext() as context:
        context.prec = 4
        assert _criteria_value(Decimal("12345.678901234567890123456789"), "Nm3", "en") == "12345.678901234567890123456789 Nm3"
    assert _criteria_value(False, None, "es") == "No"
    assert _criteria_value(Decimal(0), "Sm3", "es") == "0 Sm3"


def test_clipboard_action_uses_extended_production_copy_text():
    _, response = spanish_response()
    with patch("industrial_gases.conversational_workspace_ui.render_clipboard_button") as clipboard:
        rendered(response, SPANISH_CRITERIA)
    assert clipboard.call_count == 1
    assert clipboard.call_args.args[0] == build_workspace_response_copy_text(response, SPANISH_CRITERIA)


def test_render_and_copy_do_not_evaluate_or_call_external_dependencies():
    chat, response = spanish_response()
    forbidden = AssertionError("presentation must only read response data")
    with patch("industrial_gases.conversational_workspace.evaluate_supply_criteria", side_effect=forbidden), \
         patch("industrial_gases.objective_constraint_evaluation.evaluate_supply_criteria", side_effect=forbidden), \
         patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=forbidden), \
         patch("industrial_gases.service.build_supply_projection", side_effect=forbidden), \
         patch("llm_client.LMStudioProvider.generate_response", side_effect=forbidden), \
         patch("llm_client.OpenAIProvider.generate_response", side_effect=forbidden), \
         patch("embeddings.LMStudioEmbeddingProvider.embed_query", side_effect=forbidden), \
         patch("rag_service.RAGService.retrieve", side_effect=forbidden):
        app = rendered(response, SPANISH_CRITERIA)
        copied = build_workspace_response_copy_text(response, SPANISH_CRITERIA)
    assert app.table and not chat.model.calls and not chat.knowledge.calls
    assert not re.search(r"\b(best|winner|ranking|score|recommended|optimized|globally safe|feasible)\b", copied, re.I)


def test_input_guidance_is_in_existing_composer_without_new_form():
    def surface():
        from industrial_gases.conversational_workspace_ui import render_conversational_workspace
        render_conversational_workspace()
    app = AppTest.from_function(surface).run()
    assert not app.exception
    captions = " ".join(element.value for element in app.caption)
    assert "Objetivo: la brecha de stock de seguridad debe ser al menos 0 kg." in captions
    assert "No debe haber agotamiento antes de la entrega." in captions
    assert "punto y coma" in captions
    assert len(app.chat_input) == 1
