"""Explicit alternative supply scenarios evaluated by the supply assurance service."""
from dataclasses import dataclass

from .service import (
    SupplyAssuranceRequest,
    SupplyAssuranceResult,
    SupplyAssuranceService,
)


@dataclass(frozen=True)
class SupplyAssuranceAlternative:
    """One explicitly constructed alternative request; no implicit input patching."""

    id: str
    label: str
    alternative_request: SupplyAssuranceRequest


@dataclass(frozen=True)
class SupplyAssuranceScenarioResult:
    """Uninterpreted baseline and alternative results from the same service."""

    baseline_result: SupplyAssuranceResult
    alternative: SupplyAssuranceAlternative
    alternative_result: SupplyAssuranceResult


def evaluate_supply_assurance_alternative(
    baseline_request: SupplyAssuranceRequest,
    alternative: SupplyAssuranceAlternative,
    service: SupplyAssuranceService,
) -> SupplyAssuranceScenarioResult:
    """Assess both explicit requests and preserve each service result verbatim."""
    baseline_result = service.assess(baseline_request)
    alternative_result = service.assess(alternative.alternative_request)
    return SupplyAssuranceScenarioResult(
        baseline_result=baseline_result,
        alternative=alternative,
        alternative_result=alternative_result,
    )
