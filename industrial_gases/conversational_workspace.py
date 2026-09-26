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
from .portfolio_query import PortfolioQuery, SupplyPortfolioQueryService
from .factual_comparison import (
    FactualComparison, comparison_answer, comparison_field_for_question,
    compare_portfolio_facts, is_factual_comparison_question,
)
from .service import SupplyAssuranceService
from .supply_agent import (
    SupplyAgent,
    SupplyAgentRequest,
    SupplyAgentStatus,
    SupplyDecisionModel,
    SupplyAgentProviderError,
    _has_context_reference,
    _has_explicit_what_if,
    _is_documentary_followup,
    _requires_documentary_evidence,
    _requires_operational_context,
    _is_portfolio_query,
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
class _SemanticGuardMatch:
    reason: str
    rule: str
    pattern_id: str
    matched_phrase: str


def _semantic_guard_match(
    reason: str, rule: str, pattern_id: str, match: re.Match[str],
) -> _SemanticGuardMatch:
    phrase = re.sub(r"\s+", " ", match.group(0).replace("\r", " ").replace("\n", " ")).strip()
    return _SemanticGuardMatch(reason, rule, pattern_id, phrase[:120])


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
    comparison: FactualComparison | None = None

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
    documentary = _requires_documentary_evidence(question) or _is_documentary_followup(question, context)
    scenario = _has_explicit_what_if(question)
    operational = _requires_operational_context(question)
    follow_up = bool(context.selected_item_ids and _has_context_reference(question))
    comparison = is_factual_comparison_question(question)

    if documentary and (scenario or operational):
        intent = WorkspaceIntent.COMBINED
    elif comparison:
        intent = WorkspaceIntent.FOLLOW_UP if follow_up else WorkspaceIntent.OPERATIONAL
    elif scenario:
        intent = WorkspaceIntent.SCENARIO
    elif documentary:
        intent = WorkspaceIntent.DOCUMENTARY
    elif follow_up:
        intent = WorkspaceIntent.FOLLOW_UP
    else:
        intent = WorkspaceIntent.OPERATIONAL

    # Every response starts from deterministic query selection and retains its
    # matching position evidence, including documentary-only questions.
    capabilities = ["portfolio_query", "operational_evidence"]
    if documentary:
        capabilities.append("industrial_knowledge")
    if scenario:
        capabilities.append("scenario_evaluation")
    if comparison:
        capabilities.append("deterministic_comparison")
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

        if is_factual_comparison_question(question):
            return self._run_comparison(question, route)
        from .supply_agent import _unsupported_vague_delivery_scenario
        if _unsupported_vague_delivery_scenario(question):
            return self._run_scenario_clarification(question, route)
        contextual_operational_fact = (
            "industrial_knowledge" not in route.capabilities
            and not _has_explicit_what_if(question)
            and bool(self.session_context.selected_item_ids)
            and _requires_operational_context(question)
            and not _is_portfolio_query(question)
        )
        if ((_has_context_reference(question)
              and "industrial_knowledge" not in route.capabilities
              and not _has_explicit_what_if(question))
                or contextual_operational_fact):
            return self._run_context_followup(question, route)

        agent = SupplyAgent(
            SupplyAgentRequest(question, None, self.session_context),
            self.portfolio, self.attention, self.knowledge, self.decision_model,
            recorder=self.recorder, service=self.service, workspace_mode=True,
        )
        result = agent.run(question, timeout_seconds)
        evidence = result.evidence_bundle or PortfolioEvidenceBundle(())
        if self.recorder and result.session_context != self.session_context:
            event = self.recorder.start_stage("workspace_reference_resolution")
            self.recorder.complete_stage(
                event,
                previous_selected_item_ids=self.session_context.selected_item_ids,
                selected_item_ids=result.session_context.selected_item_ids,
                previous_focused_item_id=self.session_context.focused_item_id,
                focused_item_id=result.session_context.focused_item_id,
                previous_intent=self.session_context.last_intent,
                resolved_intent=result.session_context.last_intent,
                document_scope_item_ids=result.session_context.last_document_scope_item_ids,
                scenario_target_id=result.session_context.last_scenario_target_id,
            )
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
        guard_match = _physical_event_guard_match(explanation, evidence)
        guarded = guard_match is not None
        if self.recorder:
            event = self.recorder.start_stage(
                "workspace_response_projection", selected_item_ids=result.portfolio_query.item_ids
                if result.portfolio_query else (),
            )
            self.recorder.complete_stage(
                event, status=result.status.value,
                semantic_guard_applied=guarded,
                semantic_guard_reason=guard_match.reason if guard_match else None,
                semantic_guard_rule=guard_match.rule if guard_match else None,
                semantic_guard_pattern_id=guard_match.pattern_id if guard_match else None,
                semantic_guard_matched_phrase=guard_match.matched_phrase if guard_match else None,
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

    def _run_comparison(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        from .supply_agent import (
            _identity_filters_from_question, _resolve_portfolio_scope,
        )
        field = comparison_field_for_question(question)
        context = self.session_context
        spanish = bool(re.search(r"[¿ñáéíóú]", question, re.IGNORECASE))
        has_explicit_identity = bool(_identity_filters_from_question(question, self.portfolio))
        if not context.selected_item_ids and not has_explicit_identity:
            query_result = SupplyPortfolioQueryService().select(
                self.portfolio, self.attention, PortfolioQuery(item_id="__no_current_selection__"),
            )
            comparison = compare_portfolio_facts(query_result, field, spanish=spanish)
            context_result = context
            if not query_result.matches:
                return WorkspaceResponse(
                    SupplyAgentStatus.NEEDS_INPUT,
                    WorkspaceRoute(WorkspaceIntent.FOLLOW_UP, route.capabilities),
                    "¿Qué posiciones quieres comparar?" if re.search(r"[¿ñáéíóú]", question, re.I)
                    else "Which positions would you like to compare?",
                    query_result, PortfolioEvidenceBundle(()), context,
                    unsupported_questions=("current_comparison_set",),
                    clarification_required=True,
                )
        else:
            query_result, focus, used_context, clarification = _resolve_portfolio_scope(
                question, self.portfolio, self.attention,
                SupplyAgentRequest(question, None, context),
            )
            if clarification:
                if self.recorder:
                    event = self.recorder.start_stage("workspace_reference_resolution")
                    self.recorder.complete_stage(
                        event, previous_selected_item_ids=context.selected_item_ids,
                        selected_item_ids=(), focused_item_id=None,
                        clarification_required=True,
                    )
                return WorkspaceResponse(
                    SupplyAgentStatus.NEEDS_INPUT,
                    WorkspaceRoute(WorkspaceIntent.FOLLOW_UP, route.capabilities),
                    "¿Qué posición quieres usar? Aclara la referencia." if re.search(r"[¿ñáéíóú]", question, re.I)
                    else "Please clarify which position you mean.",
                    query_result, PortfolioEvidenceBundle(()), context,
                    unsupported_questions=("unambiguous_comparison_scope",),
                    clarification_required=True,
                )
            comparison = compare_portfolio_facts(query_result, field, spanish=spanish)
            retained_ids = (
                tuple(item_id for item_id in context.selected_item_ids
                      if any(item.item_id == item_id for item in self.portfolio.items))
                if used_context else query_result.item_ids
            )
            selected_ids = query_result.item_ids
            focus_id = focus if focus in retained_ids else (
                context.focused_item_id if context.focused_item_id in retained_ids else None
            )
            context_result = SupplyAgentSessionContext(
                selected_item_ids=retained_ids,
                focused_item_id=focus_id,
                last_query=context.last_query or query_result.query,
                last_scenario_target_id=(context.last_scenario_target_id
                    if context.last_scenario_target_id in retained_ids else None),
                last_intent="comparison",
                last_document_scope_item_ids=tuple(
                    item_id for item_id in context.last_document_scope_item_ids
                    if item_id in retained_ids
                ),
                last_scenario_change=context.last_scenario_change,
            )
            if self.recorder:
                event = self.recorder.start_stage("workspace_reference_resolution")
                self.recorder.complete_stage(
                    event, previous_selected_item_ids=context.selected_item_ids,
                    selected_item_ids=retained_ids,
                    focused_item_id=focus_id,
                    resolved_reference="current_set",
                    clarification_required=False,
                )

        evidence = PortfolioEvidenceBundle(tuple(
            PortfolioItemEvidence(match.item, match.attention)
            for match in query_result.matches
        ))
        explanation = comparison_answer(comparison, spanish=spanish)
        if self.recorder:
            event = self.recorder.start_stage(
                "deterministic_comparison", field=comparison.field,
                selected_item_ids=query_result.item_ids,
            )
            self.recorder.complete_stage(
                event, comparable=comparison.comparable,
                semantics=comparison.semantics, reason=comparison.reason,
                values=tuple({
                    "item_id": value.item_id, "value": str(value.value),
                    "comparison_value": str(value.comparison_value), "unit": value.unit,
                } for value in comparison.values),
            )
        return WorkspaceResponse(
            status=SupplyAgentStatus.COMPLETED,
            route=route,
            explanation=explanation,
            portfolio_query=query_result,
            evidence=evidence,
            session_context=context_result,
            comparison=comparison,
        )

    def _run_scenario_clarification(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        """Do not invoke generation or the evaluator for unsupported vague delivery timing."""
        by_id = {item.item_id: item for item in self.portfolio.items}
        attention_by_id = {item.item_id: item for item in self.attention.items}
        target_id = self.session_context.focused_item_id
        selected = []
        if target_id in by_id and target_id in attention_by_id:
            selected.append(PortfolioItemEvidence(by_id[target_id], attention_by_id[target_id]))
        evidence = PortfolioEvidenceBundle(tuple(selected))
        spanish = bool(re.search(r"[¿ñáéíóú]", question, re.IGNORECASE))
        explanation = (
            "Indica un adelanto o retraso explícito en días para evaluar el escenario."
            if spanish else
            "Specify an explicit number of days earlier or later to evaluate this scenario."
        )
        if self.recorder:
            event = self.recorder.start_stage(
                "scenario_clarification", target_item_id=target_id,
            )
            self.recorder.complete_stage(event, supported_day_change=False)
        return WorkspaceResponse(
            status=SupplyAgentStatus.NEEDS_INPUT,
            route=route,
            explanation=explanation,
            portfolio_query=None,
            evidence=evidence,
            session_context=self.session_context,
            unsupported_questions=("explicit_delivery_day_change",),
            clarification_required=True,
        )

    def _run_context_followup(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        """Resolve a structured follow-up and answer from the current domain result only."""
        from .supply_agent import _resolve_portfolio_scope
        query_result, focus, used_context, clarification = _resolve_portfolio_scope(
            question, self.portfolio, self.attention,
            SupplyAgentRequest(question, None, self.session_context),
        )
        evidence = PortfolioEvidenceBundle(() if clarification else tuple(
            PortfolioItemEvidence(match.item, match.attention)
            for match in query_result.matches
        ))
        valid_context_ids = tuple(
            item_id for item_id in self.session_context.selected_item_ids
            if any(item.item_id == item_id for item in self.portfolio.items)
        )
        next_ids = (
            valid_context_ids if used_context and valid_context_ids else
            valid_context_ids if clarification else query_result.item_ids
        )
        next_focus = focus if focus in next_ids else (
            self.session_context.focused_item_id
            if clarification and self.session_context.focused_item_id in next_ids else None
        )
        focus_changed = next_focus != self.session_context.focused_item_id
        next_context = SupplyAgentSessionContext(
            selected_item_ids=next_ids,
            focused_item_id=next_focus,
            last_query=(self.session_context.last_query if used_context and self.session_context.last_query
                        else query_result.query),
            last_scenario_target_id=(self.session_context.last_scenario_target_id
                if not focus_changed and self.session_context.last_scenario_target_id in next_ids else None),
            last_intent="operational",
            last_document_scope_item_ids=(
                (next_focus,) if focus_changed and self.session_context.last_document_scope_item_ids and next_focus
                else () if focus_changed else tuple(
                    item_id for item_id in self.session_context.last_document_scope_item_ids
                    if item_id in next_ids
                )
            ),
            last_scenario_change=self.session_context.last_scenario_change,
        )
        spanish = bool(re.search(r"[¿ñáéíóú]", question, re.IGNORECASE))
        if clarification:
            explanation = clarification
            candidate_labels = tuple(_portfolio_match_label(match) for match in query_result.matches)
            if candidate_labels:
                explanation += " Candidates: " + "; ".join(candidate_labels)
        else:
            explanation = _context_followup_explanation(question, evidence, spanish=spanish)
        if self.recorder:
            event = self.recorder.start_stage("workspace_reference_resolution")
            self.recorder.complete_stage(
                event,
                previous_selected_item_ids=self.session_context.selected_item_ids,
                selected_item_ids=next_ids,
                previous_focused_item_id=self.session_context.focused_item_id,
                focused_item_id=next_focus,
                previous_intent=self.session_context.last_intent,
                resolved_intent="operational",
                clarification_required=bool(clarification),
            )
        return WorkspaceResponse(
            status=SupplyAgentStatus.NEEDS_INPUT if clarification else SupplyAgentStatus.COMPLETED,
            route=route,
            explanation=explanation,
            portfolio_query=query_result,
            evidence=evidence,
            session_context=next_context,
            unsupported_questions=("unambiguous_portfolio_reference",) if clarification else (),
            clarification_required=bool(clarification),
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
_CAPACITY_CLAIM_PATTERNS = (
    ("capacity_overflow_affirmative", "capacity_overflow_affirmative_en_1", re.compile(
        r"\bthere (?:is|will be|would be) (?:a )?capacity overflow\b", re.IGNORECASE,
    )),
    ("capacity_overflow_affirmative", "capacity_overflow_affirmative_en_2", re.compile(
        r"\bcapacity overflow (?:exists|is present|is projected|will occur|would occur)\b",
        re.IGNORECASE,
    )),
    ("over_capacity", "over_capacity_en_1", re.compile(
        r"\b(?:is|are|was|were|will be|would be) over capacity\b", re.IGNORECASE,
    )),
    ("capacity_exceedance", "capacity_exceedance_en_1", re.compile(
        r"\bexceeds? (?:the )?(?:installation )?capacity\b", re.IGNORECASE,
    )),
    ("capacity_exceeded", "capacity_exceeded_en_1", re.compile(
        r"\bcapacity(?:\s+(?:is|was|will be|would be|has been))?\s+exceeded\b",
        re.IGNORECASE,
    )),
    ("capacity_overflow_affirmative", "capacity_overflow_es_1", re.compile(
        r"\bdesborda(?:miento)?\b", re.IGNORECASE,
    )),
    ("capacity_exceedance", "capacity_exceedance_es_1", re.compile(
        r"\b(?:supera|excede|superará|excederá) (?:la )?(?:capacidad|capacidad instalada)\b",
        re.IGNORECASE,
    )),
    ("capacity_exceeded", "capacity_exceeded_es_1", re.compile(
        r"\bcapacidad (?:es |ha sido |será )?(?:superada|excedida)\b", re.IGNORECASE,
    )),
    ("capacity_overflow_affirmative", "capacity_overflow_es_2", re.compile(
        r"\b(?:hay|existe|habrá) (?:un )?(?:desbordamiento|exceso) de capacidad\b",
        re.IGNORECASE,
    )),
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
    return _physical_event_guard_reason(explanation, evidence) is not None


def _physical_event_guard_reason(
    explanation: str | None,
    evidence: PortfolioEvidenceBundle,
) -> str | None:
    match = _physical_event_guard_match(explanation, evidence)
    return match.reason if match else None


def _physical_event_guard_match(
    explanation: str | None,
    evidence: PortfolioEvidenceBundle,
) -> _SemanticGuardMatch | None:
    """Return bounded match metadata when structured evidence rejects a claim."""
    if not explanation:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", explanation):
        shortage_claim = _PHYSICAL_SHORTAGE_CLAIM.search(sentence)
        capacity_claim = _find_unsupported_capacity_claim(sentence)
        conflates_capacity = _find_affirmative_equivalence(
            sentence, _SAFETY_CAPACITY_EQUIVALENCE,
        )
        conflates_stockout = _find_affirmative_equivalence(
            sentence, _SAFETY_STOCKOUT_EQUIVALENCE,
        )
        conflates_events = bool(conflates_capacity or conflates_stockout)
        if not (shortage_claim or capacity_claim or conflates_events):
            continue
        targets = _items_named_in_sentence(sentence, evidence.items)
        if not targets:
            targets = evidence.items
        for item in targets:
            projection = item.result.projection
            if conflates_capacity:
                return _semantic_guard_match(
                    "safety_stock_capacity_conflation",
                    "safety_stock_capacity_conflation",
                    "safety_capacity_equivalence_1",
                    conflates_capacity,
                )
            if conflates_stockout:
                return _semantic_guard_match(
                    "safety_stock_stockout_conflation",
                    "safety_stock_stockout_conflation",
                    "safety_stockout_equivalence_1",
                    conflates_stockout,
                )
            if projection is None:
                claim = capacity_claim
                if claim is None and shortage_claim is not None:
                    claim = _semantic_guard_match(
                        "projection_unavailable_for_physical_claim",
                        "physical_shortage_claim",
                        "physical_shortage_claim_en_es_1",
                        shortage_claim,
                    )
                if claim is not None:
                    return _SemanticGuardMatch(
                        "projection_unavailable_for_physical_claim",
                        "physical_claim_requires_projection",
                        claim.pattern_id,
                        claim.matched_phrase,
                    )
            stockout_claim = (
                _find_unsupported_stockout_claim(sentence, item)
                if shortage_claim and projection is not None and not projection.stockout_before_delivery
                else None
            )
            if stockout_claim is not None:
                return _semantic_guard_match(
                    "unsupported_stockout_claim",
                    "physical_stockout_claim",
                    "physical_shortage_claim_en_es_1",
                    stockout_claim,
                )
            if capacity_claim and not projection.capacity_exceeded:
                return capacity_claim
    return None


def _find_unsupported_stockout_claim(
    sentence: str, item: PortfolioItemEvidence,
) -> re.Match[str] | None:
    """Treat a supported safety-stock shortfall as distinct from physical stockout."""
    for match in _PHYSICAL_SHORTAGE_CLAIM.finditer(sentence):
        prefix = sentence[:match.start()]
        suffix = sentence[match.end():]
        prefix_clause = re.split(
            r"[;,]|\b(?:but|pero|however|although)\b", prefix, flags=re.IGNORECASE,
        )[-1]
        if _PHYSICAL_CLAIM_NEGATION.search(prefix_clause):
            continue
        if _PHYSICAL_CLAIM_POST_NEGATION.search(suffix):
            continue
        match_is_shortfall = match.group().casefold() == "shortfall"
        context = sentence[max(0, match.start() - 60):min(len(sentence), match.end() + 60)]
        names_safety_stock = bool(re.search(
            r"\b(?:safety[ -]?stock|stock de seguridad)\b", context, re.IGNORECASE,
        ))
        has_structured_breach = any(
            finding.code == "safety_stock_breach" for finding in item.result.findings
        )
        if match_is_shortfall and names_safety_stock and has_structured_breach:
            continue
        return match
    return None


def _find_unsupported_capacity_claim(sentence: str) -> _SemanticGuardMatch | None:
    for rule, pattern_id, pattern in _CAPACITY_CLAIM_PATTERNS:
        match = _find_unnegated_claim(sentence, pattern)
        if match is not None:
            return _semantic_guard_match(
                "unsupported_capacity_claim", rule, pattern_id, match,
            )
    return None


def _find_unnegated_claim(sentence: str, pattern: re.Pattern) -> re.Match[str] | None:
    for match in pattern.finditer(sentence):
        prefix = sentence[:match.start()]
        suffix = sentence[match.end():]
        prefix_clause = re.split(
            r"[;,]|\b(?:but|pero|however|although)\b", prefix, flags=re.IGNORECASE,
        )[-1]
        if _PHYSICAL_CLAIM_NEGATION.search(prefix_clause):
            continue
        if _PHYSICAL_CLAIM_POST_NEGATION.search(suffix):
            continue
        return match
    return None


def _find_affirmative_equivalence(sentence: str, pattern: re.Pattern) -> re.Match[str] | None:
    match = pattern.search(sentence)
    if match is None:
        return None
    if re.search(
        r"\b(?:not|never|no|does not|do not|no es|no significa|no equivale|no implica)\b",
        match.group(), re.IGNORECASE,
    ):
        return None
    return match


def _has_affirmative_equivalence(sentence: str, pattern: re.Pattern) -> bool:
    return _find_affirmative_equivalence(sentence, pattern) is not None


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


def _safe_projection_explanation(evidence: PortfolioEvidenceBundle, *, spanish: bool = False) -> str:
    if not evidence.items:
        return "No hay una proyección disponible para esta posición." if spanish else "No position-level projection is available for this request."
    lines = ["Los hechos operativos se mantienen separados por posición:" if spanish
             else "Operational facts remain separate for each position:"]
    for item in evidence.items:
        request = item.item.request
        site = getattr(request.site, "name", None)
        product = getattr(request.gas_product, "name", None)
        label = " · ".join(str(value) for value in (site, product) if value)
        if not label:
            label = "posición seleccionada" if spanish else "selected position"
        projection = item.result.projection
        if projection is None:
            lines.append(f"- {label}: estado de evaluación {item.result.status}." if spanish
                         else f"- {label}: evaluation status {item.result.status}.")
            continue
        safety_breach = any(f.code == "safety_stock_breach" for f in item.result.findings)
        if safety_breach and not projection.stockout_before_delivery:
            lines.append((
                f"- {label}: el inventario previsto queda por debajo del stock de seguridad configurado; "
                "no se prevé agotamiento físico antes de la entrega."
            ) if spanish else (
                f"- {label}: projected inventory is below configured safety stock; "
                "no physical stockout is projected before delivery."
            ))
        else:
            lines.append((
                f"- {label}: "
                f"{'se prevé' if projection.stockout_before_delivery else 'no se prevé'} agotamiento físico antes de la entrega."
            ) if spanish else (
                f"- {label}: physical stockout before delivery is "
                f"{'projected' if projection.stockout_before_delivery else 'not projected'}."
            ))
        if projection.capacity_exceeded:
            lines.append(f"  La capacidad del tanque se supera tras la entrega para {label}." if spanish
                         else f"  Tank capacity is exceeded after delivery for {label}.")
    return "\n".join(lines)


def _context_followup_explanation(
    question: str, evidence: PortfolioEvidenceBundle, *, spanish: bool,
) -> str:
    """Answer narrow operational follow-ups directly from selected projections."""
    from decimal import Decimal

    metric = None
    if re.search(r"\b(?:inventario|inventory)\b.*\b(?:antes|before)\b.*\b(?:entrega|delivery)\b", question, re.I):
        metric = "inventory_before_delivery"
    elif re.search(r"\b(?:agotamiento|stockout|stock out)\b.*\b(?:antes|before)\b", question, re.I):
        metric = "stockout_before_delivery"
    elif re.search(r"\b(?:brecha|gap)\b.*\b(?:stock de seguridad|safety stock)\b|"
                   r"\b(?:stock de seguridad|safety stock)\b.*\b(?:brecha|gap)\b", question, re.I):
        metric = "safety_stock_gap_before_delivery"
    elif re.search(r"\b(?:cu[aá]nto se consume|consumo|consumption)\b.*\b(?:hasta|until)\b.*\b(?:entrega|delivery)\b", question, re.I):
        metric = "consumption_until_delivery"
    elif re.search(r"\b(?:volumen requerido|required volume)\b", question, re.I):
        metric = "required_delivery_volume"

    if metric and evidence.items:
        lines = []
        for item in evidence.items:
            request = item.item.request
            site = getattr(request.site, "name", None)
            product = getattr(request.gas_product, "name", None)
            label = " · ".join(str(value) for value in (site, product) if value)
            label = label or ("posición seleccionada" if spanish else "selected position")
            projection = item.result.projection
            if projection is None:
                lines.append(
                    f"{label}: estado de evaluación {item.result.status}." if spanish
                    else f"{label}: evaluation status {item.result.status}."
                )
                continue
            if metric == "stockout_before_delivery":
                value = (
                    ("Sí" if projection.stockout_before_delivery else "No") if spanish
                    else ("Yes" if projection.stockout_before_delivery else "No")
                )
                measure = "Agotamiento antes de la entrega" if spanish else "Stockout before delivery"
            else:
                quantity = getattr(projection, {
                    "inventory_before_delivery": "inventory_immediately_before_delivery",
                    "safety_stock_gap_before_delivery": "safety_stock_gap_before_delivery",
                    "consumption_until_delivery": "consumption_until_delivery",
                    "required_delivery_volume": "required_delivery_volume",
                }[metric])
                formatted = format(Decimal(str(quantity.value)).normalize(), ",f")
                value = f"{formatted} {quantity.unit}"
                measure = {
                    "inventory_before_delivery": "Inventario antes de la entrega" if spanish else "Inventory before delivery",
                    "safety_stock_gap_before_delivery": "Brecha frente al stock de seguridad" if spanish else "Safety-stock gap",
                    "consumption_until_delivery": "Consumo hasta la entrega" if spanish else "Consumption until delivery",
                    "required_delivery_volume": "Volumen requerido" if spanish else "Required delivery volume",
                }[metric]
            lines.append(f"{label}: {measure}: {value}.")
        return "\n".join(lines)

    capacity_question = bool(
        re.search(r"\b(?:capacidad|capacity)\b", question, re.IGNORECASE)
        and re.search(r"\b(?:supera|excede|exceder|exceeded?|over)\b", question, re.IGNORECASE)
    )
    if capacity_question and evidence.items:
        lines = []
        for item in evidence.items:
            request = item.item.request
            site = getattr(request.site, "name", None)
            product = getattr(request.gas_product, "name", None)
            label = " · ".join(str(value) for value in (site, product) if value)
            label = label or ("posición seleccionada" if spanish else "selected position")
            projection = item.result.projection
            if projection is None:
                line = (f"{label}: evaluación {item.result.status}." if spanish
                        else f"{label}: evaluation status {item.result.status}.")
            elif projection.capacity_exceeded:
                line = (f"{label}: se supera la capacidad después de la entrega." if spanish
                        else f"{label}: capacity is exceeded after delivery.")
            else:
                line = (f"{label}: no se supera la capacidad después de la entrega." if spanish
                        else f"{label}: capacity is not exceeded after delivery.")
            lines.append(line)
        return "\n".join(lines)
    return _safe_projection_explanation(evidence, spanish=spanish)


def _portfolio_match_label(match) -> str:
    request = match.item.request
    site = getattr(request.site, "name", None)
    product = getattr(request.gas_product, "name", None)
    return " · ".join(value for value in (site, product) if value) or "Position"


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
