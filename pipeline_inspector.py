from html import escape
from textwrap import dedent
from time import perf_counter

import streamlit as st
from clipboard_text import build_diagnostics_clipboard_text
from clipboard_ui import render_clipboard_button
from execution_metrics import build_operation_metrics

from diagnostics import (
    COMMERCIAL_AGENT_PIPELINE_STAGES,
    RISK_AGENT_PIPELINE_STAGES,
    GAS_PIPELINE_STAGES,
    GAS_DOCUMENTARY_PIPELINE_STAGES,
    GAS_POSITION_PIPELINE_STAGES,
    GAS_RAG_PIPELINE_STAGES,
    PIPELINE_STAGES,
    RAG_CHAT_PIPELINE_STAGES,
    RAG_INDEX_PIPELINE_STAGES,
    PROCUREMENT_AGENT_PIPELINE_STAGES,
    PROCUREMENT_PLANNER_PIPELINE_STAGES,
    PROCUREMENT_DETERMINISTIC_PIPELINE_STAGES,
    SUPPLY_AGENT_PIPELINE_STAGES,
    PerformanceEvent,
    PerformanceSnapshot,
    PerformanceStatus,
)


STAGE_PRESENTATION = {
    "workspace_intent_routing": ("⌖", "Workspace Intent / Routing"),
    "workspace_reference_resolution": ("↪", "Workspace Reference Resolution"),
    "deterministic_comparison": ("⇄", "Deterministic Factual Comparison"),
    "scenario_clarification": ("?", "Scenario Clarification"),
    "workspace_structured_evidence": ("▣", "Structured Operational Evidence"),
    "workspace_response_projection": ("✓", "Workspace Response Projection"),
    "portfolio_query": ("⌕", "Portfolio Query / Selection"),
    "session_reference_resolution": ("↪", "Session Reference Resolution"),
    "portfolio_knowledge_retrieval": ("📚", "Scoped Portfolio Retrieval"),
    "scenario_execution": ("Δ", "Explicit Scenario Execution"),
    "citation_validation": ("✓", "Citation / Scope Validation"),
    "document_parsing": ("📄", "Document Parsing"),
    "chunking": ("✂️", "Chunking"),
    "embedding": ("🧬", "Embedding"),
    "index_persistence": ("🗂️", "Index Persistence"),
    "query_embedding": ("🧬", "Query Embedding"),
    "vector_search": ("🔍", "Vector Search"),
    "retrieved_context": ("📚", "Retrieved Context"),
    "input_parsing": ("📝", "Input Parsing"),
    "contractual_calculation": ("📐", "Contractual Calculations"),
    "scenario_generation": ("📊", "Scenario Generation"),
    "prompt_build": ("✍️", "Prompt Build"),
    "provider_start": ("🔌", "Provider Preparation"),
    "http_request": ("🌐", "Request Setup / Stream Open"),
    "model_inference": ("🧠", "Response Streaming"),
    "parse_validation": ("🔎", "Parse / Validation"),
    "tool_execution": ("🛠️", "Tool Execution"),
    "final_response": ("✅", "Final Response"),
    "agent_start": ("A", "ProcurementAgent"),
    "llm_call": ("L", "LLM Call"),
    "agent_decision": ("D", "Agent Decision"),
    "agent_observation": ("O", "Observation"),
    "agent_final": ("F", "Agent Final"),
    "llm_interpretation": ("L", "Commercial Interpretation"),
    "commercial_interpretation": ("L", "Commercial Interpretation"),
    "structured_result": ("R", "Structured Commercial Result"),
    "base_scenario": ("B", "Base Scenario"),
    "stress_scenario": ("S", "Stress Scenario"),
    "risk_delta": ("Δ", "Risk Delta"),
    "risk_interpretation": ("L", "Risk Interpretation"),
    "risk_result": ("R", "Structured Risk Result"),
    "agent_plan_created": ("P", "Plan Created"),
    "plan_validation": ("V", "Plan Validation"),
    "final_response_validation": ("V", "Final Response Validation"),
    "deterministic_start": ("D", "Deterministic Start"),
    "deterministic_final": ("F", "Deterministic Final"),
}
STATUS_LABELS = {
    "pending": "Pendiente",
    "running": "En curso",
    "completed": "Completada",
    "failed": "Fallida",
    "provider_error": "Error del proveedor",
    "needs_input": "Faltan datos",
    "skipped": "Omitida",
    "cancelled": "Cancelada",
    "timed_out": "Timeout",
}


def _event_duration(event: PerformanceEvent) -> float:
    if event.duration_seconds is not None:
        return event.duration_seconds
    if event.started_at is not None and event.status == PerformanceStatus.RUNNING:
        return max(0.0, perf_counter() - event.started_at)
    return 0.0


def _aggregate_stage(
    events: list[PerformanceEvent],
) -> tuple[PerformanceStatus, float]:
    if not events:
        return PerformanceStatus.PENDING, 0.0
    statuses = {event.status for event in events}
    if PerformanceStatus.FAILED in statuses:
        status = PerformanceStatus.FAILED
    elif PerformanceStatus.RUNNING in statuses:
        status = PerformanceStatus.RUNNING
    elif statuses == {PerformanceStatus.SKIPPED}:
        status = PerformanceStatus.SKIPPED
    else:
        status = PerformanceStatus.COMPLETED
    return status, sum(_event_duration(event) for event in events)


