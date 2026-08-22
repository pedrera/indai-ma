import streamlit as st
from time import perf_counter
from uuid import uuid4

from diagnostics import get_performance_logger
from gas_analysis import (
    GasAnalysisError,
    GasB2BPortfolioAnalysis,
    build_gas_analysis_messages,
    parse_gas_portfolio_analysis,
)
from generation import (
    GenerationJob,
    GenerationResult,
    GenerationStatus,
    get_generation_timeout_seconds,
)
from llm_client import (
    get_available_models,
    get_default_model_name,
    get_default_provider_name,
    get_llm_provider,
    get_supported_providers,
)


logger = get_performance_logger()

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


st.set_page_config(page_title="indAI MA", page_icon="🏭")

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


def start_generation(
    messages: list[dict[str, str]],
    generation_kind: str,
    operation_started_at: float | None = None,
    operation_id: str | None = None,
) -> None:
    prompt_started_at = perf_counter()
    operation_started_at = operation_started_at or prompt_started_at
    provider = get_llm_provider(selected_provider, selected_model)
    approximate_prompt_chars = sum(
        len(str(message.get("content", ""))) for message in messages
    )
    operation_id = operation_id or uuid4().hex[:8]
    logger.info(
        "stage=prompt_build operation_id=%s mode=%s provider=%s model=%s "
        "duration_seconds=%.4f message_count=%d approximate_prompt_chars=%d",
        operation_id,
        generation_kind,
        selected_provider,
        selected_model,
        perf_counter() - prompt_started_at,
        len(messages),
        approximate_prompt_chars,
    )
    job = GenerationJob(
        provider=provider,
        messages=messages,
        timeout_seconds=get_generation_timeout_seconds(),
    )
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
        arguments = execution["arguments"]
        position_label = POSITION_LABELS.get(
            execution["interpretation"],
            execution["interpretation"],
        )
        st.markdown(f"🔧 **{execution['name']}**")
        st.caption(
            f"Demanda: {arguments['expected_demand_gwh']:g} GWh · "
            f"Suministro: {arguments['contracted_supply_gwh']:g} GWh · "
            f"Resultado: {execution['result']:g} GWh "
            f"({position_label}) · "
            f"Tiempo de ejecución: {execution['elapsed_seconds'] * 1000:.3f} ms"
        )


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


def render_gas_analysis_result(analysis: GasB2BPortfolioAnalysis) -> None:
    st.subheader("Resultado del análisis")
    st.write(analysis.summary)
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
        render_tool_executions(metadata.get("tool_executions", []))

    total_demand = sum(item.demand_gwh for item in analysis.portfolio)
    total_short_position = sum(
        item.expected_short_position_gwh for item in analysis.portfolio
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Demanda total", f"{total_demand:,.2f} GWh")
    metric_columns[1].metric(
        "Posición corta esperada",
        f"{total_short_position:,.2f} GWh",
    )
    metric_columns[2].metric(
        "Riesgo de margen",
        RISK_LEVEL_LABELS.get(
            analysis.margin_risk.value,
            analysis.margin_risk.value,
        ),
    )

    portfolio_rows = [
        {
            "Gas": GAS_TYPE_LABELS.get(item.gas_type.value, item.gas_type.value),
            "Demanda (GWh)": item.demand_gwh,
            "Suministro contratado (GWh)": item.contracted_supply_gwh,
            "Posición corta (GWh)": item.expected_short_position_gwh,
            "Riesgo de suministro": RISK_LEVEL_LABELS.get(
                item.supply_risk.value,
                item.supply_risk.value,
            ),
            "Riesgo de precio": RISK_LEVEL_LABELS.get(
                item.price_risk.value,
                item.price_risk.value,
            ),
        }
        for item in analysis.portfolio
    ]
    st.dataframe(portfolio_rows, hide_index=True, width="stretch")

    st.subheader("Segmentos afectados")
    st.write(", ".join(analysis.affected_customer_segments))

    st.subheader("Impacto comercial")
    st.write(analysis.commercial_impact)

    st.subheader("Acciones recomendadas")
    for action in analysis.recommended_actions:
        st.markdown(f"- {action}")


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
        try:
            messages = build_gas_analysis_messages(portfolio_description)
            start_generation(
                messages,
                "gas_analysis",
                operation_started_at,
                operation_id,
            )
            st.rerun()
        except (GasAnalysisError, ValueError) as error:
            st.error(str(error))

    if st.session_state.gas_analysis_result is not None:
        render_gas_analysis_result(st.session_state.gas_analysis_result)


def finish_generation(result: GenerationResult) -> None:
    generation_kind = st.session_state.generation_kind
    operation_started_at = st.session_state.operation_started_at
    operation_id = st.session_state.operation_id
    st.session_state.generation_job = None
    st.session_state.generation_kind = None

    def log_operation_total(status: str = result.status.value) -> None:
        logger.info(
            "stage=operation_total operation_id=%s mode=%s provider=%s model=%s "
            "status=%s duration_seconds=%.4f",
            operation_id,
            generation_kind,
            selected_provider,
            selected_model,
            status,
            perf_counter() - operation_started_at,
        )
        st.session_state.operation_started_at = None
        st.session_state.operation_id = None

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
            try:
                st.session_state.gas_analysis_result = (
                    parse_gas_portfolio_analysis(result.content or "")
                )
                st.session_state.gas_analysis_metadata = {
                    "response_time_seconds": result.elapsed_seconds,
                    "provider": result.provider_name,
                    "model": result.model_name,
                    "tool_executions": result.tool_executions,
                }
            except GasAnalysisError as error:
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


if selected_mode == "Chat":
    render_chat()
else:
    render_gas_analysis()

render_generation_status()
