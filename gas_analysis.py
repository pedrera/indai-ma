import json
import re
from dataclasses import dataclass
from enum import Enum
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from diagnostics import PerformanceRecorder, get_performance_logger
from tools import execute_tool_call


logger = get_performance_logger()


class GasType(str, Enum):
    NATURAL_GAS = "natural_gas"
    BIOMETHANE = "biomethane"
    LNG = "lng"
    HYDROGEN_BLEND = "hydrogen_blend"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GasPortfolioItem(BaseModel):
    gas_type: GasType
    demand_gwh: float = Field(ge=0)
    contracted_supply_gwh: float = Field(ge=0)
    expected_short_position_gwh: float = Field(ge=0)
    supply_risk: RiskLevel
    price_risk: RiskLevel


class GasB2BPortfolioAnalysis(BaseModel):
    summary: str
    portfolio: list[GasPortfolioItem]
    margin_risk: RiskLevel
    affected_customer_segments: list[str]
    commercial_impact: str
    recommended_actions: list[str]


@dataclass(frozen=True)
class PreparedGasAnalysis:
    messages: list[dict[str, str]]
    tool_executions: list[dict[str, Any]]


class GasAnalysisError(ValueError):
    pass


GAS_ALIASES = {
    "gas natural": GasType.NATURAL_GAS,
    "biometano": GasType.BIOMETHANE,
    "biomethane": GasType.BIOMETHANE,
    "gnl": GasType.LNG,
    "lng": GasType.LNG,
    "mezcla de hidrógeno": GasType.HYDROGEN_BLEND,
    "mezcla de hidrogeno": GasType.HYDROGEN_BLEND,
    "hydrogen blend": GasType.HYDROGEN_BLEND,
}
GAS_PATTERN = re.compile(
    r"\b(?:gas\s+natural|biometano|biomethane|gnl|lng|"
    r"mezcla\s+de\s+hidr[oó]geno|hydrogen\s+blend)\b",
    re.IGNORECASE,
)
DEMAND_PATTERN = re.compile(
    r"\bdemanda(?:\s+(?:prevista|esperada|estimada|industrial))?"
    r"(?:\s+(?:de|para))?[^.;\n?]{0,80}?"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*gwh\b",
    re.IGNORECASE,
)
SUPPLY_PATTERN = re.compile(
    r"\b(?:suministro\s+(?:ya\s+)?contratado|"
    r"(?:tenemos\s+)?contratad(?:o|a|os|as)|volumen\s+contratado)"
    r"[^.;\n?]{0,60}?(?P<value>\d+(?:[.,]\d+)?)\s*gwh\b",
    re.IGNORECASE,
)


def _parse_gwh(match: re.Match[str]) -> float:
    return float(match.group("value").replace(",", "."))


def _extract_position(segment: str) -> dict[str, Any] | None:
    gas_matches = {
        GAS_ALIASES[" ".join(match.group(0).lower().split())]
        for match in GAS_PATTERN.finditer(segment)
    }
    demand_matches = list(DEMAND_PATTERN.finditer(segment))
    supply_matches = list(SUPPLY_PATTERN.finditer(segment))
    if (
        len(gas_matches) != 1
        or len(demand_matches) != 1
        or len(supply_matches) != 1
    ):
        return None
    gas_type = next(iter(gas_matches))
    return {
        "gas_type": gas_type,
        "demand_gwh": _parse_gwh(demand_matches[0]),
        "contracted_supply_gwh": _parse_gwh(supply_matches[0]),
    }


def extract_gas_positions(portfolio_description: str) -> list[dict[str, Any]]:
    """Extract only unambiguous demand/supply pairs expressed in GWh."""
    positions = [
        position
        for segment in re.split(r"[.;\n]+", portfolio_description)
        if (position := _extract_position(segment)) is not None
    ]
    if positions:
        return positions
    position = _extract_position(portfolio_description)
    return [position] if position is not None else []


