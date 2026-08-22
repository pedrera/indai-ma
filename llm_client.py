import os
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Event, Lock
from time import perf_counter
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)

from tools import (
    TOOL_USAGE_INSTRUCTIONS,
    execute_tool_call,
    get_chat_completion_tools,
    get_responses_tools,
)
from diagnostics import get_performance_logger


load_dotenv()
logger = get_performance_logger()

SUPPORTED_PROVIDERS = ("lmstudio", "openai")
MAX_TOOL_ROUNDS = 2
SYSTEM_INSTRUCTIONS = (
    "You are indAI MA, a concise and practical assistant for the industrial "
    "B2B gas sector. Reply in the same language as the user.\n\n"
    f"{TOOL_USAGE_INSTRUCTIONS}"
)


def _get_conversation_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in messages
    ]


def _print_lmstudio_request_diagnostics(
    round_number: int,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout_seconds: float | None,
    max_tokens: int,
    thinking_enabled: bool,
    final_generation: bool,
) -> None:
    message_content_lengths = [
        len(message.get("content") or "")
        if isinstance(message.get("content"), str)
        else len(json.dumps(message.get("content"), ensure_ascii=False))
        for message in messages
    ]
    message_serialized_lengths = [
        len(json.dumps(message, ensure_ascii=False)) for message in messages
    ]
    tools_length = len(json.dumps(tools, ensure_ascii=False))
    diagnostics = {
        "endpoint": "/v1/chat/completions",
        "round": round_number,
        "model": model,
        "max_tokens": max_tokens,
        "max_completion_tokens": None,
        "temperature": None,
        "timeout_seconds": timeout_seconds,
        "message_count": len(messages),
        "message_content_lengths": message_content_lengths,
        "message_serialized_lengths": message_serialized_lengths,
        "messages_content_total_chars": sum(message_content_lengths),
        "approximate_total_chars": sum(message_serialized_lengths) + tools_length,
        "tool_count": len(tools),
        "tools": [
            tool.get("function", {}).get("name", "unknown") for tool in tools
        ],
        "tool_choice": None if final_generation else "auto",
        "response_format": None,
        "structured_output_requested": False,
        "reasoning_parameters": {},
        "thinking_enabled": thinking_enabled,
        "final_generation": final_generation,
        "stream": True,
    }
    print(
        "[LMSTUDIO_REQUEST] "
        + json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


class GenerationCancelledError(Exception):
    pass


class LLMTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_executions: list[dict[str, Any]] = field(default_factory=list)


class LLMProvider(ABC):
    provider_name = "unknown"
    model = "unknown"

    def __init__(self) -> None:
        self._cancel_event = Event()
        self._stream_lock = Lock()
        self._active_stream: Any | None = None

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
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
    provider_name = "openai"

    def __init__(self, model_name: str | None = None) -> None:
        super().__init__()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada.")

        self.model = model_name or get_default_model_name("openai")
        self.max_tokens = get_llm_max_tokens()
        self.client = OpenAI(api_key=api_key, max_retries=0)

    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        total_started_at = perf_counter()
        self._prepare_generation()
        conversation_input: list[Any] = _get_conversation_messages(messages)
        tool_executions: list[dict[str, Any]] = []
        tools = get_responses_tools()
        logger.info(
            "stage=provider_start provider=%s model=%s message_count=%d "
            "approximate_prompt_chars=%d",
            self.provider_name,
            self.model,
            len(messages),
            len(json.dumps(conversation_input, ensure_ascii=False))
            + len(SYSTEM_INSTRUCTIONS)
            + len(json.dumps(tools)),
        )

        try:
            for round_number in range(1, MAX_TOOL_ROUNDS + 1):
                chunks: list[str] = []
                output_items: list[Any] = []
                stream = None
                http_started_at = perf_counter()

                try:
                    request_parameters: dict[str, Any] = {
                        "model": self.model,
                        "instructions": SYSTEM_INSTRUCTIONS,
                        "input": conversation_input,
                        "max_output_tokens": self.max_tokens,
                        "store": False,
                        "stream": True,
                        "timeout": timeout_seconds,
                    }
                    if round_number == 1:
                        request_parameters.update(
                            tools=tools,
                            tool_choice="auto",
                        )

                    stream = self.client.responses.create(**request_parameters)
                    request_seconds = perf_counter() - http_started_at
                    self._set_active_stream(stream)
                    self._raise_if_cancelled()

                    inference_started_at = perf_counter()
                    for event in stream:
                        self._raise_if_cancelled()
                        if event.type == "response.output_text.delta":
                            chunks.append(event.delta)
                        elif event.type == "response.output_item.done":
                            output_items.append(event.item)
                    inference_seconds = perf_counter() - inference_started_at
                except Exception:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=%d "
                        "status=interrupted elapsed_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        round_number,
                        perf_counter() - http_started_at,
                    )
                    raise
                finally:
                    self._set_active_stream(None)
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass

                function_calls = [
                    item
                    for item in output_items
                    if getattr(item, "type", None) == "function_call"
                ]
                if round_number == 2 and function_calls:
                    raise ValueError(
                        "OpenAI solicitó una tool durante la generación final."
                    )
                if round_number == 2:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=2 "
                        "final_generation=true request_seconds=%.4f "
                        "inference_seconds=%.4f http_total_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                    )
                else:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=1 "
                        "request_seconds=%.4f inference_seconds=%.4f "
                        "http_total_seconds=%.4f tool_call_count=%d",
                        self.provider_name,
                        self.model,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                        len(function_calls),
                    )
                if not function_calls:
                    content = "".join(chunks)
                    if not content:
                        raise ValueError(
                            "OpenAI no devolvió contenido en la respuesta."
                        )
                    logger.info(
                        "stage=provider_total provider=%s model=%s "
                        "duration_seconds=%.4f rounds=%d",
                        self.provider_name,
                        self.model,
                        perf_counter() - total_started_at,
                        round_number,
                    )
                    return LLMResponse(content, tool_executions)

                conversation_input.extend(output_items)
                for function_call in function_calls:
                    tool_execution = execute_tool_call(
                        function_call.name,
                        function_call.arguments,
                    )
                    tool_executions.append(tool_execution.as_dict())
                    conversation_input.append(
                        {
                            "type": "function_call_output",
                            "call_id": function_call.call_id,
                            "output": tool_execution.model_output(),
                        }
                    )

            raise ValueError("OpenAI no generó una respuesta final.")
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


