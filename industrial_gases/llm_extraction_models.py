"""Strict intermediate response contract for LLM-assisted extraction."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExtractedQuantityCandidate(_ExtractionModel):
    value: Decimal
    unit: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def _finite_nonnegative(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("candidate value must be finite and nonnegative")
        return value


class ExtractedRateCandidate(ExtractedQuantityCandidate):
    time_unit: str = Field(min_length=1)


class ExtractedRelativeTimeCandidate(_ExtractionModel):
    kind: str = Field(min_length=1)
    value: int = Field(ge=0)
    evidence: str = Field(min_length=1)


class LLMSupplyExtraction(_ExtractionModel):
    current_inventory: ExtractedQuantityCandidate | None = None
    consumption_rate: ExtractedRateCandidate | None = None
    planned_delivery_quantity: ExtractedQuantityCandidate | None = None
    planned_delivery_time: ExtractedRelativeTimeCandidate | None = None
    safety_stock: ExtractedQuantityCandidate | None = None
    customer_label: str | None = None
    site_label: str | None = None
    application_label: str | None = None
    gas_product_label: str | None = None
    installation_label: str | None = None


class ExtractionOperationalError(RuntimeError):
    """Provider/JSON/schema failure; distinct from absent business facts."""

    def __init__(self, code: Literal["invalid_json", "schema_invalid", "timeout", "provider_failure"], message: str):
        super().__init__(message)
        self.code = code
