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
from .portfolio import (
    SupplyPortfolioItem,
    SupplyPortfolioItemResult,
    SupplyPortfolioRequest,
    SupplyPortfolioResult,
    SupplyPortfolioService,
)
from .operational_attention import (
    OperationalAttentionFact,
    OperationalAttentionItem,
    OperationalAttentionResult,
    OperationalAttentionService,
)
from .decision_models import (
    DecisionAlternative,
    DecisionAnalysis,
    DecisionContext,
    ExplicitChange,
)
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
from .industrial_knowledge import IndustrialKnowledgeService, demo_knowledge_service
from .supply_agent import (
    ProviderSupplyDecisionModel,
    SupplyAgent,
    SupplyAgentRequest,
    SupplyAgentResponse,
    SupplyAgentStatus,
    SupplyAgentTools,
    SupplyEvidenceReference,
)
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
    "SupplyPortfolioItem", "SupplyPortfolioItemResult", "SupplyPortfolioRequest",
    "SupplyPortfolioResult", "SupplyPortfolioService",
    "OperationalAttentionFact", "OperationalAttentionItem",
    "OperationalAttentionResult", "OperationalAttentionService",
    "DecisionAlternative", "DecisionAnalysis", "DecisionContext", "ExplicitChange",
    "SupplyAssuranceAlternative", "SupplyAssuranceScenarioResult",
    "evaluate_supply_assurance_alternative",
    "ExtractedSupplyFacts", "ExtractionIssue", "ExtractionProvenance", "IdentityReference", "ResolvedSupplyIdentity",
    "SupplyAssuranceCompositionResult", "SupplyAssuranceRequestComposer",
    "UNIT_CATALOG", "UnitDimension", "UnitSpec", "unit_spec",
    "SupplyAssuranceIdentityContext", "SupplyAssuranceInterpretationResult", "SupplyAssuranceInterpreter",
    "ExtractedQuantityCandidate", "ExtractedRateCandidate", "ExtractedRelativeTimeCandidate",
    "ExtractionOperationalError", "LLMSupplyExtraction", "LLMSupplyAssuranceInterpreter",
    "IndustrialKnowledgeService", "demo_knowledge_service", "ProviderSupplyDecisionModel",
    "SupplyAgent", "SupplyAgentRequest", "SupplyAgentResponse", "SupplyAgentStatus",
    "SupplyAgentTools", "SupplyEvidenceReference",
]


def __getattr__(name):
    if name == "LLMSupplyAssuranceInterpreter":
        from .llm_interpreter import LLMSupplyAssuranceInterpreter
        return LLMSupplyAssuranceInterpreter
    raise AttributeError(name)
