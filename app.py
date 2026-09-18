import streamlit as st
import os
import shutil
import tempfile
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dataclasses import asdict
from execution_view import ExecutionView, find_execution, clipboard_payloads, render_execution_header
from business_output import commercial_text, risk_text, supervisor_text
from diagnostics import PerformanceRecorder
from commercial_agent import CommercialAgent, ProviderCommercialModel
from commercial_models import CommercialAgentResult
from commercial_ui import render_commercial_result
from risk_agent import RiskAgent, ProviderRiskModel
from risk_models import RiskAgentResult
from risk_ui import render_risk_result
from supervisor import Supervisor
from supervisor_models import SupervisorResult
from supervisor_ui import render_supervisor_result
from benchmark_ui import render_benchmark_history
from clipboard_text import build_all_clipboard_text, build_response_clipboard_text, _redact_text
from clipboard_ui import render_clipboard_button
from execution_metrics import build_operation_metrics
from gas_analysis import (
    GasAnalysisError,
    ScenarioAnalysis,
)
from gas_query_orchestration import prepare_gas_query
from gas_response_handling import GasResponseContract, handle_gas_response
from generation import (
    AgentJob,
    GenerationJob,
    GenerationResult,
    GenerationStatus,
)
from procurement_agent import ProcurementAgent, ProviderDecisionModel
from procurement_deterministic import (
    ProcurementDeterministicWorkflow,
    ProviderSynthesisModel,
)
from procurement_planner import ProcurementAgentPlanner, ProviderPlannerModel
from llm_client import (
    CHAT_GENERATION_OPTIONS,
    GAS_ANALYSIS_GENERATION_OPTIONS,
    RAG_CHAT_GENERATION_OPTIONS,
    GenerationOptions,
    get_available_models,
    get_default_model_name,
    get_default_provider_name,
    get_llm_provider,
    get_supported_providers,
)
from embeddings import LMStudioEmbeddingProvider, get_embedding_model_name
from pipeline_inspector import inject_pipeline_styles, render_pipeline_inspector
from rag_service import RAGService, build_rag_messages, sanitize_rag_citations
from runtime_config import LLMRuntimeConfig
from vector_store import LocalVectorStore, VectorStoreError


GAS_TYPE_LABELS = {
    "natural_gas": "Gas natural",
    "biomethane": "Biometano",
    "lng": "GNL",
    "hydrogen_blend": "Mezcla de hidrógeno",
}
RISK_LEVEL_LABELS = {
    "low": "Bajo",
    "medium": "Medio",
    "high": "Alto",
    "critical": "Crítico",
}
POSITION_LABELS = {
    "LONG": "Larga",
    "SHORT": "Corta",
    "BALANCED": "Equilibrada",
}


st.set_page_config(page_title="indAI MA", page_icon="🏭", layout="wide")

st.title("indAI MA")
st.caption("Asistente para el sector industrial")

if "execution_views" not in st.session_state:
    st.session_state.execution_views = {}
    st.session_state.execution_mode_ids = {}

if "messages" not in st.session_state:
    st.session_state.messages = []
if "gas_analysis_result" not in st.session_state:
    st.session_state.gas_analysis_result = None
if "gas_analysis_metadata" not in st.session_state:
    st.session_state.gas_analysis_metadata = None
if "gas_documentary_result" not in st.session_state:
    st.session_state.gas_documentary_result = None
if "gas_documentary_sources" not in st.session_state:
    st.session_state.gas_documentary_sources = []
if "gas_response_contract" not in st.session_state:
    st.session_state.gas_response_contract = None
if "procurement_agent_result" not in st.session_state:
    st.session_state.procurement_agent_result = None
if "commercial_result" not in st.session_state:
    st.session_state.commercial_result = None
    st.session_state.commercial_operation_id = None
if "risk_result" not in st.session_state:
    st.session_state.risk_result = None
    st.session_state.risk_operation_id = None
if "supervisor_result" not in st.session_state:
    st.session_state.supervisor_result = None
    st.session_state.supervisor_operation_id = None
if "procurement_agent_metadata" not in st.session_state:
    st.session_state.procurement_agent_metadata = None
if "generation_job" not in st.session_state:
    st.session_state.generation_job = None
if "generation_kind" not in st.session_state:
    st.session_state.generation_kind = None
if "generation_notice" not in st.session_state:
    st.session_state.generation_notice = None
if "operation_started_at" not in st.session_state:
    st.session_state.operation_started_at = None
if "operation_id" not in st.session_state:
    st.session_state.operation_id = None
if "pipeline_recorder" not in st.session_state:
    st.session_state.pipeline_recorder = None
if "gas_precomputed_tool_executions" not in st.session_state:
    st.session_state.gas_precomputed_tool_executions = []
if "gas_scenarios" not in st.session_state:
    st.session_state.gas_scenarios = []
if "llm_runtime_config" not in st.session_state:
    st.session_state.llm_runtime_config = LLMRuntimeConfig.from_environment()
if "rag_enabled" not in st.session_state:
    st.session_state.rag_enabled = False
if "rag_pending_sources" not in st.session_state:
    st.session_state.rag_pending_sources = []
if "rag_notice" not in st.session_state:
    st.session_state.rag_notice = None
if "rag_clear_confirmation" not in st.session_state:
    st.session_state.rag_clear_confirmation = False
try:
    st.session_state.rag_store = LocalVectorStore.from_environment(
        get_embedding_model_name()
    )
    st.session_state.rag_store_error = None
except VectorStoreError as error:
    st.session_state.rag_store = None
    st.session_state.rag_store_error = str(error)

generation_active = st.session_state.generation_job is not None

provider_options = get_supported_providers()
default_provider = get_default_provider_name()
default_provider_index = (
    provider_options.index(default_provider)
    if default_provider in provider_options
    else 0
)

