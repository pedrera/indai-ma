import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _read_integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        numeric_value = float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} debe ser un número entero.") from error
    if not numeric_value.is_integer():
        raise ValueError(f"{name} debe ser un número entero.")
    value = int(numeric_value)
    if value <= 0:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value


def _read_boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, str(default)).strip().lower()
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise ValueError(f"{name} debe ser 'true' o 'false'.")


@dataclass(frozen=True)
class LLMRuntimeConfig:
    max_output_tokens: int
    max_tokens: int
    timeout_seconds: int
    enable_thinking: bool

    def __post_init__(self) -> None:
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens debe ser mayor que cero.")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens debe ser mayor que cero.")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds debe ser mayor que cero.")

    @classmethod
    def from_environment(cls) -> "LLMRuntimeConfig":
        max_tokens = _read_integer("LLM_MAX_TOKENS", 384)
        return cls(
            max_output_tokens=_read_integer(
                "LLM_MAX_OUTPUT_TOKENS", max_tokens
            ),
            max_tokens=max_tokens,
            timeout_seconds=_read_integer("LLM_TIMEOUT_SECONDS", 300),
            enable_thinking=_read_boolean("LLM_ENABLE_THINKING", False),
        )
