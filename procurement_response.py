import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProcurementResponseValidation:
    content: str
    is_valid: bool
    fallback_used: bool
    reasons: tuple[str, ...]


def validate_procurement_final_response(
    content: str,
    executions: list[dict[str, Any]],
) -> ProcurementResponseValidation:
    """Validate observable business claims and provide a safe canonical fallback."""
    lowered = content.casefold()
    reasons: list[str] = []
    position = _execution(executions, "calculate_supply_position")
    exposure = _execution(executions, "calculate_spot_exposure")

    if position:
        result = position.get("result", {})
        interpretation = str(result.get("interpretation", ""))
        if interpretation and not _mentions_interpretation(lowered, interpretation):
            reasons.append("position_interpretation_missing")
        reasons.extend(_position_claim_reasons(lowered, result))

    if exposure is None and _has_unsupported_exposure_claim(lowered):
        reasons.append("economic_exposure_without_tool_result")
    authoritative_amounts = {
        round(float(item.get("result", {}).get("exposure_eur")), 2)
        for item in executions
        if item.get("name") == "calculate_spot_exposure"
        and item.get("result", {}).get("exposure_eur") is not None
    }
    claimed_amounts = _economic_amounts(content)
    if any(round(amount, 2) not in authoritative_amounts for amount in claimed_amounts):
        reasons.append("unsupported_economic_amount")

    if any(
        phrase in lowered
        for phrase in (
            "herramientas adicionales",
            "información adicional",
            "informacion adicional",
            "faltan datos",
            "se requiere evaluar herramientas",
        )
    ):
        reasons.append("unsupported_missing_information_claim")

    if not reasons:
        return ProcurementResponseValidation(content, True, False, ())
    return ProcurementResponseValidation(
        build_canonical_procurement_response(executions),
        False,
        True,
        tuple(dict.fromkeys(reasons)),
    )


def build_canonical_procurement_response(
    executions: list[dict[str, Any]],
) -> str:
    sentences: list[str] = []
    position = _execution(executions, "calculate_supply_position")
    if position:
        result = position.get("result", {})
        interpretation = str(result.get("interpretation", ""))
        magnitude = abs(float(result.get("position_gwh", 0)))
        if interpretation == "BALANCED":
            sentences.append("La posición de aprovisionamiento es BALANCED (0 GWh).")
        else:
            sentences.append(
                "La posición de aprovisionamiento es "
                f"{interpretation} en {_plain_number(magnitude)} GWh."
            )

    exposure = _execution(executions, "calculate_spot_exposure")
    if exposure:
        value = float(exposure.get("result", {}).get("exposure_eur", 0))
        sentences.append(
            "El coste para cubrir el déficit a precio spot es de "
            f"{_currency(value)} €."
        )

    for execution in executions:
        if execution.get("name") != "calculate_demand_scenario":
            continue
        result = execution.get("result", {})
        variation = float(result.get("variation_percent", 0))
        demand = float(result.get("scenario_demand_gwh", 0))
        sentences.append(
            f"El escenario de demanda {_signed_percent(variation)} resulta en "
            f"{_plain_number(demand)} GWh."
        )
    return " ".join(sentences) or "No hay resultados deterministas disponibles."


def _execution(executions: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in executions if item.get("name") == name), None)


