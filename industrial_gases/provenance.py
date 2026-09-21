from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .models import Quantity, _SignedQuantity


@dataclass(frozen=True)
class Provenance:
    field: str
    value: Quantity | _SignedQuantity | Decimal | bool | str | None
    origin: Literal["operational_input", "configuration", "deterministic_calculation"]
    source: str
