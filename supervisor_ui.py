import streamlit as st
from clipboard_text import decision_dependency_label


_PLAN_CATEGORY_LABELS = {"operational": "Operativa", "contractual": "Contractual", "risk": "Riesgo"}
_PLAN_HORIZON_LABELS = {"current_period": "Periodo actual", "before_period_close": "Antes del cierre", "monitoring": "Seguimiento"}
_PLAN_STATE_LABELS = {"review_required": "Requiere revisión", "monitor": "Monitorizar", "no_action": "Sin acción", "blocked": "Bloqueado"}


def _render_decision_plan(plan):
    if plan is None or not plan.steps:
        return
    st.markdown("**PLAN DE DECISIÓN**")
    if not plan.is_complete:
        st.info("Plan incompleto: falta información para algunos pasos.")
    for index, step in enumerate(plan.steps, 1):
        category = _PLAN_CATEGORY_LABELS.get(step.category, step.category)
        horizon = _PLAN_HORIZON_LABELS.get(step.horizon, step.horizon or "")
        prefix = " · ".join(item for item in (horizon, category) if item)
        st.markdown(f"**{index}. {prefix}**")
        st.write(step.action)
        state = _PLAN_STATE_LABELS.get(step.decision_state, step.decision_state)
        st.write(f"Estado: {state}")
        if step.depends_on:
            st.caption("Relacionado con: " + ", ".join(
                decision_dependency_label(value) for value in step.depends_on))
        if step.missing_information:
            st.write("Información necesaria:")
            for item in step.missing_information:
                st.write(f"- {item}")
    for warning in plan.warnings:
        st.caption(f"Aviso del plan: {warning}")


def render_business_api_result(result):
    """Render the reduced safe inspector and public Business API contract."""
    st.subheader("Resumen ejecutivo")
    st.write(result.summary)
    recommendation = result.recommendation
    if recommendation is not None:
        st.subheader("Recomendación")
        if recommendation.is_complete:
            st.markdown(f"**{recommendation.action}**")
        else:
            st.info("Recomendación incompleta: faltan datos para completarla.")
            st.markdown(f"**{recommendation.action}**")
        for label, value in (("Implicación contractual", recommendation.contractual_implication),
                             ("Implicación operativa", recommendation.operational_implication),
                             ("Implicación de riesgo", recommendation.risk_implication)):
            if value:
                st.write(f"**{label}:** {value}")
        if recommendation.rationale:
            st.write("**Fundamento:**")
            for item in recommendation.rationale:
                st.write(f"- {item}")
        for warning in recommendation.warnings:
            st.warning(warning)
        _render_decision_plan(getattr(recommendation, "decision_plan", None))
        actions = getattr(recommendation, "actions", ())
        if actions:
            st.markdown("**ACCIONES RECOMENDADAS**")
            category_labels = {
                "operational": "OPERATIVA",
                "contractual": "CONTRACTUAL",
                "risk": "RIESGO",
            }
            for index, item in enumerate(actions, 1):
                category = category_labels.get(item.category, item.category.upper())
                st.markdown(f"**{index}. [{category}] {item.action}**")
                if item.rationale:
                    st.write(item.rationale)
                for label, value in item.supporting_metrics:
                    st.write(f"- {label}: {value}")
    if result.metrics:
        st.subheader("Métricas clave")
        st.dataframe([metric.model_dump() for metric in result.metrics], hide_index=True)
    if result.explanations:
        st.subheader("Explicación de negocio")
        for item in result.explanations:
            st.markdown(f"**{item.title}**")
            st.write(item.text)
    if result.evidence:
        st.subheader("Evidencia")
        for item in result.evidence:
            st.caption(f"{item.document} · {item.section} · pág. {item.page}")
    if result.provenance:
        st.subheader("Provenance")
        st.dataframe([item.model_dump() for item in result.provenance], hide_index=True)
    if result.warnings:
        st.subheader("Avisos")
        for warning in result.warnings:
            st.warning(warning)
    with st.expander("Diagnóstico remoto"):
        st.json({"operation_id": result.operation_id, "status": result.status,
                 "routing": result.routing.model_dump(),
                 "specialists": [item.model_dump() for item in result.specialists],
                 "diagnostics": result.diagnostics.model_dump()})


