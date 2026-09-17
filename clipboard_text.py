import json
import re
from typing import Any

from diagnostics import PerformanceEvent, PerformanceSnapshot, PerformanceStatus
from execution_metrics import build_operation_metrics


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
        if reasons:
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

    parsing = _latest_event(events, "input_parsing")
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
            if event.metadata.get("tool_arguments") is not None:
                lines.append(f"  arguments: {_readable(event.metadata['tool_arguments'])}")
            if event.metadata.get("tool_result") is not None:
                lines.append(f"  result: {_readable(event.metadata['tool_result'])}")
            missing = event.metadata.get("missing_inputs")
            if missing:
                lines.append(f"  skipped inputs: {_readable(missing)}")
            reason = event.metadata.get("skip_reason")
            if reason:
                lines.append(f"  reason: {_redact_text(str(reason))}")
            lines.append(f"  duration: {_duration(_event_duration(event))}")
        for event in skipped_tool_events:
            lines.append(str(event.metadata.get("tool_name", "tool")))
            lines.append("  status: skipped")
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
            detail = event.metadata.get("error") or event.metadata.get("warning") or event.metadata.get("warnings") or event.metadata.get("fallback")
            reason = event.metadata.get("skip_reason")
            text = detail or reason or event.status.value
            lines.append(f"- {event.stage}: {_redact_text(str(text))}")
    return _redact_text("\n".join(lines))


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
    }.get(mode, mode)
