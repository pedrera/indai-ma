"""Conversation-first Streamlit surface over structured workspace responses."""
from __future__ import annotations

import re

import streamlit as st

from .conversational_workspace import WorkspaceResponse
from .portfolio_query import SupplyAgentSessionContext
from .portfolio_ui import _CUSTOMER_LABELS, _FINDING_LABELS, _quantity_text
from .supply_agent import _is_document_list_request, _jsonable


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
    if re.search(r"\b(?:que|posiciones|atencion|contrato|entrega|hospital|documentacion|prevision)\b", folded):
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
            st.session_state.workspace_pending_prompt = None
            st.session_state.supply_agent_context = SupplyAgentSessionContext()
            st.session_state.supply_agent_result = None
            st.rerun()

    for message in st.session_state.workspace_messages:
        role = message["role"]
        with st.chat_message(role):
            if role == "user":
                st.markdown(message["content"])
            elif isinstance(message.get("response"), WorkspaceResponse):
                _render_workspace_response(message["response"], message.get("question", ""))
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


def _render_workspace_response(response: WorkspaceResponse, question: str = "") -> None:
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

    for position in response.positions:
        _render_position_card(position, lang)
    if response.documentary_sources:
        _render_document_sources(response, lang)
    for position in response.positions:
        if position.scenario is not None:
            _render_scenario_card(position, lang)
    if response.evidence.global_sources:
        st.markdown(f"**{text['global_sources']}**")
        for source in response.evidence.global_sources:
            _render_source(source.source, source.item_ids, response, lang, global_scope=True)

    with st.expander(text["trace"]):
        st.write(f"{text['intent']}: {response.route.intent.value}")
        st.write(f"{text['capabilities']}: " + ", ".join(response.route.capabilities))
        st.write(f"{text['selected']}: " + (", ".join(response.selected_item_ids) or "none"))
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
        if response.unsupported_questions:
            st.caption("Clarification required for: " + ", ".join(response.unsupported_questions))
        if response.evidence.retrieval_failures:
            st.caption("Retrieval failures: " + ", ".join(
                failure.item_id for failure in response.evidence.retrieval_failures
            ))


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
    answer = re.sub(r"([,;:])(?=\S)", r"\1 ", answer)
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
