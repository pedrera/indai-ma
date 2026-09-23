"""Deterministic projection of existing supply findings for operational attention."""
from dataclasses import dataclass

from .portfolio import SupplyPortfolioResult
from .service import Finding, SupplyAssuranceRequest, SupplyAssuranceResult


@dataclass(frozen=True)
class OperationalAttentionFact:
    """An unchanged source finding; its values remain in the source result."""

    source_finding: Finding


@dataclass(frozen=True)
class OperationalAttentionItem:
    """Attention facts for one ordered portfolio item, with original state intact."""

    item_id: str
    request: SupplyAssuranceRequest
    result: SupplyAssuranceResult
    facts: tuple[OperationalAttentionFact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))

    @property
    def evaluation_status(self) -> str:
        """Original assessment state; INVALID/MISSING_INPUTS are not findings."""
        return self.result.status

    @property
    def validation_errors(self) -> tuple[str, ...]:
        return self.result.validation_errors

    @property
    def missing_inputs(self) -> tuple[str, ...]:
        return self.result.missing_inputs


@dataclass(frozen=True)
class OperationalAttentionResult:
    """Ordered per-item attention projection, with no portfolio-level status."""

    items: tuple[OperationalAttentionItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


class OperationalAttentionService:
    """Project completed portfolio findings without re-running supply assurance."""

    def project(self, portfolio: SupplyPortfolioResult) -> OperationalAttentionResult:
        if not isinstance(portfolio, SupplyPortfolioResult):
            raise TypeError("portfolio must be a SupplyPortfolioResult")

        items = tuple(
            OperationalAttentionItem(
                item_id=portfolio_item.item_id,
                request=portfolio_item.request,
                result=portfolio_item.result,
                facts=tuple(
                    OperationalAttentionFact(source_finding=finding)
                    for finding in portfolio_item.result.findings
                ) if portfolio_item.result.status == "COMPLETED" else (),
            )
            for portfolio_item in portfolio.items
        )
        return OperationalAttentionResult(items=items)
