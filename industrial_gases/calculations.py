from datetime import datetime, timedelta
from decimal import Decimal

from .models import (
    ConsumptionForecast, ConsumptionRate, DeliveryPlan, InventorySnapshot,
    Quantity, SupplyInstallation, SupplyProjection, _SignedQuantity,
)
from .provenance import Provenance


def _duration_days(start: datetime, end: datetime) -> Decimal:
    seconds = Decimal(str((end - start).total_seconds()))
    return seconds / Decimal("86400")


def calculate_days_of_supply(inventory: Quantity, consumption_rate: ConsumptionRate) -> Decimal | None:
    if inventory.unit != consumption_rate.quantity_unit:
        raise ValueError("inventory and consumption rate units are incompatible")
    if consumption_rate.time_unit != "day":
        raise ValueError("Phase 1A requires a daily consumption rate")
    if consumption_rate.value == 0:
        return None
    return inventory.value / consumption_rate.value


def project_inventory(snapshot: InventorySnapshot, forecast: ConsumptionForecast,
                      at: datetime) -> Quantity:
    if snapshot.installation_id != forecast.installation_id:
        raise ValueError("snapshot and forecast installations differ")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    if at < snapshot.observed_at:
        raise ValueError("projection time precedes inventory snapshot")
    if forecast.valid_until is not None and at > forecast.valid_until:
        raise ValueError("projection exceeds forecast validity")
    if snapshot.inventory.unit != forecast.rate.quantity_unit:
        raise ValueError("inventory and forecast units are incompatible")
    days = _duration_days(snapshot.observed_at, at)
    consumed = forecast.rate.value * days
    remaining = snapshot.inventory.value - consumed
    return Quantity(max(remaining, Decimal("0")), snapshot.inventory.unit)


def calculate_required_delivery_volume(current_inventory: Quantity,
                                       consumption_until_delivery: Quantity,
                                       safety_stock: Quantity) -> Quantity:
    current_inventory._check(consumption_until_delivery)
    current_inventory._check(safety_stock)
    required = max(safety_stock.value + consumption_until_delivery.value - current_inventory.value, Decimal("0"))
    return Quantity(required, current_inventory.unit)


def build_supply_projection(installation: SupplyInstallation, snapshot: InventorySnapshot,
                            forecast: ConsumptionForecast, delivery: DeliveryPlan,
                            safety_stock: Quantity, reference_time: datetime) -> SupplyProjection:
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("reference_time must be timezone-aware")
    if delivery.planned_delivery_at < reference_time:
        raise ValueError("delivery cannot be in the past")
    installation_ids = {installation.installation_id, snapshot.installation_id,
                        forecast.installation_id, delivery.installation_id}
    if len(installation_ids) != 1:
        raise ValueError("scenario objects refer to different installations")
    if installation.capacity.unit != snapshot.inventory.unit or safety_stock.unit != snapshot.inventory.unit:
        raise ValueError("capacity, inventory and safety stock units differ")
    if delivery.planned_quantity.unit != snapshot.inventory.unit:
        raise ValueError("delivery and inventory units differ")
    if snapshot.inventory.value > installation.capacity.value:
        raise ValueError("inventory exceeds installation capacity")
    if safety_stock.value > installation.capacity.value:
        raise ValueError("safety stock exceeds installation capacity")
    if reference_time != snapshot.observed_at:
        raise ValueError("reference_time must equal snapshot observed_at")
    if reference_time < forecast.valid_from:
        raise ValueError("reference_time precedes forecast validity")
    if forecast.valid_until is not None and delivery.planned_delivery_at > forecast.valid_until:
        raise ValueError("delivery exceeds forecast validity")
    days = calculate_days_of_supply(snapshot.inventory, forecast.rate)
    elapsed_days = _duration_days(reference_time, delivery.planned_delivery_at)
    consumption = Quantity(forecast.rate.value * elapsed_days, snapshot.inventory.unit)
    raw_before = snapshot.inventory.value - consumption.value
    stockout = raw_before < 0
    stockout_at = None
    if stockout and forecast.rate.value > 0:
        stockout_at = snapshot.observed_at + timedelta(seconds=float(snapshot.inventory.value / forecast.rate.value * Decimal("86400")))
    before = Quantity(max(raw_before, Decimal("0")), snapshot.inventory.unit)
    gap = _SignedQuantity(before.value - safety_stock.value, snapshot.inventory.unit)
    required = calculate_required_delivery_volume(snapshot.inventory, consumption, safety_stock)
    after_value = before.value + delivery.planned_quantity.value
    overflow = max(after_value - installation.capacity.value, Decimal("0"))
    provenance = (
        Provenance("current_inventory", snapshot.inventory, "operational_input", snapshot.source),
        Provenance("consumption_rate", Quantity(forecast.rate.value, f"{forecast.rate.quantity_unit}/{forecast.rate.time_unit}"), "operational_input", forecast.source),
        Provenance("safety_stock", safety_stock, "configuration", "supply_installation"),
        Provenance("days_of_supply", days, "deterministic_calculation", "calculate_days_of_supply"),
        Provenance("consumption_until_delivery", consumption, "deterministic_calculation", "build_supply_projection"),
        Provenance("inventory_immediately_before_delivery", before, "deterministic_calculation", "project_inventory"),
        Provenance("safety_stock_gap_before_delivery", gap, "deterministic_calculation", "safety_stock comparison"),
        Provenance("stockout_before_delivery", stockout, "deterministic_calculation", "project_inventory"),
        Provenance("stockout_at", stockout_at, "deterministic_calculation", "project_inventory"),
        Provenance("planned_delivery_quantity", delivery.planned_quantity, "operational_input", delivery.source),
        Provenance("inventory_immediately_after_delivery", Quantity(after_value, snapshot.inventory.unit), "deterministic_calculation", "delivery projection"),
        Provenance("required_delivery_volume", required, "deterministic_calculation", "calculate_required_delivery_volume"),
        Provenance("capacity", installation.capacity, "configuration", "supply_installation"),
        Provenance("capacity_exceeded", overflow > 0, "deterministic_calculation", "capacity comparison"),
        Provenance("capacity_overflow", Quantity(overflow, snapshot.inventory.unit), "deterministic_calculation", "capacity comparison"),
    )
    return SupplyProjection(
        installation.installation_id, installation.gas_product_id, reference_time,
        snapshot.inventory, days, consumption, before, safety_stock, gap,
        stockout, stockout_at, delivery.planned_quantity,
        Quantity(after_value, snapshot.inventory.unit), required, installation.capacity,
        overflow > 0, Quantity(overflow, snapshot.inventory.unit), provenance,
    )
