import streamlit as st
from time import perf_counter
from uuid import uuid4

from diagnostics import PerformanceRecorder
from gas_analysis import (
    GasAnalysisError,
    ScenarioAnalysis,
    parse_scenario_analysis,
    prepare_gas_analysis,
)
from generation import (
    GenerationJob,
    GenerationResult,
    GenerationStatus,
)
from llm_client import (
    CHAT_GENERATION_OPTIONS,
    GAS_ANALYSIS_GENERATION_OPTIONS,
    GenerationOptions,
    get_available_models,
    get_default_model_name,
    get_default_provider_name,
    get_llm_provider,
    get_supported_providers,
)
from pipeline_inspector import inject_pipeline_styles, render_pipeline_inspector
from runtime_config import LLMRuntimeConfig


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

if "messages" not in st.session_state:
    st.session_state.messages = []
if "gas_analysis_result" not in st.session_state:
    st.session_state.gas_analysis_result = None
if "gas_analysis_metadata" not in st.session_state:
    st.session_state.gas_analysis_metadata = None
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
        ("Chat", "Gas B2B Portfolio Analysis"),
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


def render_chat() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            render_tool_executions(message.get("tool_executions", []))
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
            start_generation(st.session_state.messages, "chat")
            st.rerun()
        except ValueError as error:
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
        st.session_state.gas_precomputed_tool_executions = []
        st.session_state.gas_scenarios = []
        recorder = create_pipeline_recorder(operation_id, "gas_analysis")
        try:
            prepared = prepare_gas_analysis(
                portfolio_description,
                recorder,
            )
            st.session_state.gas_precomputed_tool_executions = (
                prepared.tool_executions
            )
            st.session_state.gas_scenarios = prepared.scenarios
            start_generation(
                prepared.messages,
                "gas_analysis",
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


def finish_generation(result: GenerationResult) -> None:
    generation_kind = st.session_state.generation_kind
    operation_id = st.session_state.operation_id
    precomputed_tool_executions = (
        st.session_state.gas_precomputed_tool_executions
    )
    scenarios = st.session_state.gas_scenarios
    st.session_state.generation_job = None
    st.session_state.generation_kind = None

    def log_operation_total(status: str = result.status.value) -> None:
        recorder: PerformanceRecorder | None = st.session_state.pipeline_recorder
        if recorder is not None and recorder.operation_id == operation_id:
            recorder.finish(
                "completed"
                if status == GenerationStatus.COMPLETED.value
                else status
            )
        st.session_state.operation_started_at = None
        st.session_state.operation_id = None
        st.session_state.gas_precomputed_tool_executions = []
        st.session_state.gas_scenarios = []

    if result.status == GenerationStatus.COMPLETED:
        if generation_kind == "chat":
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "response_time_seconds": result.elapsed_seconds,
                    "provider": result.provider_name,
                    "model": result.model_name,
                    "tool_executions": result.tool_executions,
                }
            )
        else:
            recorder: PerformanceRecorder = st.session_state.pipeline_recorder
            try:
                st.session_state.gas_analysis_result = (
                    parse_scenario_analysis(
                        result.content or "",
                        scenarios,
                        recorder,
                    )
                )
                final_event = recorder.start_stage(
                    "final_response",
                    response_chars=len(result.content or ""),
                )
                recorder.complete_stage(final_event)
                st.session_state.gas_analysis_metadata = {
                    "response_time_seconds": result.elapsed_seconds,
                    "provider": result.provider_name,
                    "model": result.model_name,
                    "tool_executions": [
                        *precomputed_tool_executions,
                        *result.tool_executions,
                    ],
                }
            except GasAnalysisError as error:
                final_event = recorder.start_stage("final_response")
                recorder.fail_stage(
                    final_event,
                    error_type="parse_or_validation_error",
                )
                st.session_state.generation_notice = ("error", str(error))
                log_operation_total("parse_or_validation_error")
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
        if notice is not None:
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
            st.info("El modelo está trabajando...")
        return

    finish_generation(result)
    st.rerun()


@st.fragment(run_every=0.5)
def render_pipeline_panel() -> None:
    recorder: PerformanceRecorder | None = st.session_state.pipeline_recorder
    render_pipeline_inspector(recorder.snapshot() if recorder else None)


inject_pipeline_styles()
main_column, inspector_column = st.columns([2.15, 1], gap="large", wrap=True)
with main_column:
    if selected_mode == "Chat":
        render_chat()
    else:
        render_gas_analysis()

    render_generation_status()

with inspector_column:
    render_pipeline_panel()
