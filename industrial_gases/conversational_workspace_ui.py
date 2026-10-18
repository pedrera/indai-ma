"""Conversation-first Streamlit surface over structured workspace responses."""
from __future__ import annotations

import re

import streamlit as st

from clipboard_ui import render_clipboard_button
from .conversational_workspace import WorkspaceResponse
from .portfolio_query import SupplyAgentSessionContext
from .portfolio_ui import _CUSTOMER_LABELS, _FINDING_LABELS, _format_number, _quantity_text
from .supply_agent import _is_document_list_request, _jsonable
from .objective_constraint_models import CriterionOutcome, CriterionKind
from .decision_engine_models import DecisionScopeMode, SupplyDecisionStatus, SupplyDecisionReason


STARTER_PROMPTS = (
    "¿Qué posiciones requieren atención?",
    "¿Qué ocurre en Hospital Costa Sur?",
    "¿Qué documentación es relevante?",
    "¿Qué pasaría si adelantamos una entrega?",
)

_TEXT = {
    "en": {
        "subtitle": "Ask about industrial operations, documents or an explicit scenario.",
        "ask": "Ask about operations, documents or a scenario...",
        "try": "**Try asking:**",
        "copy": "Copy",
        "new": "New conversation",
        "scope": "Conversation scope",
        "focus": "focused position",
        "inventory": "Inventory before delivery",
        "safety_gap": "Safety-stock gap",
        "stockout": "Stockout before delivery",
        "attention": "Below configured safety stock",
        "no_attention": "No attention facts",
        "missing": "Missing information",
        "invalid": "The evaluation data is inconsistent. See Evidence & trace for details.",
        "provider_error": "The generated explanation is unavailable; verified operational facts remain below.",
        "clarify": "Please clarify the position or information needed.",
        "safe_breach": "Projected inventory remains below configured safety stock; no physical stockout is projected.",
        "safe_stockout": "A physical stockout is projected before delivery.",
        "safe_capacity": "Post-delivery inventory exceeds configured capacity.",
        "safe_fallback": "Review the position-level inventory and stockout facts below.",
        "intent": "Intent",
        "capabilities": "Capabilities",
        "selected": "Selected positions",
        "status": "Evaluation status",
        "sources": "Sources",
        "relevant_documents": "Relevant documents by position",
        "global_sources": "Applicable global documentation",
        "source": "Source",
        "applicable_to": "Applies to",
        "evaluation_issue": "Evaluation issue",
        "projection": "Projection",
        "request": "Structured request",
        "findings": "Finding codes",
        "scenario": "Scenario",
        "trace": "Evidence & trace",
        "source_boundary": "Citation validation",
        "source_passed": "Passed",
        "source_failed": "Not passed; see evaluation details",
        "source_unavailable": "Not available",
        "guardrail": "Explanation replaced because it contained a physical-event claim not supported by the selected projections.",
        "metrics": ("Measure", "Current", "Alternative"),
        "consumption": "Consumption until delivery",
        "inventory_after": "Inventory after delivery",
        "required_delivery": "Required delivery volume",
        "capacity": "Capacity exceeded",
        "scenario_comparison": "Scenario comparison",
        "scenario_measure": "Measure",
        "not_available": "Not available",
        "delivery_timing": "Delivery timing",
        "quantity_change": "Planned delivery quantity changed",
        "consumption_change": "Consumption forecast changed",
        "source_contract": "Supply contract",
        "source_procedure": "Operating procedure",
        "source_installation": "Installation specification",
        "source_policy": "Industrial gas policy",
        "global": "Global",
        "page": "page",
        "site": "Site",
        "product": "Product",
    },
    "es": {
        "subtitle": "Pregunta sobre operaciones industriales, documentos o un escenario explícito.",
        "ask": "Pregunta sobre operaciones, documentos o un escenario...",
        "try": "**Prueba con una pregunta:**",
        "copy": "Copiar",
        "new": "Nueva conversación",
        "scope": "Ámbito de la conversación",
        "focus": "posición enfocada",
        "inventory": "Inventario antes de la entrega",
        "safety_gap": "Diferencia frente al stock de seguridad",
        "stockout": "Agotamiento antes de la entrega",
        "attention": "Por debajo del stock de seguridad configurado",
        "no_attention": "Sin hechos de atención",
        "missing": "Información pendiente",
        "invalid": "Los datos de evaluación son incoherentes. Consulta Evidencia y trazabilidad para ver el detalle.",
        "provider_error": "No está disponible la explicación generada; se conservan los hechos operativos verificados.",
        "clarify": "Aclara qué posición o información necesitas.",
        "safe_breach": "El inventario previsto queda por debajo del stock de seguridad configurado; no se prevé agotamiento físico.",
        "safe_stockout": "Se prevé agotamiento físico antes de la entrega.",
        "safe_capacity": "El inventario posterior a la entrega supera la capacidad configurada.",
        "safe_fallback": "Consulta abajo el inventario y el estado de agotamiento de cada posición.",
        "intent": "Intención",
        "capabilities": "Capacidades",
        "selected": "Posiciones seleccionadas",
        "status": "Estado de evaluación",
        "sources": "Fuentes",
        "relevant_documents": "Documentación relevante por posición",
        "global_sources": "Documentación general aplicable",
        "source": "Fuente",
        "applicable_to": "Aplicable a",
        "evaluation_issue": "Problema de evaluación",
        "projection": "Proyección",
        "request": "Solicitud estructurada",
        "findings": "Códigos de hallazgo",
        "scenario": "Escenario",
        "trace": "Evidencia y trazabilidad",
        "source_boundary": "Validación de citas",
        "source_passed": "Correcta",
        "source_failed": "No superada; consulta los detalles de evaluación",
        "source_unavailable": "No disponible",
        "guardrail": "Se sustituyó la explicación porque contenía una afirmación sobre un evento físico no respaldada por las proyecciones seleccionadas.",
        "metrics": ("Medida", "Actual", "Alternativa"),
        "consumption": "Consumo hasta la entrega",
        "inventory_after": "Inventario después de la entrega",
        "required_delivery": "Volumen de entrega requerido",
        "capacity": "Capacidad superada",
        "scenario_comparison": "Comparación de escenarios",
        "scenario_measure": "Medida",
        "not_available": "No disponible",
        "delivery_timing": "Fecha de entrega",
        "quantity_change": "Cambio de cantidad de entrega",
        "consumption_change": "Cambio de previsión de consumo",
        "source_contract": "Contrato de suministro",
        "source_procedure": "Procedimiento operativo",
        "source_installation": "Especificación de instalación",
        "source_policy": "Política de gases industriales",
        "global": "General",
        "page": "página",
        "site": "Centro",
        "product": "Producto",
    },
}

