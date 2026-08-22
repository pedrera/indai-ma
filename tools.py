import json
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any


TOOL_USAGE_INSTRUCTIONS = """
You have one deterministic tool for B2B gas calculations. You MUST use
calculate_supply_position whenever a supply position must be calculated from
expected demand and contracted supply. Never perform that calculation yourself.
Use the tool result and its interpretation in your final answer. For requests
that do not require this calculation, answer without calling the tool.
""".strip()


def calculate_supply_position(
    expected_demand_gwh: float,
    contracted_supply_gwh: float,
) -> float:
    return contracted_supply_gwh - expected_demand_gwh


class ToolExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class ToolExecution:
    name: str
    arguments: dict[str, float]
    result: float
    interpretation: str
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "interpretation": self.interpretation,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def model_output(self) -> str:
        return json.dumps(
            {
                "position_gwh": self.result,
                "interpretation": self.interpretation,
            }
        )


TOOL_SPEC = {
    "name": "calculate_supply_position",
    "description": (
        "Calculate the B2B gas supply position in GWh as contracted supply "
        "minus expected demand. The result is LONG when positive, SHORT when "
        "negative, and BALANCED when zero."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expected_demand_gwh": {
                "type": "number",
                "description": "Expected gas demand in GWh.",
            },
            "contracted_supply_gwh": {
                "type": "number",
                "description": "Contracted gas supply in GWh.",
            },
        },
        "required": ["expected_demand_gwh", "contracted_supply_gwh"],
        "additionalProperties": False,
    },
}


def get_chat_completion_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_SPEC["name"],
                "description": TOOL_SPEC["description"],
                "parameters": TOOL_SPEC["parameters"],
            },
        }
    ]


def get_responses_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": TOOL_SPEC["name"],
            "description": TOOL_SPEC["description"],
            "parameters": TOOL_SPEC["parameters"],
            "strict": True,
        }
    ]


def execute_tool_call(tool_name: str, arguments_json: str) -> ToolExecution:
    if tool_name != "calculate_supply_position":
        raise ToolExecutionError(f"Herramienta no permitida: {tool_name}.")

    try:
        arguments = json.loads(arguments_json)
    except json.JSONDecodeError as error:
        raise ToolExecutionError(
            f"Los argumentos de {tool_name} no contienen JSON válido."
        ) from error

    if not isinstance(arguments, dict):
        raise ToolExecutionError(
            f"Los argumentos de {tool_name} deben ser un objeto JSON."
        )

    expected_names = {"expected_demand_gwh", "contracted_supply_gwh"}
    if set(arguments) != expected_names:
        raise ToolExecutionError(
            f"Los argumentos de {tool_name} no cumplen su contrato."
        )

    for argument_name, value in arguments.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolExecutionError(
                f"El argumento {argument_name} de {tool_name} debe ser numérico."
            )
        if not math.isfinite(value):
            raise ToolExecutionError(
                f"El argumento {argument_name} de {tool_name} debe ser finito."
            )

    numeric_arguments = {
        name: float(value) for name, value in arguments.items()
    }
    started_at = perf_counter()
    result = calculate_supply_position(**numeric_arguments)
    elapsed_seconds = perf_counter() - started_at

    if result > 0:
        interpretation = "LONG"
    elif result < 0:
        interpretation = "SHORT"
    else:
        interpretation = "BALANCED"

    return ToolExecution(
        name=tool_name,
        arguments=numeric_arguments,
        result=result,
        interpretation=interpretation,
        elapsed_seconds=elapsed_seconds,
    )