class LMStudioProvider(LLMProvider):
    provider_name = "lmstudio"

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
        self.max_tokens = get_llm_max_tokens()
        self.thinking_enabled = (
            get_llm_enable_thinking() if is_qwen3_model(model) else True
        )
        self.client = OpenAI(
            base_url=base_url,
            api_key="lm-studio",
            max_retries=0,
        )

    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        total_started_at = perf_counter()
        self._prepare_generation()
        system_instructions = SYSTEM_INSTRUCTIONS
        if not self.thinking_enabled:
            system_instructions += "\n\n/no_think"

        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": system_instructions},
            *_get_conversation_messages(messages),
        ]
        tool_executions: list[dict[str, Any]] = []
        tools = get_chat_completion_tools()
        logger.info(
            "stage=provider_start provider=%s model=%s message_count=%d "
            "approximate_prompt_chars=%d",
            self.provider_name,
            self.model,
            len(messages),
            len(json.dumps(conversation, ensure_ascii=False))
            + len(json.dumps(tools)),
        )

        try:
            for round_number in range(1, MAX_TOOL_ROUNDS + 1):
                chunks: list[str] = []
                tool_calls: dict[int, dict[str, str]] = {}
                stream = None
                http_started_at = perf_counter()
                final_generation = round_number == 2
                request_tools = [] if final_generation else tools

                try:
                    _print_lmstudio_request_diagnostics(
                        round_number,
                        self.model,
                        conversation,
                        request_tools,
                        timeout_seconds,
                        self.max_tokens,
                        self.thinking_enabled,
                        final_generation,
                    )
                    request_parameters: dict[str, Any] = {
                        "model": self.model,
                        "messages": conversation,
                        "max_tokens": self.max_tokens,
                        "stream": True,
                        "timeout": timeout_seconds,
                    }
                    if not final_generation:
                        request_parameters.update(
                            tools=tools,
                            tool_choice="auto",
                        )

                    stream = self.client.chat.completions.create(
                        **request_parameters
                    )
                    request_seconds = perf_counter() - http_started_at
                    self._set_active_stream(stream)
                    self._raise_if_cancelled()

                    inference_started_at = perf_counter()
                    for chunk in stream:
                        self._raise_if_cancelled()
                        if not chunk.choices:
                            continue

                        delta = chunk.choices[0].delta
                        if delta.content:
                            chunks.append(delta.content)

                        for tool_call in delta.tool_calls or []:
                            entry = tool_calls.setdefault(
                                tool_call.index,
                                {"id": "", "name": "", "arguments": ""},
                            )
                            if tool_call.id:
                                entry["id"] = tool_call.id
                            if tool_call.function:
                                if tool_call.function.name:
                                    entry["name"] += tool_call.function.name
                                if tool_call.function.arguments:
                                    entry["arguments"] += tool_call.function.arguments
                    inference_seconds = perf_counter() - inference_started_at
                except Exception:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=%d "
                        "status=interrupted elapsed_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        round_number,
                        perf_counter() - http_started_at,
                    )
                    raise
                finally:
                    self._set_active_stream(None)
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass

                if final_generation:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=2 "
                        "final_generation=true request_seconds=%.4f "
                        "inference_seconds=%.4f http_total_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                    )
                else:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=1 "
                        "request_seconds=%.4f inference_seconds=%.4f "
                        "http_total_seconds=%.4f tool_call_count=%d",
                        self.provider_name,
                        self.model,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                        len(tool_calls),
                    )

                if final_generation and tool_calls:
                    raise ValueError(
                        "LM Studio solicitó una tool durante la generación final."
                    )
                if not tool_calls:
                    content = "".join(chunks)
                    if not content:
                        raise ValueError(
                            "LM Studio no devolvió contenido en la respuesta."
                        )
                    logger.info(
                        "stage=provider_total provider=%s model=%s "
                        "duration_seconds=%.4f rounds=%d",
                        self.provider_name,
                        self.model,
                        perf_counter() - total_started_at,
                        round_number,
                    )
                    return LLMResponse(content, tool_executions)

                ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
                for tool_call in ordered_calls:
                    if not tool_call["id"] or not tool_call["name"]:
                        raise ValueError(
                            "LM Studio devolvió una tool call incompleta. "
                            "Comprueba que el modelo soporte function calling."
                        )

                conversation.append(
                    {
                        "role": "assistant",
                        "content": "".join(chunks) or None,
                        "tool_calls": [
                            {
                                "id": tool_call["id"],
                                "type": "function",
                                "function": {
                                    "name": tool_call["name"],
                                    "arguments": tool_call["arguments"],
                                },
                            }
                            for tool_call in ordered_calls
                        ],
                    }
                )
                for tool_call in ordered_calls:
                    tool_execution = execute_tool_call(
                        tool_call["name"],
                        tool_call["arguments"],
                    )
                    tool_executions.append(tool_execution.as_dict())
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "content": tool_execution.model_output(),
                        }
                    )

            raise ValueError("LM Studio no generó una respuesta final.")
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
            if error.status_code in {400, 404, 422}:
                raise ValueError(
                    "LM Studio o el modelo seleccionado rechazó function calling. "
                    "Selecciona un modelo con soporte de tools."
                ) from error
            raise ValueError(
                f"LM Studio devolvió un error HTTP {error.status_code}. "
                "Comprueba que el modelo configurado esté cargado."
            ) from error
        except Exception as error:
            self._raise_if_cancelled()
            raise error


def get_supported_providers() -> tuple[str, ...]:
    return SUPPORTED_PROVIDERS


def get_llm_max_tokens() -> int:
    raw_value = os.getenv("LLM_MAX_TOKENS", "384").strip()
    try:
        max_tokens = int(raw_value)
    except ValueError as error:
        raise ValueError("LLM_MAX_TOKENS debe ser un número entero.") from error

    if max_tokens <= 0:
        raise ValueError("LLM_MAX_TOKENS debe ser mayor que cero.")

    return max_tokens


def get_llm_enable_thinking() -> bool:
    raw_value = os.getenv("LLM_ENABLE_THINKING", "false").strip().lower()
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ValueError("LLM_ENABLE_THINKING debe ser 'true' o 'false'.")


def is_qwen3_model(model_name: str) -> bool:
    normalized_name = model_name.strip().lower()
    return normalized_name.startswith(
        ("qwen/qwen3", "qwen3", "lmstudio-community/qwen3")
    )


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