def _format_duration(seconds: float, show_zero: bool = True) -> str:
    if seconds <= 0 and not show_zero:
        return "—"
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.2f} s"


def _metric_sum(events: tuple[PerformanceEvent, ...], key: str) -> float:
    return sum(
        float(event.metadata.get(key, 0) or 0)
        for event in events
        if event.status == PerformanceStatus.COMPLETED
    )


def _latest_metric(
    events: tuple[PerformanceEvent, ...], key: str, default: int = 0
) -> int:
    value = _latest_metadata(events, key)
    return int(value) if value is not None else default


def _latest_metadata(
    events: tuple[PerformanceEvent, ...], key: str
) -> object | None:
    for event in reversed(events):
        value = event.metadata.get(key)
        if value is not None:
            return value
    return None


def _format_tool_mapping(values: object) -> str:
    if not isinstance(values, dict) or not values:
        return "—"
    return " · ".join(
        f"{escape(str(key))}: {escape(f'{value:g}' if isinstance(value, (int, float)) else str(value))}"
        for key, value in values.items()
    )


def _render_tool_events(events: list[PerformanceEvent]) -> str:
    parts = ['<div class="pi-tools">']
    for event in events:
        name = escape(str(event.metadata.get("tool_name", "tool")))
        scenario_name = event.metadata.get("scenario_name")
        scenario_label = (
            f"{escape(str(scenario_name))} · " if scenario_name else ""
        )
        if event.status == PerformanceStatus.SKIPPED:
            missing = event.metadata.get("missing_inputs", [])
            missing_text = ", ".join(
                escape(str(item)) for item in missing
            ) or "inputs no disponibles"
            parts.append(
                '<div class="pi-tool">'
                f'<div><strong>{scenario_label}{name}</strong><time>Omitida</time></div>'
                f'<small><b>Motivo</b> · Faltan: {missing_text}</small>'
                "</div>"
            )
            continue
        arguments = _format_tool_mapping(
            event.metadata.get("tool_arguments")
        )
        result = _format_tool_mapping(event.metadata.get("tool_result"))
        tool_seconds = float(
            event.metadata.get("tool_seconds", _event_duration(event)) or 0
        )
        parts.append(
            '<div class="pi-tool">'
            f'<div><strong>{scenario_label}{name}</strong><time>{tool_seconds * 1000:.3f} ms</time></div>'
            f'<small><b>Parámetros</b> · {arguments}</small>'
            f'<small><b>Resultado</b> · {result}</small>'
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _render_parsing_result(events: list[PerformanceEvent]) -> str:
    result = next(
        (
            event.metadata.get("parsing_result")
            for event in reversed(events)
            if isinstance(event.metadata.get("parsing_result"), dict)
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""

    def values(key: str, unit: str = "") -> str:
        candidates = result.get(key)
        if not isinstance(candidates, list) or not candidates:
            return "—"
        return ", ".join(
            escape(f"{item:g}{unit}" if isinstance(item, (int, float)) else str(item))
            for item in candidates
        )

    ambiguities = result.get("ambiguities")
    ambiguity_text = (
        ", ".join(escape(str(item)) for item in ambiguities)
        if isinstance(ambiguities, list) and ambiguities
        else "ninguna"
    )
    gas_labels = {
        "natural_gas": "Gas natural",
        "biomethane": "Biometano",
        "lng": "GNL",
        "hydrogen_blend": "Mezcla de hidrógeno",
    }
    detected = result.get("detected_gas_types")
    gas_text = (
        ", ".join(
            escape(gas_labels.get(str(item), str(item))) for item in detected
        )
        if isinstance(detected, list) and detected
        else "—"
    )
    source_text = {
        "user": "Usuario",
        "rag": "Documento RAG",
    }.get(result.get("gas_type_source"), "—")
    rows = (
        ("Gas detectado", gas_text),
        ("Origen", escape(source_text)),
        ("Demanda detectada", values("base_demand_candidates", " GWh")),
        (
            "Suministro detectado",
            values("contracted_supply_candidates", " GWh"),
        ),
        ("Coste", values("supply_cost_candidates", " €/MWh")),
        ("Venta", values("sales_price_candidates", " €/MWh")),
        ("Spot", values("spot_price_candidates", " €/MWh")),
        ("Escenarios", values("scenario_variations", " %")),
        ("Ambigüedades", ambiguity_text),
    )
    content = "".join(
        f"<small><b>{escape(label)}</b> · {value}</small>"
        for label, value in rows
    )
    return f'<div class="pi-tools"><div class="pi-tool">{content}</div></div>'


def _render_contractual_result(events: list[PerformanceEvent]) -> str:
    event = next(
        (
            event
            for event in reversed(events)
            if isinstance(event.metadata.get("contractual_result"), dict)
        ),
        None,
    )
    result = event.metadata.get("contractual_result") if event else None
    if not isinstance(result, dict):
        return ""

    def metric(label: str, key: str, unit: str) -> str:
        value = result.get(key)
        formatted = (
            f"{value:,.3f}".rstrip("0").rstrip(".")
            if isinstance(value, (int, float))
            else "—"
        )
        return (
            f"<small><b>{escape(label)}</b> · "
            f"{escape(formatted)} {escape(unit)}</small>"
        )

    content = "".join(
        (
            metric("Contractual excess", "contractual_excess_gwh", "GWh"),
            (
                f"<small><b>Supply short</b> · "
                f"{float(event.metadata['supply_short_gwh']):,.3f} GWh</small>"
                if event and event.metadata.get("supply_short_gwh") is not None
                else ""
            ),
            (
                f"<small><b>Spot coverage volume</b> · "
                f"{float(event.metadata['spot_coverage_volume_mwh']):,.0f} MWh</small>"
                if event and event.metadata.get("spot_coverage_volume_mwh") is not None
                else ""
            ),
            (
                f"<small><b>Spot coverage cost</b> · "
                f"{float(event.metadata['spot_coverage_cost_eur']):,.0f} €</small>"
                if event and event.metadata.get("spot_coverage_cost_eur") is not None
                else ""
            ),
            metric("Contractual maximum", "contractual_max_gwh", "GWh"),
            metric(
                "Contractual excess price",
                "contractual_excess_price_eur_mwh",
                "€/MWh",
            ),
        )
    )
    return f'<div class="pi-tools"><div class="pi-tool">{content}</div></div>'


def _render_retrieved_sources(events: list[PerformanceEvent]) -> str:
    sources = next(
        (
            event.metadata.get("sources")
            for event in reversed(events)
            if isinstance(event.metadata.get("sources"), list)
        ),
        [],
    )
    resolution = next(
        (
            event.metadata.get("gas_type_resolution")
            for event in reversed(events)
            if isinstance(event.metadata.get("gas_type_resolution"), dict)
        ),
        None,
    )
    if not sources and not resolution:
        return ""
    parts = ['<div class="pi-tools">']
    if isinstance(resolution, dict) and resolution.get("gas_type"):
        labels = {
            "natural_gas": "Gas natural",
            "biomethane": "Biometano",
            "lng": "GNL",
            "hydrogen_blend": "Mezcla de hidrógeno",
        }
        gas_type = str(resolution["gas_type"])
        source = "Usuario" if resolution.get("source") == "user" else "Documento RAG"
        parts.append(
            '<div class="pi-tool">'
            f'<small><b>Gas detectado</b> · {escape(labels.get(gas_type, gas_type))}</small>'
            f'<small><b>Origen</b> · {escape(source)}</small>'
            "</div>"
        )
    elif isinstance(resolution, dict) and resolution.get("status") in {
        "conflict",
        "ambiguous",
    }:
        conflicts = resolution.get("conflicts", [])
        labels = {
            "natural_gas": "Gas natural",
            "biomethane": "Biometano",
            "lng": "GNL",
            "hydrogen_blend": "Mezcla de hidrógeno",
        }
        conflict_text = ", ".join(
            escape(labels.get(str(item), str(item)))
            for item in conflicts
        )
        parts.append(
            '<div class="pi-tool">'
            '<small><b>Resolución de gas</b> · Conflicto</small>'
            f'<small><b>Candidatos</b> · {conflict_text}</small>'
            "</div>"
        )
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = escape(str(source.get("document_name", "Documento")))
        section = escape(str(source.get("section") or "Sin sección"))
        page = escape(str(source.get("page_start", "—")))
        score = float(source.get("score", 0) or 0)
        parts.append(
            '<div class="pi-tool">'
            f'<div><strong>{name}</strong><time>{score:.3f}</time></div>'
            f'<small><b>Sección</b> · {section} · página {page}</small>'
            "</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _render_agent_timeline(snapshot: PerformanceSnapshot) -> list[str]:
    parts: list[str] = []
    call_metrics = {
        call.call_number: call for call in build_operation_metrics(snapshot).llm_calls
    }
    visible_stages = {
        "agent_start",
        "llm_call",
        "agent_decision",
        "tool_execution",
        "agent_observation",
        "agent_final",
        "agent_plan_created",
        "plan_validation",
        "final_response_validation",
        "deterministic_start",
        "deterministic_final",
    }
    for event in snapshot.events:
        if event.stage not in visible_stages:
            continue
        icon, label = STAGE_PRESENTATION.get(event.stage, ("·", event.stage))
        if event.stage == "llm_call":
            call_number = int(event.metadata.get("call_number", event.round or 0))
            label = f"LLM Call #{call_number}"
        status = event.status.value
        parts.append(
            dedent(
                f"""\
                <div class="pi-stage pi-stage-{status}">
                  <div class="pi-dot">{icon}</div>
                  <div class="pi-stage-copy"><strong>{escape(label)}</strong></div>
                  <span class="pi-state">{STATUS_LABELS.get(status, status)}</span>
                  <time>{_format_duration(_event_duration(event))}</time>
                </div>
                """
            )
        )
        if event.stage == "llm_call":
            metric = call_metrics.get(int(event.metadata.get("call_number", event.round or 0)))
            if metric:
                inference = (
                    _format_duration(metric.inference_time)
                    if metric.inference_time is not None
                    else "unavailable"
                )
                parts.append(
                    '<div class="pi-tools"><div class="pi-tool">'
                    f'<strong>{escape(metric.purpose)}</strong>'
                    f'<small>Request wall: {_format_duration(metric.request_wall_time)} · '
                    f'Response stream: {_format_duration(metric.response_stream_time or 0)} · '
                    f'Server inference: {inference}</small></div></div>'
                )
        elif event.stage == "agent_decision":
            action = escape(str(event.metadata.get("action", "")))
            tool = escape(str(event.metadata.get("tool_name") or ""))
            summary = escape(str(event.metadata.get("decision_summary", "")))
            target = f" → {tool}" if tool else ""
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<strong>{action}{target}</strong><small>{summary}</small></div></div>'
            )
        elif event.stage == "tool_execution":
            parts.append(_render_tool_events([event]))
        elif event.stage == "agent_observation":
            kind = escape(str(event.metadata.get("observation_type", "observation")))
            result = _format_tool_mapping(event.metadata.get("observation_result"))
            message = escape(str(event.metadata.get("message") or ""))
            detail = result or message
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<strong>{kind}</strong><small>{detail}</small></div></div>'
            )
        elif event.stage == "agent_plan_created":
            actions = event.metadata.get("plan_actions", [])
            for index, action in enumerate(actions, 1):
                parts.append(
                    '<div class="pi-tools"><div class="pi-tool">'
                    f'<strong>#{index} {escape(str(action.get("tool_name", "tool")))}</strong>'
                    f'<small>{escape(_format_tool_mapping(action.get("arguments", {})))}</small>'
                    '</div></div>'
                )
        elif event.stage == "final_response_validation":
            validation_status = escape(
                str(event.metadata.get("validation_status", "unavailable"))
            )
            fallback = (
                "yes"
                if event.metadata.get("deterministic_fallback", False)
                else "no"
            )
            reasons = event.metadata.get("validation_reasons", [])
            reason_text = (
                ", ".join(escape(str(reason)) for reason in reasons)
                if reasons
                else "none"
            )
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<strong>{validation_status}</strong>'
                f'<small>Deterministic fallback: {fallback}</small>'
                f'<small>Reasons: {reason_text}</small></div></div>'
            )
    return parts


def _render_supply_agent_timeline(snapshot: PerformanceSnapshot) -> list[str]:
    """Render Supply Agent stages in observed time order, including bounded preflight RAG."""
    parts: list[str] = []
    call_metrics = {
        call.call_number: call for call in build_operation_metrics(snapshot).llm_calls
    }
    events = sorted(
        (event for event in snapshot.events if event.stage in SUPPLY_AGENT_PIPELINE_STAGES),
        key=lambda event: (event.started_at is None, event.started_at or float("inf")),
    )
    for event in events:
        icon, label = STAGE_PRESENTATION.get(event.stage, ("·", event.stage))
        if event.stage == "agent_start":
            label = "SupplyAgent"
        if event.stage == "llm_call":
            call_number = int(event.metadata.get("call_number", event.round or 0))
            label = f"LLM Call #{call_number}"
        elif event.stage == "tool_execution":
            label = str(event.metadata.get("tool_name", label))
        parts.append(
            dedent(
                f"""\
                <div class="pi-stage pi-stage-{event.status.value}">
                  <div class="pi-dot">{icon}</div>
                  <div class="pi-stage-copy"><strong>{escape(label)}</strong></div>
                  <span class="pi-state">{STATUS_LABELS.get(event.status.value, event.status.value)}</span>
                  <time>{_format_duration(_event_duration(event))}</time>
                </div>
                """
            )
        )
        if event.stage == "workspace_intent_routing":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Intent: {escape(str(event.metadata.get("intent", "unknown")))}</small>'
                f'<small>Capabilities: {escape(str(event.metadata.get("capabilities", ())))}</small>'
                '</div></div>'
            )
        elif event.stage == "portfolio_query":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Resolved item IDs: {escape(str(event.metadata.get("resolved_item_ids", ())))}</small>'
                f'<small>Matched by: {escape(str(event.metadata.get("matched_by", {})))}</small>'
                '</div></div>'
            )
        elif event.stage == "session_reference_resolution":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Resolution: {escape(str(event.metadata.get("resolution", "structured_context")))}</small>'
                f'<small>Focused item: {escape(str(event.metadata.get("focused_item_id") or "none"))}</small>'
                f'<small>Selected: {escape(str(event.metadata.get("selected_item_ids", ())))}</small>'
                '</div></div>'
            )
        elif event.stage == "workspace_reference_resolution":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Previous selection: {escape(str(event.metadata.get("previous_selected_item_ids", ())))}</small>'
                f'<small>Resolved selection: {escape(str(event.metadata.get("selected_item_ids", ())))}</small>'
                f'<small>Focus: {escape(str(event.metadata.get("focused_item_id") or "none"))}</small>'
                f'<small>Intent: {escape(str(event.metadata.get("resolved_intent", "reference")))}</small>'
                f'<small>Clarification required: {escape(str(event.metadata.get("clarification_required", False)))}</small>'
                '</div></div>'
            )
        elif event.stage == "deterministic_comparison":
            values = event.metadata.get("values", ())
            formatted = "; ".join(
                f'{item.get("item_id", "unknown")}: {item.get("value")} {item.get("unit") or ""}'.strip()
                for item in values if isinstance(item, dict)
            )
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Field: {escape(str(event.metadata.get("field", "unknown")))}</small>'
                f'<small>Selected items: {escape(str(event.metadata.get("selected_item_ids", ())))}</small>'
                f'<small>Comparable: {escape(str(event.metadata.get("comparable", "pending")))}</small>'
                f'<small>Values: {escape(formatted or "none")}</small>'
                '</div></div>'
            )
        elif event.stage == "scenario_clarification":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Target item: {escape(str(event.metadata.get("target_item_id") or "none"))}</small>'
                f'<small>Explicit day change supported: {escape(str(event.metadata.get("supported_day_change", False)))}</small>'
                '</div></div>'
            )
        elif event.stage == "portfolio_knowledge_retrieval":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Item: {escape(str(event.metadata.get("item_id", "unknown")))}</small>'
                f'<small>Status: {escape(str(event.metadata.get("knowledge_status", "unknown")))}</small>'
                f'<small>Retrieved chunk IDs: {escape(str(event.metadata.get("retrieved_chunk_ids", ())))}</small>'
                '</div></div>'
            )
        elif event.stage == "scenario_execution":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Item: {escape(str(event.metadata.get("item_id", "unknown")))}</small>'
                f'<small>Alternative: {escape(str(event.metadata.get("alternative_id", "pending")))}</small>'
                '</div></div>'
            )
        elif event.stage == "workspace_structured_evidence":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Position: {escape(str(event.metadata.get("item_id", "unknown")))}</small>'
                f'<small>Evaluation: {escape(str(event.metadata.get("domain_status", "unknown")))}</small>'
                f'<small>Finding codes: {escape(str(event.metadata.get("finding_codes", ())))}</small>'
                f'<small>Projection available: {escape(str(event.metadata.get("has_projection", "unknown")))}</small>'
                f'<small>Missing inputs: {escape(str(event.metadata.get("missing_input_count", "unknown")))}</small>'
                '</div></div>'
            )
        elif event.stage == "workspace_response_projection":
            guard_applied = bool(event.metadata.get("semantic_guard_applied", False))
            guard_diagnostics = ""
            if guard_applied:
                guard_diagnostics = (
                    f'<small>Semantic guard reason: {escape(str(event.metadata.get("semantic_guard_reason") or "unknown"))}</small>'
                    f'<small>Rule: {escape(str(event.metadata.get("semantic_guard_rule") or "unknown"))}</small>'
                    f'<small>Pattern: {escape(str(event.metadata.get("semantic_guard_pattern_id") or "unknown"))}</small>'
                    f'<small>Matched phrase: {escape(str(event.metadata.get("semantic_guard_matched_phrase") or "unknown"))}</small>'
                )
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Selected item IDs: {escape(str(event.metadata.get("selected_item_ids", ())))}</small>'
                f'<small>Semantic guard applied: {escape(str(event.metadata.get("semantic_guard_applied", "unknown")))}</small>'
                f'{guard_diagnostics}'
                '</div></div>'
            )
        elif event.stage == "citation_validation":
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<small>Valid: {escape(str(event.metadata.get("valid", "pending")))}</small>'
                f'<small>Selected scopes: {escape(str(event.metadata.get("selected_item_ids", ())))}</small>'
                '</div></div>'
            )
        if event.stage == "tool_execution":
            parts.append(_render_tool_events([event]))
        elif event.stage == "agent_decision" and event.status == PerformanceStatus.FAILED:
            error_type = escape(str(event.metadata.get("error_type", "ExecutionError")))
            parts.append(
                '<div class="pi-tools"><div class="pi-tool">'
                f'<strong>Decision failed</strong><small>{error_type}</small></div></div>'
            )
        elif event.stage == "llm_call":
            metric = call_metrics.get(int(event.metadata.get("call_number", event.round or 0)))
            if metric:
                inference = (
                    _format_duration(metric.inference_time)
                    if metric.inference_time is not None else "unavailable"
                )
                parts.append(
                    '<div class="pi-tools"><div class="pi-tool">'
                    f'<strong>{escape(metric.purpose)}</strong>'
                    f'<small>Request wall: {_format_duration(metric.request_wall_time)} · '
                    f'Response stream: {_format_duration(metric.response_stream_time or 0)} · '
                    f'Server inference: {inference}</small></div></div>'
                )
    return parts