with st.sidebar:
    st.header("Configuración")
    selected_mode = st.radio(
        "Modo",
        ("Chat", "Gas B2B Portfolio Analysis", "ProcurementAgent", "CommercialAgent", "RiskAgent", "Multi-Agent Supervisor", "Evaluation"),
        key="selected_mode",
        disabled=generation_active,
    )
    selected_provider = st.selectbox(
        "Proveedor LLM",
        provider_options,
        index=default_provider_index,
        key="selected_provider",
        disabled=generation_active,
    )

    try:
        available_models = get_available_models(selected_provider)
        default_model = get_default_model_name(selected_provider)
        default_model_index = (
            available_models.index(default_model)
            if default_model in available_models
            else 0
        )
        selected_model = st.selectbox(
            "Modelo",
            available_models,
            index=default_model_index,
            key=f"selected_model_{selected_provider}",
            disabled=generation_active,
        )
    except ValueError as error:
        st.error(str(error))
        selected_model = None

    runtime_defaults: LLMRuntimeConfig = st.session_state.llm_runtime_config
    with st.expander("Configuración avanzada del LLM"):
        max_output_tokens = st.number_input(
            "Max output tokens",
            min_value=32,
            max_value=8192,
            value=runtime_defaults.max_output_tokens,
            step=32,
            key="llm_max_output_tokens",
            disabled=generation_active,
            help=(
                "Límite de salida utilizado por proveedores que diferencian "
                "output tokens."
            ),
        )
        max_tokens = st.number_input(
            "Max tokens",
            min_value=32,
            max_value=8192,
            value=runtime_defaults.max_tokens,
            step=32,
            key="llm_max_tokens",
            disabled=generation_active,
            help="Límite máximo de tokens generados por el modelo.",
        )
        timeout_seconds = st.number_input(
            "Timeout (segundos)",
            min_value=10,
            max_value=900,
            value=runtime_defaults.timeout_seconds,
            step=10,
            key="llm_timeout_seconds",
            disabled=generation_active,
            help="Tiempo máximo permitido para completar la operación.",
        )
        enable_thinking = st.toggle(
            "Thinking",
            value=runtime_defaults.enable_thinking,
            key="llm_enable_thinking",
            disabled=generation_active,
            help=(
                "Activa o desactiva el modo de razonamiento en modelos "
                "compatibles."
            ),
        )
    st.session_state.llm_runtime_config = LLMRuntimeConfig(
        max_output_tokens=int(max_output_tokens),
        max_tokens=int(max_tokens),
        timeout_seconds=int(timeout_seconds),
        enable_thinking=enable_thinking,
    )

    if st.button(
        "Nueva conversación",
        width="stretch",
        disabled=generation_active,
    ):
        st.session_state.messages = []
        st.rerun()

    if generation_active and st.button(
        "Detener generación",
        type="secondary",
        width="stretch",
        disabled=st.session_state.generation_job.cancel_requested,
    ):
        st.session_state.generation_job.cancel()
        st.rerun()


def remember_execution_start(recorder):
    st.session_state.generation_notice_mode = selected_mode
    config = {**asdict(st.session_state.llm_runtime_config),
              "provider": recorder.provider, "model": recorder.model}
    if selected_mode == "ProcurementAgent":
        config["strategy"] = st.session_state.procurement_orchestration_mode
    if selected_mode == "CommercialAgent":
        config["interpretation"] = st.session_state.commercial_interpretation
    if selected_mode == "RiskAgent":
        config["interpretation"] = "llm_prioritized" if st.session_state.risk_use_llm else "deterministic"
    if selected_mode == "Multi-Agent Supervisor":
        config["routing"] = "deterministic_first"
        config["synthesis"] = "llm_prioritized" if st.session_state.supervisor_synthesis else "deterministic"
    recorder.record_stage("execution_configuration", effective_configuration=config)
    view = ExecutionView.capture(selected_mode, None, config, recorder.snapshot())
    st.session_state.execution_views[view.operation_id] = view
    st.session_state.execution_mode_ids[selected_mode] = view.operation_id


def visible_execution():
    operation_id = st.session_state.execution_mode_ids.get(selected_mode)
    if selected_mode == "Chat":
        last = next((m for m in reversed(st.session_state.messages) if m["role"] == "assistant"), None)
        if last:
            operation_id = last.get("operation_id")
    view = find_execution(st.session_state.execution_views, operation_id)
    recorder = st.session_state.pipeline_recorder
    if view and view.status == "running" and recorder and recorder.operation_id == view.operation_id:
        return ExecutionView.capture(view.mode, None, view.configuration, recorder.snapshot())
    return view


def create_pipeline_recorder(
    operation_id: str,
    generation_kind: str,
) -> PerformanceRecorder:
    recorder = PerformanceRecorder(
        operation_id=operation_id,
        provider=selected_provider,
        model=selected_model,
        mode=generation_kind,
    )
    st.session_state.pipeline_recorder = recorder
    return recorder


