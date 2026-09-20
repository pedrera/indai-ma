"""Deterministic parsing and projection primitives for temporal take-or-pay."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TakeOrPayInputs(BaseModel):
    """Operational inputs; missing values remain explicitly absent."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    cumulative_consumption_gwh: float | None = Field(default=None, ge=0)
    remaining_forecast_consumption_gwh: float | None = Field(default=None, ge=0)


class TakeOrPayProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    cumulative_consumption_gwh: float
    remaining_forecast_consumption_gwh: float
    projected_annual_consumption_gwh: float
    take_or_pay_minimum_gwh: float
    projected_take_or_pay_deficit_gwh: float
    status: Literal["ABOVE_MINIMUM", "AT_MINIMUM", "BELOW_MINIMUM"]


_NUMBER = r"\d+(?:[\.,]\d+)?"
_CUMULATIVE = re.compile(
    rf"(?:consumo\s+acumulado|acumulado\s+(?:hasta\s+la\s+fecha|actualmente)?|hemos\s+consumido)\s*(?:es\s+de|de)?\s*({_NUMBER})\s*gwh",
    re.IGNORECASE,
)
_REMAINING = re.compile(
    rf"(?:esperamos\s+consumir\s+otros?|previsi[oó]n\s+restante|consumo\s+restante\s+previsto)\s*(?:es\s+de|de)?\s*({_NUMBER})\s*gwh",
    re.IGNORECASE,
)


def _number(value: str) -> float:
    return float(value.replace(".", "").replace(",", ".")) if "," in value else float(value)


def parse_take_or_pay_inputs(text: str) -> TakeOrPayInputs:
    """Extract TOP temporal inputs without populating monthly demand/supply."""
    cumulative = _CUMULATIVE.search(text)
    remaining = _REMAINING.search(text)
    return TakeOrPayInputs(
        cumulative_consumption_gwh=_number(cumulative.group(1)) if cumulative else None,
        remaining_forecast_consumption_gwh=_number(remaining.group(1)) if remaining else None,
    )


def calculate_take_or_pay_projection(
    take_or_pay_minimum_gwh: float,
    cumulative_consumption_gwh: float,
    remaining_forecast_consumption_gwh: float,
) -> TakeOrPayProjection:
    """Project annual consumption against an existing TOP minimum."""
    if min(take_or_pay_minimum_gwh, cumulative_consumption_gwh,
           remaining_forecast_consumption_gwh) < 0:
        raise ValueError("Take-or-pay inputs must be non-negative.")
    projected = round(cumulative_consumption_gwh + remaining_forecast_consumption_gwh, 12)
    deficit = round(max(take_or_pay_minimum_gwh - projected, 0.0), 12)
    if projected > take_or_pay_minimum_gwh:
        status = "ABOVE_MINIMUM"
    elif projected == take_or_pay_minimum_gwh:
        status = "AT_MINIMUM"
    else:
        status = "BELOW_MINIMUM"
    return TakeOrPayProjection(
        cumulative_consumption_gwh=cumulative_consumption_gwh,
        remaining_forecast_consumption_gwh=remaining_forecast_consumption_gwh,
        projected_annual_consumption_gwh=projected,
        take_or_pay_minimum_gwh=take_or_pay_minimum_gwh,
        projected_take_or_pay_deficit_gwh=deficit,
        status=status,
    )
