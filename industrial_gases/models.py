from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


class DomainValidationError(ValueError):
    """Expected invalid domain or input data."""


def _decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric domain value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as error:
        raise ValueError("value must be numeric") from error
    if not result.is_finite():
        raise ValueError("value must be finite")
    return result


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str

    def __post_init__(self):
        value = _decimal(self.value)
        if value < 0:
            raise ValueError("quantity cannot be negative")
        if not self.unit:
            raise ValueError("quantity unit is required")
        object.__setattr__(self, "value", value)

    def _check(self, other: "Quantity"):
        if self.unit != other.unit:
            raise ValueError(f"incompatible units: {self.unit} and {other.unit}")

    def add(self, other: "Quantity") -> "Quantity":
        self._check(other)
        return Quantity(self.value + other.value, self.unit)

    def subtract(self, other: "Quantity") -> "Quantity":
        self._check(other)
        if self.value < other.value:
            raise ValueError("quantity subtraction would be negative")
        return Quantity(self.value - other.value, self.unit)


@dataclass(frozen=True)
class _SignedQuantity:
    """Internal derived signed magnitude; never accepted as a physical input."""
    value: Decimal
    unit: str

    def __post_init__(self):
        object.__setattr__(self, "value", _decimal(self.value))
        if not self.unit:
            raise ValueError("signed quantity unit is required")


@dataclass(frozen=True)
class ConsumptionRate:
    value: Decimal
    quantity_unit: str
    time_unit: str = "day"

    def __post_init__(self):
        value = _decimal(self.value)
        if value < 0 or not self.quantity_unit or not self.time_unit:
            raise ValueError("invalid consumption rate")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str


@dataclass(frozen=True)
class Site:
    site_id: str
    customer_id: str
    name: str


@dataclass(frozen=True)
class GasProduct:
    gas_product_id: str
    name: str
    physical_form: str


@dataclass(frozen=True)
class ApplicationGasRequirement:
    gas_product_id: str
    role: str


@dataclass(frozen=True)
class Application:
    application_id: str
    site_id: str
    name: str
    gas_requirements: tuple[ApplicationGasRequirement, ...] = ()


@dataclass(frozen=True)
class SupplyInstallation:
    installation_id: str
    site_id: str
    gas_product_id: str
    supply_mode: str
    storage_type: str
    capacity: Quantity

    def __post_init__(self):
        if self.capacity.value <= 0:
            raise ValueError("installation capacity must be positive")


@dataclass(frozen=True)
class InventorySnapshot:
    installation_id: str
    observed_at: datetime
    inventory: Quantity
    source: str = "inventory_snapshot"

    def __post_init__(self):
        _aware(self.observed_at)


@dataclass(frozen=True)
class ConsumptionForecast:
    installation_id: str
    valid_from: datetime
    valid_until: datetime | None
    rate: ConsumptionRate
    source: str = "consumption_forecast"

    def __post_init__(self):
        _aware(self.valid_from)
        if self.valid_until is not None:
            _aware(self.valid_until)
            if self.valid_until <= self.valid_from:
                raise ValueError("forecast validity interval must be positive")


@dataclass(frozen=True)
class DeliveryPlan:
    installation_id: str
    planned_delivery_at: datetime
    planned_quantity: Quantity
    source: str = "delivery_plan"

    def __post_init__(self):
        _aware(self.planned_delivery_at)


@dataclass(frozen=True)
class SupplyProjection:
    installation_id: str
    gas_product_id: str
    reference_time: datetime
    current_inventory: Quantity
    days_of_supply: Decimal | None
    consumption_until_delivery: Quantity
    inventory_immediately_before_delivery: Quantity
    safety_stock: Quantity
    safety_stock_gap_before_delivery: _SignedQuantity
    stockout_before_delivery: bool
    stockout_at: datetime | None
    planned_delivery_quantity: Quantity
    inventory_immediately_after_delivery: Quantity
    required_delivery_volume: Quantity
    capacity: Quantity
    capacity_exceeded: bool
    capacity_overflow: Quantity
    provenance: tuple[object, ...] = ()

    def __post_init__(self):
        _aware(self.reference_time)