_FINDINGS = {
    "safety_stock_breach": {"en": "Below configured safety stock", "es": "Por debajo del stock de seguridad configurado"},
    "stockout_before_delivery": {"en": "Stockout before delivery", "es": "Agotamiento antes de la entrega"},
    "capacity_overflow": {"en": "Post-delivery capacity exceeded", "es": "Capacidad superada tras la entrega"},
}

_MISSING = {
    "site": {"en": "Site", "es": "Centro"},
    "application": {"en": "Application", "es": "Aplicación"},
    "gas_product": {"en": "Gas product", "es": "Gas"},
    "installation": {"en": "Installation", "es": "Instalación"},
    "inventory_snapshot": {"en": "Current inventory", "es": "Inventario actual"},
    "consumption_forecast": {"en": "Consumption forecast", "es": "Previsión de consumo"},
    "delivery_plan": {"en": "Planned delivery", "es": "Entrega prevista"},
    "safety_stock": {"en": "Safety stock", "es": "Stock de seguridad"},
    "reference_time": {"en": "Reference time", "es": "Fecha de referencia"},
}


def _language(text: str) -> str:
    folded = text.casefold()
    if any(mark in folded for mark in ("¿", "¡", "á", "é", "í", "ó", "ú", "ñ")):
        return "es"
    if re.search(r"\b(?:que|posiciones|atencion|contrato|entrega|hospital|documentacion|prevision|"
                 r"compara|comparar|escenarios?|inventario|dias|antes|despues|diferencia|"
                 r"objetivo|capacidad|agotamiento|brecha|debe)\b", folded):
        return "es"
    return "en"


def render_conversational_workspace(on_submit=None, *, running: bool = False) -> None:
    """Render history, structured evidence cards and a persistent chat composer."""
    st.header("indAI MA")
    if "workspace_messages" not in st.session_state:
        st.session_state.workspace_messages = []
    if "workspace_context" not in st.session_state:
        st.session_state.workspace_context = SupplyAgentSessionContext()

    recent_question = next(
        (message.get("content", "") for message in reversed(st.session_state.workspace_messages)
         if message.get("role") == "user"),
        "",
    )
    conversation_lang = _language(recent_question) if recent_question else "es"
    text = _TEXT[conversation_lang]
    st.caption(text["subtitle"])
    _, reset = st.columns([5, 1])
    with reset:
        if st.button(text["new"], key="workspace_new_conversation", disabled=running):
            st.session_state.workspace_messages = []
            st.session_state.workspace_context = SupplyAgentSessionContext()
            for entry in st.session_state.get("workspace_execution_history", ()):
                st.session_state.get("execution_views", {}).pop(entry.operation_id, None)
            st.session_state.workspace_execution_history = ()
            st.session_state.workspace_pipeline_selected_operation_id = None
            st.session_state.workspace_operation_id = None
            st.session_state.workspace_pending_prompt = None
            st.session_state.supply_agent_context = SupplyAgentSessionContext()
            st.session_state.supply_agent_result = None
            st.rerun()

    for message_index, message in enumerate(st.session_state.workspace_messages):
        role = message["role"]
        with st.chat_message(role):
            if role == "user":
                st.markdown(message["content"])
            elif isinstance(message.get("response"), WorkspaceResponse):
                copy_key = message.get("operation_id") or f"message-{message_index}"
                _render_workspace_response(
                    message["response"], message.get("question", ""), copy_key=str(copy_key),
                )
            else:
                st.markdown(message.get("content", ""))

    if not st.session_state.workspace_messages:
        st.markdown(text["try"])
        prompt_columns = st.columns(2)
        for index, prompt in enumerate(STARTER_PROMPTS):
            if prompt_columns[index % 2].button(
                prompt, key=f"workspace_starter_{index}",
                disabled=running or on_submit is None,
                width="stretch",
            ):
                _submit_prompt(prompt, on_submit)

    st.caption(
        'Criterios explícitos para un conjunto de escenarios: '
        '«Objetivo: la brecha de stock de seguridad debe ser al menos 0 kg.»; '
        '«No debe haber agotamiento antes de la entrega.» Separa los criterios con punto y coma.'
        if conversation_lang == "es" else
        'Explicit criteria for a scenario set: “Objective: Safety-stock gap must be at least 0 kg.”; '
        '“There must be no stockout before delivery.” Separate criteria with semicolons.'
    )
    prompt = st.chat_input(
        text["ask"],
        key="workspace_chat_input",
        disabled=running or on_submit is None,
    )
    if prompt:
        _submit_prompt(prompt, on_submit)


def _submit_prompt(prompt: str, on_submit) -> None:
    text = prompt.strip()
    if not text:
        return
    context = st.session_state.workspace_context
    st.session_state.workspace_messages.append({"role": "user", "content": text})
    st.session_state.workspace_pending_prompt = text
    response = on_submit(text, context)
    if isinstance(response, WorkspaceResponse):
        st.session_state.workspace_messages.append({
            "role": "assistant", "question": text, "response": response,
        })
        st.session_state.workspace_context = response.session_context
        st.session_state.workspace_pending_prompt = None
    st.rerun()


