import os
from abc import ABC, abstractmethod
from threading import Event, Lock
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)


load_dotenv()

SUPPORTED_PROVIDERS = ("lmstudio", "openai")


class GenerationCancelledError(Exception):
    pass


class LLMTimeoutError(TimeoutError):
    pass


class LLMProvider(ABC):
    def __init__(self) -> None:
        self._cancel_event = Event()
        self._stream_lock = Lock()
        self._active_stream: Any | None = None

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> str:
        pass

    def cancel(self) -> None:
        self._cancel_event.set()
        with self._stream_lock:
            stream = self._active_stream

        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

        client = getattr(self, "client", None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _prepare_generation(self) -> None:
        self._raise_if_cancelled()

    def _set_active_stream(self, stream: Any | None) -> None:
        with self._stream_lock:
            self._active_stream = stream

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise GenerationCancelledError


class OpenAIProvider(LLMProvider):
    def __init__(self, model_name: str | None = None) -> None:
        super().__init__()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada.")

        self.model = model_name or get_default_model_name("openai")
        self.client = OpenAI(api_key=api_key, max_retries=0)

    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> str:
        self._prepare_generation()
        chunks: list[str] = []
        stream = None

        try:
            stream = self.client.responses.create(
                model=self.model,
                instructions=(
                    "You are indAI MA, a concise and practical assistant for the "
                    "industrial sector. Reply in the same language as the user."
                ),
                input=messages,
                store=False,
                stream=True,
                timeout=timeout_seconds,
            )
            self._set_active_stream(stream)
            self._raise_if_cancelled()

            for event in stream:
                self._raise_if_cancelled()
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
        except APITimeoutError as error:
            raise LLMTimeoutError(
                "La llamada a OpenAI superó el tiempo límite."
            ) from error
        except APIConnectionError as error:
            self._raise_if_cancelled()
            raise ValueError("No se pudo conectar con OpenAI.") from error
        except APIStatusError as error:
            raise ValueError(
                f"OpenAI devolvió un error HTTP {error.status_code}."
            ) from error
        except Exception as error:
            self._raise_if_cancelled()
            raise error
        finally:
            self._set_active_stream(None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

        content = "".join(chunks)
        self._raise_if_cancelled()
        if not content:
            raise ValueError("OpenAI no devolvió contenido en la respuesta.")

        return content


class LMStudioProvider(LLMProvider):
    def __init__(self, model_name: str | None = None) -> None:
        super().__init__()
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
        self.client = OpenAI(
            base_url=base_url,
            api_key="lm-studio",
            max_retries=0,
        )

    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> str:
        self._prepare_generation()
        chunks: list[str] = []
        stream = None

        try:
            stream = self.client.chat.completions.create(
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
                    *messages,
                ],
                stream=True,
                timeout=timeout_seconds,
            )
            self._set_active_stream(stream)
            self._raise_if_cancelled()

            for chunk in stream:
                self._raise_if_cancelled()
                if chunk.choices and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
        except APITimeoutError as error:
            raise LLMTimeoutError(
                "La llamada a LM Studio superó el tiempo límite."
            ) from error
        except APIConnectionError as error:
            self._raise_if_cancelled()
            raise ValueError(
                "No se pudo conectar con LM Studio. Comprueba que el servidor "
                "local esté iniciado y que LMSTUDIO_BASE_URL sea correcta."
            ) from error
        except APIStatusError as error:
            raise ValueError(
                f"LM Studio devolvió un error HTTP {error.status_code}. "
                "Comprueba que el modelo configurado esté cargado."
            ) from error
        except Exception as error:
            self._raise_if_cancelled()
            raise error
        finally:
            self._set_active_stream(None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

        content = "".join(chunks)
        self._raise_if_cancelled()
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
