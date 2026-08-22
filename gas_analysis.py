import json
from enum import Enum
from time import perf_counter

from pydantic import BaseModel, Field, ValidationError

from diagnostics import get_performance_logger


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


class GasAnalysisError(ValueError):
    pass


def build_gas_analysis_messages(
    portfolio_description: str,
) -> list[dict[str, str]]:
    started_at = perf_counter()
    description = portfolio_description.strip()
    if not description:
        raise GasAnalysisError("Introduce los datos de la cartera que quieres analizar.")

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
{description}
""".strip()

    messages = [{"role": "user", "content": prompt}]
    logger.info(
        "stage=prompt_build mode=gas_analysis duration_seconds=%.4f "
        "message_count=%d approximate_prompt_chars=%d",
        perf_counter() - started_at,
        len(messages),
        len(prompt),
    )
    return messages


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
