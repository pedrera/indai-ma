import re
from dataclasses import dataclass
from typing import Any, Iterable


NUMBER = r"(?P<value>\d+(?:[.,]\d+)?)"
REFERENCE_PATTERN = re.compile(
    rf"\b(?:consumo|volumen)\s+mensual(?:\s+de)?\s+referencia"
    rf"[^.;]{{0,80}}?{NUMBER}\s*gwh\b",
    re.IGNORECASE,
)
REFERENCE_INVERTED_PATTERN = re.compile(
    rf"\breferencia(?:\s+un)?\s+(?:consumo|volumen)\s+mensual"
    rf"(?:\s+de)?[^.;]{{0,40}}?{NUMBER}\s*gwh\b",
    re.IGNORECASE,
)
FLEXIBILITY_PATTERN = re.compile(
    rf"\bflexibilidad(?:\s+de\s+consumo)?[^.;\n]{{0,50}}?"
    rf"(?:[+\-±]\s*)?{NUMBER}\s*%",
    re.IGNORECASE,
)
SURCHARGE_PATTERN = re.compile(
    rf"\b(?:precio\s+)?spot\s*\+\s*{NUMBER}\s*"
    rf"(?:€|eur)\s*/\s*mwh\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContractualVolumeTerms:
    reference_volume_gwh: float | None = None
    flexibility_percent: float | None = None
    excess_surcharge_eur_mwh: float | None = None
    source_chunk_ids: tuple[str, ...] = ()
    field_sources: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "contractual_reference_gwh": self.reference_volume_gwh,
            "flexibility_percent": self.flexibility_percent,
            "excess_surcharge_eur_mwh": self.excess_surcharge_eur_mwh,
            "source_chunk_ids": list(self.source_chunk_ids),
            "field_sources": dict(self.field_sources or {}),
        }


@dataclass(frozen=True)
class ContractualVolumeImpact:
    contractual_reference_gwh: float
    contractual_max_gwh: float
    forecast_demand_gwh: float
    contractual_excess_gwh: float
    spot_price_eur_mwh: float | None = None
    contractual_excess_price_eur_mwh: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "contractual_reference_gwh": self.contractual_reference_gwh,
            "contractual_max_gwh": self.contractual_max_gwh,
            "forecast_demand_gwh": self.forecast_demand_gwh,
            "contractual_excess_gwh": self.contractual_excess_gwh,
            "spot_price_eur_mwh": self.spot_price_eur_mwh,
            "contractual_excess_price_eur_mwh": (
                self.contractual_excess_price_eur_mwh
            ),
        }


def extract_contractual_volume_terms(
    chunks: Iterable[dict[str, str]],
) -> ContractualVolumeTerms:
    """Extract unambiguous contractual facts without using an LLM."""
    candidates: dict[str, list[tuple[float, str]]] = {
        "reference_volume_gwh": [],
        "flexibility_percent": [],
        "excess_surcharge_eur_mwh": [],
    }
    patterns = {
        "reference_volume_gwh": (
            REFERENCE_PATTERN,
            REFERENCE_INVERTED_PATTERN,
        ),
        "flexibility_percent": (FLEXIBILITY_PATTERN,),
        "excess_surcharge_eur_mwh": (SURCHARGE_PATTERN,),
    }
    for chunk in chunks:
        text = chunk.get("text", "")
        chunk_id = chunk.get("chunk_id", "")
        for field_name, field_patterns in patterns.items():
            for pattern in field_patterns:
                for match in pattern.finditer(text):
                    value = _number(match.group("value"))
                    item = (value, chunk_id)
                    if item not in candidates[field_name]:
                        candidates[field_name].append(item)

    resolved: dict[str, float | None] = {}
    sources: dict[str, str] = {}
    for field_name, values in candidates.items():
        distinct = {value for value, _ in values}
        if len(distinct) == 1:
            resolved[field_name] = next(iter(distinct))
            sources[field_name] = next(
                (chunk_id for _, chunk_id in values if chunk_id), ""
            )
        else:
            resolved[field_name] = None
    return ContractualVolumeTerms(
        **resolved,
        source_chunk_ids=tuple(
            item for item in dict.fromkeys(sources.values()) if item
        ),
        field_sources=sources,
    )


def calculate_contractual_volume_impact(
    terms: ContractualVolumeTerms,
    forecast_demand_gwh: float,
    spot_price_eur_mwh: float | None = None,
) -> ContractualVolumeImpact | None:
    """Calculate contractual excess independently from supply position."""
    if (
        terms.reference_volume_gwh is None
        or terms.flexibility_percent is None
    ):
        return None
    contractual_max = terms.reference_volume_gwh * (
        1 + terms.flexibility_percent / 100
    )
    excess = max(forecast_demand_gwh - contractual_max, 0)
    excess_price = None
    if spot_price_eur_mwh is not None and terms.excess_surcharge_eur_mwh is not None:
        excess_price = spot_price_eur_mwh + terms.excess_surcharge_eur_mwh
    return ContractualVolumeImpact(
        contractual_reference_gwh=_rounded(terms.reference_volume_gwh),
        contractual_max_gwh=_rounded(contractual_max),
        forecast_demand_gwh=_rounded(forecast_demand_gwh),
        contractual_excess_gwh=_rounded(excess),
        spot_price_eur_mwh=spot_price_eur_mwh,
        contractual_excess_price_eur_mwh=(
            _rounded(excess_price) if excess_price is not None else None
        ),
    )


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _rounded(value: float) -> float:
    return round(value, 12)
