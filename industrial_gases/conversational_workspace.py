"""Conversation-first orchestration over existing Industrial Gases capabilities."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Callable

from diagnostics import PerformanceRecorder
from .industrial_knowledge import IndustrialKnowledgeService
from .operational_attention import OperationalAttentionResult
from .portfolio import SupplyPortfolioResult
from .portfolio_query import PortfolioEvidenceBundle, PortfolioItemEvidence, PortfolioQueryResult, ScopedKnowledgeSource, SupplyAgentSessionContext
from .service import SupplyAssuranceService
from .supply_agent import (
    SupplyAgent,
    SupplyAgentRequest,
    SupplyAgentStatus,
    SupplyDecisionModel,
    SupplyAgentProviderError,
    _has_context_reference,
    _has_explicit_what_if,
    _requires_documentary_evidence,
    _requires_operational_context,
)


class WorkspaceIntent(str, Enum):
    OPERATIONAL = "operational"
    DOCUMENTARY = "documentary"
    SCENARIO = "scenario"
    COMBINED = "combined"
    FOLLOW_UP = "follow_up"


@dataclass(frozen=True)
class WorkspaceRoute:
    intent: WorkspaceIntent
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceResponse:
    """Structured response for rendering; explanation is never a data source."""

    status: SupplyAgentStatus
    route: WorkspaceRoute
    explanation: str | None
    portfolio_query: PortfolioQueryResult | None
    evidence: PortfolioEvidenceBundle
    session_context: SupplyAgentSessionContext
    evidence_references: tuple[Any, ...] = ()
    tool_executions: tuple[dict[str, Any], ...] = ()
    operational_errors: tuple[str, ...] = ()
    unsupported_questions: tuple[str, ...] = ()
    clarification_required: bool = False
    semantic_guard_applied: bool = False

    @property
    def content(self) -> str | None:
        return self.explanation

    @property
    def selected_item_ids(self) -> tuple[str, ...]:
        return tuple(item.item.item_id for item in self.evidence.items)

    @property
    def positions(self) -> tuple[PortfolioItemEvidence, ...]:
        return self.evidence.items

    @property
    def matched_positions(self) -> tuple[PortfolioItemEvidence, ...]:
        return self.evidence.items

    @property
    def attention_facts(self) -> tuple[tuple[str, tuple[Any, ...]], ...]:
        return tuple((item.item.item_id, tuple(item.attention.facts)) for item in self.evidence.items)

    @property
    def scenario_analyses(self) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (item.item.item_id, item.scenario)
            for item in self.evidence.items if item.scenario is not None
        )

    @property
    def provenance(self) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (item.item.item_id, item.result.projection.provenance)
            for item in self.evidence.items if item.result.projection is not None
        )

    @property
    def evaluation_issues(self) -> tuple[PortfolioItemEvidence, ...]:
        return tuple(
            item for item in self.evidence.items
            if item.result.status in {"INVALID", "MISSING_INPUTS"}
        )

    @property
    def documentary_sources(self) -> tuple[ScopedKnowledgeSource, ...]:
        return tuple(
            ScopedKnowledgeSource(source, (item.item.item_id,))
            for item in self.evidence.items for source in item.knowledge_sources
        )


class _NoGenerationDecisionModel:
    """Allows deterministic workspace requests without a configured model."""

    def decide(self, question, state, tools, timeout_seconds=None):
        raise SupplyAgentProviderError(
            "A generation model is required to explain this request; structured evidence is retained."
        )


def route_workspace_intent(
    question: str,
    context: SupplyAgentSessionContext | None = None,
) -> WorkspaceRoute:
    """Map user intent to existing capabilities without choosing data or calculating."""
    context = context or SupplyAgentSessionContext()
    documentary = _requires_documentary_evidence(question)
    scenario = _has_explicit_what_if(question)
    operational = _requires_operational_context(question)
    follow_up = bool(context.selected_item_ids and _has_context_reference(question))

    if documentary and (scenario or operational or follow_up):
        intent = WorkspaceIntent.COMBINED
    elif scenario:
        intent = WorkspaceIntent.SCENARIO
    elif follow_up:
        intent = WorkspaceIntent.FOLLOW_UP
    elif documentary:
        intent = WorkspaceIntent.DOCUMENTARY
    else:
        intent = WorkspaceIntent.OPERATIONAL

    # Every response starts from deterministic query selection and retains its
    # matching position evidence, including documentary-only questions.
    capabilities = ["portfolio_query", "operational_evidence"]
    if documentary:
        capabilities.append("industrial_knowledge")
    if scenario:
        capabilities.append("scenario_evaluation")
    return WorkspaceRoute(intent, tuple(capabilities))


class ConversationalWorkspaceOrchestrator:
    """Orchestrate one conversational turn over existing deterministic capabilities."""

    def __init__(
        self,
        portfolio: SupplyPortfolioResult,
        attention: OperationalAttentionResult,
        knowledge: IndustrialKnowledgeService | Callable[[], IndustrialKnowledgeService],
        decision_model: SupplyDecisionModel | None,
        session_context: SupplyAgentSessionContext | None = None,
        recorder: PerformanceRecorder | None = None,
        service: SupplyAssuranceService | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.attention = attention
        self.knowledge = knowledge
        self.decision_model = decision_model or _NoGenerationDecisionModel()
        self.session_context = session_context or SupplyAgentSessionContext()
        self.recorder = recorder
        self.service = service

    def run(self, request: str, timeout_seconds: float | None = None) -> WorkspaceResponse:
        question = request.strip() if isinstance(request, str) else ""
        if not question:
            raise ValueError("workspace request must be a non-empty string")
        route = route_workspace_intent(question, self.session_context)
        if self.recorder:
            event = self.recorder.start_stage(
                "workspace_intent_routing", intent=route.intent.value,
                capabilities=route.capabilities,
            )
            self.recorder.complete_stage(event)

        agent = SupplyAgent(
            SupplyAgentRequest(question, None, self.session_context),
            self.portfolio, self.attention, self.knowledge, self.decision_model,
            recorder=self.recorder, service=self.service, workspace_mode=True,
        )
        result = agent.run(question, timeout_seconds)
        evidence = result.evidence_bundle or PortfolioEvidenceBundle(())
        if self.recorder:
            for item in evidence.items:
                event = self.recorder.start_stage(
                    "workspace_structured_evidence", item_id=item.item.item_id,
                    domain_status=item.result.status,
                )
                self.recorder.complete_stage(
                    event, finding_codes=tuple(
                        fact.source_finding.code for fact in item.attention.facts
                    ), has_projection=item.result.projection is not None,
                    missing_input_count=len(item.result.missing_inputs),
                )

        explanation = result.answer
        guarded = _guard_physical_event_language(explanation, evidence)
        if self.recorder:
            event = self.recorder.start_stage(
                "workspace_response_projection", selected_item_ids=result.portfolio_query.item_ids
                if result.portfolio_query else (),
            )
            self.recorder.complete_stage(
                event, status=result.status.value,
                semantic_guard_applied=guarded,
            )
        return WorkspaceResponse(
            status=result.status,
            route=route,
            explanation=explanation if not guarded else _safe_projection_explanation(evidence),
            portfolio_query=result.portfolio_query,
            evidence=evidence,
            session_context=result.session_context,
            evidence_references=tuple(result.evidence_references),
            tool_executions=tuple(_tool_trace_summary(item) for item in result.tool_executions),
            operational_errors=tuple(result.operational_errors),
            unsupported_questions=tuple(result.unsupported_questions),
            clarification_required=result.clarification_required,
            semantic_guard_applied=guarded,
        )


_PHYSICAL_SHORTAGE_CLAIM = re.compile(
    r"\b(?:stock[ -]?out|out of stock|run(?:ning)? out|shortage|shortfall|"
    r"supply short|desabastec\w*|sin suministro|sin existencias|sin stock|"
    r"agotamiento|falta de suministro|quedarse sin (?:producto|gas|stock))\b",
    re.IGNORECASE,
)
_PHYSICAL_CLAIM_NEGATION = re.compile(
    r"\b(?:no|not|never|without|does not|doesn't|will not|won't|is not|isn't|"
    r"cannot|sin|no se|no hay|no prev\w*|no indica)(?:\s+\w+){0,4}\s*$",
    re.IGNORECASE,
)
_PHYSICAL_CLAIM_POST_NEGATION = re.compile(
    r"^\s*(?:(?:is|are|was|were|will be|would be)\s+)?(?:not|never|no)\b",
    re.IGNORECASE,
)
_CAPACITY_CLAIM = re.compile(
    r"\b(?:capacity overflow|over capacity|exceeds? (?:the )?capacity|"
    r"desborda(?:miento)?|supera la capacidad|exceso de capacidad)\b",
    re.IGNORECASE,
)
_SAFETY_CAPACITY_EQUIVALENCE = re.compile(
    r"(?:safety.stock|stock de seguridad).{0,55}(?:means?|is|equals?|equivale|significa).{0,55}"
    r"(?:capacity|capacidad)|(?:capacity|capacidad).{0,55}(?:means?|is|equals?|equivale|significa).{0,55}"
    r"(?:safety.stock|stock de seguridad)",
    re.IGNORECASE,
)
_SAFETY_STOCKOUT_EQUIVALENCE = re.compile(
    r"(?:safety.stock|stock de seguridad).{0,55}(?:means?|is|equals?|equivale|significa|causa|implica).{0,55}"
    r"(?:stock[ -]?out|shortage|desabastec\w*|agotamiento)|(?:stock[ -]?out|shortage|desabastec\w*|agotamiento).{0,55}"
    r"(?:means?|is|equals?|equivale|significa|causa|implica).{0,55}(?:safety.stock|stock de seguridad)",
    re.IGNORECASE,
)


def _guard_physical_event_language(
    explanation: str | None,
    evidence: PortfolioEvidenceBundle,
) -> bool:
    if not explanation:
        return False
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", explanation):
        shortage_claim = _has_unsupported_physical_claim(sentence, _PHYSICAL_SHORTAGE_CLAIM)
        capacity_claim = _has_unsupported_physical_claim(sentence, _CAPACITY_CLAIM)
        conflates_events = bool(
            _has_affirmative_equivalence(sentence, _SAFETY_CAPACITY_EQUIVALENCE)
            or _has_affirmative_equivalence(sentence, _SAFETY_STOCKOUT_EQUIVALENCE)
        )
        if not (shortage_claim or capacity_claim or conflates_events):
            continue
        targets = _items_named_in_sentence(sentence, evidence.items)
        if not targets:
            targets = evidence.items
        for item in targets:
            projection = item.result.projection
            if projection is None:
                return True
            if shortage_claim and not projection.stockout_before_delivery:
                return True
            if capacity_claim and not projection.capacity_exceeded:
                return True
            if conflates_events:
                return True
    return False


def _has_unsupported_physical_claim(sentence: str, claim_pattern: re.Pattern) -> bool:
    for match in claim_pattern.finditer(sentence):
        prefix = sentence[:match.start()]
        suffix = sentence[match.end():]
        prefix_clause = re.split(r"[;,]|\b(?:but|pero|however|although)\b", prefix, flags=re.IGNORECASE)[-1]
        if _PHYSICAL_CLAIM_NEGATION.search(prefix_clause):
            continue
        if _PHYSICAL_CLAIM_POST_NEGATION.search(suffix):
            continue
        return True
    return False


def _has_affirmative_equivalence(sentence: str, pattern: re.Pattern) -> bool:
    match = pattern.search(sentence)
    if match is None:
        return False
    return not bool(re.search(
        r"\b(?:not|never|no|does not|do not|no es|no significa|no equivale|no implica)\b",
        match.group(), re.IGNORECASE,
    ))


def _items_named_in_sentence(sentence: str, items: tuple[PortfolioItemEvidence, ...]) -> tuple[PortfolioItemEvidence, ...]:
    from .supply_agent import _identity

    lowered = sentence.casefold()
    found = []
    for evidence in items:
        names = _identity(evidence.item).values()
        names = tuple(value for value in names if value)
        if any(str(name).casefold() in lowered for name in names):
            found.append(evidence)
    return tuple(found)


def _safe_projection_explanation(evidence: PortfolioEvidenceBundle) -> str:
    if not evidence.items:
        return "No position-level projection is available for this request."
    lines = ["Operational facts remain separate for each position:"]
    from .supply_agent import _identity

    for item in evidence.items:
        identity = _identity(item.item)
        label = " / ".join(str(value) for value in identity.values() if value) or item.item.item_id
        projection = item.result.projection
        if projection is None:
            lines.append(f"- {label}: evaluation status {item.result.status}.")
            continue
        safety_breach = any(f.code == "safety_stock_breach" for f in item.result.findings)
        if safety_breach and not projection.stockout_before_delivery:
            lines.append(
                f"- {label}: projected inventory is below configured safety stock; "
                "no physical stockout is projected before delivery."
            )
        else:
            lines.append(
                f"- {label}: physical stockout before delivery is "
                f"{'projected' if projection.stockout_before_delivery else 'not projected'}."
            )
        if projection.capacity_exceeded:
            lines.append(f"  Tank capacity is exceeded after delivery for {label}.")
    return "\n".join(lines)


def _tool_trace_summary(execution: dict[str, Any]) -> dict[str, Any]:
    name = execution.get("name")
    result = execution.get("result", {})
    if name == "search_industrial_knowledge":
        result = {
            "status": result.get("status"),
            "source_ids": tuple(
                source.get("chunk_id") for source in result.get("sources", ())
            ),
        }
    return {
        "name": name,
        "arguments": execution.get("arguments", {}),
        "result": result,
        "elapsed_seconds": execution.get("elapsed_seconds", 0.0),
    }
