"""Read saved benchmark results without executing procurement workflows."""

import json
import sqlite3
from datetime import datetime

import streamlit as st

from comparison_store import ComparisonStore, DEFAULT_STORE
from procurement_benchmark import summarize


def render_benchmark_history(path=DEFAULT_STORE):
    st.subheader("Historial del benchmark")
    st.button("Actualizar historial", key="benchmark_refresh")
    if not path.exists():
        st.info("Todavía no hay ejecuciones guardadas. Ejecuta un lote con la CLI del benchmark para compararlo aquí.")
        return
    try:
        store = ComparisonStore(path)
        batches = store.list_batches()
        if not batches:
            st.info("Todavía no hay ejecuciones guardadas.")
            return
        counts = {batch["batch_id"]: batch["run_count"] for batch in batches}
        dates = {batch["batch_id"]: datetime.fromisoformat(batch["first_saved_at"])
                 for batch in batches}
        latest_id = batches[0]["batch_id"]
        if st.button("Ver lote más reciente", key="benchmark_latest"):
            st.session_state.benchmark_batch = latest_id
        batch_id = st.selectbox(
            "Lote", list(counts), key="benchmark_batch",
            format_func=lambda value: (
                f"{dates[value]:%Y-%m-%d %H:%M:%S} UTC · {value[:8]} · "
                f"{counts[value]} ejecuciones"
                + (" · Más reciente" if value == latest_id else "")
            ),
        )
        st.caption(f"Lote seleccionado: {batch_id}")
        rows = store.read(batch_id)
        cases = st.multiselect("Casos", sorted({r["case_id"] for r in rows}),
                               key=f"benchmark_cases_{batch_id}")
        modes = st.multiselect("Estrategias", sorted({r["record"]["mode"] for r in rows}),
                               key=f"benchmark_modes_{batch_id}")
        filtered = [r for r in rows if (not cases or r["case_id"] in cases)
                    and (not modes or r["record"]["mode"] in modes)]
        if not filtered:
            st.info("No hay ejecuciones para los filtros seleccionados.")
            return
        summary = summarize(filtered)
        st.caption("Sin filtros se muestran todos los casos y estrategias. Mediana y p95 incluyen solo ejecuciones completadas; los fallbacks se cuentan por separado.")
        st.dataframe([{
            "Caso": r["case_id"], "Estrategia": r["mode"],
            "Proveedor": r["provider"], "Modelo": r["model"],
            "Configuración": json.dumps(r["config"], ensure_ascii=False, sort_keys=True),
            "Ejecuciones": r["runs"], "Completadas": r["completed"],
            "No completadas": r["not_completed"], "Planes inválidos": r["plan_failures"],
            "Fallbacks": r["fallbacks"], "Acciones omitidas": r["skipped_actions"],
            "Llamadas LLM": r["llm_calls"],
            "Mediana (s)": r["median_seconds_completed"],
            "p95 (s)": r["p95_seconds_completed"],
        } for r in summary], hide_index=True)
        st.download_button(
            "Descargar informe JSON",
            json.dumps({"batch_id": batch_id, "summary": summary, "runs": filtered},
                       ensure_ascii=False, indent=2),
            file_name=f"procurement-benchmark-{dates[batch_id]:%Y%m%d-%H%M%S}-UTC-{batch_id}.json",
            mime="application/json",
            key="benchmark_download",
        )
        with st.expander("Detalle de las ejecuciones"):
            st.dataframe([{
                "Fecha UTC": r["created_at"], "Caso": r["case_id"],
                "Estrategia": r["record"]["mode"], "Repetición": r["repetition"],
                "Estado": r["record"]["status"],
                "Motivo": r["record"]["termination_reason"],
                "Fallback": r["fallback"],
                "Motivos de validación": (
                    "No registrados" if r.get("validation_reasons") is None
                    else ", ".join(r["validation_reasons"]) or "Sin incidencias"
                ),
                "Tiempo (s)": r["record"]["total_wall_time"],
                "Respuesta": r["record"]["result_summary"],
            } for r in filtered], hide_index=True)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        st.error("No se pudo leer el historial del benchmark. Comprueba que la base local está disponible y contiene registros válidos.")
