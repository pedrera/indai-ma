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
from .supply_scenarios import (
    SupplyAssuranceAlternative,
    SupplyAssuranceScenarioResult,
    evaluate_supply_assurance_alternative,
)
from .interpretation_models import (
    ExtractedSupplyFacts, ExtractionIssue, ExtractionProvenance,
    IdentityReference, ResolvedSupplyIdentity,
)
from .request_composer import SupplyAssuranceCompositionResult, SupplyAssuranceRequestComposer
from .units import UNIT_CATALOG, UnitDimension, UnitSpec, unit_spec
from .interpreter import SupplyAssuranceIdentityContext, SupplyAssuranceInterpretationResult, SupplyAssuranceInterpreter
from .llm_extraction_models import (
    ExtractedQuantityCandidate, ExtractedRateCandidate,
    ExtractedRelativeTimeCandidate, ExtractionOperationalError, LLMSupplyExtraction,
)

__all__ = [
    "Application", "ApplicationGasRequirement", "ConsumptionForecast",
    "ConsumptionRate", "Customer", "DeliveryPlan", "GasProduct",
    "InventorySnapshot", "Provenance", "Quantity", "Site",
    "SupplyInstallation", "SupplyProjection", "DomainValidationError", "build_supply_projection",
    "calculate_days_of_supply", "calculate_required_delivery_volume",
    "project_inventory",
    "Finding", "SupplyAssuranceRequest", "SupplyAssuranceResult", "SupplyAssuranceService",
    "SupplyAssuranceAlternative", "SupplyAssuranceScenarioResult",
    "evaluate_supply_assurance_alternative",
    "ExtractedSupplyFacts", "ExtractionIssue", "ExtractionProvenance", "IdentityReference", "ResolvedSupplyIdentity",
    "SupplyAssuranceCompositionResult", "SupplyAssuranceRequestComposer",
    "UNIT_CATALOG", "UnitDimension", "UnitSpec", "unit_spec",
    "SupplyAssuranceIdentityContext", "SupplyAssuranceInterpretationResult", "SupplyAssuranceInterpreter",
    "ExtractedQuantityCandidate", "ExtractedRateCandidate", "ExtractedRelativeTimeCandidate",
    "ExtractionOperationalError", "LLMSupplyExtraction", "LLMSupplyAssuranceInterpreter",
]


def __getattr__(name):
    if name == "LLMSupplyAssuranceInterpreter":
        from .llm_interpreter import LLMSupplyAssuranceInterpreter
        return LLMSupplyAssuranceInterpreter
    raise AttributeError(name)
