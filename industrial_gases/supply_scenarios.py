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


@dataclass(frozen=True)
class SupplyAssuranceScenarioSet:
    """An ordered set of explicit alternatives for one Supply Assurance identity."""

    baseline_request: SupplyAssuranceRequest
    alternatives: tuple[SupplyAssuranceAlternative, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_request, SupplyAssuranceRequest):
            raise TypeError("scenario set baseline must be a SupplyAssuranceRequest")
        alternatives = tuple(self.alternatives)
        if not alternatives:
            raise ValueError("a scenario set must contain at least one alternative")
        if any(not isinstance(item, SupplyAssuranceAlternative) for item in alternatives):
            raise TypeError("scenario set alternatives must be SupplyAssuranceAlternative values")
        ids = tuple(item.id for item in alternatives)
        if any(not isinstance(item_id, str) or not item_id.strip() for item_id in ids):
            raise ValueError("alternative IDs must be non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("alternative IDs must be unique within a scenario set")

        baseline_identity = _request_identity(self.baseline_request)
        for alternative in alternatives:
            if not isinstance(alternative.alternative_request, SupplyAssuranceRequest):
                raise TypeError("alternative requests must be SupplyAssuranceRequest values")
            alternative_identity = _request_identity(alternative.alternative_request)
            for field, baseline_value, alternative_value in zip(
                _IDENTITY_FIELDS, baseline_identity, alternative_identity,
            ):
                if baseline_value != alternative_value:
                    raise ValueError(
                        f"alternative {alternative.id!r} has a different {field} from the baseline"
                    )
        object.__setattr__(self, "alternatives", alternatives)


@dataclass(frozen=True)
class SupplyAssuranceScenarioSetResult:
    """Baseline once plus ordered, independent results for every alternative."""

    baseline_result: SupplyAssuranceResult
    scenario_results: tuple[SupplyAssuranceScenarioResult, ...]


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


def evaluate_supply_assurance_scenario_set(
    scenario_set: SupplyAssuranceScenarioSet,
    service: SupplyAssuranceService,
) -> SupplyAssuranceScenarioSetResult:
    """Evaluate one baseline and each explicit alternative independently, in order."""
    baseline_result = service.assess(scenario_set.baseline_request)
    scenario_results = tuple(
        SupplyAssuranceScenarioResult(
            baseline_result=baseline_result,
            alternative=alternative,
            alternative_result=service.assess(alternative.alternative_request),
        )
        for alternative in scenario_set.alternatives
    )
    return SupplyAssuranceScenarioSetResult(baseline_result, scenario_results)


_IDENTITY_FIELDS = (
    "customer_id", "site_id", "application_id", "gas_product_id", "installation_id",
)


def _request_identity(request: SupplyAssuranceRequest) -> tuple[str | None, ...]:
    return (
        request.customer_id,
        getattr(request.site, "site_id", None),
        getattr(request.application, "application_id", None),
        getattr(request.gas_product, "gas_product_id", None),
        getattr(request.installation, "installation_id", None),
    )