def _render_risk_timeline(snapshot):
    parts = []
    for event in snapshot.events:
        if event.stage not in RISK_AGENT_PIPELINE_STAGES:
            continue
        icon, label = STAGE_PRESENTATION[event.stage]
        if event.stage == "agent_start":
            label = "RiskAgent"
        scenario = event.metadata.get("scenario_name", "")
        parts.append(
            f'<div class="pi-stage pi-stage-{event.status.value}">'
            f'<div class="pi-dot">{icon}</div><div class="pi-stage-copy">'
            f'<strong>{escape(label)}</strong><small>{escape(str(scenario))}</small></div>'
            f'<span class="pi-state">{STATUS_LABELS[event.status.value]}</span>'
            f'<time>{_format_duration(_event_duration(event))}</time></div>'
        )
        if event.stage == "tool_execution":
            parts.append(_render_tool_events([event]))
        else:
            data = event.metadata.get("delta", event.metadata.get("risk_inputs"))
            if event.stage == "risk_result":
                result = event.metadata.get("structured_result", {})
                data = {"scenario_count": event.metadata.get("scenario_count"),
                        "scenario_names": event.metadata.get("scenario_names"),
                        "status": result.get("status"), "warnings": result.get("warnings")}
            if data is not None:
                parts.append(f'<div class="pi-tools"><small>{escape(str(data))}</small></div>')
    return parts


