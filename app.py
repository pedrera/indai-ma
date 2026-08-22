import streamlit as st

from llm_client import generate_response


st.set_page_config(page_title="indAI MA", page_icon="🏭")

st.title("indAI MA")
st.caption("Asistente para el sector industrial")

user_message = st.chat_input("Escribe tu consulta")

if user_message:
    with st.chat_message("user"):
        st.write(user_message)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Generando respuesta..."):
                response = generate_response(user_message)
            st.write(response)
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error("No se pudo obtener una respuesta. Inténtalo de nuevo.")