def render_rag_configuration() -> None:
    store: LocalVectorStore | None = st.session_state.rag_store
    with st.sidebar:
        with st.expander("Documentación RAG"):
            if st.session_state.rag_store_error:
                st.error(st.session_state.rag_store_error)
                st.warning("El índice RAG fue creado con una configuración anterior y debe reconstruirse antes de utilizarlo.")
                rebuild_files = st.file_uploader("Documentos para reconstruir el índice", type=["txt", "pdf"], accept_multiple_files=True, disabled=generation_active, key="rag_rebuild_files")
                if st.button("Reconstruir índice", disabled=generation_active or not rebuild_files, width="stretch", key="rag_rebuild"):
                    target = Path(os.getenv("RAG_INDEX_PATH", ".indai_ma/rag_index")); target = target if target.is_absolute() else Path(__file__).resolve().parent / target
                    temp_path = Path(tempfile.mkdtemp(prefix="rag-rebuild-", dir=str(target.parent)))
                    recorder = PerformanceRecorder(uuid4().hex[:8], "lmstudio", get_embedding_model_name(), "rag_index")
                    st.session_state.pipeline_recorder = recorder
                    try:
                        embedding_model = get_embedding_model_name()
                        temp_store = LocalVectorStore(temp_path, embedding_model)
                        result = RAGService(LMStudioEmbeddingProvider(model=embedding_model), temp_store, recorder).ingest([(item.name, item.getvalue()) for item in rebuild_files])
                        verified = temp_store.reopen()
                        if verified.document_count != result.document_count or verified.chunk_count == 0: raise ValueError("La verificación del índice reconstruido ha fallado.")
                        target.mkdir(parents=True, exist_ok=True)
                        for name in ("chunks.jsonl", "documents.json", "manifest.json"): os.replace(str(temp_path / name), str(target / name))
                        st.session_state.rag_store = LocalVectorStore(target, embedding_model); st.session_state.rag_store_error = None; recorder.finish("completed")
                        st.session_state.rag_notice = ("success", f"Índice reconstruido: {result.document_count} documentos y {result.chunk_count} chunks.")
                    except Exception as error:
                        recorder.finish("failed"); st.session_state.rag_notice = ("error", f"No se pudo reconstruir el índice: {error}")
                    finally: shutil.rmtree(temp_path, ignore_errors=True)
                    st.rerun()
                return
            st.caption(f"Documentos indexados: {store.document_count}")
            st.caption(f"Chunks indexados: {store.chunk_count}")
            uploaded_files = st.file_uploader(
                "Documentos empresariales",
                type=["txt", "pdf"],
                accept_multiple_files=True,
                disabled=generation_active,
                help="Los documentos se procesan localmente mediante LM Studio.",
            )
            if st.button(
                "Indexar documentos",
                disabled=generation_active or not uploaded_files,
                width="stretch",
            ):
                operation_id = uuid4().hex[:8]
                embedding_model = get_embedding_model_name()
                recorder = PerformanceRecorder(
                    operation_id,
                    "lmstudio",
                    embedding_model,
                    "rag_index",
                )
                st.session_state.pipeline_recorder = recorder
                try:
                    service = RAGService(
                        LMStudioEmbeddingProvider(model=embedding_model),
                        store,
                        recorder,
                    )
                    result = service.ingest(
                        [(item.name, item.getvalue()) for item in uploaded_files]
                    )
                    st.session_state.rag_store = store.reopen()
                    recorder.finish("completed")
                    st.session_state.rag_notice = (
                        "success",
                        f"Indexados {result.document_count} documentos y "
                        f"{result.chunk_count} chunks. "
                        f"Duplicados omitidos: {result.duplicate_count}.",
                    )
                    st.rerun()
                except Exception as error:
                    recorder.finish("failed")
                    st.session_state.rag_notice = ("error", str(error))
                    st.rerun()
            index_recorder = st.session_state.pipeline_recorder
            if index_recorder is not None and index_recorder.mode == "rag_index":
                st.session_state.rag_index_snapshot = index_recorder.snapshot()
            if st.session_state.get("rag_index_snapshot") is not None:
                with st.expander("Diagnóstico de indexación RAG"):
                    render_pipeline_inspector(st.session_state.rag_index_snapshot)
            notice = st.session_state.rag_notice
            if notice:
                getattr(st, notice[0])(notice[1])
            st.toggle(
                "Usar documentación en Chat",
                key="rag_enabled",
                disabled=generation_active or store.chunk_count == 0,
                help="Recupera fragmentos locales antes de consultar al LLM.",
            )
            if store.chunk_count and st.button(
                "Vaciar índice RAG",
                disabled=generation_active,
                width="stretch",
            ):
                st.session_state.rag_clear_confirmation = True
                st.rerun()
            if st.session_state.rag_clear_confirmation:
                st.warning(
                    "Esta acción eliminará todos los documentos y embeddings "
                    "del índice RAG local."
                )
                confirm_column, cancel_column = st.columns(2)
                if confirm_column.button(
                    "Confirmar vaciado",
                    type="primary",
                    disabled=generation_active,
                ):
                    store.clear()
                    verified_empty = store.reopen()
                    if (
                        verified_empty.document_count
                        or verified_empty.chunk_count
                    ):
                        st.session_state.rag_notice = (
                            "error",
                            "No se ha podido verificar el vaciado del índice.",
                        )
                    else:
                        st.session_state.rag_store = verified_empty
                        st.session_state.rag_enabled = False
                        st.session_state.rag_notice = (
                            "info",
                            "Índice RAG vaciado de forma persistente.",
                        )
                    st.session_state.rag_clear_confirmation = False
                    st.rerun()
                if cancel_column.button("Cancelar"):
                    st.session_state.rag_clear_confirmation = False
                    st.rerun()


def start_generation(
    messages: list[dict[str, str]],
    generation_kind: str,
    operation_started_at: float | None = None,
    operation_id: str | None = None,
    recorder: PerformanceRecorder | None = None,
    options: GenerationOptions = CHAT_GENERATION_OPTIONS,
    prompt_already_recorded: bool = False,
) -> None:
    prompt_started_at = perf_counter()
    operation_started_at = operation_started_at or prompt_started_at
    approximate_prompt_chars = sum(
        len(str(message.get("content", ""))) for message in messages
    )
    operation_id = operation_id or uuid4().hex[:8]
    recorder = recorder or create_pipeline_recorder(
        operation_id,
        generation_kind,
    )
    if not prompt_already_recorded:
        recorder.record_stage(
            "prompt_build",
            duration_seconds=perf_counter() - operation_started_at,
            message_count=len(messages),
            approximate_prompt_chars=approximate_prompt_chars,
        )
    try:
        provider = get_llm_provider(
            selected_provider,
            selected_model,
            recorder=recorder,
            runtime_config=st.session_state.llm_runtime_config,
        )
        job = GenerationJob(
            provider=provider,
            messages=messages,
            timeout_seconds=(
                st.session_state.llm_runtime_config.timeout_seconds
            ),
            recorder=recorder,
            options=options,
        )
    except Exception:
        recorder.finish("failed")
        raise
    st.session_state.generation_job = job
    st.session_state.generation_kind = generation_kind
    st.session_state.generation_notice = None
    st.session_state.operation_started_at = operation_started_at
    st.session_state.operation_id = operation_id
    remember_execution_start(recorder)
    job.start()