def _build_gas_analysis_messages(
    portfolio_description: str,
    calculations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    schema = json.dumps(
        GasB2BPortfolioAnalysis.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    prompt = f"""
Analyze the following B2B gas portfolio for healthcare and industrial customers.
Return only one valid JSON object. Do not use Markdown, code fences, comments, or
additional text. The JSON must conform exactly to this schema:

{schema}

Use only these gas types: natural_gas, biomethane, lng, hydrogen_blend.
Use only these risk levels: low, medium, high, critical.
Write every descriptive text in Spanish, including summary, commercial_impact,
affected_customer_segments, recommended_actions, assumptions, and any other
human-readable explanation. Keep all enum values and JSON field names exactly
as defined in the schema; do not translate values such as natural_gas, low,
medium, high, or critical.
Express all energy quantities in GWh. If information is missing, make a reasonable
commercial estimate and explain the assumption briefly in the summary.

Portfolio information:
{portfolio_description}

Deterministic business calculations (authoritative; do not recalculate):
{json.dumps(calculations, ensure_ascii=False, indent=2)}
""".strip()

    messages = [{"role": "user", "content": prompt}]
    return messages


def prepare_gas_analysis(
    portfolio_description: str,
    recorder: PerformanceRecorder,
) -> PreparedGasAnalysis:
    prompt_event = recorder.start_stage(
        "prompt_build",
        input_chars=len(portfolio_description),
    )
    try:
        description = portfolio_description.strip()
        if not description:
            raise GasAnalysisError(
                "Introduce los datos de la cartera que quieres analizar."
            )
        positions = extract_gas_positions(description)
    except Exception:
        recorder.fail_stage(prompt_event)
        raise
    recorder.complete_stage(
        prompt_event,
        message_count=1,
        approximate_prompt_chars=len(description),
        extracted_position_count=len(positions),
    )

    calculations: list[dict[str, Any]] = []
    tool_executions: list[dict[str, Any]] = []
    for position in positions:
        arguments = {
            "expected_demand_gwh": position["demand_gwh"],
            "contracted_supply_gwh": position["contracted_supply_gwh"],
        }
        tool_event = recorder.start_stage(
            "tool_execution",
            tool_name="calculate_supply_position",
        )
        try:
            execution = execute_tool_call(
                "calculate_supply_position",
                json.dumps(arguments),
            )
        except Exception:
            recorder.fail_stage(tool_event, tool_arguments=arguments)
            raise
        recorder.complete_stage(
            tool_event,
            tool_call_count=1,
            tool_seconds=execution.elapsed_seconds,
            tool_arguments=execution.arguments,
            tool_result=execution.result,
        )
        execution_data = execution.as_dict()
        tool_executions.append(execution_data)
        calculations.append(
            {
                "gas_type": position["gas_type"].value,
                "demand_gwh": position["demand_gwh"],
                "contracted_supply_gwh": position[
                    "contracted_supply_gwh"
                ],
                **execution.result,
            }
        )

    messages = _build_gas_analysis_messages(description, calculations)
    return PreparedGasAnalysis(messages, tool_executions)


def build_gas_analysis_messages(
    portfolio_description: str,
) -> list[dict[str, str]]:
    """Backward-compatible prompt builder without orchestration."""
    description = portfolio_description.strip()
    if not description:
        raise GasAnalysisError(
            "Introduce los datos de la cartera que quieres analizar."
        )
    return _build_gas_analysis_messages(description, [])


def parse_gas_portfolio_analysis(
    response_text: str,
) -> GasB2BPortfolioAnalysis:
    json_text = _remove_json_code_fence(response_text)

    parsing_started_at = perf_counter()
    print("[GAS_ANALYSIS] Iniciando parsing JSON", flush=True)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise GasAnalysisError(
            "El LLM no devolvió JSON válido. "
            f"Error cerca de la línea {error.lineno}, columna {error.colno}."
        ) from error
    finally:
        logger.info(
            "stage=json_parsing mode=gas_analysis duration_seconds=%.4f "
            "response_chars=%d",
            perf_counter() - parsing_started_at,
            len(response_text),
        )

    validation_started_at = perf_counter()
    try:
        analysis = GasB2BPortfolioAnalysis.model_validate(data)
        print(
            "[GAS_ANALYSIS] Validación Pydantic completada "
            f"parsing_seconds={validation_started_at - parsing_started_at:.3f} "
            f"validation_seconds={perf_counter() - validation_started_at:.3f}",
            flush=True,
        )
        return analysis
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()[:3]
        )
        raise GasAnalysisError(
            f"La respuesta JSON no cumple el modelo de análisis: {details}."
        ) from error
    finally:
        logger.info(
            "stage=pydantic_validation mode=gas_analysis duration_seconds=%.4f",
            perf_counter() - validation_started_at,
        )


def _remove_json_code_fence(response_text: str) -> str:
    text = response_text.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text

    return "\n".join(lines[1:-1]).strip()
