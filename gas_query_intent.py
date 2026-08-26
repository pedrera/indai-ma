import re
import unicodedata
from dataclasses import dataclass


DOCUMENTARY_SIGNALS = (
    "cual es",
    "que establece",
    "segun el contrato",
    "flexibilidad",
    "take-or-pay",
    "take or pay",
    "clausula",
    "penalizacion",
    "precio contractual",
    "vencimiento",
    "condiciones",
    "rango mensual",
    "sin penalizacion",
)
QUANTITATIVE_SIGNALS = (
    "demanda",
    "suministro",
    "aprovisionado",
    "aprovisionamiento",
    "posicion",
    "short",
    "long",
    "margen",
    "escenario",
    "exposicion",
    "spot",
    "calcula",
    "calcular",
    "analiza",
)
STRONG_CALCULATION_SIGNALS = (
    "posicion",
    "short",
    "long",
    "margen",
    "escenario",
    "exposicion",
    "calcula",
    "calcular",
    "analiza",
)
GWH_PATTERN = re.compile(r"\d+(?:[.,]\d+)?\s*gwh\b", re.IGNORECASE)
PRICE_PATTERN = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:€|eur)\s*/\s*mwh\b", re.IGNORECASE
)
PERCENT_PATTERN = re.compile(r"[+-]?\s*\d+(?:[.,]\d+)?\s*%")


@dataclass(frozen=True)
class GasQueryIntentResult:
    intent: str
    documentary_signals: list[str]
    quantitative_signals: list[str]
    numeric_evidence: dict[str, int]
    reason: str


def classify_gas_query(user_text: str) -> GasQueryIntentResult:
    normalized = _normalize(user_text)
    documentary = [item for item in DOCUMENTARY_SIGNALS if item in normalized]
    quantitative = [item for item in QUANTITATIVE_SIGNALS if item in normalized]
    evidence = {
        "gwh_values": len(GWH_PATTERN.findall(user_text)),
        "price_values": len(PRICE_PATTERN.findall(user_text)),
        "percent_values": len(PERCENT_PATTERN.findall(user_text)),
    }
    categories = sum(value > 0 for value in evidence.values())
    strong_calculation = any(
        item in normalized for item in STRONG_CALCULATION_SIGNALS
    )
    sufficient_numeric_data = (
        evidence["gwh_values"] >= 2
        or (strong_calculation and categories >= 2)
    )
    if sufficient_numeric_data and quantitative:
        return GasQueryIntentResult(
            "quantitative",
            documentary,
            quantitative,
            evidence,
            "sufficient_numeric_evidence",
        )
    reason = (
        "documentary_signals_without_sufficient_numeric_evidence"
        if documentary
        else "insufficient_numeric_evidence_defaults_to_documentary"
    )
    return GasQueryIntentResult(
        "documentary", documentary, quantitative, evidence, reason
    )


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(character for character in decomposed if not unicodedata.combining(character))