def start_procurement_agent(
    request: str, orchestration_mode: str = "react_agent"
) -> None:
    operation_id = uuid4().hex[:8]
    generation_kind = {
        "deterministic": "procurement_deterministic",
        "planner_agent": "procurement_planner",
        "react_agent": "procurement_agent",
    }[orchestration_mode]
    recorder = create_pipeline_recorder(operation_id, generation_kind)
    recorder.record_stage(
        "prompt_build",
        message_count=1,
        approximate_prompt_chars=len(request),
        orchestration_mode=orchestration_mode,
    )
    try:
        provider = get_llm_provider(
            selected_provider,
            selected_model,
            recorder=recorder,
            runtime_config=st.session_state.llm_runtime_config,
        )
        if orchestration_mode == "deterministic":
            runner = ProcurementDeterministicWorkflow(
                ProviderSynthesisModel(provider), recorder=recorder
            )
        elif orchestration_mode == "planner_agent":
            runner = ProcurementAgentPlanner(
                ProviderPlannerModel(provider), recorder=recorder
            )
        else:
            runner = ProcurementAgent(
                ProviderDecisionModel(provider), recorder=recorder
            )
        job = AgentJob(
            agent=runner,
            request=request,
            timeout_seconds=st.session_state.llm_runtime_config.timeout_seconds,
            provider=provider,
        )
    except Exception:
        recorder.finish("failed")
        raise
    st.session_state.procurement_agent_result = None
    st.session_state.procurement_agent_metadata = None
    st.session_state.generation_job = job
    st.session_state.generation_kind = generation_kind
    st.session_state.generation_notice = None
    st.session_state.operation_started_at = perf_counter()
    st.session_state.operation_id = operation_id
    remember_execution_start(recorder)
    job.start()


def format_provider_name(provider_name: str | None) -> str:
    names = {"lmstudio": "LM Studio", "openai": "OpenAI"}
    return names.get(provider_name or "", provider_name or "Proveedor desconocido")


def format_response_metadata(
    elapsed_seconds: float,
    provider_name: str | None,
    model_name: str | None,
) -> str:
    return (
        f"{elapsed_seconds:.1f} s · {format_provider_name(provider_name)} · "
        f"{model_name or 'Modelo desconocido'}"
    )


def format_interrupted_generation(
    message: str,
    result: GenerationResult,
) -> str:
    if result.elapsed_seconds is None:
        return message
    return f"{message} Tiempo transcurrido: {result.elapsed_seconds:.1f} s."


def render_tool_executions(tool_executions: list[dict]) -> None:
    if not tool_executions:
        return
    with st.expander("Detalles técnicos de herramientas"):
        for execution in tool_executions:
            arguments = execution.get("arguments", {})
            result = execution.get("result", {})
            if not isinstance(result, dict):
                result = {
                    "position_gwh": result,
                    "interpretation": execution.get("interpretation", ""),
                }
            name = execution.get("name", "tool")
            duration_ms = execution.get("elapsed_seconds", 0) * 1000
            st.markdown(f"🔧 **{name}**")

            if name == "calculate_supply_position":
                interpretation = str(result.get("interpretation", ""))
                details = (
                    f"Demanda: {arguments.get('expected_demand_gwh', 0):g} GWh · "
                    f"Suministro: {arguments.get('contracted_supply_gwh', 0):g} GWh · "
                    f"Posición: {result.get('position_gwh', 0):g} GWh "
                    f"({POSITION_LABELS.get(interpretation, interpretation)})"
                )
            elif name == "calculate_margin":
                details = (
                    f"Venta: {arguments.get('sales_price_eur_mwh', 0):g} €/MWh · "
                    f"Coste: {arguments.get('supply_cost_eur_mwh', 0):g} €/MWh · "
                    f"Volumen: {arguments.get('volume_gwh', 0):g} GWh · "
                    f"Margen: {result.get('margin_eur_mwh', 0):g} €/MWh · "
                    f"Margen total: {result.get('total_margin_eur', 0):,.2f} €"
                )
            elif name == "calculate_demand_scenario":
                details = (
                    f"Demanda base: {result.get('base_demand_gwh', 0):g} GWh · "
                    f"Variación: {result.get('variation_percent', 0):g} % · "
                    f"Demanda escenario: {result.get('scenario_demand_gwh', 0):g} GWh"
                )
            elif name == "calculate_spot_exposure":
                details = (
                    f"Demanda: {arguments.get('expected_demand_gwh', 0):g} GWh · "
                    f"Suministro: {arguments.get('contracted_supply_gwh', 0):g} GWh · "
                    f"Posición corta: {result.get('short_position_gwh', 0):g} GWh · "
                    f"Spot: {result.get('spot_price_eur_mwh', 0):g} €/MWh · "
                    f"Exposición: {result.get('exposure_eur', 0):,.2f} €"
                )
            else:
                details = f"Parámetros: {arguments} · Resultado: {result}"
            st.caption(f"{details} · Tiempo de ejecución: {duration_ms:.3f} ms")


def render_response_copy_actions(
    response,
    tool_executions: list[dict],
    *,
    key: str,
    operation_id: str | None = None,
) -> None:
    view = find_execution(st.session_state.execution_views, operation_id) if operation_id else visible_execution()
    if view is None or view.result is None:
        render_clipboard_button(_redact_text(build_response_clipboard_text(response)), "Copiar respuesta", key=f"{key}-response")
        return
    payloads = clipboard_payloads(view)
    for action, label in (("response", "Copiar respuesta"), ("diagnostics", "Copiar diagnóstico"), ("all", "Copiar todo")):
        render_clipboard_button(payloads[action], label, key=f"{key}-{action}-{view.operation_id}")


