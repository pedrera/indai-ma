"""Structured facts and identity state for the Industrial Gases boundary."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .models import ConsumptionRate, Quantity


@dataclass(frozen=True)
class ExtractionProvenance:
    field: str
    origin: Literal["explicit_input", "resolved_configuration"]
    extractor: Literal["deterministic", "llm"] = "deterministic"
    source_text: str | None = None


@dataclass(frozen=True)
class ExtractedSupplyFacts:
    current_inventory: Quantity | None = None
    consumption_rate: ConsumptionRate | None = None
    planned_delivery_at: datetime | None = None
    planned_delivery_quantity: Quantity | None = None
    safety_stock: Quantity | None = None
    reference_time: datetime | None = None
    provenance: tuple[ExtractionProvenance, ...] = ()


@dataclass(frozen=True)
class IdentityReference:
    """Resolved object, absent label, unresolved label, or ambiguous candidates."""
    value: Any = None
    label: str | None = None
    candidates: tuple[str, ...] = ()

    def __post_init__(self):
        label = self.label.strip() if isinstance(self.label, str) else self.label
        candidates = tuple(candidate.strip() if isinstance(candidate, str) else candidate
                          for candidate in self.candidates)
        if any(not isinstance(candidate, str) or not candidate for candidate in candidates):
            raise ValueError("identity candidates must be non-empty strings")
        if self.value is not None and candidates:
            raise ValueError("resolved identity cannot contain ambiguous candidates")
        object.__setattr__(self, "label", label or None)
        object.__setattr__(self, "candidates", candidates)

    @property
    def status(self) -> str:
        if self.value is not None:
            return "resolved"
        if self.candidates:
            return "ambiguous"
        if self.label:
            return "unresolved"
        return "absent"


@dataclass(frozen=True)
class ResolvedSupplyIdentity:
    customer: IdentityReference = IdentityReference()
    site: IdentityReference = IdentityReference()
    application: IdentityReference = IdentityReference()
    gas_product: IdentityReference = IdentityReference()
    installation: IdentityReference = IdentityReference()


@dataclass(frozen=True)
class ExtractionIssue:
    field: str
    code: Literal[
        "evidence_not_found", "number_mismatch", "unit_mismatch",
        "unsupported_unit", "ambiguous_numeric_evidence",
        "field_evidence_mismatch", "unsupported_temporal_expression",
    ]