def _position_claim_reasons(text: str, result: dict[str, Any]) -> list[str]:
    """Check explicit quantity claims; signed net position differs from cover volume."""
    text = re.sub(r"[*_`]+", "", text).replace("−", "-")
    number = r"[+-]?\s*\d+(?:[.,]\d+)?"
    labels = (
        r"posici[oó]n\s+neta|saldo\s+neto|"
        r"posici[oó]n(?:\s+de\s+aprovisionamiento)?|"
        r"d[eé]ficit|excedente|exceso|volumen\s+a\s+cubrir|short|long|balanced"
    )
    pattern = re.compile(
        rf"\b(?P<label>{labels})\b\s*"
        rf"(?:(?:es|de|en|del|equivale\s+a|short|long|balanced)\s+)*"
        rf"[:=]?\s*(?P<value>{number})\s*gwh\b"
    )
    signed_position = float(result.get("position_gwh", 0))
    reasons = []
    for match in pattern.finditer(text):
        label = match["label"]
        value = float(re.sub(r"\s", "", match["value"]).replace(",", "."))
        is_net = label in {"posición neta", "posicion neta", "saldo neto"}
        if is_net:
            expected = signed_position
        elif label in {"déficit", "deficit", "volumen a cubrir"}:
            expected = max(0, -signed_position)
        elif label in {"excedente", "exceso"}:
            expected = max(0, signed_position)
        else:
            expected = abs(signed_position)
        if value < 0 and not is_net and result.get("interpretation") == "SHORT":
            reasons.append("short_deficit_presented_as_negative")
        if not abs(value - expected) <= 1e-9:
            reasons.append("position_quantity_mismatch")
    return reasons


def _has_unsupported_exposure_claim(text: str) -> bool:
    remaining = text
    negated_patterns = (
        r"(?:no\s+(?:hay|existe)|sin)\s+(?:una\s+)?exposición(?:\s+económica|\s+(?:al|en\s+el)\s+(?:precio\s+)?spot|\s+spot)",
        r"no\s+se\s+(?:calcula|ha\s+calculado|requiere(?:\s+calcular)?)\s+(?:la\s+)?exposición(?:\s+económica|\s+(?:al|en\s+el)\s+(?:precio\s+)?spot|\s+spot)",
        r"no\s+(?:es\s+)?necesari[oa]\s+(?:calcular|evaluar|cubrir)\s+(?:la\s+)?exposición(?:\s+económica|\s+(?:al|en\s+el)\s+(?:precio\s+)?spot|\s+spot)",
        r"la\s+exposición(?:\s+económica|\s+(?:al|en\s+el)\s+(?:precio\s+)?spot|\s+spot)\s+no\s+es\s+relevante",
        r"(?:no\s+(?:hay|existe)|sin)\s+(?:un\s+)?(?:coste|costo)\s+para\s+cubrir",
        r"no\s+(?:es\s+)?necesari[oa]\s+(?:calcular|asumir)\s+(?:un\s+)?(?:coste|costo)\s+para\s+cubrir",
    )
    for pattern in negated_patterns:
        remaining = re.sub(pattern, "", remaining)
    return any(
        phrase in remaining
        for phrase in (
            "exposición económica",
            "exposición en el mercado spot",
            "exposición spot",
            "coste para cubrir",
            "costo para cubrir",
        )
    ) or bool(re.search(r"(?:€|\beur\b)(?!\s*/\s*mwh)", remaining))


def _mentions_interpretation(text: str, interpretation: str) -> bool:
    terms = {
        "SHORT": ("short", "corta", "corto", "déficit", "deficit"),
        "LONG": ("long", "larga", "largo", "exceso", "excedente"),
        "BALANCED": ("balanced", "equilibrada", "equilibrado", "equilibrio"),
    }
    return any(term in text for term in terms.get(interpretation, (interpretation.casefold(),)))


def _economic_amounts(text: str) -> list[float]:
    values: list[float] = []
    pattern = re.compile(r"(\d[\d.,\s]*)\s*(?:€|EUR\b)(?!\s*/\s*MWh)", re.IGNORECASE)
    for match in pattern.finditer(text):
        raw = re.sub(r"\s", "", match.group(1))
        if "," in raw and "." in raw:
            decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            raw = raw.replace(thousands, "").replace(decimal, ".")
        elif raw.count(".") > 1 or raw.count(",") > 1:
            raw = raw.replace(".", "").replace(",", "")
        elif re.fullmatch(r"\d+[.,]\d{3}", raw):
            raw = raw.replace(".", "").replace(",", "")
        else:
            raw = raw.replace(",", ".")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return values


def _plain_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _currency(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _signed_percent(value: float) -> str:
    return f"{value:+g}%"