def render_rag_sources(sources: list[dict]) -> None:
    if not sources:
        return
    with st.expander("Fuentes consultadas"):
        for source in sources:
            page_start = source.get("page_start")
            page_end = source.get("page_end")
            page_label = (
                str(page_start)
                if page_start == page_end
                else f"{page_start}-{page_end}"
            )
            st.markdown(f"**{source.get('document_name', 'Documento')}**")
            st.caption(
                f"Sección: {source.get('section') or 'Sin sección'} · "
                f"Página: {page_label}"
            )
            if source.get("excerpt"):
                st.write(source["excerpt"])


def render_chat() -> None:
    latest_assistant_index = next(
        (
            index
            for index in range(len(st.session_state.messages) - 1, -1, -1)
            if st.session_state.messages[index]["role"] == "assistant"
        ),
        None,
    )
    for index, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if index == latest_assistant_index:
                render_response_copy_actions(
                    message["content"],
                    message.get("tool_executions", []),
                    key=f"chat-{index}",
                    operation_id=message.get("operation_id"),
                )
            render_tool_executions(message.get("tool_executions", []))
            render_rag_sources(message.get("rag_sources", []))
            if message["role"] == "assistant" and message.get(
                "response_time_seconds"
            ) is not None:
                st.caption(
                    format_response_metadata(
                        message["response_time_seconds"],
                        message.get("provider"),
                        message.get("model"),
                    )
                )

    user_message = st.chat_input(
        "Escribe tu consulta",
        disabled=selected_model is None or generation_active,
    )

    if user_message:
        st.session_state.messages.append(
            {"role": "user", "content": user_message}
        )

        with st.chat_message("user"):
            st.write(user_message)

        try:
            if st.session_state.rag_enabled:
                operation_id = uuid4().hex[:8]
                recorder = create_pipeline_recorder(operation_id, "rag_chat")
                store: LocalVectorStore = st.session_state.rag_store
                service = RAGService(
                    LMStudioEmbeddingProvider(), store, recorder
                )
                retrieval = service.retrieve(user_message)
                st.session_state.rag_pending_sources = [
                    {
                        **item.source_dict(),
                        "excerpt": item.chunk.text[:320],
                    }
                    for item in retrieval.matches
                ]
                start_generation(
                    build_rag_messages(st.session_state.messages, retrieval),
                    "rag_chat",
                    operation_id=operation_id,
                    recorder=recorder,
                    options=RAG_CHAT_GENERATION_OPTIONS,
                    prompt_already_recorded=True,
                )
            else:
                start_generation(st.session_state.messages, "chat")
            st.rerun()
        except Exception as error:
            recorder = st.session_state.pipeline_recorder
            if recorder is not None and recorder.mode == "rag_chat":
                recorder.finish("failed")
            st.error(str(error))


def render_gas_analysis_result(analysis: ScenarioAnalysis) -> None:
    st.subheader("Resultado del análisis")
    metadata = st.session_state.gas_analysis_metadata
    if metadata is not None:
        st.caption(
            "Tiempo total del análisis: "
            + format_response_metadata(
                metadata["response_time_seconds"],
                metadata.get("provider"),
                metadata.get("model"),
            )
        )
    scenario_rows = [
        {
            "Escenario": item.name,
            "Demanda (GWh)": item.demand_gwh,
            "Posición (GWh)": item.supply_position_gwh,
            "Short (GWh)": item.short_position_gwh,
            "Precio spot (€/MWh)": item.spot_price_eur_mwh,
            "Exposición spot (€)": item.spot_exposure_eur,
            "Margen estimado (€)": item.estimated_margin_eur,
        }
        for item in analysis.scenarios
    ]
    st.dataframe(scenario_rows, hide_index=True, width="stretch")
    st.subheader("Resumen")
    st.write(analysis.summary)
    st.subheader("Riesgos clave")
    for risk in analysis.key_risks:
        st.markdown(f"- {risk}")
    st.subheader("Recomendación")
    st.write(analysis.recommendation)
    render_response_copy_actions(
        analysis,
        (metadata or {}).get("tool_executions", []),
        key="gas-analysis",
        operation_id=(metadata or {}).get("operation_id"),
    )


def render_gas_analysis() -> None:
    st.subheader("Gas B2B Portfolio Analysis")
    st.write(
        "Describe la demanda, los contratos, los segmentos de clientes y los "
        "riesgos conocidos de la cartera multigás."
    )
    portfolio_description = st.text_area(
        "Información de la cartera",
        height=220,
        placeholder=(
            "Ejemplo: demanda industrial de gas natural de 120 GWh, suministro "
            "contratado de 95 GWh, clientes hospitalarios con contratos fijos..."
        ),
    )
    if st.button(
        "Analizar cartera",
        type="primary",
        disabled=selected_model is None or generation_active,
    ):
        operation_started_at = perf_counter()
        operation_id = uuid4().hex[:8]
        st.session_state.gas_analysis_result = None
        st.session_state.gas_analysis_metadata = None
        st.session_state.gas_documentary_result = None
        st.session_state.gas_documentary_sources = []
        st.session_state.gas_precomputed_tool_executions = []
        st.session_state.gas_scenarios = []
        st.session_state.rag_pending_sources = []
        st.session_state.gas_response_contract = None
        recorder = create_pipeline_recorder(operation_id, "gas_analysis")
        try:
            store: LocalVectorStore = st.session_state.rag_store
            rag_service = (
                RAGService(LMStudioEmbeddingProvider(), store, recorder)
                if store is not None and store.chunk_count
                else None
            )
            prepared_query = prepare_gas_query(
                portfolio_description, recorder, rag_service
            )
            st.session_state.gas_response_contract = (
                prepared_query.response_contract
            )
            if prepared_query.quantitative_analysis is not None:
                st.session_state.gas_precomputed_tool_executions = (
                    prepared_query.quantitative_analysis.tool_executions
                )
                st.session_state.gas_scenarios = (
                    prepared_query.quantitative_analysis.scenarios
                )
            st.session_state.rag_pending_sources = [
                {
                    **item.source_dict(),
                    "excerpt": item.chunk.text[:320],
                }
                for item in prepared_query.retrieval.matches
            ]
            generation_kind = (
                "gas_documentary"
                if prepared_query.intent.intent == "documentary"
                else "gas_analysis"
            )
            start_generation(
                prepared_query.messages,
                generation_kind,
                operation_started_at,
                operation_id,
                recorder=recorder,
                options=GAS_ANALYSIS_GENERATION_OPTIONS,
                prompt_already_recorded=True,
            )
            st.rerun()
        except (GasAnalysisError, ValueError) as error:
            recorder.finish("failed")
            st.error(str(error))

    if st.session_state.gas_analysis_result is not None:
        render_gas_analysis_result(st.session_state.gas_analysis_result)
    if st.session_state.gas_documentary_result is not None:
        st.subheader("Resultado del análisis")
        st.write(st.session_state.gas_documentary_result)
        render_response_copy_actions(
            st.session_state.gas_documentary_result,
            [],
            key="gas-documentary",
        )
        render_rag_sources(st.session_state.gas_documentary_sources)