def render_supervisor_result(result):
    from business_output import build_supervisor_executive_sections
    projection = build_supervisor_executive_sections(result)
    st.subheader("Resumen ejecutivo")
    st.write(projection.summary)
    if projection.metrics:
        st.subheader("Métricas clave")
        st.dataframe([{"Métrica": metric.label, "Valor": metric.value, "Origen": metric.origin}
                      for metric in projection.metrics], hide_index=True)
    if projection.explanations:
        st.subheader("Explicación de negocio")
        for title, explanation in projection.explanations:
            st.markdown(f"**{title}**")
            st.write(explanation)
    if projection.evidence:
        st.subheader("Evidencia")
        for item in projection.evidence:
            st.caption(f"{item.document} · {item.section} · pág. {item.page}")
    if projection.provenance:
        st.subheader("Provenance")
        st.dataframe([{"Categoría": item.category, "Concepto": item.label,
                        "Valor": item.value, "Origen": item.origin}
                       for item in projection.provenance], hide_index=True)
    if projection.warnings:
        st.subheader("Avisos")
        for warning in projection.warnings:
            st.warning(warning)
    with st.expander("Detalles técnicos del Supervisor"):
        st.json(result.model_dump(mode="json"))
        st.download_button("Descargar resultado JSON", result.model_dump_json(indent=2),
                           "supervisor-result.json", "application/json", key="supervisor_json")


def supervisor_diagnostics(snapshot):
    events = snapshot.events
    routing = next((e.metadata for e in reversed(events) if e.stage == "routing"), {})
    lines = ["Supervisor Execution", "--------------------",
             f"Routing method: {routing.get('routing_method', 'pending')}",
             "Selected agents: " + ", ".join(routing.get("selected_agents", [])),
             "Skipped agents: " + ", ".join(routing.get("skipped_agents", [])),
             "Routing evidence: " + " ".join(routing.get("routing_reasons", []))]
    for event in events:
        if event.stage == "specialist_execution":
            data = event.metadata
            lines += ["", str(data.get("agent_name")), f"status: {data.get('status', event.status.value)}",
                      f"duration: {data.get('duration_seconds', event.duration_seconds or 0):.4f} s",
                      f"LLM calls: {data.get('llm_calls', 0)}", f"RAG calls: {data.get('rag_calls', 0)}",
                      f"tool calls: {data.get('tool_calls', 0)}", f"reused tool results: {data.get('reused_tool_results', 0)}"]
    result = next((e.metadata['structured_result'] for e in reversed(events) if e.stage == "supervisor_result"), {})
    for item in result.get("specialist_results", []):
        if item["agent_name"] == "CommercialAgent" and item.get("result"):
            lines.append("CommercialAgent interpretation mode: " + item["result"].get("interpretation_mode", "llm"))
    lines += ["", f"Supervisor synthesis: {result.get('synthesis_status', 'pending')}",
              f"Synthesis LLM calls: {result.get('synthesis_llm_calls', 0)}",
              f"Router LLM calls: {result.get('router_llm_calls', 0)}",
              f"Total wall time: {snapshot.elapsed_seconds:.4f} s (independent; includes nested agents)",
              f"Total LLM calls: {result.get('total_llm_calls', 0)}",
              f"Total RAG calls: {result.get('total_rag_calls', 0)}",
              f"Total tool calls: {result.get('total_tool_calls', 0)}"]
    return "\n".join(lines)


def render_supervisor_inspector(snapshot):
    st.text(supervisor_diagnostics(snapshot))
    for event in snapshot.events:
        owner = event.metadata.get("supervisor_agent", event.metadata.get("agent_name", "Supervisor"))
        with st.expander(f"{owner} · {event.stage} · {event.status.value}"):
            st.caption(f"Duración: {event.duration_seconds or 0:.4f} s")
            # Only validated public metadata is generated by Supervisor/adapters.
            from clipboard_text import _sanitize
            st.json(_sanitize(event.metadata))