def _render_workspace_response(
    response: WorkspaceResponse, question: str = "", *, copy_key: str = "workspace-response",
) -> None:
    lang = _language(question)
    text = _TEXT[lang]
    if response.status.value == "provider_error":
        st.warning(text["provider_error"])
    elif response.status.value == "needs_input":
        st.info(_primary_answer(response, question, lang) or text["clarify"])
    elif response.semantic_guard_applied:
        st.markdown(_safe_grounded_answer(response, lang))
    elif (_is_document_list_request(question)
          and (response.documentary_sources or response.evidence.global_sources)):
        _render_document_inventory(response, lang)
    elif response.explanation:
        st.markdown(_primary_answer(response, question, lang))

    if response.scenario_comparison is None:
        for position in response.positions:
            _render_position_card(position, lang)
    if response.documentary_sources:
        _render_document_sources(response, lang)
    for position in response.positions:
        if position.scenario is not None:
            _render_scenario_card(position, lang)
    if response.scenario_comparison is not None:
        if response.positions:
            st.markdown(f"**{_position_label(response.positions[0], lang)}**")
        _render_scenario_comparison(response.scenario_comparison, lang)
    if response.criteria_evaluation is not None:
        _render_criteria_evaluation(response, lang)
    if response.decision_evaluation is not None:
        _render_decision_evaluation(response)
    if response.evidence.global_sources:
        st.markdown(f"**{text['global_sources']}**")
        for source in response.evidence.global_sources:
            _render_source(source.source, source.item_ids, response, lang, global_scope=True)

    render_clipboard_button(
        build_workspace_response_copy_text(response, question),
        text["copy"], key=f"workspace-response-{copy_key}",
    )

    with st.expander(text["trace"]):
        st.write(f"{text['intent']}: {response.route.intent.value}")
        st.write(f"{text['capabilities']}: " + ", ".join(response.route.capabilities))
        st.write(f"{text['selected']}: " + (", ".join(response.selected_item_ids) or "none"))
        st.write(f"{text['focus']}: " + (response.session_context.focused_item_id or "none"))
        st.write("Previous structured intent: " + (response.session_context.last_intent or "none"))
        st.write("Document scope: " + (", ".join(response.session_context.last_document_scope_item_ids) or "none"))
        st.write("Scenario target: " + (response.session_context.last_scenario_target_id or "none"))
        if response.session_context.last_scenario_change:
            st.write("Last explicit scenario change:", response.session_context.last_scenario_change)
        if response.comparison is not None:
            st.markdown("**Structured comparison**")
            st.json(_jsonable(response.comparison))
        if response.semantic_guard_applied:
            st.warning(text["guardrail"])
        if response.documentary_sources:
            if "applicable_documentary_source" in response.unsupported_questions:
                citation_state = text["source_failed"]
            elif response.status.value == "completed":
                citation_state = text["source_passed"]
            else:
                citation_state = text["source_unavailable"]
            st.write(f"{text['source_boundary']}: {citation_state}")
        for item in response.evidence.items:
            st.markdown(f"**{item.item.item_id}**")
            st.write(f"{text['status']}: {item.result.status}")
            st.markdown(f"**{text['request']}**")
            st.json(_jsonable(item.item.request))
            st.markdown(f"**{text['projection']} / result**")
            st.json(_jsonable(item.result))
            st.markdown(f"**{text['findings']}**")
            st.json(_jsonable(item.result.findings))
            for reference in item.evidence_references:
                st.write(
                    f"{reference.evidence_type}: {reference.source_id} · "
                    + ", ".join(reference.fields)
                )
            for source in item.knowledge_sources:
                st.markdown(f"**{text['sources']} · {source.chunk.chunk_id}**")
                st.json({**source.source_dict(), "document_id": source.chunk.document_id})
                st.code(source.chunk.text)
            if item.scenario is not None:
                st.markdown(f"**{text['scenario']} · structured baseline and alternative**")
                st.json(_jsonable(item.scenario))
        for scoped in response.evidence.global_sources:
            st.markdown(f"**{text['global_sources']} · {scoped.source.chunk.chunk_id}**")
            st.json({**scoped.source.source_dict(), "document_id": scoped.source.chunk.document_id,
                     "applicability": "global", "item_ids": scoped.item_ids})
            st.code(scoped.source.chunk.text)
        for reference in response.evidence_references:
            st.write(
                f"{reference.item_id} · {reference.evidence_type}: {reference.source_id} · "
                + ", ".join(reference.fields)
            )
        for execution in response.tool_executions:
            st.write(
                f"Tool: {execution['name']} · {execution.get('elapsed_seconds', 0.0):.3f}s"
            )
        if response.operational_errors:
            st.warning("Operational errors: " + "; ".join(response.operational_errors))
            categories = _operational_error_categories(response.operational_errors)
            if categories:
                st.caption("Failure category: " + ", ".join(categories))
        if response.unsupported_questions:
            st.caption("Clarification required for: " + ", ".join(response.unsupported_questions))
        if response.evidence.retrieval_failures:
            st.caption("Retrieval failures: " + ", ".join(
                failure.item_id for failure in response.evidence.retrieval_failures
            ))


def build_workspace_response_copy_text(response: WorkspaceResponse, question: str = "") -> str:
    """Copy only the answer and structured facts presented in the Workspace response."""
    lang = _language(question)
    text = _TEXT[lang]
    sections: list[str] = []
    if response.status.value == "provider_error":
        sections.append(text["provider_error"])
    elif response.status.value == "needs_input":
        answer = _primary_answer(response, question, lang) or text["clarify"]
        sections.append(answer)
    elif response.semantic_guard_applied:
        sections.append(_safe_grounded_answer(response, lang))
    elif (_is_document_list_request(question)
          and (response.documentary_sources or response.evidence.global_sources)):
        sections.append(_document_inventory_answer(response, lang))
    elif response.explanation:
        sections.append(_primary_answer(response, question, lang))

    position_lines: list[str] = []
    for item in response.positions:
        result = item.result
        label = _position_label(item, lang)
        position_lines.append(label)
        if response.scenario_comparison is not None:
            continue
        projection = result.projection
        if result.status == "COMPLETED" and projection is not None:
            position_lines.extend((
                f"- {text['inventory']}: {_quantity_text(projection.inventory_immediately_before_delivery)}",
                f"- {text['safety_gap']}: {_quantity_text(projection.safety_stock_gap_before_delivery)}",
                f"- {text['stockout']}: {_yes_no(projection.stockout_before_delivery, lang)}",
            ))
            finding_labels = [
                _FINDINGS.get(fact.source_finding.code, {}).get(lang)
                or _FINDING_LABELS.get(fact.source_finding.code)
                for fact in item.attention.facts
            ]
            finding_labels = [value for value in finding_labels if value]
            position_lines.append(
                ("Attention: " if lang == "en" else "Atención: ")
                + ("; ".join(finding_labels) if finding_labels else text["no_attention"])
            )
        else:
            position_lines.append(f"{text['status']}: {result.status}")
            if result.status == "MISSING_INPUTS":
                missing_labels = [
                    _MISSING.get(key, {}).get(lang) or _humanize_key(key)
                    for key in result.missing_inputs
                ]
                position_lines.append(text["missing"] + ": " + ", ".join(missing_labels))
            elif result.status == "INVALID":
                position_lines.append(text["invalid"])
    if position_lines:
        sections.append("\n".join(position_lines))

    if response.documentary_sources:
        source_lines = [text["sources"]]
        for item in response.evidence.items:
            for source in item.knowledge_sources:
                source_lines.append(_workspace_source_caption(
                    source, _position_label(item, lang), lang,
                ))
        sections.append("\n".join(source_lines))

    for item in response.positions:
        if item.scenario is not None:
            sections.append(_workspace_scenario_copy_text(item, lang))

    if response.scenario_comparison is not None:
        sections.append(_scenario_comparison_copy_text(response.scenario_comparison, lang))

    if response.criteria_evaluation is not None:
        sections.append(_criteria_copy_text(response, lang))
    if response.decision_evaluation is not None:
        sections.append(_decision_copy_text(response))

    if response.evidence.global_sources:
        source_lines = [text["global_sources"]]
        for scoped in response.evidence.global_sources:
            source_lines.append(_workspace_source_caption(scoped.source, text["global"], lang))
        sections.append("\n".join(source_lines))
    return "\n\n".join(section for section in sections if section.strip()).strip()