def render_procurement_agent() -> None:
    st.subheader("ProcurementAgent")
    with st.expander("Benchmark de estrategias de Procurement"):
        render_benchmark_history()
    st.write(
        "El agente decide qué cálculos deterministas necesita para evaluar "
        "la cobertura de suministro y la exposición al mercado."
    )
    orchestration_label = st.radio(
        "Estrategia de orquestación",
        ("Deterministic", "Planner Agent", "ReAct Agent"),
        horizontal=True,
        key="procurement_orchestration_mode",
        disabled=generation_active,
    )
    st.caption("La orquestación Deterministic usa herramientas predeterminadas y una síntesis LLM; no significa cero llamadas LLM.")
    orchestration_mode = {
        "Deterministic": "deterministic",
        "Planner Agent": "planner_agent",
        "ReAct Agent": "react_agent",
    }[orchestration_label]
    request = st.text_area(
        "Posición de aprovisionamiento",
        height=220,
        placeholder=(
            "Ejemplo: demanda prevista de gas natural de 120 GWh, suministro "
            "contratado de 95 GWh y precio spot de 42 EUR/MWh."
        ),
        key="procurement_agent_request",
    )
    if st.button(
        "Ejecutar ProcurementAgent",
        type="primary",
        disabled=selected_model is None or generation_active or not request.strip(),
    ):
        try:
            start_procurement_agent(request, orchestration_mode)
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    if st.session_state.procurement_agent_result is not None:
        view = visible_execution()
        if view:
            render_execution_header(view)
        st.subheader("Resultado del agente")
        st.write(st.session_state.procurement_agent_result)
        metadata = st.session_state.procurement_agent_metadata or {}
        render_response_copy_actions(
            st.session_state.procurement_agent_result,
            metadata.get("tool_executions", []),
            key="procurement-agent",
            operation_id=metadata.get("operation_id"),
        )
        render_tool_executions(metadata.get("tool_executions", []))


def render_commercial_agent() -> None:
    st.subheader("CommercialAgent")
    st.write("Analiza contratos, flexibilidad, excesos y condiciones comerciales con evidencia documental.")
    request = st.text_area("Consulta comercial", key="commercial_request", height=150,
        placeholder="El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes. Analiza las implicaciones comerciales según su contrato.")
    interpretation = st.radio("Interpretación", ("deterministic", "llm"),
        format_func=lambda mode: {"deterministic":"Determinista", "llm":"LLM"}[mode],
        key="commercial_interpretation", horizontal=True, disabled=generation_active)
    if interpretation == "deterministic":
        st.caption("No utiliza generación LLM. El modelo de embeddings sigue siendo necesario para recuperar el contrato.")
    store = st.session_state.rag_store
    if store is None or store.chunk_count == 0:
        st.info("Indexa el contrato en Documentación RAG para realizar el análisis comercial.")
    if st.button("Analizar contrato", key="commercial_start", type="primary",
                 disabled=generation_active or not request.strip() or (interpretation == "llm" and selected_model is None)
                 or store is None or store.chunk_count == 0):
        operation_id = uuid4().hex[:8]
        recorder = PerformanceRecorder(operation_id, selected_provider if interpretation == "llm" else "deterministic",
            selected_model if interpretation == "llm" else "none", "commercial_agent")
        st.session_state.pipeline_recorder = recorder
        try:
            provider = (get_llm_provider(selected_provider, selected_model, recorder=recorder,
                                       runtime_config=st.session_state.llm_runtime_config) if interpretation == "llm" else None)
            service = RAGService(LMStudioEmbeddingProvider(), store, recorder)
            runner = CommercialAgent(ProviderCommercialModel(provider) if provider else None, service, recorder,
                                     interpretation_mode=interpretation)
            job = AgentJob(runner, request, st.session_state.llm_runtime_config.timeout_seconds, provider)
            st.session_state.commercial_result = None
            st.session_state.commercial_operation_id = operation_id
            st.session_state.generation_job = job
            st.session_state.generation_kind = "commercial_agent"
            st.session_state.generation_notice = None
            st.session_state.operation_started_at = perf_counter()
            st.session_state.operation_id = operation_id
            remember_execution_start(recorder)
            job.start()
        except Exception as error:
            recorder.finish("failed")
            st.error(str(error))
        else:
            st.rerun()
    if st.session_state.commercial_result is not None:
        view = visible_execution()
        if view:
            render_execution_header(view)
        commercial = CommercialAgentResult.model_validate(st.session_state.commercial_result)
        render_commercial_result(commercial)
        render_response_copy_actions(commercial_text(commercial), commercial.tool_executions,
            key="commercial", operation_id=st.session_state.commercial_operation_id)


