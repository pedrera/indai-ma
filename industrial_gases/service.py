"""Deterministic supply assurance application service."""
from dataclasses import dataclass
from datetime import datetime

from .calculations import build_supply_projection
from .models import (
    Application, ConsumptionForecast, DeliveryPlan, GasProduct,
    InventorySnapshot, Quantity, Site, SupplyInstallation, SupplyProjection,
    DomainValidationError,
)


@dataclass(frozen=True)
class Finding:
    code: str
    source_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupplyAssuranceRequest:
    customer_id: str | None = None
    site: Site | None = None
    application: Application | None = None
    gas_product: GasProduct | None = None
    installation: SupplyInstallation | None = None
    inventory_snapshot: InventorySnapshot | None = None
    consumption_forecast: ConsumptionForecast | None = None
    delivery_plan: DeliveryPlan | None = None
    safety_stock: Quantity | None = None
    reference_time: datetime | None = None


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
        missing = tuple(name for name, value in required
                        if value is None or (name == "customer_id" and not value.strip()))
        if missing:
            return SupplyAssuranceResult("MISSING_INPUTS", **identity, missing_inputs=missing)
        relation_errors = []
        if request.site.customer_id != request.customer_id:
            relation_errors.append("site_customer_mismatch")
        if request.application.site_id != request.site.site_id:
            relation_errors.append("application_site_mismatch")
        if request.installation.site_id != request.site.site_id:
            relation_errors.append("installation_site_mismatch")
        if request.installation.gas_product_id != request.gas_product.gas_product_id:
            relation_errors.append("installation_product_mismatch")
        if not any(item.gas_product_id == request.gas_product.gas_product_id
                   for item in request.application.gas_requirements):
            relation_errors.append("application_gas_product_not_declared")
        if request.inventory_snapshot.installation_id != request.installation.installation_id:
            relation_errors.append("snapshot_installation_mismatch")
        if request.consumption_forecast.installation_id != request.installation.installation_id:
            relation_errors.append("forecast_installation_mismatch")
        if request.delivery_plan.installation_id != request.installation.installation_id:
            relation_errors.append("delivery_installation_mismatch")
        if relation_errors:
            return SupplyAssuranceResult("INVALID", **identity,
                                         validation_errors=tuple(relation_errors))
        try:
            projection = build_supply_projection(
                request.installation,
                request.inventory_snapshot,
                request.consumption_forecast,
                request.delivery_plan,
                request.safety_stock,
                request.reference_time,
            )
        except DomainValidationError as error:
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
