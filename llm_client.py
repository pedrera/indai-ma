import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


def generate_response(user_message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada.")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model="gpt-4o-mini",
        instructions=(
            "You are indAI MA, a concise and practical assistant for the "
            "industrial sector. Reply in the same language as the user."
        ),
        input=user_message,
    )
    return response.output_text