def render_supervisor() -> None:
    st.subheader("Multi-Agent Supervisor")
    st.write("Selecciona y ejecuta los especialistas necesarios para tu consulta.")
    st.caption("Routing determinista primero. El modelo de generación solo interviene ante ambigüedad o si activas la priorización LLM.")
    request = st.text_area("Consulta al Supervisor", key="supervisor_request", height=180)
    use_synthesis = st.checkbox("Priorizar resultados con LLM (una llamada adicional)", key="supervisor_synthesis",
                                disabled=generation_active)
    if st.button("Ejecutar Supervisor", key="supervisor_start", type="primary",
                 disabled=generation_active or not request.strip() or (use_synthesis and selected_model is None)):
        operation_id = uuid4().hex[:8]
        recorder = PerformanceRecorder(operation_id, selected_provider if selected_model else "deterministic",
                                       selected_model or "none", "supervisor")
        st.session_state.pipeline_recorder = recorder
        runtime = st.session_state.llm_runtime_config
        store = st.session_state.rag_store
        provider_name, model_name = selected_provider, selected_model
        def provider_factory(child_recorder):
            return get_llm_provider(provider_name, model_name, recorder=child_recorder, runtime_config=runtime)
        def rag_factory(child_recorder):
            if store is None or not store.chunk_count:
                raise ValueError("Indexa el contrato en Documentación RAG.")
            return RAGService(LMStudioEmbeddingProvider(), store, child_recorder)
        runner = Supervisor(recorder, provider_factory if model_name is not None else None,
                            rag_factory, use_llm_synthesis=use_synthesis)
        job = AgentJob(runner, request, runtime.timeout_seconds, runner)
        st.session_state.supervisor_result = None
        st.session_state.supervisor_operation_id = operation_id
        st.session_state.generation_job = job
        st.session_state.generation_kind = "supervisor"
        st.session_state.generation_notice = None
        st.session_state.operation_started_at = perf_counter()
        st.session_state.operation_id = operation_id
        remember_execution_start(recorder)
        job.start()
        st.rerun()
    if st.session_state.supervisor_result is not None:
        view = visible_execution()
        if view:
            render_execution_header(view)
        result = SupervisorResult.model_validate(st.session_state.supervisor_result)
        render_supervisor_result(result)
        render_response_copy_actions(supervisor_text(result), result.tool_executions, key="supervisor",
                                     operation_id=st.session_state.supervisor_operation_id)


def render_risk_agent() -> None:
    st.subheader("RiskAgent")
    st.write("Compara demanda, cobertura y exposición spot en la base y los escenarios solicitados.")
    request = st.text_area("Consulta de riesgo", key="risk_request", height=170,
        placeholder="Demanda de 4,8 GWh, suministro contratado de 4,3 GWh y precio spot de 42 EUR/MWh. Analiza escenarios +10 % y +20 %.")
    use_llm = st.checkbox("Priorizar hallazgos con LLM (una llamada)", value=False,
                          disabled=generation_active, key="risk_use_llm")
    if not use_llm:
        st.caption("Interpretación determinista: el modelo de generación seleccionado no interviene.")
    if st.button("Analizar riesgo", key="risk_start", type="primary",
                 disabled=generation_active or not request.strip() or (use_llm and selected_model is None)):
        operation_id = uuid4().hex[:8]
        recorder = PerformanceRecorder(operation_id,
            selected_provider if use_llm else "deterministic",
            selected_model if use_llm else "none", "risk_agent")
        st.session_state.pipeline_recorder = recorder
        try:
            provider = (get_llm_provider(selected_provider, selected_model, recorder=recorder,
                        runtime_config=st.session_state.llm_runtime_config) if use_llm else None)
            runner = RiskAgent(ProviderRiskModel(provider) if provider else None, recorder)
            job = AgentJob(runner, request, st.session_state.llm_runtime_config.timeout_seconds, provider)
            st.session_state.risk_result = None
            st.session_state.risk_operation_id = operation_id
            st.session_state.generation_job = job
            st.session_state.generation_kind = "risk_agent"
            st.session_state.generation_notice = None
            st.session_state.operation_started_at = perf_counter()
            st.session_state.operation_id = operation_id
            remember_execution_start(recorder)
            job.start()
        except Exception as error:
            recorder.finish("failed")
            st.error(str(error))
        else:
            st.rerun()
    if st.session_state.risk_result is not None:
        view = visible_execution()
        if view:
            render_execution_header(view)
        risk = RiskAgentResult.model_validate(st.session_state.risk_result)
        render_risk_result(risk)
        render_response_copy_actions(risk_text(risk), risk.tool_executions, key="risk",
                                     operation_id=st.session_state.risk_operation_id)


