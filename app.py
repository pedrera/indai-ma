import streamlit as st

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


st.set_page_config(page_title="indAI MA", page_icon="🏭")

st.title("indAI MA")
st.caption("Asistente para el sector industrial")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "gas_analysis_result" not in st.session_state:
    st.session_state.gas_analysis_result = None
if "generation_job" not in st.session_state:
    st.session_state.generation_job = None
if "generation_kind" not in st.session_state:
    st.session_state.generation_kind = None
if "generation_notice" not in st.session_state:
    st.session_state.generation_notice = None

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
) -> None:
    provider = get_llm_provider(selected_provider, selected_model)
    job = GenerationJob(
        provider=provider,
        messages=messages,
        timeout_seconds=get_generation_timeout_seconds(),
    )
    st.session_state.generation_job = job
    st.session_state.generation_kind = generation_kind
    st.session_state.generation_notice = None
    job.start()


def render_chat() -> None:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

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
    metric_columns[2].metric("Riesgo de margen", analysis.margin_risk.value)

    portfolio_rows = [
        {
            "Gas": item.gas_type.value,
            "Demanda (GWh)": item.demand_gwh,
            "Suministro contratado (GWh)": item.contracted_supply_gwh,
            "Posición corta (GWh)": item.expected_short_position_gwh,
            "Riesgo de suministro": item.supply_risk.value,
            "Riesgo de precio": item.price_risk.value,
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
        st.session_state.gas_analysis_result = None
        try:
            messages = build_gas_analysis_messages(portfolio_description)
            start_generation(messages, "gas_analysis")
            st.rerun()
        except (GasAnalysisError, ValueError) as error:
            st.error(str(error))

    if st.session_state.gas_analysis_result is not None:
        render_gas_analysis_result(st.session_state.gas_analysis_result)


def finish_generation(result: GenerationResult) -> None:
    generation_kind = st.session_state.generation_kind
    st.session_state.generation_job = None
    st.session_state.generation_kind = None

    if result.status == GenerationStatus.COMPLETED:
        if generation_kind == "chat":
            st.session_state.messages.append(
                {"role": "assistant", "content": result.content or ""}
            )
        else:
            try:
                st.session_state.gas_analysis_result = (
                    parse_gas_portfolio_analysis(result.content or "")
                )
            except GasAnalysisError as error:
                st.session_state.generation_notice = ("error", str(error))
        return

    if result.status == GenerationStatus.CANCELLED:
        st.session_state.generation_notice = (
            "info",
            "Generación cancelada.",
        )
    elif result.status == GenerationStatus.TIMED_OUT:
        st.session_state.generation_notice = (
            "warning",
            "La generación superó el tiempo límite y fue cancelada.",
        )
    else:
        st.session_state.generation_notice = (
            "error",
            result.error or "El proveedor no pudo completar la generación.",
        )


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
