from html import escape
from textwrap import dedent
from time import perf_counter

import streamlit as st

from diagnostics import (
    GAS_PIPELINE_STAGES,
    GAS_DOCUMENTARY_PIPELINE_STAGES,
    GAS_POSITION_PIPELINE_STAGES,
    GAS_RAG_PIPELINE_STAGES,
    PIPELINE_STAGES,
    RAG_CHAT_PIPELINE_STAGES,
    RAG_INDEX_PIPELINE_STAGES,
    PerformanceEvent,
    PerformanceSnapshot,
    PerformanceStatus,
)


STAGE_PRESENTATION = {
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
    "provider_start": ("🔌", "Provider Start"),
    "http_request": ("🌐", "HTTP Request"),
    "model_inference": ("🧠", "Model Inference"),
    "parse_validation": ("🔎", "Parse / Validation"),
    "tool_execution": ("🛠️", "Tool Execution"),
    "final_response": ("✅", "Final Response"),
}
STATUS_LABELS = {
    "pending": "Pendiente",
    "running": "En curso",
    "completed": "Completada",
    "failed": "Fallida",
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


def render_pipeline_inspector(snapshot: PerformanceSnapshot | None) -> None:
    with st.expander("Pipeline Inspector", expanded=True):
        if snapshot is None:
            st.caption("El flujo de la próxima petición aparecerá aquí.")
            return

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

        pipeline_stages = {
            "gas_analysis": GAS_PIPELINE_STAGES,
            "rag_chat": RAG_CHAT_PIPELINE_STAGES,
            "rag_index": RAG_INDEX_PIPELINE_STAGES,
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
        for stage in pipeline_stages:
            icon, label = STAGE_PRESENTATION[stage]
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
            elif stage == "input_parsing" and events:
                timeline_parts.append(_render_parsing_result(events))
            elif stage == "contractual_calculation" and events:
                timeline_parts.append(_render_contractual_result(events))
            elif stage == "retrieved_context" and events:
                timeline_parts.append(_render_retrieved_sources(events))
        timeline_parts.append("</div>")
        st.markdown("".join(timeline_parts), unsafe_allow_html=True)

        with st.expander("Métricas avanzadas"):
            message_count = _latest_metric(snapshot.events, "message_count")
            prompt_chars = _latest_metric(
                snapshot.events, "approximate_prompt_chars"
            )
            rounds = max(
                (event.round or 0 for event in snapshot.events), default=0
            )
            tool_calls = int(_metric_sum(snapshot.events, "tool_call_count"))
            http_seconds = _metric_sum(
                snapshot.events, "http_total_seconds"
            )
            inference_seconds = _metric_sum(
                snapshot.events, "inference_seconds"
            )
            first_row = st.columns(3)
            first_row[0].metric("Mensajes", message_count)
            first_row[1].metric("Caracteres prompt", f"{prompt_chars:,}")
            first_row[2].metric("Round", rounds or "—")
            second_row = st.columns(3)
            second_row[0].metric("Tool calls", tool_calls)
            second_row[1].metric("Tiempo HTTP", _format_duration(http_seconds))
            second_row[2].metric(
                "Inferencia", _format_duration(inference_seconds)
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
