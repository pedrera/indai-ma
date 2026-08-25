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
from diagnostics import PerformanceRecorder, get_performance_logger
from runtime_config import LLMRuntimeConfig


load_dotenv()
logger = get_performance_logger()

SUPPORTED_PROVIDERS = ("lmstudio", "openai")
MAX_TOOL_ROUNDS = 4
BASE_SYSTEM_INSTRUCTIONS = (
    "You are indAI MA, a concise and practical assistant for the industrial "
    "B2B gas sector. Reply in the same language as the user."
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
        "response_format": None,
        "structured_output_requested": False,
        "reasoning_parameters": {},
        "thinking_enabled": thinking_enabled,
        "final_generation": final_generation,
        "stream": True,
    }
    if not final_generation:
        diagnostics["tool_choice"] = "auto"
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


@dataclass(frozen=True)
class GenerationOptions:
    tool_calling_enabled: bool = True
    max_rounds: int = MAX_TOOL_ROUNDS

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds debe ser al menos 1.")


CHAT_GENERATION_OPTIONS = GenerationOptions()
GAS_ANALYSIS_GENERATION_OPTIONS = GenerationOptions(
    tool_calling_enabled=False,
    max_rounds=1,
)


def _get_system_instructions(options: GenerationOptions) -> str:
    if not options.tool_calling_enabled:
        return BASE_SYSTEM_INSTRUCTIONS
    return f"{BASE_SYSTEM_INSTRUCTIONS}\n\n{TOOL_USAGE_INSTRUCTIONS}"


