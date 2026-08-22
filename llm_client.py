import os
from abc import ABC, abstractmethod

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, OpenAI


load_dotenv()

SUPPORTED_PROVIDERS = ("lmstudio", "openai")


class LLMProvider(ABC):
    @abstractmethod
    def generate_response(self, user_message: str) -> str:
        pass


class OpenAIProvider(LLMProvider):
    def __init__(self, model_name: str | None = None) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada.")

        self.model = model_name or get_default_model_name("openai")
        self.client = OpenAI(api_key=api_key)

    def generate_response(self, user_message: str) -> str:
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "You are indAI MA, a concise and practical assistant for the "
                "industrial sector. Reply in the same language as the user."
            ),
            input=user_message,
        )
        return response.output_text


class LMStudioProvider(LLMProvider):
    def __init__(self, model_name: str | None = None) -> None:
        base_url = os.getenv("LMSTUDIO_BASE_URL", "").strip()
        model = model_name or get_default_model_name("lmstudio")

        if not base_url:
            raise ValueError(
                "La variable de entorno LMSTUDIO_BASE_URL no está configurada."
            )
        if not model:
            raise ValueError(
                "La variable de entorno LMSTUDIO_MODEL no está configurada."
            )

        self.model = model
        self.client = OpenAI(base_url=base_url, api_key="lm-studio")

    def generate_response(self, user_message: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are indAI MA, a concise and practical assistant "
                            "for the industrial sector. Reply in the same language "
                            "as the user."
                        ),
                    },
                    {"role": "user", "content": user_message},
                ],
            )
        except APIConnectionError as error:
            raise ValueError(
                "No se pudo conectar con LM Studio. Comprueba que el servidor "
                "local esté iniciado y que LMSTUDIO_BASE_URL sea correcta."
            ) from error
        except APIStatusError as error:
            raise ValueError(
                f"LM Studio devolvió un error HTTP {error.status_code}. "
                "Comprueba que el modelo configurado esté cargado."
            ) from error

        content = response.choices[0].message.content
        if not content:
            raise ValueError("LM Studio no devolvió contenido en la respuesta.")

        return content


def get_supported_providers() -> tuple[str, ...]:
    return SUPPORTED_PROVIDERS


def get_default_provider_name() -> str:
    return os.getenv("LLM_PROVIDER", "openai").strip().lower()


def get_default_model_name(provider_name: str) -> str:
    normalized_name = provider_name.strip().lower()

    if normalized_name == "openai":
        return (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    if normalized_name == "lmstudio":
        return os.getenv("LMSTUDIO_MODEL", "").strip()

    raise ValueError(f"Proveedor LLM no compatible: {provider_name}.")


def get_available_models(provider_name: str) -> list[str]:
    normalized_name = provider_name.strip().lower()

    if normalized_name == "openai":
        return [get_default_model_name("openai")]
    if normalized_name != "lmstudio":
        raise ValueError(f"Proveedor LLM no compatible: {provider_name}.")

    base_url = os.getenv("LMSTUDIO_BASE_URL", "").strip()
    if not base_url:
        raise ValueError(
            "La variable de entorno LMSTUDIO_BASE_URL no está configurada."
        )

    client = OpenAI(base_url=base_url, api_key="lm-studio")
    try:
        response = client.models.list()
    except APIConnectionError as error:
        raise ValueError(
            "No se pudo consultar LM Studio. Comprueba que el servidor local "
            "esté iniciado y que LMSTUDIO_BASE_URL sea correcta."
        ) from error
    except APIStatusError as error:
        raise ValueError(
            f"LM Studio devolvió un error HTTP {error.status_code} al listar modelos."
        ) from error

    models = sorted(
        model.id for model in response.data if "embed" not in model.id.lower()
    )
    if not models:
        raise ValueError("LM Studio no tiene modelos de chat disponibles.")

    return models


def get_llm_provider(
    provider_name: str | None = None,
    model_name: str | None = None,
) -> LLMProvider:
    normalized_name = (provider_name or get_default_provider_name()).strip().lower()

    if normalized_name == "openai":
        return OpenAIProvider(model_name)
    if normalized_name == "lmstudio":
        return LMStudioProvider(model_name)

    raise ValueError("LLM_PROVIDER debe ser 'openai' o 'lmstudio'.")