def render_pipeline_inspector(snapshot: PerformanceSnapshot | None) -> None:
    with st.expander("Pipeline Inspector", expanded=False):
        if snapshot is None:
            st.caption("El flujo de la próxima petición aparecerá aquí.")
            return

        st.caption("Operación: " + snapshot.operation_id)
        status_class = (
            snapshot.status
            if snapshot.status in {"running", "completed", "failed"}
            else "failed"
        )
        provider = {"lmstudio": "LM Studio", "openai": "OpenAI"}.get(
            snapshot.provider, snapshot.provider
        )
        mode = {
            "chat": "Chat",
            "rag_chat": "RAG Chat",
            "rag_index": "RAG Indexing",
            "gas_analysis": "Gas Analysis",
            "conversational_workspace": "Conversational Workspace",
            "procurement_agent": "ProcurementAgent",
            "procurement_planner": "Planner Agent",
            "procurement_deterministic": "Deterministic Procurement",
        }.get(snapshot.mode, snapshot.mode)
        st.markdown(
            dedent(
                f"""\
                <div class="pi-summary">
                  <div><span>Proveedor</span><strong>{escape(provider)}</strong></div>
                  <div><span>Modelo</span><strong>{escape(snapshot.model)}</strong></div>
                  <div><span>Modo</span><strong>{escape(mode)}</strong></div>
                  <div><span>Estado</span><strong class="pi-{status_class}">{escape(STATUS_LABELS.get(snapshot.status, snapshot.status))}</strong></div>
                  <div><span>Tiempo total</span><strong>{_format_duration(snapshot.elapsed_seconds)}</strong></div>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )

        render_clipboard_button(
            build_diagnostics_clipboard_text(snapshot),
            "Copiar diagnóstico",
            key=f"diagnostics-{snapshot.operation_id}",
        )

        if snapshot.mode == "supervisor":
            from supervisor_ui import render_supervisor_inspector
            render_supervisor_inspector(snapshot)
            render_advanced_metrics(snapshot)
            return

        pipeline_stages = {
            "conversational_workspace": SUPPLY_AGENT_PIPELINE_STAGES,
            "supply_agent": SUPPLY_AGENT_PIPELINE_STAGES,
            "risk_agent": RISK_AGENT_PIPELINE_STAGES,
            "commercial_agent": COMMERCIAL_AGENT_PIPELINE_STAGES,
            "gas_analysis": GAS_PIPELINE_STAGES,
            "rag_chat": RAG_CHAT_PIPELINE_STAGES,
            "rag_index": RAG_INDEX_PIPELINE_STAGES,
            "procurement_agent": PROCUREMENT_AGENT_PIPELINE_STAGES,
            "procurement_planner": PROCUREMENT_PLANNER_PIPELINE_STAGES,
            "procurement_deterministic": PROCUREMENT_DETERMINISTIC_PIPELINE_STAGES,
        }.get(snapshot.mode, PIPELINE_STAGES)
        if snapshot.mode == "gas_analysis" and any(
            event.stage == "query_embedding" for event in snapshot.events
        ):
            pipeline_stages = GAS_RAG_PIPELINE_STAGES
        query_intent = next(
            (
                event.metadata.get("intent")
                for event in reversed(snapshot.events)
                if event.stage == "query_classification"
            ),
            None,
        )
        if snapshot.mode == "gas_analysis" and query_intent == "documentary":
            pipeline_stages = GAS_DOCUMENTARY_PIPELINE_STAGES
        analysis_kind = next(
            (
                event.metadata.get("analysis_kind")
                for event in reversed(snapshot.events)
                if event.stage == "input_parsing"
            ),
            None,
        )
        if snapshot.mode == "gas_analysis" and analysis_kind == "position":
            pipeline_stages = GAS_POSITION_PIPELINE_STAGES
        event_groups = {
            stage: [event for event in snapshot.events if event.stage == stage]
            for stage in pipeline_stages
        }
        timeline_parts = ['<div class="pi-timeline">']
        if snapshot.mode in {"supply_agent", "conversational_workspace"}:
            timeline_parts.extend(_render_supply_agent_timeline(snapshot))
            pipeline_stages = ()
        elif snapshot.mode == "risk_agent":
            timeline_parts.extend(_render_risk_timeline(snapshot))
            pipeline_stages = ()
        if snapshot.mode in {
            "procurement_agent",
            "procurement_planner",
            "procurement_deterministic",
        }:
            timeline_parts.extend(_render_agent_timeline(snapshot))
            pipeline_stages = ()
        for stage in pipeline_stages:
            icon, label = STAGE_PRESENTATION[stage]
            if snapshot.mode == "supply_agent" and stage == "agent_start":
                label = "SupplyAgent"
            if snapshot.mode == "commercial_agent" and stage == "agent_start":
                label = "CommercialAgent"
            if snapshot.mode == "rag_chat" and stage == "tool_execution":
                label = "Business Tools"
            events = event_groups[stage]
            status, duration = _aggregate_stage(events)
            detail = ""
            rounds = sorted(
                {event.round for event in events if event.round is not None}
            )
            if len(rounds) > 1:
                detail = f'<small>{len(rounds)} rounds</small>'
            elif stage == "tool_execution" and events:
                detail = f'<small>{len(events)} ejecuciones</small>'
            elif stage == "document_parsing" and events:
                count = events[-1].metadata.get("document_count", 0)
                detail = f"<small>{escape(str(count))} documentos</small>"
            elif stage == "chunking" and events:
                count = events[-1].metadata.get("chunk_count", 0)
                detail = f"<small>{escape(str(count))} chunks</small>"
            elif stage == "index_persistence" and events:
                count = events[-1].metadata.get(
                    "persisted_chunk_count",
                    events[-1].metadata.get("chunk_count", 0),
                )
                detail = f"<small>{escape(str(count))} chunks</small>"
            elif stage in {"embedding", "vector_search", "retrieved_context"} and events:
                count = events[-1].metadata.get(
                    "embedding_count",
                    events[-1].metadata.get("retrieved_chunk_count", 0),
                )
                detail = f"<small>{escape(str(count))} elementos</small>"
            timeline_parts.append(
                dedent(
                    f"""\
                    <div class="pi-stage pi-stage-{status.value}">
                      <div class="pi-dot">{icon}</div>
                      <div class="pi-stage-copy"><strong>{label}</strong>{detail}</div>
                      <span class="pi-state">{STATUS_LABELS[status.value]}</span>
                      <time>{_format_duration(duration, status != PerformanceStatus.PENDING)}</time>
                    </div>
                    """
                )
            )
            if stage == "tool_execution" and events:
                timeline_parts.append(_render_tool_events(events))
            elif stage == "agent_decision" and events:
                for event in events:
                    action = escape(str(event.metadata.get("action", "")))
                    tool = escape(str(event.metadata.get("tool_name") or ""))
                    summary = escape(str(event.metadata.get("decision_summary", "")))
                    target = f" → {tool}" if tool else ""
                    timeline_parts.append(
                        '<div class="pi-tools"><div class="pi-tool">'
                        f'<strong>#{event.round or "—"} {action}{target}</strong>'
                        f'<small>{summary}</small></div></div>'
                    )
            elif stage == "agent_observation" and events:
                for event in events:
                    kind = escape(str(event.metadata.get("observation_type", "observation")))
                    result = _format_tool_mapping(event.metadata.get("observation_result"))
                    timeline_parts.append(
                        '<div class="pi-tools"><div class="pi-tool">'
                        f'<strong>#{event.round or "—"} {kind}</strong>'
                        f'<small>{result}</small></div></div>'
                    )
            elif stage == "input_parsing" and events:
                timeline_parts.append(_render_parsing_result(events))
            elif stage == "contractual_calculation" and events:
                timeline_parts.append(_render_contractual_result(events))
            elif stage == "retrieved_context" and events:
                timeline_parts.append(_render_retrieved_sources(events))
            elif stage in {"structured_result", "agent_final"} and events and snapshot.mode == "commercial_agent":
                metadata = events[-1].metadata
                structured = metadata.get("structured_result", {})
                detail = escape(str({
                    "estado": structured.get("status", metadata.get("agent_status")),
                    "hechos": len(structured.get("contract_facts", [])),
                    "cálculos": len(structured.get("calculations", [])),
                    "avisos": structured.get("warnings", []),
                }))
                timeline_parts.append(f'<div class="pi-tools"><small>{detail}</small></div>')
        timeline_parts.append("</div>")
        st.markdown("".join(timeline_parts), unsafe_allow_html=True)
        if snapshot.mode == "risk_agent":
            structured = _latest_metadata(snapshot.events, "structured_result")
            if structured is not None:
                with st.expander("Resultado de riesgo estructurado"):
                    st.json(structured)
        if snapshot.mode == "commercial_agent":
            structured = _latest_metadata(snapshot.events, "structured_result")
            if structured is not None:
                with st.expander("Resultado comercial estructurado"):
                    st.json(structured)

        render_advanced_metrics(snapshot)


def render_advanced_metrics(snapshot):
    with st.expander("Métricas avanzadas"):
        operation_metrics = build_operation_metrics(snapshot)
        message_count = _latest_metric(snapshot.events, "message_count")
        prompt_chars = _latest_metric(
            snapshot.events, "approximate_prompt_chars"
        )
        rounds = max(
            (event.round or 0 for event in snapshot.events), default=0
        )
        tool_calls = int(_metric_sum(snapshot.events, "tool_call_count"))
        first_row = st.columns(3)
        first_row[0].metric("Mensajes", message_count)
        first_row[1].metric("Caracteres prompt", f"{prompt_chars:,}")
        first_row[2].metric("Round", rounds or "—")
        second_row = st.columns(3)
        second_row[0].metric("Tool calls", tool_calls)
        second_row[1].metric(
            "LLM request wall",
            _format_duration(operation_metrics.llm_request_wall_time_total),
        )
        second_row[2].metric(
            "Server inference",
            (
                _format_duration(operation_metrics.llm_inference_time_total)
                if operation_metrics.llm_inference_time_total is not None
                else "No disponible"
            ),
        )
        max_tokens = _latest_metadata(snapshot.events, "max_tokens")
        max_output_tokens = _latest_metadata(
            snapshot.events, "max_output_tokens"
        )
        timeout_seconds = _latest_metadata(
            snapshot.events, "timeout_seconds"
        )
        thinking_enabled = _latest_metadata(
            snapshot.events, "thinking_enabled"
        )
        config_first_row = st.columns(2)
        config_first_row[0].metric(
            "Max tokens", max_tokens if max_tokens is not None else "—"
        )
        config_first_row[1].metric(
            "Max output tokens",
            max_output_tokens
            if max_output_tokens is not None
            else "—",
        )
        config_second_row = st.columns(2)
        config_second_row[0].metric(
            "Timeout",
            f"{timeout_seconds} s"
            if timeout_seconds is not None
            else "—",
        )
        config_second_row[1].metric(
            "Thinking",
            (
                "Sí" if bool(thinking_enabled) else "No"
                if thinking_enabled is not None
                else "—"
            ),
        )


def inject_pipeline_styles() -> None:
    st.markdown(
        dedent(
            """\
            <style>
            .pi-summary {display:grid;grid-template-columns:1fr 1fr;gap:.55rem;margin-bottom:1rem}
            .pi-summary div {background:color-mix(in srgb,var(--text-color) 5%,transparent);border-radius:.5rem;padding:.55rem .65rem;min-width:0}
            .pi-summary div:last-child {grid-column:1/-1}
            .pi-summary span {display:block;font-size:.7rem;opacity:.65;text-transform:uppercase;letter-spacing:.04em}
            .pi-summary strong {display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
            .pi-running {color:#d99a00}.pi-completed {color:#21a366}.pi-failed {color:#e05252}
            .pi-timeline {margin:.25rem 0 .8rem .25rem}
            .pi-stage {position:relative;display:grid;grid-template-columns:2rem minmax(0,1fr) auto;grid-template-rows:auto auto;column-gap:.55rem;min-height:3.55rem;padding-bottom:.55rem}
            .pi-stage:not(:last-child):before {content:"";position:absolute;left:.95rem;top:1.9rem;bottom:-.1rem;border-left:2px solid color-mix(in srgb,var(--text-color) 16%,transparent)}
            .pi-dot {z-index:1;width:2rem;height:2rem;display:grid;place-items:center;border-radius:50%;background:var(--secondary-background-color);font-size:.9rem}
            .pi-stage-running .pi-dot {box-shadow:0 0 0 3px rgba(217,154,0,.2)}
            .pi-stage-failed .pi-dot {box-shadow:0 0 0 3px rgba(224,82,82,.18)}
            .pi-stage-copy {padding-top:.25rem;min-width:0}.pi-stage-copy strong {display:block;font-size:.92rem}.pi-stage-copy small {opacity:.6}
            .pi-state {font-size:.72rem;padding-top:.35rem;opacity:.7}.pi-stage time {grid-column:2/-1;font-size:.75rem;opacity:.62}
            .pi-tools {margin:-.3rem 0 .8rem 2.55rem;display:grid;gap:.45rem}
            .pi-tool {padding:.5rem .6rem;border:1px solid color-mix(in srgb,var(--text-color) 12%,transparent);border-radius:.5rem;background:color-mix(in srgb,var(--text-color) 3%,transparent)}
            .pi-tool>div {display:flex;justify-content:space-between;gap:.5rem}.pi-tool strong {font-size:.8rem}.pi-tool time {font-size:.7rem;opacity:.65;white-space:nowrap}
            .pi-tool small {display:block;margin-top:.18rem;opacity:.68;overflow-wrap:anywhere}
            </style>
            """
        ),
        unsafe_allow_html=True,
    )