_CRITERIA_TEXT = {
    "es": {
        "title": "Objetivos y restricciones explícitos",
        "boundary": "Estos resultados evalúan únicamente los criterios explícitamente enviados.",
        "kind": "Tipo", "criterion": "Criterio", "definition": "Condición", "scenario": "Escenario",
        "objective": "Objetivo", "hard_constraint": "Restricción obligatoria",
        "aggregate": "Cumplimiento de restricciones", "observed": "observado",
        "position": "Posición enfocada", "references": "Referencias de criterios y proyecciones",
        "unavailable": "No disponible",
        "outcomes": {"SATISFIED": "Cumple", "VIOLATED": "No cumple", "UNKNOWN": "No evaluable"},
        "aggregates": {"SATISFIED": "cumple", "VIOLATED": "incumple", "UNKNOWN": "desconocido", "NOT_APPLICABLE": "no aplica"},
    },
    "en": {
        "title": "Explicit objectives and hard constraints",
        "boundary": "These results assess only the explicitly submitted criteria.",
        "kind": "Kind", "criterion": "Criterion", "definition": "Condition", "scenario": "Scenario",
        "objective": "Objective", "hard_constraint": "Hard constraint",
        "aggregate": "Hard-constraint compliance", "observed": "observed",
        "position": "Focused position", "references": "Criterion and projection references",
        "unavailable": "Not available",
        "outcomes": {"SATISFIED": "Satisfied", "VIOLATED": "Violated", "UNKNOWN": "Unknown"},
        "aggregates": {"SATISFIED": "satisfied", "VIOLATED": "violated", "UNKNOWN": "unknown", "NOT_APPLICABLE": "not applicable"},
    },
}

_CRITERIA_REASONS = {
    "source_invalid": ("Source data is invalid", "Los datos de origen no son válidos"),
    "source_missing_inputs": ("Required source inputs are missing", "Faltan datos de entrada en el origen"),
    "source_not_completed": ("Source assessment is not completed", "La evaluación de origen no está completada"),
    "projection_unavailable": ("Projection unavailable", "Proyección no disponible"),
    "metric_unavailable": ("Metric unavailable", "Medida no disponible"),
    "fact_unavailable": ("Fact unavailable", "Hecho no disponible"),
    "invalid_fact_type": ("Invalid fact type", "Tipo de dato no válido"),
    "invalid_fact_unit": ("Invalid operational unit", "Unidad operativa no válida"),
    "invalid_fact_value": ("Invalid fact value", "Valor del hecho no válido"),
    "provenance_unavailable": ("Provenance unavailable", "Procedencia no disponible"),
    "provenance_fact_mismatch": ("Provenance does not match the fact", "La procedencia no coincide con el hecho"),
    "incompatible_operational_units": ("Incompatible operational units", "Unidades operativas incompatibles"),
}


def _criteria_value(value, unit, lang):
    if value is None:
        return _CRITERIA_TEXT[lang]["unavailable"]
    if isinstance(value, bool):
        return _yes_no(value, lang)
    # Preserve precision without normalizing under the ambient Decimal context.
    return f"{format(value, 'f')} {unit or ''}".strip()


def _criteria_definition(predicate, lang):
    keys = {
        "consumption_until_delivery": "consumption", "inventory_immediately_before_delivery": "inventory",
        "safety_stock_gap_before_delivery": "safety_gap", "stockout_before_delivery": "stockout",
        "inventory_immediately_after_delivery": "inventory_after", "required_delivery_volume": "required_delivery",
        "capacity_exceeded": "capacity",
    }
    operator = {"lt": "<", "le": "≤", "eq": "=", "ge": "≥", "gt": ">"}[predicate.operator.value]
    return (f"{_TEXT[lang][keys[predicate.metric.value]]} {operator} "
            f"{_criteria_value(predicate.target, predicate.unit, lang)}")


def _criteria_presentation(response, lang):
    """Format retained evaluation data for both UI and Copy, without evaluation."""
    evaluation = response.criteria_evaluation
    text = _CRITERIA_TEXT[lang]
    retained = response.session_context.scenario_set_state
    results = response.scenario_set_result or (retained.result if retained else None)
    labels = {"baseline": "Base" if lang == "es" else "Baseline"}
    if results is not None:
        labels.update({branch.alternative.id: branch.alternative.label for branch in results.scenario_results})
    scenarios = evaluation.scenario_evaluations
    headings = [labels.get(s.source.scenario_id, s.source.scenario_id) for s in scenarios]
    headings = [f"{label} ({s.source.scenario_id})" if headings.count(label) > 1
                or label in {text["kind"], text["criterion"], text["definition"]} else label
                for label, s in zip(headings, scenarios)]
    definitions = tuple((CriterionKind.OBJECTIVE, item) for item in evaluation.criteria.objectives) + tuple(
        (CriterionKind.HARD_CONSTRAINT, item) for item in evaluation.criteria.constraints)
    rows = []
    for kind, definition in definitions:
        row = {text["kind"]: text[kind.value], text["criterion"]: definition.label,
               text["definition"]: _criteria_definition(definition.predicate, lang)}
        for heading, scenario in zip(headings, scenarios):
            cell = next(item for item in scenario.evaluations if item.criterion_id == definition.id)
            row[heading] = _criteria_cell(cell, lang)
        rows.append(row)
    aggregates = [{text["scenario"]: heading, text["aggregate"]:
                   f"{text['aggregates'][scenario.hard_constraint_outcome.value]} ({scenario.hard_constraint_outcome.value})"}
                  for heading, scenario in zip(headings, scenarios)]
    binding = evaluation.criteria.source_set_binding
    position = (_position_label(response.positions[0], lang) if response.positions else
                " / ".join(value or "?" for value in vars(binding.position_identity).values()))
    references = [f"{text['position']}: {position}",
                  f"Set: {binding.set_reference} · revision: {binding.set_revision}",
                  f"Position identity: {vars(binding.position_identity)}",
                  f"Evaluator: {evaluation.evaluator_version}"]
    for _, definition in definitions:
        source = definition.definition_source
        references.append(f"{definition.id} · {definition.label}: {definition.predicate.metric.value} "
                          f"{definition.predicate.operator.value} {_criteria_value(definition.predicate.target, definition.predicate.unit, lang)}; "
                          f"origin: {source.origin.value}; reference: {source.reference}; revision: {source.revision}")
    for heading, scenario in zip(headings, scenarios):
        source = scenario.source
        references.extend((f"{heading} · scenario_id: {source.scenario_id}",
                           f"Result: {source.result_reference}",
                           f"Projection: {source.projection_reference or text['unavailable']}"))
        references.extend(f"Provenance: {reference}" for reference in source.provenance_references)
    return rows, aggregates, references


