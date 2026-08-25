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


class ScenarioResult(BaseModel):
    name: str
    demand_variation_percent: float
    demand_gwh: float = Field(ge=0)
    supply_position_gwh: float
    short_position_gwh: float = Field(ge=0)
    spot_price_eur_mwh: float = Field(ge=0)
    spot_exposure_eur: float = Field(ge=0)
    estimated_margin_eur: float


class ScenarioInterpretation(BaseModel):
    summary: str
    key_risks: list[str]
    recommendation: str


class ScenarioAnalysis(ScenarioInterpretation):
    scenarios: list[ScenarioResult]


class ExtractedGasCase(BaseModel):
    gas_type: GasType
    base_demand_gwh: float = Field(ge=0)
    contracted_supply_gwh: float = Field(ge=0)
    supply_cost_eur_mwh: float = Field(ge=0)
    sales_price_eur_mwh: float = Field(ge=0)
    spot_price_eur_mwh: float = Field(ge=0)


@dataclass(frozen=True)
class PreparedGasAnalysis:
    messages: list[dict[str, str]]
    tool_executions: list[dict[str, Any]]
    scenarios: list[ScenarioResult]


@dataclass(frozen=True)
class ScenarioParsingResult:
    detected_gas_types: list[str]
    base_demand_candidates: list[float]
    contracted_supply_candidates: list[float]
    supply_cost_candidates: list[float]
    sales_price_candidates: list[float]
    spot_price_candidates: list[float]
    scenario_variations: list[float]
    missing_fields: list[str]
    ambiguities: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "detected_gas_types": self.detected_gas_types,
            "base_demand_candidates": self.base_demand_candidates,
            "contracted_supply_candidates": (
                self.contracted_supply_candidates
            ),
            "supply_cost_candidates": self.supply_cost_candidates,
            "sales_price_candidates": self.sales_price_candidates,
            "spot_price_candidates": self.spot_price_candidates,
            "scenario_variations": self.scenario_variations,
            "missing_fields": self.missing_fields,
            "ambiguities": self.ambiguities,
        }


class GasAnalysisError(ValueError):
    pass


DEFAULT_DEMAND_SCENARIOS = (
    ("Base", 0.0),
    ("Demanda +10 %", 10.0),
    ("Demanda +20 %", 20.0),
)


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
    r"\bdemanda(?:\s+(?:base|prevista|esperada|estimada|industrial))?"
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
SUPPLY_VALUE_FIRST_PATTERN = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*gwh\s+de\s+"
    r"(?:suministro\s+(?:ya\s+)?contratado|volumen\s+contratado)",
    re.IGNORECASE,
)
SCENARIO_BASE_PATTERN = re.compile(
    r"\bescenario\s+base\b", re.IGNORECASE
)
VARIATION_PATTERN = re.compile(
    r"\b(?P<label>variaci[oó]n|escenario|incremento|reducci[oó]n|"
    r"aumento|ca[ií]da|aumenta|incrementa|sube|disminuye|reduce|cae|"
    r"demanda)"
    r"(?:\s+(?:de|en|un))?\s*"
    r"(?P<value>[+-]?\s*\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
SPOT_PRICE_PATTERN = re.compile(
    r"\b(?:precio\s+(?:medio\s+)?spot|spot)[^.;\n?]{0,40}?"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:€|eur)\s*/\s*mwh\b",
    re.IGNORECASE,
)
SALES_PRICE_PATTERN = re.compile(
    r"\b(?:precio\s+(?:medio\s+)?de\s+venta|venta)"
    r"[^.;\n?]{0,40}?"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:€|eur)\s*/\s*mwh\b",
    re.IGNORECASE,
)
SUPPLY_COST_PATTERN = re.compile(
    r"\b(?:coste|costo)(?:\s+medio)?(?:\s+de\s+suministro)?"
    r"[^.;\n?]{0,40}?"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:€|eur)\s*/\s*mwh\b",
    re.IGNORECASE,
)
VOLUME_PATTERN = re.compile(
    r"\b(?:volumen(?:\s+comercial)?|para\s+un\s+volumen)"
    r"\s+(?:de\s+)?"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*gwh\b",
    re.IGNORECASE,
)


def _parse_gwh(match: re.Match[str]) -> float:
    return float(match.group("value").replace(",", "."))


