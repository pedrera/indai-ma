"""Deterministic supply assurance application service."""
from dataclasses import dataclass
from datetime import datetime

from .calculations import build_supply_projection
from .models import (
    Application, ConsumptionForecast, DeliveryPlan, GasProduct,
    InventorySnapshot, Quantity, Site, SupplyInstallation, SupplyProjection,
)


@dataclass(frozen=True)
class Finding:
    code: str
    source_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupplyAssuranceRequest:
    customer_id: str
    site: Site | None
    application: Application | None
    gas_product: GasProduct | None
    installation: SupplyInstallation | None
    inventory_snapshot: InventorySnapshot | None
    consumption_forecast: ConsumptionForecast | None
    delivery_plan: DeliveryPlan | None
    safety_stock: Quantity | None
    reference_time: datetime | None


@dataclass(frozen=True)
class SupplyAssuranceResult:
    status: str
    customer_id: str | None
    site_id: str | None
    application_id: str | None
    gas_product_id: str | None
    installation_id: str | None
    projection: SupplyProjection | None = None
    missing_inputs: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()


class SupplyAssuranceService:
    """Compose a structured inventory continuity projection without agents or LLMs."""

    def assess(self, request: SupplyAssuranceRequest) -> SupplyAssuranceResult:
        identity = {
            "customer_id": request.customer_id or None,
            "site_id": getattr(request.site, "site_id", None),
            "application_id": getattr(request.application, "application_id", None),
            "gas_product_id": getattr(request.gas_product, "gas_product_id", None),
            "installation_id": getattr(request.installation, "installation_id", None),
        }
        required = (
            ("site", request.site),
            ("application", request.application),
            ("gas_product", request.gas_product),
            ("installation", request.installation),
            ("inventory_snapshot", request.inventory_snapshot),
            ("consumption_forecast", request.consumption_forecast),
            ("delivery_plan", request.delivery_plan),
            ("safety_stock", request.safety_stock),
            ("reference_time", request.reference_time),
        )
        missing = tuple(name for name, value in required if value is None)
        if missing:
            return SupplyAssuranceResult("MISSING_INPUTS", **identity, missing_inputs=missing)
        try:
            projection = build_supply_projection(
                request.installation,
                request.inventory_snapshot,
                request.consumption_forecast,
                request.delivery_plan,
                request.safety_stock,
                request.reference_time,
            )
        except (TypeError, ValueError) as error:
            return SupplyAssuranceResult("INVALID", **identity, validation_errors=(str(error),))
        findings = []
        if projection.stockout_before_delivery:
            findings.append(Finding("stockout_before_delivery",
                ("stockout_before_delivery", "stockout_at")))
        if projection.safety_stock_gap_before_delivery.value < 0:
            findings.append(Finding("safety_stock_breach",
                ("safety_stock_gap_before_delivery",)))
        if projection.capacity_exceeded:
            findings.append(Finding("capacity_overflow",
                ("capacity_exceeded", "capacity_overflow")))
        return SupplyAssuranceResult("COMPLETED", **identity, projection=projection,
                                     findings=tuple(findings))