def _criteria_cell(cell, lang):
    text = _CRITERIA_TEXT[lang]
    rendered = (f"{text['outcomes'][cell.outcome.value]} ({cell.outcome.value}) · "
                f"{text['observed']}: {_criteria_value(cell.observed_value, cell.observed_unit, lang)}")
    if cell.outcome is CriterionOutcome.UNKNOWN:
        reason = _CRITERIA_REASONS.get(cell.reason, ("Fact cannot be evaluated", "El hecho no es evaluable"))
        rendered += f" · {reason[lang == 'es']} ({cell.reason})"
    return rendered


_DECISION_TITLE = "Decisión determinista"
_DECISION_BOUNDARY = (
    "Alcance técnico: el resultado se limita al cumplimiento de las restricciones explícitas "
    "dentro del alcance autorizado. No autoriza acciones externas. Las referencias de sesión "
    "no constituyen auditoría persistente, autenticación ni almacenamiento histórico."
)
_DECISION_REASONS = {
    SupplyDecisionReason.UNIQUE_ELIGIBLE_CANDIDATE:
        "Existe una única candidata elegible; las demás son definitivamente no elegibles.",
    SupplyDecisionReason.NO_HARD_CONSTRAINTS: "No se definieron restricciones obligatorias.",
    SupplyDecisionReason.NO_ELIGIBLE_CANDIDATE: "Ninguna candidata cumple todas las restricciones.",
    SupplyDecisionReason.MULTIPLE_ELIGIBLE_CANDIDATES: "Varias candidatas cumplen todas las restricciones.",
    SupplyDecisionReason.ELIGIBILITY_UNRESOLVED:
        "Hay candidatas con elegibilidad pendiente por restricciones UNKNOWN; "
        "estos hechos impiden establecer una selección única. UNKNOWN no implica incumplimiento.",
    SupplyDecisionReason.UNSUPPORTED_PREFERENCE: "Existe una preferencia no soportada.",
    SupplyDecisionReason.POLICY_REQUIRED: "Falta una política explícita de selección.",
    SupplyDecisionReason.UNSUPPORTED_POLICY: "La política de selección no está soportada.",
}


def _decision_presentation(response):
    """Format only this response's recorded decision; never consult session decisions."""
    decision = response.decision_evaluation
    labels = {"baseline": "Base"}
    if response.scenario_set_result is not None:
        labels.update((branch.alternative.id, branch.alternative.label)
                      for branch in response.scenario_set_result.scenario_results)
    lines = [f"Estado: {decision.status.value}"]
    lines.extend(f"Motivo: {reason.value} · {_DECISION_REASONS[reason]}" for reason in decision.reasons)
    policy, scope = decision.policy, decision.scope
    lines.append(f"Política: {policy.policy_id} · versión: {policy.policy_version} · "
                 f"regla: {policy.selection_rule.value}" if policy else "Política: No disponible")
    lines.append(f"Alcance autorizado: {scope.mode.value}" if scope else "Alcance autorizado: No disponible")
    if scope is not None:
        lines.append("Baseline participa explícitamente como candidata." if scope.mode is DecisionScopeMode.ALL_SCENARIOS
                     else "Baseline excluido de las candidatas; se conserva como referencia factual.")
    lines.append("Candidatas participantes: " + (", ".join(decision.candidate_ids) or "Ninguna"))
    if decision.status is SupplyDecisionStatus.DECIDED:
        lines.extend(("Alternativa seleccionada bajo la política de cumplimiento único de restricciones explícitas.",
                      f"Identificador seleccionado: {decision.selected_scenario_id}"))
    rows, references = [], []
    definitions = decision.criteria_evaluation.criteria.constraints
    for assessment in decision.candidate_assessments:
        row = {"Candidata": labels.get(assessment.scenario_id, assessment.scenario_id),
               "ID": assessment.scenario_id}
        cells = {cell.criterion_id: cell for cell in assessment.scenario_evaluation.evaluations
                 if cell.criterion_kind is CriterionKind.HARD_CONSTRAINT}
        for definition in definitions:
            row[f"{definition.id}: {_criteria_definition(definition.predicate, 'es')}"] = _criteria_cell(cells[definition.id], "es")
        row["Elegibilidad"] = (assessment.eligibility.value if assessment.eligibility is not None
                              else f"No aplica ({assessment.hard_constraint_outcome.value})")
        rows.append(row)
        satisfied = tuple(cell.criterion_id for cell in cells.values() if cell.outcome is CriterionOutcome.SATISFIED)
        lines.append(f"{assessment.scenario_id} · {row['Elegibilidad']} · restricciones satisfechas: "
                     f"{', '.join(satisfied) or 'Ninguna'}; violadas: "
                     f"{', '.join(assessment.violation_references) or 'Ninguna'}; UNKNOWN: "
                     f"{', '.join(assessment.unknown_references) or 'Ninguna'}.")
    binding = decision.source_set_binding
    references.extend((f"Identidad de la posición: {vars(binding.position_identity)}",
        f"ScenarioSet: {binding.set_reference} · revisión: {binding.set_revision}",
        f"Versión del motor: {decision.engine_version}",
        f"Evaluación de criterios: {decision.criteria_evaluation_reference}",
        f"Versión del evaluador: {decision.criteria_evaluation.evaluator_version}"))
    if policy is not None:
        origin = policy.definition_source
        references.append(f"Origen de política: {origin.origin.value} · referencia: {origin.reference} · revisión: {origin.revision}")
    if scope is not None:
        references.append(f"Fuente del alcance: {scope.source_reference}")
    criteria = decision.criteria_evaluation.criteria
    references.append(f"Definición de criterios: {criteria.definition_source.reference} · revisión: {criteria.definition_source.revision}")
    for definition in criteria.objectives + criteria.constraints:
        origin = definition.definition_source
        references.append(f"{definition.id}: {definition.label} · {_criteria_definition(definition.predicate, 'es')} · "
                          f"origen: {origin.origin.value} · referencia: {origin.reference} · revisión: {origin.revision}")
    for scenario in decision.criteria_evaluation.scenario_evaluations:
        source = scenario.source
        references.extend((f"{source.scenario_id} · resultado: {source.result_reference}",
                           f"Proyección: {source.projection_reference or 'No disponible'}"))
        references.extend(f"Procedencia: {ref}" for ref in source.provenance_references)
        references.extend(f"{source.scenario_id} / {cell.criterion_id}: {_criteria_cell(cell, 'es')} · motivo: {cell.reason}"
                          for cell in scenario.evaluations)
    references.extend(f"Referencia: {ref}" for ref in decision.provenance_references)
    return lines, rows, references


