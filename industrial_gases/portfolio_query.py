"""Deterministic, ordered filtering and bounded conversational evidence contracts."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from rag_models import RetrievedChunk

from .operational_attention import OperationalAttentionItem, OperationalAttentionResult
from .portfolio import SupplyPortfolioItemResult, SupplyPortfolioResult
from .service import SupplyAssuranceResult
from .supply_scenarios import SupplyAssuranceScenarioResult


@dataclass(frozen=True)
class PortfolioQuery:
    item_id: str | None = None
    item_ids: tuple[str, ...] = ()
    customer: str | None = None
    site: str | None = None
    application: str | None = None
    gas_product: str | None = None
    installation: str | None = None
    evaluation_statuses: tuple[str, ...] = ()
    finding_code: str | None = None
    has_attention_facts: bool | None = None

    def __post_init__(self) -> None:
        statuses = tuple(status.upper() for status in self.evaluation_statuses)
        if any(status not in {"COMPLETED", "INVALID", "MISSING_INPUTS"} for status in statuses):
            raise ValueError("Unsupported portfolio evaluation status")
        object.__setattr__(self, "evaluation_statuses", statuses)
        object.__setattr__(self, "item_ids", tuple(dict.fromkeys(self.item_ids)))
        for name in ("item_id", "customer", "site", "application", "gas_product", "installation", "finding_code"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                object.__setattr__(self, name, None)


@dataclass(frozen=True)
class PortfolioQueryMatch:
    item: SupplyPortfolioItemResult
    attention: OperationalAttentionItem
    matched_by: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "matched_by", tuple(self.matched_by))

    @property
    def item_id(self) -> str:
        return self.item.item_id


@dataclass(frozen=True)
class PortfolioQueryResult:
    query: PortfolioQuery
    matches: tuple[PortfolioQueryMatch, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "matches", tuple(self.matches))

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(match.item_id for match in self.matches)


class SupplyPortfolioQueryService:
    """Apply exact structured filters and preserve source portfolio order."""

    def select(
        self,
        portfolio: SupplyPortfolioResult,
        attention: OperationalAttentionResult,
        query: PortfolioQuery,
    ) -> PortfolioQueryResult:
        if not isinstance(portfolio, SupplyPortfolioResult):
            raise TypeError("portfolio must be a SupplyPortfolioResult")
        if not isinstance(attention, OperationalAttentionResult):
            raise TypeError("attention must be an OperationalAttentionResult")
        if not isinstance(query, PortfolioQuery):
            raise TypeError("query must be a PortfolioQuery")
        attention_by_id = {item.item_id: item for item in attention.items}
        matches: list[PortfolioQueryMatch] = []
        for item in portfolio.items:
            attention_item = attention_by_id.get(item.item_id)
            if attention_item is None:
                continue
            request = item.request
            identity_values = {
                "item_id": (item.item_id,),
                "customer": (item.result.customer_id, request.customer_id),
                "site": (getattr(request.site, "site_id", None), getattr(request.site, "name", None), item.result.site_id),
                "application": (getattr(request.application, "application_id", None), getattr(request.application, "name", None), item.result.application_id),
                "gas_product": (getattr(request.gas_product, "gas_product_id", None), getattr(request.gas_product, "name", None), item.result.gas_product_id),
                "installation": (getattr(request.installation, "installation_id", None), item.result.installation_id),
            }
            required = (
                ("item_id", query.item_id), ("customer", query.customer),
                ("site", query.site), ("application", query.application),
                ("gas_product", query.gas_product), ("installation", query.installation),
            )
            if query.item_ids and item.item_id not in query.item_ids:
                continue
            if any(not _identity_matches(value, identity_values[field]) for field, value in required if value):
                continue
            if query.evaluation_statuses and item.result.status.upper() not in query.evaluation_statuses:
                continue
            if query.finding_code and not any(
                finding.code == query.finding_code for finding in item.result.findings
            ):
                continue
            if query.has_attention_facts is not None and bool(attention_item.facts) is not query.has_attention_facts:
                continue
            matched_by = tuple(
                field for field, value in required if value
            )
            if query.item_ids:
                matched_by += ("item_ids",)
            if query.evaluation_statuses:
                matched_by += ("evaluation_status",)
            if query.finding_code:
                matched_by += ("finding_code",)
            if query.has_attention_facts is not None:
                matched_by += ("attention_facts" if query.has_attention_facts else "no_attention_facts",)
            if not matched_by:
                matched_by = ("all_positions",)
            matches.append(PortfolioQueryMatch(item, attention_item, matched_by))
        return PortfolioQueryResult(query, tuple(matches))


@dataclass(frozen=True)
class SupplyAgentSessionContext:
    """Bounded structured references; never stores free-form model memory."""

    selected_item_ids: tuple[str, ...] = ()
    focused_item_id: str | None = None
    last_query: PortfolioQuery | None = None
    last_scenario_target_id: str | None = None

    def __post_init__(self) -> None:
        ids = tuple(dict.fromkeys(item_id for item_id in self.selected_item_ids if item_id))[:32]
        if self.focused_item_id and self.focused_item_id not in ids:
            raise ValueError("focused item must be in the bounded selected item IDs")
        if self.last_scenario_target_id and self.last_scenario_target_id not in ids:
            raise ValueError("scenario target must be in the bounded selected item IDs")
        object.__setattr__(self, "selected_item_ids", ids)


@dataclass(frozen=True)
class PortfolioItemEvidence:
    """One independent evidence scope retaining its original domain objects."""

    item: SupplyPortfolioItemResult
    attention: OperationalAttentionItem
    knowledge_sources: tuple[RetrievedChunk, ...] = ()
    knowledge_status: str = "not_requested"
    scenario: SupplyAssuranceScenarioResult | None = None
    evidence_references: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "knowledge_sources", tuple(self.knowledge_sources))
        object.__setattr__(self, "evidence_references", tuple(self.evidence_references))

    @property
    def result(self) -> SupplyAssuranceResult:
        return self.item.result


@dataclass(frozen=True)
class ScopedKnowledgeSource:
    source: RetrievedChunk
    item_ids: tuple[str, ...]
    global_scope: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_ids", tuple(dict.fromkeys(self.item_ids)))


@dataclass(frozen=True)
class PortfolioRetrievalFailure:
    item_id: str
    code: str


@dataclass(frozen=True)
class PortfolioEvidenceBundle:
    items: tuple[PortfolioItemEvidence, ...]
    global_sources: tuple[ScopedKnowledgeSource, ...] = ()
    retrieval_failures: tuple[PortfolioRetrievalFailure, ...] = ()

    def __post_init__(self) -> None:
        ids = tuple(item.item.item_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("portfolio evidence items must have unique item IDs")
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "global_sources", tuple(self.global_sources))
        object.__setattr__(self, "retrieval_failures", tuple(self.retrieval_failures))


def _normalize_identity(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", value.strip().casefold())


def _identity_matches(selector: str, candidates: tuple[str | None, ...]) -> bool:
    wanted = _normalize_identity(selector)
    for candidate in candidates:
        if not candidate:
            continue
        normalized = _normalize_identity(candidate)
        if wanted == normalized:
            return True
    return False