def extract_demand_scenarios(
    text: str,
) -> tuple[tuple[str, float], ...]:
    """Return explicitly requested scenarios or the product defaults."""
    variations: list[float] = []
    if SCENARIO_BASE_PATTERN.search(text):
        variations.append(0.0)
    for match in VARIATION_PATTERN.finditer(text):
        value = _parse_gwh(match)
        label = match.group("label").lower()
        if re.fullmatch(
            r"reducci[oó]n|ca[ií]da|disminuye|reduce|cae", label
        ) and value > 0:
            value = -value
        if value not in variations:
            variations.append(value)
    if not variations:
        return DEFAULT_DEMAND_SCENARIOS
    return tuple((_scenario_name(value), value) for value in variations)


def _scenario_name(variation_percent: float) -> str:
    if variation_percent == 0:
        return "Base"
    sign = "+" if variation_percent > 0 else ""
    return f"Demanda {sign}{variation_percent:g} %"


def _candidate_values(
    text: str, *patterns: re.Pattern[str]
) -> list[float]:
    values = {
        _parse_gwh(match)
        for pattern in patterns
        for match in pattern.finditer(text)
    }
    return sorted(values)


def parse_scenario_input(text: str) -> ScenarioParsingResult:
    """Extract safe diagnostic candidates without interpreting with an LLM."""
    gas_types = sorted(
        {
            GAS_ALIASES[" ".join(match.group(0).lower().split())].value
            for match in GAS_PATTERN.finditer(text)
        }
    )
    candidates = {
        "base_demand_gwh": _candidate_values(text, DEMAND_PATTERN),
        "contracted_supply_gwh": _candidate_values(
            text, SUPPLY_PATTERN, SUPPLY_VALUE_FIRST_PATTERN
        ),
        "supply_cost_eur_mwh": _candidate_values(
            text, SUPPLY_COST_PATTERN
        ),
        "sales_price_eur_mwh": _candidate_values(
            text, SALES_PRICE_PATTERN
        ),
        "spot_price_eur_mwh": _candidate_values(text, SPOT_PRICE_PATTERN),
    }
    missing_fields = [
        field_name
        for field_name, values in candidates.items()
        if not values
    ]
    if not gas_types:
        missing_fields.insert(0, "gas_type")
    ambiguities = [
        field_name
        for field_name, values in candidates.items()
        if len(values) > 1
    ]
    if len(gas_types) > 1:
        ambiguities.insert(0, "gas_type")
    scenarios = extract_demand_scenarios(text)
    return ScenarioParsingResult(
        detected_gas_types=gas_types,
        base_demand_candidates=candidates["base_demand_gwh"],
        contracted_supply_candidates=candidates[
            "contracted_supply_gwh"
        ],
        supply_cost_candidates=candidates["supply_cost_eur_mwh"],
        sales_price_candidates=candidates["sales_price_eur_mwh"],
        spot_price_candidates=candidates["spot_price_eur_mwh"],
        scenario_variations=[value for _, value in scenarios],
        missing_fields=missing_fields,
        ambiguities=ambiguities,
    )


def _raise_for_parsing_errors(result: ScenarioParsingResult) -> None:
    missing_messages = {
        "gas_type": "No se ha identificado el tipo de gas.",
        "base_demand_gwh": "Falta la demanda base.",
        "contracted_supply_gwh": (
            "No se ha identificado el suministro contratado."
        ),
        "supply_cost_eur_mwh": (
            "No se ha identificado el coste de suministro."
        ),
        "sales_price_eur_mwh": (
            "No se ha identificado el precio de venta."
        ),
        "spot_price_eur_mwh": "No se ha identificado el precio spot.",
    }
    if result.missing_fields:
        raise GasAnalysisError(missing_messages[result.missing_fields[0]])
    if "gas_type" in result.ambiguities:
        raise GasAnalysisError("Se han detectado varios tipos de gas.")
    ambiguity_values = {
        "base_demand_gwh": (
            "varias",
            "demandas posibles",
            result.base_demand_candidates,
            "GWh",
        ),
        "contracted_supply_gwh": (
            "varios",
            "suministros contratados posibles",
            result.contracted_supply_candidates,
            "GWh",
        ),
        "supply_cost_eur_mwh": (
            "varios",
            "costes de suministro posibles",
            result.supply_cost_candidates,
            "€/MWh",
        ),
        "sales_price_eur_mwh": (
            "varios",
            "precios de venta posibles",
            result.sales_price_candidates,
            "€/MWh",
        ),
        "spot_price_eur_mwh": (
            "varios",
            "precios spot posibles",
            result.spot_price_candidates,
            "€/MWh",
        ),
    }
    if result.ambiguities:
        quantifier, label, values, unit = ambiguity_values[
            result.ambiguities[0]
        ]
        formatted = ", ".join(f"{value:g} {unit}" for value in values)
        raise GasAnalysisError(
            f"Se han detectado {quantifier} {label}: {formatted}."
        )


