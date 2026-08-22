import json
from enum import Enum

from pydantic import BaseModel, Field, ValidationError


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
Express all energy quantities in GWh. If information is missing, make a reasonable
commercial estimate and explain the assumption briefly in the summary.

Portfolio information:
{description}
""".strip()

    return [{"role": "user", "content": prompt}]


def parse_gas_portfolio_analysis(
    response_text: str,
) -> GasB2BPortfolioAnalysis:
    json_text = _remove_json_code_fence(response_text)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise GasAnalysisError(
            "El LLM no devolvió JSON válido. "
            f"Error cerca de la línea {error.lineno}, columna {error.colno}."
        ) from error

    try:
        return GasB2BPortfolioAnalysis.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()[:3]
        )
        raise GasAnalysisError(
            f"La respuesta JSON no cumple el modelo de análisis: {details}."
        ) from error


def _remove_json_code_fence(response_text: str) -> str:
    text = response_text.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text

    return "\n".join(lines[1:-1]).strip()
