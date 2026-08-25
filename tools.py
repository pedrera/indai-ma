import json
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable


TOOL_USAGE_INSTRUCTIONS = """
You have deterministic tools for quantitative B2B gas portfolio analysis.
Use calculate_supply_position for contracted supply positions, calculate_margin
for unit and total margins, calculate_demand_scenario for percentage demand
scenarios, and calculate_spot_exposure for the cost of covering a SHORT
position. Never perform these calculations yourself when the corresponding
tool applies. You may call several tools, including in successive rounds when
one calculation depends on a previous result. Use every relevant tool result
in your final answer. For requests that need no calculation, answer directly.
""".strip()


def calculate_supply_position(
    expected_demand_gwh: float,
    contracted_supply_gwh: float,
) -> float:
    return contracted_supply_gwh - expected_demand_gwh


def calculate_margin(
    sales_price_eur_mwh: float,
    supply_cost_eur_mwh: float,
    volume_gwh: float,
) -> dict[str, float]:
    margin_eur_mwh = sales_price_eur_mwh - supply_cost_eur_mwh
    return {
        "sales_price_eur_mwh": sales_price_eur_mwh,
        "supply_cost_eur_mwh": supply_cost_eur_mwh,
        "volume_gwh": volume_gwh,
        "margin_eur_mwh": margin_eur_mwh,
        "total_margin_eur": margin_eur_mwh * volume_gwh * 1000,
    }


def calculate_demand_scenario(
    base_demand_gwh: float,
    variation_percent: float,
) -> dict[str, float]:
    return {
        "base_demand_gwh": base_demand_gwh,
        "variation_percent": variation_percent,
        "scenario_demand_gwh": base_demand_gwh
        * (1 + variation_percent / 100),
    }


def calculate_spot_exposure(
    expected_demand_gwh: float,
    contracted_supply_gwh: float,
    spot_price_eur_mwh: float,
) -> dict[str, float]:
    short_position_gwh = max(
        expected_demand_gwh - contracted_supply_gwh, 0
    )
    return {
        "short_position_gwh": short_position_gwh,
        "spot_price_eur_mwh": spot_price_eur_mwh,
        "exposure_eur": short_position_gwh * 1000 * spot_price_eur_mwh,
    }


class ToolExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class ToolExecution:
    name: str
    arguments: dict[str, float]
    result: dict[str, float | str]
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def model_output(self) -> str:
        return json.dumps(self.result, ensure_ascii=False)


def _number_property(description: str, minimum: float | None = 0) -> dict:
    property_schema: dict[str, Any] = {
        "type": "number",
        "description": description,
    }
    if minimum is not None:
        property_schema["minimum"] = minimum
    return property_schema


TOOL_SPECS = (
    {
        "name": "calculate_supply_position",
        "description": (
            "Calculate contracted supply minus expected demand in GWh and "
            "classify the position as LONG, SHORT, or BALANCED."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_demand_gwh": _number_property(
                    "Expected gas demand in GWh."
                ),
                "contracted_supply_gwh": _number_property(
                    "Contracted gas supply in GWh."
                ),
            },
            "required": ["expected_demand_gwh", "contracted_supply_gwh"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_margin",
        "description": (
            "Calculate unit margin in EUR/MWh and total margin in EUR for a "
            "gas volume expressed in GWh."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sales_price_eur_mwh": _number_property(
                    "Sales price in EUR/MWh."
                ),
                "supply_cost_eur_mwh": _number_property(
                    "Supply cost in EUR/MWh."
                ),
                "volume_gwh": _number_property("Gas volume in GWh."),
            },
            "required": [
                "sales_price_eur_mwh",
                "supply_cost_eur_mwh",
                "volume_gwh",
            ],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_demand_scenario",
        "description": (
            "Apply a positive or negative percentage variation to a base gas "
            "demand and return the scenario demand in GWh."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_demand_gwh": _number_property(
                    "Base gas demand in GWh."
                ),
                "variation_percent": _number_property(
                    "Percentage demand variation; negative values reduce demand.",
                    minimum=None,
                ),
            },
            "required": ["base_demand_gwh", "variation_percent"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_spot_exposure",
        "description": (
            "Calculate the EUR cost of buying the uncovered SHORT gas position "
            "at a given spot price. Returns zero exposure when not SHORT."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expected_demand_gwh": _number_property(
                    "Expected gas demand in GWh."
                ),
                "contracted_supply_gwh": _number_property(
                    "Contracted gas supply in GWh."
                ),
                "spot_price_eur_mwh": _number_property(
                    "Spot purchase price in EUR/MWh."
                ),
            },
            "required": [
                "expected_demand_gwh",
                "contracted_supply_gwh",
                "spot_price_eur_mwh",
            ],
            "additionalProperties": False,
        },
    },
)

TOOL_SPEC_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}


def get_chat_completion_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for spec in TOOL_SPECS
    ]


def get_responses_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": spec["name"],
            "description": spec["description"],
            "parameters": spec["parameters"],
            "strict": True,
        }
        for spec in TOOL_SPECS
    ]


def _supply_position_result(arguments: dict[str, float]) -> dict[str, float | str]:
    position = calculate_supply_position(**arguments)
    if position > 0:
        interpretation = "LONG"
    elif position < 0:
        interpretation = "SHORT"
    else:
        interpretation = "BALANCED"
    return {"position_gwh": position, "interpretation": interpretation}


TOOL_FUNCTIONS: dict[
    str, Callable[[dict[str, float]], dict[str, float | str]]
] = {
    "calculate_supply_position": _supply_position_result,
    "calculate_margin": lambda arguments: calculate_margin(**arguments),
    "calculate_demand_scenario": lambda arguments: calculate_demand_scenario(
        **arguments
    ),
    "calculate_spot_exposure": lambda arguments: calculate_spot_exposure(
        **arguments
    ),
}


def execute_tool_call(tool_name: str, arguments_json: str) -> ToolExecution:
    spec = TOOL_SPEC_BY_NAME.get(tool_name)
    if spec is None:
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

    expected_names = set(spec["parameters"]["required"])
    if set(arguments) != expected_names:
        raise ToolExecutionError(
            f"Los argumentos de {tool_name} no cumplen su contrato."
        )

    numeric_arguments: dict[str, float] = {}
    properties = spec["parameters"]["properties"]
    for argument_name, value in arguments.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolExecutionError(
                f"El argumento {argument_name} de {tool_name} debe ser numérico."
            )
        if not math.isfinite(value):
            raise ToolExecutionError(
                f"El argumento {argument_name} de {tool_name} debe ser finito."
            )
        minimum = properties[argument_name].get("minimum")
        if minimum is not None and value < minimum:
            raise ToolExecutionError(
                f"El argumento {argument_name} de {tool_name} no puede ser negativo."
            )
        numeric_arguments[argument_name] = float(value)

    if (
        tool_name == "calculate_demand_scenario"
        and numeric_arguments["variation_percent"] < -100
    ):
        raise ToolExecutionError(
            "La variación de demanda no puede producir una demanda negativa."
        )

    started_at = perf_counter()
    result = TOOL_FUNCTIONS[tool_name](numeric_arguments)
    elapsed_seconds = perf_counter() - started_at
    return ToolExecution(
        name=tool_name,
        arguments=numeric_arguments,
        result=result,
        elapsed_seconds=elapsed_seconds,
    )