def _execute_recorded_tool(
    tool_name: str,
    arguments: dict[str, float],
    recorder: PerformanceRecorder,
    scenario_name: str | None = None,
    demand_variation_percent: float | None = None,
) -> dict[str, Any]:
    tool_event = recorder.start_stage(
        "tool_execution",
        tool_name=tool_name,
        scenario_name=scenario_name,
        demand_variation_percent=demand_variation_percent,
    )
    try:
        execution = execute_tool_call(tool_name, json.dumps(arguments))
    except Exception:
        recorder.fail_stage(tool_event, tool_arguments=arguments)
        raise
    recorder.complete_stage(
        tool_event,
        tool_call_count=1,
        tool_seconds=execution.elapsed_seconds,
        tool_arguments=execution.arguments,
        tool_result=execution.result,
        scenario_name=scenario_name,
        demand_variation_percent=demand_variation_percent,
    )
    return execution.as_dict()


def _extract_position(segment: str) -> dict[str, Any] | None:
    gas_matches = {
        GAS_ALIASES[" ".join(match.group(0).lower().split())]
        for match in GAS_PATTERN.finditer(segment)
    }
    demand_matches = list(DEMAND_PATTERN.finditer(segment))
    supply_matches = list(SUPPLY_PATTERN.finditer(segment))
    supply_matches.extend(SUPPLY_VALUE_FIRST_PATTERN.finditer(segment))
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
        ScenarioInterpretation.model_json_schema(),
        ensure_ascii=False,
        indent=2,
    )
    prompt = f"""
Interpret the following deterministic B2B gas demand scenarios for healthcare
and industrial customers. Do not calculate or alter any numeric result.
Return only one valid JSON object. Do not use Markdown, code fences, comments, or
additional text. The JSON must conform exactly to this schema:

{schema}

Write summary, key_risks, and recommendation in Spanish. Base the interpretation
only on the authoritative deterministic results below.

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
    input_event = recorder.start_stage(
        "input_parsing",
        input_chars=len(portfolio_description),
    )
    parsing_result: ScenarioParsingResult | None = None
    try:
        description = portfolio_description.strip()
        parsing_result = parse_scenario_input(description)
        if not description:
            raise GasAnalysisError(
                "Introduce los datos de la cartera que quieres analizar."
            )
        _raise_for_parsing_errors(parsing_result)
        base_case = ExtractedGasCase(
            gas_type=GasType(parsing_result.detected_gas_types[0]),
            base_demand_gwh=parsing_result.base_demand_candidates[0],
            contracted_supply_gwh=(
                parsing_result.contracted_supply_candidates[0]
            ),
            supply_cost_eur_mwh=parsing_result.supply_cost_candidates[0],
            sales_price_eur_mwh=parsing_result.sales_price_candidates[0],
            spot_price_eur_mwh=parsing_result.spot_price_candidates[0],
        )
        demand_scenarios = tuple(
            (_scenario_name(value), value)
            for value in parsing_result.scenario_variations
        )
    except Exception:
        recorder.fail_stage(
            input_event,
            parsing_result=(
                parsing_result.as_dict() if parsing_result else None
            ),
        )
        raise
    recorder.complete_stage(
        input_event,
        message_count=1,
        approximate_prompt_chars=len(description),
        extracted_position_count=1,
        gas_type=base_case.gas_type.value,
        parsing_result=parsing_result.as_dict(),
    )

    scenario_event = recorder.start_stage(
        "scenario_generation",
        scenario_count=len(demand_scenarios),
    )
    tool_executions: list[dict[str, Any]] = []
    scenarios: list[ScenarioResult] = []
    try:
        for name, variation_percent in demand_scenarios:
            scenario, executions = _calculate_scenario(
                name, variation_percent, base_case, recorder
            )
            scenarios.append(scenario)
            tool_executions.extend(executions)
    except Exception:
        recorder.fail_stage(scenario_event)
        raise
    recorder.complete_stage(
        scenario_event,
        scenario_count=len(scenarios),
    )
    calculations = [
        {"gas_type": base_case.gas_type.value, **item.model_dump()}
        for item in scenarios
    ]
    messages = _build_gas_analysis_messages(description, calculations)
    return PreparedGasAnalysis(messages, tool_executions, scenarios)


def _calculate_scenario(
    name: str,
    variation_percent: float,
    base_case: ExtractedGasCase,
    recorder: PerformanceRecorder,
) -> tuple[ScenarioResult, list[dict[str, Any]]]:
    metadata = {
        "scenario_name": name,
        "demand_variation_percent": variation_percent,
    }
    demand = _execute_recorded_tool(
        "calculate_demand_scenario",
        {
            "base_demand_gwh": base_case.base_demand_gwh,
            "variation_percent": variation_percent,
        },
        recorder,
        **metadata,
    )
    demand_gwh = demand["result"]["scenario_demand_gwh"]
    position = _execute_recorded_tool(
        "calculate_supply_position",
        {
            "expected_demand_gwh": demand_gwh,
            "contracted_supply_gwh": base_case.contracted_supply_gwh,
        },
        recorder,
        **metadata,
    )
    exposure = _execute_recorded_tool(
        "calculate_spot_exposure",
        {
            "expected_demand_gwh": demand_gwh,
            "contracted_supply_gwh": base_case.contracted_supply_gwh,
            "spot_price_eur_mwh": base_case.spot_price_eur_mwh,
        },
        recorder,
        **metadata,
    )
    margin = _execute_recorded_tool(
        "calculate_margin",
        {
            "sales_price_eur_mwh": base_case.sales_price_eur_mwh,
            "supply_cost_eur_mwh": base_case.supply_cost_eur_mwh,
            "volume_gwh": demand_gwh,
        },
        recorder,
        **metadata,
    )
    short_position_gwh = exposure["result"]["short_position_gwh"]
    spot_cost_delta_eur = short_position_gwh * 1000 * (
        base_case.spot_price_eur_mwh - base_case.supply_cost_eur_mwh
    )
    scenario = ScenarioResult(
        name=name,
        demand_variation_percent=variation_percent,
        demand_gwh=demand_gwh,
        supply_position_gwh=position["result"]["position_gwh"],
        short_position_gwh=short_position_gwh,
        spot_price_eur_mwh=base_case.spot_price_eur_mwh,
        spot_exposure_eur=exposure["result"]["exposure_eur"],
        estimated_margin_eur=(
            margin["result"]["total_margin_eur"] - spot_cost_delta_eur
        ),
    )
    return scenario, [demand, position, exposure, margin]


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


def parse_scenario_analysis(
    response_text: str,
    scenarios: list[ScenarioResult],
    recorder: PerformanceRecorder,
) -> ScenarioAnalysis:
    parse_event = recorder.start_stage(
        "parse_validation",
        response_chars=len(response_text),
    )
    json_text = _remove_json_code_fence(response_text)
    try:
        data = json.loads(json_text)
        interpretation = ScenarioInterpretation.model_validate(data)
        analysis = ScenarioAnalysis(
            scenarios=scenarios,
            **interpretation.model_dump(),
        )
    except json.JSONDecodeError as error:
        recorder.fail_stage(
            parse_event,
            error_type="invalid_json",
            error_line=error.lineno,
            error_column=error.colno,
        )
        raise GasAnalysisError(
            "El LLM no devolvió JSON válido. "
            f"Error cerca de la línea {error.lineno}, columna {error.colno}."
        ) from error
    except ValidationError as error:
        recorder.fail_stage(
            parse_event,
            error_type="schema_validation",
            validation_error_count=error.error_count(),
        )
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()[:3]
        )
        raise GasAnalysisError(
            f"La interpretación no cumple el modelo esperado: {details}."
        ) from error
    recorder.complete_stage(parse_event)
    return analysis


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