def _render_decision_evaluation(response):
    lines, rows, references = _decision_presentation(response)
    st.markdown(f"**{_DECISION_TITLE}**")
    for line in lines:
        st.text(line)
    if rows:
        st.table(rows)
    st.caption(_DECISION_BOUNDARY)
    with st.expander("Procedencia de la decisión"):
        st.text("\n".join(references))


def _decision_copy_text(response):
    lines, rows, references = _decision_presentation(response)
    parts = [_DECISION_TITLE, *lines]
    if rows:
        parts.append(" | ".join(rows[0]))
        parts.extend(" | ".join(row.values()) for row in rows)
    return "\n".join((*parts, _DECISION_BOUNDARY, *references))


def _render_criteria_evaluation(response, lang):
    text = _CRITERIA_TEXT[lang]
    rows, aggregates, references = _criteria_presentation(response, lang)
    st.markdown(f"**{text['title']}**")
    st.caption(text["boundary"])
    st.table(rows)
    st.markdown(f"**{text['aggregate']}**")
    st.table(aggregates)
    with st.expander(text["references"]):
        st.text("\n".join(references))


def _criteria_copy_text(response, lang):
    text = _CRITERIA_TEXT[lang]
    rows, aggregates, references = _criteria_presentation(response, lang)
    lines = [text["title"], text["boundary"]]
    for table in (rows, aggregates):
        if table:
            lines.append(" | ".join(table[0]))
            lines.extend(" | ".join(row.values()) for row in table)
    lines.extend(references)
    return "\n".join(lines)


def _workspace_source_caption(source, identity: str, lang: str) -> str:
    chunk = source.chunk
    details = [identity, _document_type_label(chunk.metadata.get("document_type"), lang)]
    if chunk.page_start is not None:
        page = chunk.page_start if chunk.page_end in (None, chunk.page_start) else f"{chunk.page_start}–{chunk.page_end}"
        details.append(f"{_TEXT[lang]['page']} {page}")
    return " · ".join(details)


def _workspace_scenario_copy_text(item, lang: str) -> str:
    scenario = item.scenario
    text = _TEXT[lang]
    lines = [f"{_scenario_title(item, lang)} · {_position_label(item, lang)}"]
    change_label = _scenario_change_label(item, lang)
    if change_label:
        lines.append(change_label)
    for label, result_name in (("Current", scenario.baseline_result), ("Alternative", scenario.alternative_result)):
        if result_name.projection is None:
            if result_name.status == "MISSING_INPUTS":
                missing = ", ".join(
                    _MISSING.get(key, {}).get(lang) or _humanize_key(key)
                    for key in result_name.missing_inputs
                )
                lines.append(f"{label}: {_TEXT[lang]['missing']}: {missing}")
            elif result_name.status == "INVALID":
                lines.append(f"{label}: {_TEXT[lang]['invalid']}")
    current = scenario.baseline_result.projection
    alternative = scenario.alternative_result.projection
    if current is None or alternative is None:
        return "\n".join(lines)
    metrics = (
        (text["consumption"], current.consumption_until_delivery, alternative.consumption_until_delivery),
        (text["inventory"], current.inventory_immediately_before_delivery, alternative.inventory_immediately_before_delivery),
        (text["safety_gap"], current.safety_stock_gap_before_delivery, alternative.safety_stock_gap_before_delivery),
        (text["stockout"], current.stockout_before_delivery, alternative.stockout_before_delivery),
        (text["inventory_after"], current.inventory_immediately_after_delivery, alternative.inventory_immediately_after_delivery),
        (text["required_delivery"], current.required_delivery_volume, alternative.required_delivery_volume),
        (text["capacity"], current.capacity_exceeded, alternative.capacity_exceeded),
    )
    lines.extend((text["metrics"][0] + " | " + text["metrics"][1] + " | " + text["metrics"][2],))
    for label, baseline, alternative_value in metrics:
        format_value = _yes_no if isinstance(baseline, bool) else _quantity_text
        current_text = format_value(baseline, lang) if isinstance(baseline, bool) else format_value(baseline)
        alternative_text = format_value(alternative_value, lang) if isinstance(alternative_value, bool) else format_value(alternative_value)
        lines.append(f"{label} | {current_text} | {alternative_text}")
    return "\n".join(lines)


def _scenario_comparison_rows(comparison, lang: str):
    text = _TEXT[lang]
    rows = []
    for metric in comparison.metrics:
        row = {text["scenario_measure"]: metric.label}
        for value in metric.values:
            if value.value is None:
                rendered = _scenario_unavailable_label(value.unavailable_reason, lang)
            elif isinstance(value.value, bool):
                rendered = _yes_no(value.value, lang)
            else:
                rendered = f"{_format_decimal(value.value)} {value.unit or ''}".strip()
                if metric.field == "safety_stock_gap_before_delivery" and value.value > 0:
                    rendered = "+" + rendered
            row[value.label] = rendered
        rows.append(row)
    return rows


def _render_scenario_comparison(comparison, lang: str) -> None:
    st.markdown(f"**{_TEXT[lang]['scenario_comparison']}**")
    for metric in comparison.metrics:
        if not metric.comparable:
            reason = (
                "Unidades operativas incompatibles; se muestran los valores sin compararlos."
                if metric.reason == "incompatible_operational_units" and lang == "es" else
                "Incompatible operational units; values are shown without ordering."
                if metric.reason == "incompatible_operational_units" else
                "Falta una proyección válida para uno o más escenarios."
                if lang == "es" else "A valid projection is missing for one or more scenarios."
            )
            st.caption(f"{metric.label}: {reason}")
    st.table(_scenario_comparison_rows(comparison, lang))


def _scenario_comparison_copy_text(comparison, lang: str) -> str:
    rows = _scenario_comparison_rows(comparison, lang)
    if not rows:
        return ""
    columns = tuple(rows[0])
    lines = [_TEXT[lang]["scenario_comparison"], " | ".join(columns)]
    lines.extend(" | ".join(str(row.get(column, _TEXT[lang]["not_available"])) for column in columns)
                 for row in rows)
    for metric in comparison.metrics:
        if not metric.comparable:
            reason = ("Unidades operativas incompatibles" if lang == "es" else "Incompatible operational units") if metric.reason == "incompatible_operational_units" else (
                "Proyección no disponible" if lang == "es" else "Projection unavailable"
            )
            lines.append(f"{metric.label}: {reason}")
    return "\n".join(lines)


def _format_decimal(value):
    return _format_number(value)


