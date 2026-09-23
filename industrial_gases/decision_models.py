"""Small, non-evaluative records for explicit supply what-if analyses."""
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from .models import ConsumptionRate, Quantity
from .operational_attention import OperationalAttentionItem
from .service import SupplyAssuranceRequest, SupplyAssuranceResult
from .supply_scenarios import SupplyAssuranceScenarioResult


ChangePath = Literal[
    "delivery_plan.planned_delivery_at",
    "delivery_plan.planned_quantity",
    "consumption_forecast.rate",
]


@dataclass(frozen=True)
class DecisionContext:
    """Original ordered portfolio item that anchors explicit alternatives."""

    source_item: OperationalAttentionItem

    @property
    def item_id(self) -> str:
        return self.source_item.item_id

    @property
    def request(self) -> SupplyAssuranceRequest:
        return self.source_item.request

    @property
    def baseline_result(self) -> SupplyAssuranceResult:
        return self.source_item.result

    @property
    def attention_facts(self):
        return self.source_item.facts

    @property
    def customer_id(self) -> str | None:
        return self.source_item.result.customer_id

    @property
    def site_id(self) -> str | None:
        return self.source_item.result.site_id

    @property
    def application_id(self) -> str | None:
        return self.source_item.result.application_id

    @property
    def gas_product_id(self) -> str | None:
        return self.source_item.result.gas_product_id

    @property
    def installation_id(self) -> str | None:
        return self.source_item.result.installation_id

    @property
    def evaluation_status(self) -> str:
        return self.source_item.evaluation_status


@dataclass(frozen=True)
class ExplicitChange:
    """One user-specified input change; it carries no evaluation or preference."""

    field_path: ChangePath
    before: datetime | Quantity | ConsumptionRate
    after: datetime | Quantity | ConsumptionRate


@dataclass(frozen=True)
class DecisionAlternative:
    """One explicit supply scenario and its unchanged service result."""

    item_id: str
    change: ExplicitChange
    scenario: SupplyAssuranceScenarioResult

    @property
    def alternative_request(self) -> SupplyAssuranceRequest:
        return self.scenario.alternative.alternative_request

    @property
    def result(self) -> SupplyAssuranceResult:
        return self.scenario.alternative_result


@dataclass(frozen=True)
class DecisionAnalysis:
    """Ordered records of explicit alternatives, with no selected outcome."""

    context: DecisionContext
    alternatives: tuple[DecisionAlternative, ...] = ()

    def __post_init__(self) -> None:
        alternatives = tuple(self.alternatives)
        if any(not isinstance(item, DecisionAlternative) for item in alternatives):
            raise TypeError("alternatives must contain DecisionAlternative instances")
        base = self.context.request
        for item in alternatives:
            if item.item_id != self.context.item_id:
                raise ValueError("alternative item_id must match its decision context")
            if item.scenario.baseline_result != self.context.baseline_result:
                raise ValueError("alternative baseline result must match its decision context")
            expected_request = _request_for_explicit_change(base, item.change)
            if item.alternative_request != expected_request:
                raise ValueError("alternative request must contain only its declared change")
        object.__setattr__(self, "alternatives", alternatives)


def _request_for_explicit_change(
    request: SupplyAssuranceRequest,
    change: ExplicitChange,
) -> SupplyAssuranceRequest:
    """Apply exactly one declared field replacement, without deriving outcomes."""
    if change.field_path == "delivery_plan.planned_delivery_at":
        if request.delivery_plan is None or not isinstance(change.before, datetime) or not isinstance(change.after, datetime):
            raise ValueError("delivery timing change requires explicit datetimes")
        if request.delivery_plan.planned_delivery_at != change.before:
            raise ValueError("delivery timing before value must match the baseline")
        return replace(
            request,
            delivery_plan=replace(request.delivery_plan, planned_delivery_at=change.after),
        )
    if change.field_path == "delivery_plan.planned_quantity":
        if request.delivery_plan is None or not isinstance(change.before, Quantity) or not isinstance(change.after, Quantity):
            raise ValueError("delivery quantity change requires explicit quantities")
        if request.delivery_plan.planned_quantity != change.before:
            raise ValueError("delivery quantity before value must match the baseline")
        return replace(
            request,
            delivery_plan=replace(request.delivery_plan, planned_quantity=change.after),
        )
    if change.field_path == "consumption_forecast.rate":
        if request.consumption_forecast is None or not isinstance(change.before, ConsumptionRate) or not isinstance(change.after, ConsumptionRate):
            raise ValueError("consumption rate change requires explicit rates")
        if request.consumption_forecast.rate != change.before:
            raise ValueError("consumption rate before value must match the baseline")
        return replace(
            request,
            consumption_forecast=replace(request.consumption_forecast, rate=change.after),
        )
    raise ValueError("unsupported explicit change path")
