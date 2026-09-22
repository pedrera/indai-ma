"""Industrial gases inventory and supply continuity foundation."""

from .calculations import (
    build_supply_projection,
    calculate_days_of_supply,
    calculate_required_delivery_volume,
    project_inventory,
)
from .models import (
    Application,
    ApplicationGasRequirement,
    ConsumptionForecast,
    ConsumptionRate,
    Customer,
    DeliveryPlan,
    GasProduct,
    InventorySnapshot,
    Quantity,
    Site,
    SupplyInstallation,
    SupplyProjection,
    DomainValidationError,
)
from .provenance import Provenance
from .service import Finding, SupplyAssuranceRequest, SupplyAssuranceResult, SupplyAssuranceService
from .interpretation_models import ExtractedSupplyFacts, ExtractionProvenance, IdentityReference, ResolvedSupplyIdentity
from .request_composer import SupplyAssuranceCompositionResult, SupplyAssuranceRequestComposer

__all__ = [
    "Application", "ApplicationGasRequirement", "ConsumptionForecast",
    "ConsumptionRate", "Customer", "DeliveryPlan", "GasProduct",
    "InventorySnapshot", "Provenance", "Quantity", "Site",
    "SupplyInstallation", "SupplyProjection", "DomainValidationError", "build_supply_projection",
    "calculate_days_of_supply", "calculate_required_delivery_volume",
    "project_inventory",
    "Finding", "SupplyAssuranceRequest", "SupplyAssuranceResult", "SupplyAssuranceService",
    "ExtractedSupplyFacts", "ExtractionProvenance", "IdentityReference", "ResolvedSupplyIdentity",
    "SupplyAssuranceCompositionResult", "SupplyAssuranceRequestComposer",
]