class LLMProvider(ABC):
    provider_name = "unknown"
    model = "unknown"

    def __init__(self, recorder: PerformanceRecorder | None = None) -> None:
        self.recorder = recorder
        self._cancel_event = Event()
        self._stream_lock = Lock()
        self._active_stream: Any | None = None

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
        options: GenerationOptions | None = None,
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

    def __init__(
        self,
        model_name: str | None = None,
        recorder: PerformanceRecorder | None = None,
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        super().__init__(recorder)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("La variable de entorno OPENAI_API_KEY no está configurada.")

        self.model = model_name or get_default_model_name("openai")
        self.runtime_config = (
            runtime_config or LLMRuntimeConfig.from_environment()
        )
        self.max_output_tokens = self.runtime_config.max_output_tokens
        self.client = OpenAI(api_key=api_key, max_retries=0)

    def generate_response(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: float | None = None,
        options: GenerationOptions | None = None,
    ) -> LLMResponse:
        total_started_at = perf_counter()
        self._prepare_generation()
        options = options or CHAT_GENERATION_OPTIONS
        timeout_seconds = (
            timeout_seconds or self.runtime_config.timeout_seconds
        )
        system_instructions = _get_system_instructions(options)
        conversation_input: list[Any] = _get_conversation_messages(messages)
        tool_executions: list[dict[str, Any]] = []
        tools = get_responses_tools() if options.tool_calling_enabled else []
        if self.recorder is not None:
            provider_event = self.recorder.start_stage(
                "provider_start",
                message_count=len(messages),
                approximate_prompt_chars=(
                    len(json.dumps(conversation_input, ensure_ascii=False))
                    + len(system_instructions)
                    + len(json.dumps(tools))
                ),
                max_tokens=self.runtime_config.max_tokens,
                max_output_tokens=self.max_output_tokens,
                timeout_seconds=self.runtime_config.timeout_seconds,
                thinking_enabled=False,
            )
            self.recorder.complete_stage(provider_event)
        logger.info(
            "stage=provider_start provider=%s model=%s message_count=%d "
            "approximate_prompt_chars=%d",
            self.provider_name,
            self.model,
            len(messages),
            len(json.dumps(conversation_input, ensure_ascii=False))
            + len(system_instructions)
            + len(json.dumps(tools)),
        )

        try:
            for round_number in range(1, options.max_rounds + 1):
                chunks: list[str] = []
                output_items: list[Any] = []
                stream = None
                final_generation = (
                    not options.tool_calling_enabled
                    or round_number == options.max_rounds
                )
                http_started_at = perf_counter()
                http_event = (
                    self.recorder.start_stage("http_request", round=round_number)
                    if self.recorder is not None
                    else None
                )
                inference_event: str | None = None

                try:
                    request_parameters: dict[str, Any] = {
                        "model": self.model,
                        "instructions": system_instructions,
                        "input": conversation_input,
                        "max_output_tokens": self.max_output_tokens,
                        "store": False,
                        "stream": True,
                        "timeout": timeout_seconds,
                    }
                    if not final_generation:
                        request_parameters.update(
                            tools=tools,
                            tool_choice="auto",
                        )

                    stream = self.client.responses.create(**request_parameters)
                    request_seconds = perf_counter() - http_started_at
                    if self.recorder is not None and http_event is not None:
                        self.recorder.complete_stage(
                            http_event, http_seconds=request_seconds
                        )
                    self._set_active_stream(stream)
                    self._raise_if_cancelled()

                    inference_started_at = perf_counter()
                    if self.recorder is not None:
                        inference_event = self.recorder.start_stage(
                            "model_inference", round=round_number
                        )
                    for event in stream:
                        self._raise_if_cancelled()
                        if event.type == "response.output_text.delta":
                            chunks.append(event.delta)
                        elif event.type == "response.output_item.done":
                            output_items.append(event.item)
                    inference_seconds = perf_counter() - inference_started_at
                    if self.recorder is not None and inference_event is not None:
                        self.recorder.complete_stage(
                            inference_event,
                            inference_seconds=inference_seconds,
                            http_total_seconds=(
                                request_seconds + inference_seconds
                            ),
                        )
                except Exception:
                    if self.recorder is not None:
                        if inference_event is not None:
                            self.recorder.fail_stage(inference_event)
                        elif http_event is not None:
                            self.recorder.fail_stage(http_event)
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
                if final_generation and function_calls:
                    raise ValueError(
                        "OpenAI solicitó una tool durante la generación final."
                    )
                if final_generation:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=%d "
                        "final_generation=true request_seconds=%.4f "
                        "inference_seconds=%.4f http_total_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        round_number,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                    )
                else:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=%d "
                        "request_seconds=%.4f inference_seconds=%.4f "
                        "http_total_seconds=%.4f tool_call_count=%d",
                        self.provider_name,
                        self.model,
                        round_number,
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
                    tool_event = (
                        self.recorder.start_stage(
                            "tool_execution",
                            round=round_number,
                            tool_name=function_call.name,
                        )
                        if self.recorder is not None
                        else None
                    )
                    try:
                        tool_execution = execute_tool_call(
                            function_call.name,
                            function_call.arguments,
                        )
                    except Exception:
                        if self.recorder is not None and tool_event is not None:
                            self.recorder.fail_stage(tool_event)
                        raise
                    if self.recorder is not None and tool_event is not None:
                        self.recorder.complete_stage(
                            tool_event,
                            tool_call_count=1,
                            tool_seconds=tool_execution.elapsed_seconds,
                            tool_arguments=tool_execution.arguments,
                            tool_result=tool_execution.result,
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

    def __init__(
        self,
        model_name: str | None = None,
        recorder: PerformanceRecorder | None = None,
        runtime_config: LLMRuntimeConfig | None = None,
    ) -> None:
        super().__init__(recorder)
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
        self.runtime_config = (
            runtime_config or LLMRuntimeConfig.from_environment()
        )
        self.max_tokens = self.runtime_config.max_tokens
        self.thinking_supported = is_qwen3_model(model)
        self.thinking_enabled = (
            self.runtime_config.enable_thinking
            if self.thinking_supported
            else False
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
        options: GenerationOptions | None = None,
    ) -> LLMResponse:
        total_started_at = perf_counter()
        self._prepare_generation()
        options = options or CHAT_GENERATION_OPTIONS
        timeout_seconds = (
            timeout_seconds or self.runtime_config.timeout_seconds
        )
        system_instructions = _get_system_instructions(options)
        if self.thinking_supported and not self.thinking_enabled:
            system_instructions += "\n\n/no_think"

        conversation: list[dict[str, Any]] = [
            {"role": "system", "content": system_instructions},
            *_get_conversation_messages(messages),
        ]
        tool_executions: list[dict[str, Any]] = []
        tools = (
            get_chat_completion_tools()
            if options.tool_calling_enabled
            else []
        )
        if self.recorder is not None:
            provider_event = self.recorder.start_stage(
                "provider_start",
                message_count=len(messages),
                approximate_prompt_chars=(
                    len(json.dumps(conversation, ensure_ascii=False))
                    + len(json.dumps(tools))
                ),
                max_tokens=self.max_tokens,
                max_output_tokens=self.runtime_config.max_output_tokens,
                timeout_seconds=self.runtime_config.timeout_seconds,
                thinking_enabled=self.thinking_enabled,
            )
            self.recorder.complete_stage(provider_event)
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
            for round_number in range(1, options.max_rounds + 1):
                chunks: list[str] = []
                tool_calls: dict[int, dict[str, str]] = {}
                stream = None
                http_started_at = perf_counter()
                http_event = (
                    self.recorder.start_stage("http_request", round=round_number)
                    if self.recorder is not None
                    else None
                )
                inference_event: str | None = None
                final_generation = (
                    not options.tool_calling_enabled
                    or round_number == options.max_rounds
                )
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
                    if self.recorder is not None and http_event is not None:
                        self.recorder.complete_stage(
                            http_event, http_seconds=request_seconds
                        )
                    self._set_active_stream(stream)
                    self._raise_if_cancelled()

                    inference_started_at = perf_counter()
                    if self.recorder is not None:
                        inference_event = self.recorder.start_stage(
                            "model_inference", round=round_number
                        )
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
                    if self.recorder is not None and inference_event is not None:
                        self.recorder.complete_stage(
                            inference_event,
                            inference_seconds=inference_seconds,
                            http_total_seconds=(
                                request_seconds + inference_seconds
                            ),
                        )
                except Exception:
                    if self.recorder is not None:
                        if inference_event is not None:
                            self.recorder.fail_stage(inference_event)
                        elif http_event is not None:
                            self.recorder.fail_stage(http_event)
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
                        "stage=http_call provider=%s model=%s round=%d "
                        "final_generation=true request_seconds=%.4f "
                        "inference_seconds=%.4f http_total_seconds=%.4f",
                        self.provider_name,
                        self.model,
                        round_number,
                        request_seconds,
                        inference_seconds,
                        request_seconds + inference_seconds,
                    )
                else:
                    logger.info(
                        "stage=http_call provider=%s model=%s round=%d "
                        "request_seconds=%.4f inference_seconds=%.4f "
                        "http_total_seconds=%.4f tool_call_count=%d",
                        self.provider_name,
                        self.model,
                        round_number,
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
                    tool_event = (
                        self.recorder.start_stage(
                            "tool_execution",
                            round=round_number,
                            tool_name=tool_call["name"],
                        )
                        if self.recorder is not None
                        else None
                    )
                    try:
                        tool_execution = execute_tool_call(
                            tool_call["name"],
                            tool_call["arguments"],
                        )
                    except Exception:
                        if self.recorder is not None and tool_event is not None:
                            self.recorder.fail_stage(tool_event)
                        raise
                    if self.recorder is not None and tool_event is not None:
                        self.recorder.complete_stage(
                            tool_event,
                            tool_call_count=1,
                            tool_seconds=tool_execution.elapsed_seconds,
                            tool_arguments=tool_execution.arguments,
                            tool_result=tool_execution.result,
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
    return LLMRuntimeConfig.from_environment().max_tokens


def get_llm_enable_thinking() -> bool:
    return LLMRuntimeConfig.from_environment().enable_thinking


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
    recorder: PerformanceRecorder | None = None,
    runtime_config: LLMRuntimeConfig | None = None,
) -> LLMProvider:
    normalized_name = (provider_name or get_default_provider_name()).strip().lower()

    if normalized_name == "openai":
        return OpenAIProvider(model_name, recorder, runtime_config)
    if normalized_name == "lmstudio":
        return LMStudioProvider(model_name, recorder, runtime_config)

    raise ValueError("LLM_PROVIDER debe ser 'openai' o 'lmstudio'.")
