import streamlit as st

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

provider_options = get_supported_providers()
default_provider = get_default_provider_name()
default_provider_index = (
    provider_options.index(default_provider)
    if default_provider in provider_options
    else 0
)

with st.sidebar:
    st.header("Configuración")
    selected_provider = st.selectbox(
        "Proveedor LLM",
        provider_options,
        index=default_provider_index,
        key="selected_provider",
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
        )
    except ValueError as error:
        st.error(str(error))
        selected_model = None

    if st.button("Nueva conversación", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

user_message = st.chat_input(
    "Escribe tu consulta",
    disabled=selected_model is None,
)

if user_message:
    st.session_state.messages.append(
        {"role": "user", "content": user_message}
    )

    with st.chat_message("user"):
        st.write(user_message)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Generando respuesta..."):
                provider = get_llm_provider(selected_provider, selected_model)
                response = provider.generate_response(st.session_state.messages)
            st.session_state.messages.append(
                {"role": "assistant", "content": response}
            )
            st.write(response)
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error("No se pudo obtener una respuesta. Inténtalo de nuevo.")