def _scenario_unavailable_label(reason: str | None, lang: str) -> str:
    if reason == "evaluation_invalid":
        return "Evaluación inválida" if lang == "es" else "Invalid evaluation"
    if reason == "evaluation_missing_inputs":
        return "Faltan datos" if lang == "es" else "Missing inputs"
    return _TEXT[lang]["not_available"]


def _position_label(item, lang: str) -> str:
    request = item.item.request
    site = getattr(request.site, "name", None) or item.result.site_id
    product = request.gas_product
    product_id = getattr(product, "gas_product_id", None) or item.result.gas_product_id
    known_products = {
        "medical-oxygen": {"en": "Medical oxygen (O₂)", "es": "Oxígeno medicinal (O₂)"},
        "co2": {"en": "CO₂", "es": "CO₂"},
        "n2": {"en": "N₂", "es": "N₂"},
    }
    product_label = known_products.get(product_id, {}).get(lang)
    if not product_label and product is not None:
        product_label = product.name
    if not product_label:
        product_label = product_id
    return " · ".join(value for value in (site, product_label) if value) or item.item.item_id


def _operational_error_categories(errors: tuple[str, ...]) -> tuple[str, ...]:
    """Expose stable error classes separately from provider-specific messages."""
    categories = []
    for error in errors:
        prefix = error.split(":", 1)[0].strip()
        if prefix.endswith("Error") and prefix not in categories:
            categories.append(prefix)
    return tuple(categories)


def _render_position_card(item, lang: str = "en") -> None:
    result = item.result
    text = _TEXT[lang]
    with st.container(border=True):
        st.markdown(f"**{_position_label(item, lang)}**")
        projection = result.projection
        if result.status == "COMPLETED" and projection is not None:
            metrics = (
                (text["inventory"], _quantity_text(projection.inventory_immediately_before_delivery)),
                (text["safety_gap"], _quantity_text(projection.safety_stock_gap_before_delivery)),
                (text["stockout"], _yes_no(projection.stockout_before_delivery, lang)),
            )
            columns = st.columns(len(metrics))
            for column, (label, value) in zip(columns, metrics):
                column.metric(label, value)
            if item.attention.facts:
                labels = []
                for fact in item.attention.facts:
                    label = _FINDINGS.get(fact.source_finding.code, {}).get(lang)
                    labels.append(label or _FINDING_LABELS.get(
                        fact.source_finding.code, "Operational finding" if lang == "en" else "Hecho operativo",
                    ))
                st.write(("Attention: " if lang == "en" else "Atención: ") + "; ".join(labels))
            else:
                st.write(text["no_attention"])
        else:
            st.markdown(f"**{text['evaluation_issue']}**")
            if result.status == "MISSING_INPUTS":
                labels = [
                    _MISSING.get(key, {}).get(lang) or _humanize_key(key)
                    for key in result.missing_inputs
                ]
                st.write(text["missing"] + ": " + ", ".join(labels))
            else:
                st.write(text["invalid"])


def _render_document_sources(response: WorkspaceResponse, lang: str = "en") -> None:
    text = _TEXT[lang]
    st.markdown(f"**{text['sources']}**")
    for item in response.evidence.items:
        for source in item.knowledge_sources:
            _render_source(source, (item.item.item_id,), response, lang)


def _render_document_inventory(response: WorkspaceResponse, lang: str) -> None:
    """List document types directly from validated per-position retrieval metadata."""
    st.markdown(_document_inventory_answer(response, lang))


def _document_inventory_answer(response: WorkspaceResponse, lang: str) -> str:
    """Compose a localized source inventory without interpreting document contents."""
    text = _TEXT[lang]
    lines = [f"**{text['relevant_documents']}**"]
    for item in response.evidence.items:
        labels = tuple(dict.fromkeys(
            _document_type_label(source.chunk.metadata.get("document_type"), lang)
            for source in item.knowledge_sources
        ))
        if labels:
            lines.extend((f"**{_position_label(item, lang)}**", ", ".join(labels)))
    global_labels = tuple(dict.fromkeys(
        _document_type_label(source.source.chunk.metadata.get("document_type"), lang)
        for source in response.evidence.global_sources
    ))
    if global_labels:
        lines.extend((f"**{text['global_sources']}**", ", ".join(global_labels)))
    return "\n\n".join(lines)


def _render_source(source, item_ids: tuple[str, ...], response: WorkspaceResponse,
                   lang: str, *, global_scope: bool = False) -> None:
    chunk = source.chunk
    text = _TEXT[lang]
    kind = chunk.metadata.get("document_type")
    kind_label = _document_type_label(kind, lang)
    if global_scope:
        identity = text["global"]
    else:
        selected = [item for item in response.evidence.items if item.item.item_id in item_ids]
        identity = ", ".join(_position_label(item, lang) for item in selected) or text["source"]
    details = [identity, kind_label]
    if chunk.page_start is not None:
        page = chunk.page_start if chunk.page_end in (None, chunk.page_start) else f"{chunk.page_start}–{chunk.page_end}"
        details.append(f"{text['page']} {page}")
    with st.container(border=True):
        st.markdown(f"**{text['source']}**")
        st.caption(" · ".join(details))
        if global_scope:
            st.caption(text["global_sources"])


def _render_scenario_card(item, lang: str = "en") -> None:
    scenario = item.scenario
    text = _TEXT[lang]
    current = scenario.baseline_result.projection
    alternative = scenario.alternative_result.projection
    with st.container(border=True):
        title = _scenario_title(item, lang)
        st.markdown(f"**{title} · {_position_label(item, lang)}**")
        change_label = _scenario_change_label(item, lang)
        if change_label:
            st.caption(change_label)
        if current is None or alternative is None:
            _render_scenario_issue(scenario.baseline_result, lang, "Current")
            _render_scenario_issue(scenario.alternative_result, lang, "Alternative")
            return
        rows = [
            {text["metrics"][0]: text["consumption"], text["metrics"][1]: _quantity_text(current.consumption_until_delivery), text["metrics"][2]: _quantity_text(alternative.consumption_until_delivery)},
            {text["metrics"][0]: text["inventory"], text["metrics"][1]: _quantity_text(current.inventory_immediately_before_delivery), text["metrics"][2]: _quantity_text(alternative.inventory_immediately_before_delivery)},
            {text["metrics"][0]: text["safety_gap"], text["metrics"][1]: _quantity_text(current.safety_stock_gap_before_delivery), text["metrics"][2]: _quantity_text(alternative.safety_stock_gap_before_delivery)},
            {text["metrics"][0]: text["stockout"], text["metrics"][1]: _yes_no(current.stockout_before_delivery, lang), text["metrics"][2]: _yes_no(alternative.stockout_before_delivery, lang)},
            {text["metrics"][0]: text["inventory_after"], text["metrics"][1]: _quantity_text(current.inventory_immediately_after_delivery), text["metrics"][2]: _quantity_text(alternative.inventory_immediately_after_delivery)},
            {text["metrics"][0]: text["required_delivery"], text["metrics"][1]: _quantity_text(current.required_delivery_volume), text["metrics"][2]: _quantity_text(alternative.required_delivery_volume)},
            {text["metrics"][0]: text["capacity"], text["metrics"][1]: _yes_no(current.capacity_exceeded, lang), text["metrics"][2]: _yes_no(alternative.capacity_exceeded, lang)},
        ]
        st.table(rows)