def finish_generation(result: GenerationResult) -> None:
    generation_kind = st.session_state.generation_kind
    operation_id = st.session_state.operation_id
    precomputed_tool_executions = (
        st.session_state.gas_precomputed_tool_executions
    )
    scenarios = st.session_state.gas_scenarios
    rag_sources = st.session_state.rag_pending_sources
    response_contract = st.session_state.gas_response_contract
    st.session_state.generation_job = None
    st.session_state.generation_kind = None

    def log_operation_total(status: str = result.status.value) -> None:
        recorder: PerformanceRecorder | None = st.session_state.pipeline_recorder
        if recorder is not None and recorder.operation_id == operation_id:
            operation_status = result.execution_status or status
            recorder.finish(
                "completed"
                if operation_status == GenerationStatus.COMPLETED.value
                else operation_status
            )
        prior = find_execution(st.session_state.execution_views, operation_id)
        if prior and recorder and recorder.operation_id == operation_id:
            domain = result.domain_result
            text = result.content or result.error or ""
            tools = list(result.tool_executions)
            if generation_kind == "commercial_agent" and domain is not None:
                text = commercial_text(domain)
            elif generation_kind == "risk_agent" and domain is not None:
                text = risk_text(domain)
            elif generation_kind == "supervisor" and domain is not None:
                text = supervisor_text(domain)
            elif generation_kind in {"gas_analysis", "gas_documentary"}:
                domain = st.session_state.gas_analysis_result or st.session_state.gas_documentary_result
                text = build_response_clipboard_text(domain) if domain is not None else text
                tools = [*precomputed_tool_executions, *tools]
            elif generation_kind in {"chat", "rag_chat"} and st.session_state.messages:
                latest = st.session_state.messages[-1]
                if latest.get("operation_id") == operation_id:
                    text = latest["content"]
            st.session_state.execution_views[operation_id] = ExecutionView.capture(
                prior.mode, domain if domain is not None else text, prior.configuration, recorder.snapshot(), text, tools)
        st.session_state.operation_started_at = None
        st.session_state.operation_id = None
        st.session_state.gas_precomputed_tool_executions = []
        st.session_state.gas_scenarios = []
        st.session_state.rag_pending_sources = []
        st.session_state.gas_response_contract = None

    if result.status == GenerationStatus.COMPLETED:
        if generation_kind == "supervisor":
            st.session_state.supervisor_result = result.structured_result
        elif generation_kind == "risk_agent":
            st.session_state.risk_result = result.structured_result
        elif generation_kind == "commercial_agent":
            st.session_state.commercial_result = result.structured_result
        elif generation_kind in {
            "procurement_agent",
            "procurement_planner",
            "procurement_deterministic",
        }:
            recorder = st.session_state.pipeline_recorder
            operation_metrics = (
                build_operation_metrics(recorder.snapshot())
                if recorder is not None
                else None
            )
            st.session_state.procurement_agent_result = result.content or ""
            st.session_state.procurement_agent_metadata = {
                "operation_id": operation_id,
                "response_time_seconds": result.elapsed_seconds,
                "provider": result.provider_name,
                "model": result.model_name,
                "tool_executions": result.tool_executions,
                "llm_call_count": (
                    operation_metrics.llm_call_count if operation_metrics else 0
                ),
                "orchestration_mode": {
                    "procurement_agent": "ReAct Agent",
                    "procurement_planner": "Planner Agent",
                    "procurement_deterministic": "Deterministic",
                }[generation_kind],
            }
        elif generation_kind in {"chat", "rag_chat"}:
            response_content = result.content or ""
            if generation_kind == "rag_chat":
                response_content = sanitize_rag_citations(
                    response_content, rag_sources
                )
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "operation_id": operation_id,
                    "content": response_content,
                    "response_time_seconds": result.elapsed_seconds,
                    "provider": result.provider_name,
                    "model": result.model_name,
                    "tool_executions": result.tool_executions,
                    "rag_sources": (
                        rag_sources if generation_kind == "rag_chat" else []
                    ),
                }
            )
        else:
            recorder: PerformanceRecorder = st.session_state.pipeline_recorder
            try:
                if response_contract is None:
                    raise GasAnalysisError(
                        "No se ha definido el contrato de salida del análisis."
                    )
                handled = handle_gas_response(
                    GasResponseContract(response_contract),
                    result.content or "",
                    recorder,
                    scenarios,
                    rag_sources,
                )
                if handled.contract in {
                    GasResponseContract.DOCUMENT_TEXT,
                    GasResponseContract.QUANTITATIVE_TEXT,
                }:
                    st.session_state.gas_documentary_result = (
                        handled.documentary_content
                    )
                    st.session_state.gas_documentary_sources = rag_sources
                else:
                    st.session_state.gas_analysis_result = (
                        handled.scenario_analysis
                    )
                    st.session_state.gas_analysis_metadata = {
                        "operation_id": operation_id,
                        "response_time_seconds": result.elapsed_seconds,
                        "provider": result.provider_name,
                        "model": result.model_name,
                        "tool_executions": [
                            *precomputed_tool_executions,
                            *result.tool_executions,
                        ],
                    }
            except GasAnalysisError as error:
                st.session_state.generation_notice = ("error", str(error))
                log_operation_total("response_handling_error")
                return
        log_operation_total()
        return

    if result.status == GenerationStatus.CANCELLED:
        st.session_state.generation_notice = (
            "info",
            format_interrupted_generation("Generación cancelada.", result),
        )
    elif result.status == GenerationStatus.TIMED_OUT:
        st.session_state.generation_notice = (
            "warning",
            format_interrupted_generation(
                "La generación superó el tiempo límite y fue cancelada.",
                result,
            ),
        )
    else:
        st.session_state.generation_notice = (
            "error",
            format_interrupted_generation(
                result.error or "El proveedor no pudo completar la generación.",
                result,
            ),
        )
    log_operation_total()


@st.fragment(run_every=0.5)
def render_generation_status() -> None:
    job: GenerationJob | None = st.session_state.generation_job
    if job is None:
        notice = st.session_state.generation_notice
        if notice is not None and st.session_state.get("generation_notice_mode", selected_mode) == selected_mode:
            level, message = notice
            getattr(st, level)(message)
        return

    result = job.poll()
    if result.status == GenerationStatus.RUNNING:
        if job.cancel_requested:
            if job.cancel_reason == GenerationStatus.TIMED_OUT:
                st.warning("Cancelando la generación por timeout...")
            else:
                st.info("Cancelando la generación...")
        else:
            st.info("Ejecución en curso...")
        return

    finish_generation(result)
    st.rerun()


@st.fragment(run_every=0.5)
def render_pipeline_panel() -> None:
    view = visible_execution()
    render_pipeline_inspector(view.snapshot if view else None)


render_rag_configuration()
inject_pipeline_styles()
main_column, inspector_column = st.columns([2.15, 1], gap="large", wrap=True)
with main_column:
    if selected_mode == "Evaluation":
        from evals.ui import render_evaluation
        render_evaluation()
    elif selected_mode == "Chat":
        render_chat()
    elif selected_mode == "Gas B2B Portfolio Analysis":
        render_gas_analysis()
    elif selected_mode == "CommercialAgent":
        render_commercial_agent()
    elif selected_mode == "Multi-Agent Supervisor":
        render_supervisor()
    elif selected_mode == "RiskAgent":
        render_risk_agent()
    else:
        render_procurement_agent()

    render_generation_status()

with inspector_column:
    render_pipeline_panel()
