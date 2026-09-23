"""Independent supply assurance results collected into a portfolio."""
from dataclasses import dataclass

from .service import SupplyAssuranceRequest, SupplyAssuranceResult, SupplyAssuranceService


@dataclass(frozen=True)
class SupplyPortfolioItem:
    """A caller-identified request within an independently evaluated portfolio."""

    item_id: str
    request: SupplyAssuranceRequest

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ValueError("portfolio item_id must be a non-empty string")


@dataclass(frozen=True)
class SupplyPortfolioRequest:
    """An ordered collection of independent supply assurance requests."""

    items: tuple[SupplyPortfolioItem, ...]

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(not isinstance(item, SupplyPortfolioItem) for item in items):
            raise TypeError("portfolio items must be SupplyPortfolioItem instances")
        item_ids = tuple(item.item_id for item in items)
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("portfolio item_id values must be unique")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True)
class SupplyPortfolioItemResult:
    """The original item identity, request and unmodified service result."""

    item_id: str
    request: SupplyAssuranceRequest
    result: SupplyAssuranceResult


@dataclass(frozen=True)
class SupplyPortfolioResult:
    """Ordered individual results; deliberately contains no aggregate values."""

    items: tuple[SupplyPortfolioItemResult, ...]


class SupplyPortfolioService:
    """Evaluate each portfolio item once through one assurance service instance."""

    def __init__(self, supply_assurance_service: SupplyAssuranceService | None = None) -> None:
        self._supply_assurance_service = (
            supply_assurance_service
            if supply_assurance_service is not None
            else SupplyAssuranceService()
        )

    def evaluate(self, request: SupplyPortfolioRequest) -> SupplyPortfolioResult:
        results = tuple(
            SupplyPortfolioItemResult(
                item_id=item.item_id,
                request=item.request,
                result=self._supply_assurance_service.assess(item.request),
            )
            for item in request.items
        )
        return SupplyPortfolioResult(items=results)
