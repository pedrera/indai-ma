import json
import re
from typing import Any

from diagnostics import PerformanceEvent, PerformanceSnapshot, PerformanceStatus
from execution_metrics import build_operation_metrics


_PLAN_CATEGORY_LABELS = {"operational": "OPERATIVA", "contractual": "CONTRACTUAL", "risk": "RIESGO"}
_PLAN_HORIZON_LABELS = {"current_period": "PERIODO ACTUAL", "before_period_close": "ANTES DEL CIERRE", "monitoring": "SEGUIMIENTO"}
_PLAN_STATE_LABELS = {"review_required": "Requiere revisión", "monitor": "Monitorizar", "no_action": "Sin acción", "blocked": "Bloqueado"}
_DECISION_DEPENDENCY_LABELS = {"operational-short": "Cobertura del SHORT operativo"}
_READINESS_LABELS = {"READY": "Completa", "PARTIALLY_READY": "Parcial", "BLOCKED": "Insuficiente"}
_MISSING_INFORMATION_LABELS = {
    "coverage_volume_gwh": "Volumen de cobertura",
    "coverage_price_eur_mwh": "Precio de cobertura",
    "revised_remaining_forecast_consumption_gwh": "Previsión restante revisada",
    "spot_price_eur_mwh": "Precio spot",
    "forecast_demand_gwh": "Previsión de demanda",
    "expected_demand_gwh": "Demanda esperada",
    "contracted_supply_gwh": "Suministro contratado",
    "cumulative_consumption_gwh": "Consumo acumulado",
    "remaining_forecast_consumption_gwh": "Previsión de consumo restante",
    "take_or_pay_minimum_gwh": "Mínimo contractual take-or-pay",
}
_EVALUATION_STATUS_LABELS = {"EVALUATED": "Calculada", "PARTIALLY_EVALUATED": "Parcial", "NOT_EVALUATED": "Pendiente"}
_EVALUATION_INPUT_LABELS = {"coverage_volume_gwh": ("Volumen de cobertura", "GWh"), "coverage_price_eur_mwh": ("Precio de cobertura", "€/MWh"), "revised_remaining_forecast_consumption_gwh": ("Previsión restante revisada", "GWh")}
_EVALUATION_OUTCOME_LABELS = {"covered_volume_gwh": ("Volumen cubierto", "GWh"), "remaining_short_gwh": ("SHORT restante", "GWh"), "remaining_long_gwh": ("LONG restante", "GWh"), "projected_consumption_gwh": ("Consumo proyectado", "GWh"), "projected_top_deficit_gwh": ("Déficit TOP proyectado", "GWh"), "coverage_cost_eur": ("Coste de cobertura", "€"), "spot_exposure_eur": ("Exposición spot", "€")}


def decision_dependency_label(value: str) -> str:
    """Map a technical decision dependency ID to presentation text only."""
    return _DECISION_DEPENDENCY_LABELS.get(value, value)


def decision_readiness_label(value: str) -> str:
    return _READINESS_LABELS.get(value, value)


def decision_missing_information_label(value: str) -> str:
    return _MISSING_INFORMATION_LABELS.get(value, value)


def _format_evaluation_number(value, unit: str) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    rendered = f"{value:,}" if isinstance(value, (int, float)) and unit == "€" else (f"{value:g}" if isinstance(value, (int, float)) else str(value))
    return f"{rendered} {unit}".strip()


def format_alternative_evaluation(evaluation: Any) -> list[str]:
    """Format public evaluation data without calculating or interpreting it."""
    if evaluation is None:
        return []
    lines = [f"Evaluación: {_EVALUATION_STATUS_LABELS.get(evaluation.status, evaluation.status)}"]
    inputs = getattr(evaluation, "inputs", None)
    input_lines = []
    if inputs is not None:
        for key, (label, unit) in _EVALUATION_INPUT_LABELS.items():
            value = getattr(inputs, key, None)
            if value is not None:
                input_lines.append(f"- {label}: {_format_evaluation_number(value, unit)}")
    if input_lines:
        lines.extend(["Supuestos", *input_lines])
    missing_lines = [f"- {decision_missing_information_label(item)}" for item in getattr(evaluation, "missing_inputs", ())]
    if missing_lines:
        lines.extend(["Falta", *missing_lines])
    outcomes = []
    for outcome in getattr(evaluation, "outcomes", ()):
        label, unit = _EVALUATION_OUTCOME_LABELS.get(outcome.metric, (outcome.metric, outcome.unit))
        outcomes.append(f"- {label}: {_format_evaluation_number(outcome.value, unit)}")
    if outcomes:
        lines.extend(["Consecuencias", *outcomes])
    return lines


SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)(api[_-]?key|authorization|password|secret|access[_-]?token)"
        r"\s*[:=]\s*\S+"
    ),
)


def build_response_clipboard_text(response: Any) -> str:
    """Return the same human-readable response content shown by the UI."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    scenarios = getattr(response, "scenarios", None)
    summary = getattr(response, "summary", None)
    risks = getattr(response, "key_risks", None)
    recommendation = getattr(response, "recommendation", None)
    if scenarios is not None and summary is not None:
        lines = ["Escenarios", "----------"]
        for scenario in scenarios:
            lines.extend(
                [
                    str(getattr(scenario, "name", "Escenario")),
                    f"  Demanda: {_number(getattr(scenario, 'demand_gwh', None))} GWh",
                    f"  Posición: {_number(getattr(scenario, 'supply_position_gwh', None))} GWh",
                    f"  Short: {_number(getattr(scenario, 'short_position_gwh', None))} GWh",
                    f"  Precio spot: {_number(getattr(scenario, 'spot_price_eur_mwh', None))} EUR/MWh",
                    f"  Exposición spot: {_number(getattr(scenario, 'spot_exposure_eur', None))} EUR",
                    f"  Margen estimado: {_number(getattr(scenario, 'estimated_margin_eur', None))} EUR",
                ]
            )
        lines.extend(["", "Resumen", "-------", str(summary)])
        lines.extend(["", "Riesgos clave", "--------------"])
        lines.extend(f"- {risk}" for risk in risks or [])
        lines.extend(["", "Recomendación", "-------------", str(recommendation or "")])
        return "\n".join(lines).strip()
    return str(response)


def build_tool_executions_clipboard_text(
    tool_executions: Any,
) -> str:
    """Build a readable, sanitized list of already executed tools."""
    lines: list[str] = []
    for index, execution in enumerate(tool_executions or (), 1):
        if not isinstance(execution, dict):
            continue
        name = execution.get("name") or execution.get("tool_name") or "tool"
        lines.extend(
            [
                f"{index}. {_redact_text(str(name))}",
                f"   Status: {_redact_text(str(execution.get('status', 'completed')))}",
            ]
        )
        if execution.get("arguments") is not None:
            lines.append(f"   Arguments: {_readable(execution['arguments'])}")
        if execution.get("result") is not None:
            lines.append(f"   Result: {_readable(execution['result'])}")
        duration = execution.get("elapsed_seconds")
        if duration is not None:
            lines.append(f"   Duration: {_duration(float(duration))}")
        reason = execution.get("skip_reason")
        if reason:
            lines.append(f"   Reason: {_redact_text(str(reason))}")
    return "\n".join(lines) if lines else "No tools were executed."


def build_all_clipboard_text(
    response: Any,
    tool_executions: Any,
    snapshot: PerformanceSnapshot | None,
) -> str:
    """Combine current visible result, tools and diagnostics without side effects."""
    sections = [
        "indAI MA — Complete operation",
        "=============================",
        "",
        "Response",
        "--------",
        build_response_clipboard_text(response),
        "",
        "Tools used",
        "----------",
        build_tool_executions_clipboard_text(tool_executions),
        "",
        "Diagnostics",
        "-----------",
        build_diagnostics_clipboard_text(snapshot),
    ]
    return _redact_text("\n".join(sections).strip())


def build_business_copy_payload(result: Any, *, operation_id: str | None = None,
                                status: str | None = None, duration_seconds: float | None = None) -> str:
    """Build complete readable text from the public frontend/API result only."""
    lines = ["indAI MA — Business analysis", "============================="]
    _append_optional(lines, "Status", status or getattr(result, "status", None))
    _append_optional(lines, "Duration", f"{duration_seconds:.2f} s" if duration_seconds is not None else None)
    _append_optional(lines, "Operation ID", operation_id or getattr(result, "operation_id", None))
    routing = getattr(result, "routing", None)
    if routing:
        lines.extend(["", "Routing", "-------", f"Method: {routing.method}"])
        if routing.selected_agents:
            lines.append("Selected agents: " + ", ".join(routing.selected_agents))
        if routing.skipped_agents:
            lines.append("Skipped agents: " + ", ".join(routing.skipped_agents))
    _copy_section(lines, "Executive summary", [getattr(result, "summary", "")])
    recommendation = getattr(result, "recommendation", None)
    if recommendation is not None:
        recommendation_lines = [
            f"Action: {recommendation.action}",
            f"Status: {'complete' if recommendation.is_complete else 'incomplete'}",
        ]
        for label, value in (("Contractual implication", recommendation.contractual_implication),
                             ("Operational implication", recommendation.operational_implication),
                             ("Risk implication", recommendation.risk_implication)):
            if value:
                recommendation_lines.append(f"{label}: {value}")
        recommendation_lines.extend(f"- {item}" for item in recommendation.rationale)
        recommendation_lines.extend(f"Warning: {item}" for item in recommendation.warnings)
        _copy_section(lines, "RECOMENDACIÓN", recommendation_lines)
        plan = getattr(recommendation, "decision_plan", None)
        if plan is not None and plan.steps:
            plan_lines = []
            if not plan.is_complete:
                plan_lines.append("Plan incompleto: falta información para algunos pasos.")
            if plan.readiness is not None:
                plan_lines.append(f"Información del plan: {decision_readiness_label(plan.readiness)}")
            for index, step in enumerate(plan.steps, 1):
                category = _PLAN_CATEGORY_LABELS.get(step.category, step.category.upper())
                horizon = _PLAN_HORIZON_LABELS.get(step.horizon, step.horizon or "")
                prefix = " · ".join(item for item in (horizon, category) if item)
                plan_lines.extend([f"{index}. [{prefix}] {step.action}",
                                   f"   Estado: {_PLAN_STATE_LABELS.get(step.decision_state, step.decision_state)}"])
                if step.readiness is not None:
                    plan_lines.append(f"   Información: {decision_readiness_label(step.readiness)}")
                if step.depends_on:
                    plan_lines.append("   Relacionado con: " + ", ".join(
                        decision_dependency_label(value) for value in step.depends_on))
                if step.missing_information:
                    plan_lines.append("   Falta:")
                    plan_lines.extend(f"   - {decision_missing_information_label(item)}" for item in step.missing_information)
                alternatives = getattr(step, "alternatives", ())
                if alternatives:
                    plan_lines.append("   Opciones a considerar:")
                    for alternative in alternatives:
                        plan_lines.extend([
                            f"   - {alternative.label}",
                            f"     {alternative.description}",
                        ])
                        plan_lines.extend(f"     {line}" for line in format_alternative_evaluation(
                            getattr(alternative, "evaluation", None)))
            plan_lines.extend(f"Aviso del plan: {warning}" for warning in plan.warnings)
            _copy_section(lines, "PLAN DE DECISIÓN", plan_lines)
        actions = getattr(recommendation, "actions", ())
        if actions:
            category_labels = {
                "operational": "OPERATIVA",
                "contractual": "CONTRACTUAL",
                "risk": "RIESGO",
            }
            action_lines = []
            for index, item in enumerate(actions, 1):
                category = category_labels.get(item.category, item.category.upper())
                action_lines.append(f"{index}. [{category}] {item.action}")
                if item.rationale:
                    action_lines.append(f"   {item.rationale}")
                action_lines.extend(f"   - {label}: {value}"
                                    for label, value in item.supporting_metrics)
            _copy_section(lines, "ACCIONES RECOMENDADAS", action_lines)
    metrics = [f"- {item.label}: {item.value} ({item.origin})" for item in getattr(result, "metrics", ())]
    _copy_section(lines, "Key metrics", metrics)
    explanations = [f"- {item.title}: {item.text}" for item in getattr(result, "explanations", ())]
    _copy_section(lines, "Business explanations", explanations)
    evidence = [f"- {item.document} · {item.section} · pág. {item.page}" for item in getattr(result, "evidence", ())]
    _copy_section(lines, "Evidence", evidence)
    provenance = [f"- {item.category}: {item.label} = {item.value} ({item.origin})" for item in getattr(result, "provenance", ())]
    _copy_section(lines, "Provenance", provenance)
    warnings = [f"- {item}" for item in getattr(result, "warnings", ())]
    _copy_section(lines, "Warnings", warnings)
    specialists = [f"- {item.agent_name}: {item.status} (LLM {item.llm_calls}, RAG {item.rag_calls}, tools {item.tool_calls})"
                   for item in getattr(result, "specialists", ())]
    _copy_section(lines, "Specialists", specialists)
    diagnostics = getattr(result, "diagnostics", None)
    if diagnostics:
        lines.extend(["", "Remote diagnostics", "------------------",
                      f"LLM calls: {diagnostics.llm_calls}", f"RAG calls: {diagnostics.rag_calls}",
                      f"Tool calls: {diagnostics.tool_calls}"])
    return _redact_text("\n".join(lines).strip())


def _copy_section(lines: list[str], title: str, items: list[str]) -> None:
    items = [str(item) for item in items if str(item).strip()]
    if items:
        lines.extend(["", title, "-" * len(title), *items])


def build_diagnostics_clipboard_text(
    snapshot: PerformanceSnapshot | None,
) -> str:
    if snapshot is None:
        return "indAI MA Diagnostics\n====================\n\nNo operation data available."
    events = tuple(snapshot.events)
    lines = [
        "indAI MA Diagnostics",
        "====================",
        "",
        "Operation",
        "---------",
        f"ID: {snapshot.operation_id}",
        f"Mode: {_mode_label(snapshot.mode)}",
        f"Status: {snapshot.status}",
        f"Total duration: {_duration(snapshot.elapsed_seconds)}",
        "",
        "LLM",
        "---",
        f"Provider: {snapshot.provider}",
        f"Model: {snapshot.model}",
    ]
    _append_optional(lines, "Thinking", _latest(events, "thinking_enabled"), _enabled)
    _append_optional(lines, "Max tokens", _latest(events, "max_tokens"))
    _append_optional(lines, "Max output tokens", _latest(events, "max_output_tokens"))
    _append_optional(lines, "Timeout", _latest(events, "timeout_seconds"), lambda x: f"{x} s")
    metrics = build_operation_metrics(snapshot)
    if snapshot.mode == "conversational_workspace":
        _append_workspace_execution_copy(lines, events)
    if snapshot.mode == "supervisor":
        from supervisor_ui import supervisor_diagnostics
        lines.extend(["", supervisor_diagnostics(snapshot)])
    if snapshot.mode == "risk_agent":
        risk_event = _latest_event(events, "risk_result")
        data = risk_event.metadata.get("structured_result", {}) if risk_event else {}
        base = data.get("base_scenario")
        stress = data.get("stress_scenarios", [])
        lines.extend(["", "Risk Execution", "--------------", "Agent: RiskAgent",
            f"Base scenarios: {1 if base else 0}", f"Stress scenarios: {len(stress)}",
            f"Scenario count: {len(stress) + (1 if base else 0)}",
            f"LLM calls: {metrics.llm_call_count}",
            f"Tool calls: {sum(bool(e.metadata.get('tool_call_count')) for e in events if e.stage == 'tool_execution')}",
            f"RAG calls: {sum(e.stage == 'vector_search' for e in events)}",
            f"Result status: {data.get('status', 'running')}"])
        calculation_time = _latest(events, "calculation_wall_seconds")
        lines.append("Deterministic calculation wall time: " + (
            _duration(float(calculation_time)) if calculation_time is not None else "unavailable"))
        for scenario in ([base] if base else []) + stress:
            lines.extend(["", str(scenario["name"]),
                f"  demand: {scenario['demand_gwh']:g} GWh",
                f"  supply: {scenario['supply_gwh']:g} GWh",
                f"  position: {scenario['position_gwh']:g} GWh ({scenario['interpretation']})",
                f"  SHORT: {scenario['short_position_gwh']:g} GWh",
                "  spot exposure: " + (f"{scenario['spot_exposure_eur']:,.2f} EUR"
                                          if scenario['spot_exposure_eur'] is not None else "unavailable")])
        for delta in data.get("deltas", []):
            lines.extend(["", f"Delta — {delta['scenario_name']}",
                f"  demand: {delta['demand_change_gwh']:+g} GWh",
                f"  position: {delta['position_change_gwh']:+g} GWh",
                f"  SHORT: {delta['short_position_change_gwh']:+g} GWh",
                "  exposure: " + (f"{delta['exposure_change_eur']:+,.2f} EUR"
                                      if delta['exposure_change_eur'] is not None else "unavailable")])
        lines.extend(f"Warning: {warning}" for warning in data.get("warnings", []))
    lines.append(f"LLM calls: {metrics.llm_call_count}")
    lines.append(
        "LLM request wall time total: "
        + _duration(metrics.llm_request_wall_time_total)
    )
    lines.append(
        "Server inference time total: "
        + _optional_duration(metrics.llm_inference_time_total)
    )
    lines.append(
        "LLM overhead time total: "
        + _optional_duration(metrics.llm_overhead_time_total)
    )
    lines.append(
        f"Tool execution time total: {_duration(metrics.tool_execution_time_total)}"
    )
    lines.append(f"Retrieval time total: {_duration(metrics.retrieval_time_total)}")
    lines.append(f"Parsing time total: {_duration(metrics.parsing_time_total)}")
    for call in metrics.llm_calls:
        lines.extend(
            [
                "",
                f"LLM Call #{call.call_number}",
                "-" * (10 + len(str(call.call_number))),
                f"Purpose: {call.purpose}",
                f"Provider: {call.provider}",
                f"Model: {call.model}",
                f"Messages: {_available(call.message_count)}",
                f"Prompt characters: {_available(call.prompt_character_count)}",
                "Tool schema characters: "
                + _available(call.tool_schema_character_count),
                f"Response characters: {_available(call.response_character_count)}",
                f"Request wall time: {_duration(call.request_wall_time)}",
                "Request setup / wait for stream: "
                + _optional_duration(call.request_setup_time),
                "Response stream wall time: "
                + _optional_duration(call.response_stream_time),
                "Server inference time: "
                + _optional_duration(call.inference_time),
                "Overhead time: " + _optional_duration(call.overhead_time),
                f"Input tokens: {_available(call.input_tokens)}",
                f"Output tokens: {_available(call.output_tokens)}",
                f"Total tokens: {_available(call.total_tokens)}",
                f"Status: {call.status}",
            ]
        )

    response_validation = _latest_event(events, "final_response_validation")
    if response_validation:
        validation_status = response_validation.metadata.get(
            "validation_status", "unavailable"
        )
        fallback_used = bool(
            response_validation.metadata.get("deterministic_fallback", False)
        )
        reasons = response_validation.metadata.get("validation_reasons", [])
        lines.extend(
            [
                "",
                "Final Response Validation",
                "-------------------------",
                f"Status: {validation_status}",
                f"Deterministic fallback: {'yes' if fallback_used else 'no'}",
            ]
        )
        if reasons and snapshot.mode != "conversational_workspace":
            lines.append("Reasons:")
            lines.extend(f"- {_redact_text(str(reason))}" for reason in reasons)

    if snapshot.mode in {
        "procurement_agent",
        "procurement_planner",
        "procurement_deterministic",
    }:
        decisions = [event for event in events if event.stage == "agent_decision"]
        completed_tools = [
            event for event in events
            if event.stage == "tool_execution" and event.metadata.get("tool_call_count")
        ]
        repeated = len([
            event for event in events
            if event.stage == "agent_observation"
            and event.metadata.get("observation_type") == "duplicate_tool_call"
        ])
        final = _latest_event(events, "agent_final") or _latest_event(
            events, "deterministic_final"
        )
        mode_label = {
            "procurement_agent": "ReAct Agent",
            "procurement_planner": "Planner Agent",
            "procurement_deterministic": "Deterministic",
        }[snapshot.mode]
        agent_name = {
            "procurement_agent": "ProcurementAgent",
            "procurement_planner": "ProcurementAgentPlanner",
            "procurement_deterministic": "—",
        }[snapshot.mode]
        plan_event = _latest_event(events, "agent_plan_created")
        planned_actions = (
            plan_event.metadata.get("plan_actions", []) if plan_event else []
        )
        action_names = (
            [action.get("tool_name", "tool") for action in planned_actions]
            if planned_actions
            else [event.metadata.get("tool_name", "tool") for event in completed_tools]
        )
        lines.extend(
            [
                "",
                "Execution Summary",
                "-----------------",
                f"Mode: {mode_label}",
                f"Agent: {agent_name}",
                f"Iterations: {len(decisions) or metrics.llm_call_count}",
                f"Plan actions: {len(planned_actions)}",
                f"LLM calls: {metrics.llm_call_count}",
                f"Tool calls: {len(completed_tools)}",
                f"Unique tools used: {len({event.metadata.get('tool_name') for event in completed_tools})}",
                f"Repeated tool calls: {repeated}",
                "Termination reason: "
                + str(final.metadata.get("termination_reason", "unavailable") if final else "unavailable"),
                "",
                "Actions:",
            ]
        )
        if snapshot.mode == "procurement_deterministic":
            lines.insert(len(lines) - 2, "Tool selection: application-controlled")
        if decisions:
            action_names = []
            for decision in decisions:
                action = decision.metadata.get("action")
                action_names.append(
                    decision.metadata.get("tool_name")
                    if action == "call_tool"
                    else ("final_answer" if action == "finish" else action)
                )
        if not action_names or action_names[-1] != "final_answer":
            action_names.append("final_answer")
        for index, label in enumerate(action_names, 1):
            lines.append(f"{index}. {label}")

    parsing = _latest_event(events, "input_parsing") if snapshot.mode != "conversational_workspace" else None
    if parsing:
        parsed = parsing.metadata.get("parsing_result", {})
        if isinstance(parsed, dict):
            lines.extend(["", "Input / Parsing", "---------------"])
            fields = (
                ("Detected gas type", "detected_gas_types"),
                ("Parsed demand", "base_demand_candidates"),
                ("Parsed supply", "contracted_supply_candidates"),
                ("Spot price", "spot_price_candidates"),
                ("Supply cost", "supply_cost_candidates"),
                ("Sales price", "sales_price_candidates"),
                ("Detected scenarios", "scenario_variations"),
                ("Ambiguities", "ambiguities"),
                ("Missing/skipped fields", "missing_fields"),
            )
            for label, key in fields:
                _append_optional(lines, label, parsed.get(key), _readable)

    rag_events = [event for event in events if event.stage in {
        "query_embedding", "vector_search", "retrieved_context"
    }]
    if rag_events:
        lines.extend(["", "RAG", "---", "RAG enabled: yes"])
        _append_optional(lines, "Documents indexed", _latest(events, "persisted_document_count"))
        _append_optional(lines, "Chunks", _latest(events, "persisted_chunk_count"))
        _append_optional(lines, "Top-K", _latest(events, "top_k"))
        embedding = _latest_event(events, "query_embedding")
        search = _latest_event(events, "vector_search")
        if embedding:
            lines.append(f"Query embedding duration: {_duration(_event_duration(embedding))}")
        if search:
            lines.append(f"Retrieval duration: {_duration(_event_duration(search))}")
        context = _latest_event(events, "retrieved_context")
        sources = context.metadata.get("sources", []) if context else []
        if isinstance(sources, list) and sources:
            lines.extend(["", "Retrieved sources:"])
            for index, source in enumerate(sources, 1):
                if not isinstance(source, dict):
                    continue
                document = source.get("document_name") or source.get("document") or "Document"
                section = source.get("section")
                label = f"{document} · {section}" if section else str(document)
                lines.append(f"{index}. {_redact_text(label)}")
                if source.get("score") is not None:
                    lines.append(f"   Score: {_number(source['score'])}")

    tool_events = [event for event in events if event.stage == "tool_execution"]
    skipped_tool_events = [
        event
        for event in events
        if event.stage == "agent_observation"
        and event.metadata.get("observation_type") == "tool_skipped"
    ]
    if tool_events or skipped_tool_events:
        lines.extend(["", "Tools", "-----"])
        for event in tool_events:
            name = event.metadata.get("tool_name", "tool")
            lines.append(str(name))
            lines.append(f"  status: {event.status.value}")
            if snapshot.mode == "conversational_workspace":
                _append_workspace_tool_copy(lines, event)
            else:
                if event.metadata.get("tool_arguments") is not None:
                    lines.append(f"  arguments: {_readable(event.metadata['tool_arguments'])}")
                if event.metadata.get("tool_result") is not None:
                    lines.append(f"  result: {_readable(event.metadata['tool_result'])}")
            missing = event.metadata.get("missing_inputs")
            if missing:
                lines.append(f"  skipped inputs: {_readable(missing)}")
            reason = event.metadata.get("skip_reason")
            if reason and snapshot.mode != "conversational_workspace":
                lines.append(f"  reason: {_redact_text(str(reason))}")
            lines.append(f"  duration: {_duration(_event_duration(event))}")
        for event in skipped_tool_events:
            lines.append(str(event.metadata.get("tool_name", "tool")))
            lines.append("  status: skipped")
            if snapshot.mode != "conversational_workspace":
                message = event.metadata.get("message") or "No reason recorded."
                lines.append(f"  reason: {_redact_text(str(message))}")
            lines.append(f"  duration: {_duration(_event_duration(event))}")

    lines.extend(["", "Pipeline", "--------"])
    pipeline_events = [
        event for event in events
        if not (
            metrics.llm_calls
            and event.stage in {"http_request", "model_inference"}
        )
    ]
    pipeline_labels = {
        "provider_start": "provider_preparation",
        "llm_call": "llm_request_wall_clock",
    }
    for index, event in enumerate(pipeline_events, 1):
        label = event.metadata.get("tool_name") if event.stage == "tool_execution" else pipeline_labels.get(event.stage, event.stage)
        lines.append(f"{index}. {label} ........ {_duration(_event_duration(event))} [{event.status.value}]")

    problems = [
        event for event in events
        if (event.status == PerformanceStatus.FAILED or
            (event.status == PerformanceStatus.SKIPPED and event.metadata.get("skip_kind") != "expected_optional"))
        or any(key in event.metadata for key in ("error", "warning", "fallback")) or event.metadata.get("warnings")
    ]
    if snapshot.status not in {"running", "completed"} or problems:
        lines.extend(["", "Errors / warnings", "-----------------"])
        if snapshot.status not in {"running", "completed"}:
            lines.append(f"Operation status: {snapshot.status}")
        for event in problems:
            if snapshot.mode == "conversational_workspace":
                error_type = event.metadata.get("error_type")
                lines.append(f"- {event.stage}: {error_type or event.status.value}")
                continue
            detail = event.metadata.get("error") or event.metadata.get("warning") or event.metadata.get("warnings") or event.metadata.get("fallback")
            reason = event.metadata.get("skip_reason")
            text = detail or reason or event.status.value
            lines.append(f"- {event.stage}: {_redact_text(str(text))}")
    return _redact_text("\n".join(lines))


_WORKSPACE_EVENT_FIELDS = {
    "workspace_intent_routing": ("intent", "capabilities"),
    "workspace_reference_resolution": (
        "previous_selected_item_ids", "selected_item_ids", "previous_focused_item_id",
        "focused_item_id", "previous_intent", "resolved_intent", "document_scope_item_ids",
        "scenario_target_id",
    ),
    "deterministic_comparison": ("comparison_type", "comparable", "selected_item_ids"),
    "scenario_clarification": ("target_item_id", "supported_day_change"),
    "workspace_structured_evidence": (
        "item_id", "domain_status", "finding_codes", "has_projection", "missing_input_count",
    ),
    "portfolio_query": ("resolved_item_ids", "matched_by"),
    "session_reference_resolution": ("resolved_item_ids", "matched_by"),
    "agent_start": ("agent_name", "item_id", "selected_item_ids"),
    "portfolio_knowledge_retrieval": ("item_id", "knowledge_status", "retrieved_chunk_ids"),
    "citation_validation": ("valid", "selected_item_ids", "source_count"),
    "scenario_execution": ("item_id", "alternative_id", "change_type"),
    "workspace_response_projection": (
        "selected_item_ids", "status", "semantic_guard_applied", "semantic_guard_reason",
        "semantic_guard_rule", "semantic_guard_pattern_id", "semantic_guard_matched_phrase",
    ),
    "provider_start": (
        "provider", "model", "timeout_seconds", "max_tokens", "max_output_tokens",
        "thinking_enabled",
    ),
}
_WORKSPACE_SAFE_TOOL_FIELDS = {
    "item_id", "change_type", "value", "alternative_id", "status", "source_ids",
    "missing_inputs", "metric", "unit", "capacity_exceeded", "stockout_before_delivery",
}
_WORKSPACE_PRIVATE_FIELD_PARTS = (
    "prompt", "answer", "response", "content", "reasoning", "chain_of_thought",
    "decision_summary", "query", "raw",
)


def _append_workspace_execution_copy(lines: list[str], events: tuple[PerformanceEvent, ...]) -> None:
    """Append allowlisted Workspace execution facts, never arbitrary event payloads."""
    lines.extend(["", "Workspace execution", "--------------------"])
    for event in events:
        lines.append(
            f"{event.stage}: {event.status.value} · {_duration(_event_duration(event))}"
        )
        fields = _WORKSPACE_EVENT_FIELDS.get(event.stage, ())
        safe = {key: event.metadata[key] for key in fields if key in event.metadata}
        if event.stage == "workspace_response_projection":
            safe.setdefault("semantic_guard_applied", bool(event.metadata.get("semantic_guard_applied", False)))
            if not safe["semantic_guard_applied"]:
                for key in (
                    "semantic_guard_reason", "semantic_guard_rule", "semantic_guard_pattern_id",
                    "semantic_guard_matched_phrase",
                ):
                    safe.pop(key, None)
            else:
                phrase = safe.get("semantic_guard_matched_phrase")
                if phrase is not None:
                    safe["semantic_guard_matched_phrase"] = str(phrase)[:120]
        if event.stage == "tool_execution":
            args = _workspace_safe_mapping(event.metadata.get("tool_arguments"))
            result = _workspace_safe_mapping(event.metadata.get("tool_result"))
            safe = {"tool_name": event.metadata.get("tool_name", "tool")}
            if args:
                safe["parameters"] = args
            if result:
                safe["result"] = result
        if safe:
            lines.append("  " + _readable(safe))


def _append_workspace_tool_copy(lines: list[str], event: PerformanceEvent) -> None:
    # The detailed allowlisted tool summary is emitted with its pipeline event.
    # This section repeats only the already visible tool name/status/timing.
    name = event.metadata.get("tool_name", "tool")
    lines.append(f"  name: {_redact_text(str(name))}")


def _workspace_safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe = {}
    for key, item in value.items():
        normalized = str(key).casefold()
        if any(part in normalized for part in _WORKSPACE_PRIVATE_FIELD_PARTS):
            continue
        if key not in _WORKSPACE_SAFE_TOOL_FIELDS:
            continue
        if isinstance(item, dict):
            nested = _workspace_safe_mapping(item)
            if nested:
                safe[key] = nested
        elif isinstance(item, (list, tuple)):
            safe[key] = [
                _workspace_safe_mapping(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        elif isinstance(item, (str, int, float, bool)) or item is None:
            safe[key] = item
    return safe


def _latest(events: tuple[PerformanceEvent, ...], key: str) -> Any:
    for event in reversed(events):
        if key in event.metadata:
            return event.metadata[key]
    return None


def _latest_event(events: tuple[PerformanceEvent, ...], stage: str) -> PerformanceEvent | None:
    return next((event for event in reversed(events) if event.stage == stage), None)


def _sum_metric(events: tuple[PerformanceEvent, ...], key: str) -> float:
    return sum(float(event.metadata.get(key, 0) or 0) for event in events)


def _event_duration(event: PerformanceEvent) -> float:
    if event.stage == "tool_execution" and "tool_seconds" in event.metadata:
        return float(event.metadata.get("tool_seconds") or 0)
    return float(event.duration_seconds or 0)


def _append_optional(lines: list[str], label: str, value: Any, formatter=str) -> None:
    if value is not None and value != [] and value != "":
        lines.append(f"{label}: {formatter(value)}")


def _duration(seconds: float) -> str:
    if seconds < 0.001:
        return f"{seconds * 1000:.3f} ms"
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


def _optional_duration(seconds: float | None) -> str:
    return _duration(seconds) if seconds is not None else "unavailable"


def _available(value: Any) -> str:
    return str(value) if value is not None else "unavailable"


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.6g}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _readable(value: Any) -> str:
    safe = _sanitize(value)
    if isinstance(safe, (dict, list, tuple)):
        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    return str(safe)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in SENSITIVE_KEY_PARTS) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return _redact_text(str(value)) if isinstance(value, str) else value


def _redact_text(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _enabled(value: Any) -> str:
    return "enabled" if bool(value) else "disabled"


def _mode_label(mode: str) -> str:
    return {
        "chat": "Chat",
        "rag_chat": "RAG Chat",
        "gas_analysis": "Gas B2B Analysis",
        "procurement_agent": "ProcurementAgent",
        "commercial_agent": "CommercialAgent",
        "risk_agent": "RiskAgent",
        "procurement_planner": "Planner Agent",
        "procurement_deterministic": "Deterministic",
        "rag_index": "RAG Indexing",
        "conversational_workspace": "Conversational Workspace",
    }.get(mode, mode)