def _render_scenario_issue(result, lang: str, label: str) -> None:
    text = _TEXT[lang]
    st.markdown(f"**{label}**")
    if result.status == "MISSING_INPUTS":
        st.write(text["missing"] + ": " + ", ".join(
            _MISSING.get(key, {}).get(lang) or _humanize_key(key) for key in result.missing_inputs
        ))
    elif result.status == "INVALID":
        st.write(text["invalid"])


def _scenario_title(item, lang: str) -> str:
    scenario_id = item.scenario.alternative.id
    if scenario_id == "planned-delivery-time":
        return _TEXT[lang]["delivery_timing"]
    if scenario_id == "planned-delivery-quantity":
        return _TEXT[lang]["quantity_change"]
    if scenario_id == "consumption-rate":
        return _TEXT[lang]["consumption_change"]
    return item.scenario.alternative.label


def _scenario_change_label(item, lang: str) -> str:
    if item.scenario.alternative.id != "planned-delivery-time":
        return ""
    current = item.item.request.delivery_plan.planned_delivery_at
    alternative = item.scenario.alternative.alternative_request.delivery_plan.planned_delivery_at
    difference = current - alternative
    days = difference.total_seconds() / 86400
    if days == 0 or not days.is_integer():
        return ""
    count = abs(int(days))
    if lang == "es":
        unit = "día" if count == 1 else "días"
        relation = "antes" if days > 0 else "después"
    else:
        unit = "day" if count == 1 else "days"
        relation = "earlier" if days > 0 else "later"
    return f"{count} {unit} {relation}"


def _safe_grounded_answer(response: WorkspaceResponse, lang: str) -> str:
    projections = [item for item in response.evidence.items if item.result.projection is not None]
    lines = []
    if any(
        any(fact.source_finding.code == "safety_stock_breach" for fact in item.attention.facts)
        and not item.result.projection.stockout_before_delivery for item in projections
    ):
        lines.append(_TEXT[lang]["safe_breach"])
    if any(item.result.projection.stockout_before_delivery for item in projections):
        lines.append(_TEXT[lang]["safe_stockout"])
    if any(item.result.projection.capacity_exceeded for item in projections):
        lines.append(_TEXT[lang]["safe_capacity"])
    return " ".join(lines) or _TEXT[lang]["safe_fallback"]


def _primary_answer(response: WorkspaceResponse, question: str, lang: str) -> str:
    if (_is_document_list_request(question)
            and (response.documentary_sources or response.evidence.global_sources)):
        return _document_inventory_answer(response, lang)
    answer = response.explanation or ""
    replacements = {}
    for item in response.evidence.items:
        request = item.item.request
        label = _position_label(item, lang)
        product = request.gas_product
        product_id = getattr(product, "gas_product_id", None) or item.result.gas_product_id
        product_label = {
            "medical-oxygen": "Medical oxygen (O₂)" if lang == "en" else "Oxígeno medicinal (O₂)",
            "co2": "CO₂", "n2": "N₂",
        }.get(product_id, getattr(product, "name", ""))
        customer_name = _CUSTOMER_LABELS.get(item.result.customer_id)
        site_name = getattr(request.site, "name", None)
        application_name = getattr(request.application, "name", None)
        aliases = {
            item.item.item_id: label,
            item.result.customer_id: customer_name or label,
            item.result.site_id: site_name or label,
            item.result.application_id: application_name or label,
            item.result.gas_product_id: product_label or label,
            item.result.installation_id: label,
            getattr(request.site, "site_id", None): site_name or label,
            getattr(request.application, "application_id", None): application_name or label,
            getattr(request.installation, "installation_id", None): label,
            getattr(product, "name", None): product_label or label,
            application_name: application_name,
        }
        compound_aliases = (
            " / ".join(str(value) for value in (
                item.result.customer_id, site_name, getattr(product, "name", None),
            ) if value),
            " / ".join(str(value) for value in (
                item.item.item_id, item.result.site_id, item.result.application_id,
                item.result.gas_product_id, item.result.installation_id,
            ) if value),
        )
        for alias in compound_aliases:
            if alias:
                replacements[alias] = label
        replacements.update({key: value for key, value in aliases.items() if key})
    for scoped in response.documentary_sources:
        replacements[scoped.source.chunk.document_name] = _document_type_label(
            scoped.source.chunk.metadata.get("document_type"), lang,
        )
    for identifier, human_label in sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True):
        if identifier:
            answer = re.sub(re.escape(identifier), human_label, answer, flags=re.IGNORECASE)
    answer = _strip_technical_references(answer)
    answer = re.sub(r"\(\s*\)", "", answer)
    answer = re.sub(r"([,;])(?:\s*[,;])+", r"\1", answer)
    answer = re.sub(r":\s*[,;]+(?=\s*[.!?]|$)", "", answer)
    answer = re.sub(r",\s*(?=[.!?])", "", answer)
    answer = re.sub(r"\s+([.,;:!?])", r"\1", answer)
    # Add missing punctuation spacing without splitting grouped numbers such
    # as "-1,100" into the misleading "-1, 100".
    answer = re.sub(r"([,;:])(?=[^\s\d])", r"\1 ", answer)
    answer = re.sub(r"[ \t]{2,}", " ", answer)
    return answer.strip()


def _strip_technical_references(answer: str) -> str:
    """Remove citation tokens for normal prose; evidence remains in its trace."""
    return re.sub(r"\s*\[chunk_id:[^\]]+\]", "", answer)


def _yes_no(value: bool, lang: str) -> str:
    if lang == "es":
        return "Sí" if value else "No"
    return "Yes" if value else "No"


def _document_type_label(kind: str | None, lang: str) -> str:
    text = _TEXT[lang]
    return {
        "supply_contract": text["source_contract"],
        "operating_procedure": text["source_procedure"],
        "installation_specification": text["source_installation"],
        "supply_policy": text["source_policy"],
    }.get(kind, text["source"])


def _humanize_key(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _item_short_label(item) -> str:
    request = item.item.request
    site = request.site.name if request.site else item.result.site_id
    product = request.gas_product.name if request.gas_product else item.result.gas_product_id
    return " / ".join(value for value in (site, product) if value) or item.item.item_id
