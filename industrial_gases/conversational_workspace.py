"""Conversation-first orchestration over existing Industrial Gases capabilities."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Callable

from diagnostics import PerformanceRecorder
from .industrial_knowledge import IndustrialKnowledgeService
from .operational_attention import OperationalAttentionResult
from .portfolio import SupplyPortfolioResult
from .portfolio_query import PortfolioEvidenceBundle, PortfolioItemEvidence, PortfolioQueryResult, ScopedKnowledgeSource, SupplyAgentSessionContext, WorkspaceScenarioSetState
from .portfolio_query import WorkspaceScenario
from .portfolio_query import PortfolioQuery, SupplyPortfolioQueryService
from .factual_comparison import (
    FactualComparison, comparison_answer, comparison_field_for_question,
    compare_portfolio_facts, is_factual_comparison_question,
    ScenarioSetComparison, compare_scenarios, scenario_comparison_answer,
)
from .service import SupplyAssuranceService
from .models import ConsumptionRate, Quantity
from .lexical import numeric_lexemes, normalize_unit_lexeme, parse_decimal_number
from .supply_scenarios import (
    SupplyAssuranceAlternative, SupplyAssuranceScenarioSet,
    SupplyAssuranceScenarioSetResult, evaluate_supply_assurance_scenario_set,
)
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
from .factual_comparison import compare_supply_assurance_scenario_set


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


@dataclass(frozen=True)
class _ExplicitScenarioValue:
    value: Decimal
    unit: str
    baseline_reference: bool = False
    time_unit: str | None = None


@dataclass(frozen=True)
class _ScenarioSetSyntax:
    dimension: str | None
    values: tuple[_ExplicitScenarioValue, ...] = ()
    issue: str | None = None

    @property
    def baseline_was_stated(self) -> bool:
        return any(value.baseline_reference for value in self.values)


class _ScenarioSetInputError(ValueError):
    """A deterministic interpretation/validation issue suitable for clarification."""


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
    scenario_comparison: ScenarioSetComparison | None = None
    scenario_set_result: SupplyAssuranceScenarioSetResult | None = None

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
    scenario = _has_explicit_what_if(question) or _looks_like_scenario_set_request(question)
    operational = _requires_operational_context(question)
    follow_up = bool(context.selected_item_ids and _has_context_reference(question))
    comparison = is_factual_comparison_question(question)
    scenario_comparison = not _requests_scenario_search(question) and (
        _is_scenario_comparison_request(question)
        or (bool(context.scenario_history) and _scenario_question_semantics(question)[0] is not None)
        or _scenario_set_followup_ids(question, context) is not None
    )

    if documentary and (scenario or operational):
        intent = WorkspaceIntent.COMBINED
    elif scenario_comparison:
        intent = WorkspaceIntent.FOLLOW_UP
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
    if scenario_comparison:
        capabilities.append("deterministic_scenario_comparison")
    if _looks_like_scenario_set_request(question):
        capabilities.extend(("scenario_set_evaluation", "deterministic_scenario_comparison"))
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

        scenario_syntax = _parse_scenario_set_syntax(question)
        if scenario_syntax is not None:
            return self._run_explicit_scenario_set(question, route, scenario_syntax)

        if _scenario_set_best_request(question, self.session_context):
            return self._scenario_set_non_ranking_response(question, route)
        if _scenario_set_followup_ids(question, self.session_context) is not None:
            return self._run_scenario_set_followup(question, route)

        if _requests_scenario_search(question):
            return self._run_scenario_clarification(question, route)
        if is_factual_comparison_question(question):
            if _scenario_set_followup_ids(question, self.session_context) is not None:
                return self._run_scenario_set_followup(question, route)
            if _is_scenario_comparison_question(question, self.session_context):
                return self._run_scenario_comparison(question, route)
            return self._run_comparison(question, route)
        if (_is_scenario_comparison_request(question)
                or _is_scenario_comparison_question(question, self.session_context)):
            return self._run_scenario_comparison(question, route)
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
        result_context = result.session_context
        scenario = result.scenario_analysis
        if scenario is not None and result.evidence_bundle and len(result.evidence_bundle.items) == 1:
            target_item = result.evidence_bundle.items[0].item
            result_context = _retain_scenario_history(
                result_context, target_item.item_id, target_item.request, scenario, question,
            )
            result = replace(result, session_context=result_context)
            if self.recorder:
                history_event = self.recorder.start_stage("scenario_history_resolution")
                self.recorder.complete_stage(
                    history_event, focused_item_id=target_item.item_id,
                    scenario_ids=tuple(entry.scenario_id for entry in result_context.scenario_history),
                    scenario_labels=tuple(entry.label for entry in result_context.scenario_history),
                )
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

    def _run_explicit_scenario_set(self, question: str, route: WorkspaceRoute,
                                   syntax: "_ScenarioSetSyntax") -> WorkspaceResponse:
        """Resolve one existing position, then use the domain scenario-set contracts."""
        from .supply_agent import _resolve_portfolio_scope

        context = self.session_context
        query_result, focus, used_context, clarification = _resolve_portfolio_scope(
            question, self.portfolio, self.attention,
            SupplyAgentRequest(question, None, context),
        )
        if clarification or len(query_result.matches) != 1:
            labels = tuple(_portfolio_match_label(match) for match in query_result.matches)
            message = (
                "¿Qué posición quieres comparar? Selecciona una sola posición."
                if _is_spanish_workspace_text(question) else
                "Which position should be compared? Select one portfolio position."
            )
            if labels:
                message += (" Opciones: " if _is_spanish_workspace_text(question) else " Choices: ") + "; ".join(labels)
            return self._scenario_set_needs_input(
                question, route, query_result, message, "single_portfolio_position",
            )

        match = query_result.matches[0]
        item_id = match.item_id
        baseline = match.item.request
        if (_requires_documentary_evidence(question)
                or _is_documentary_followup(question, context)):
            return self._scenario_set_needs_input(
                question, route, query_result,
                ("Separa la comparación física de la consulta documental y envíalas por separado."
                 if _is_spanish_workspace_text(question) else
                 "Separate the physical scenario comparison from the documentary question and submit them separately."),
                "documentary_scenario_set_combination_not_supported",
                focused_item_id=item_id,
            )
        if syntax.issue:
            message = _scenario_set_issue_message(syntax.issue, _is_spanish_workspace_text(question))
            return self._scenario_set_needs_input(
                question, route, query_result, message, syntax.issue,
                focused_item_id=item_id,
            )
        try:
            scenario_set = _build_explicit_scenario_set(
                question, baseline, syntax, spanish=_is_spanish_workspace_text(question),
            )
        except _ScenarioSetInputError as error:
            return self._scenario_set_needs_input(
                question, route, query_result,
                _scenario_set_issue_message(str(error), _is_spanish_workspace_text(question)),
                str(error), focused_item_id=item_id,
            )

        recorder = self.recorder
        if recorder:
            event = recorder.start_stage(
                "scenario_set_interpretation", focused_item_id=item_id,
                dimension=syntax.dimension,
                explicit_values=tuple(str(value) for value in syntax.values),
                alternative_count=len(scenario_set.alternatives),
                scope_source="explicit_or_existing_workspace_resolution",
            )
            recorder.complete_stage(event, baseline_deduplicated=syntax.baseline_was_stated)
            event = recorder.start_stage("deterministic_scenario_set_evaluation")
        result = evaluate_supply_assurance_scenario_set(
            scenario_set, self.service or SupplyAssuranceService(),
        )
        comparison = compare_supply_assurance_scenario_set(
            result, spanish=_is_spanish_workspace_text(question),
        )
        if recorder:
            recorder.complete_stage(
                event, branch_count=1 + len(scenario_set.alternatives),
                evaluation_statuses=tuple(
                    branch.alternative_result.status for branch in result.scenario_results
                ), generation_llm_calls=0, embedding_calls=0, rag_calls=0,
            )
            event = recorder.start_stage("deterministic_scenario_set_comparison")
            recorder.complete_stage(
                event, scenario_ids=tuple(value.id for value in scenario_set.alternatives),
                compared_fields=tuple(metric.field for metric in comparison.metrics),
                generation_llm_calls=0, embedding_calls=0, rag_calls=0,
            )

        previous_focus = context.focused_item_id
        next_context = SupplyAgentSessionContext(
            selected_item_ids=(item_id,), focused_item_id=item_id,
            last_query=context.last_query or query_result.query,
            last_scenario_target_id=item_id, last_intent="scenario_set",
            last_document_scope_item_ids=(
                (item_id,) if item_id in context.last_document_scope_item_ids else ()
            ),
            last_scenario_change=context.last_scenario_change,
            scenario_history=context.scenario_history if previous_focus == item_id else (),
            scenario_set_state=WorkspaceScenarioSetState(
                item_id, scenario_set, result, comparison,
            ),
        )
        evidence = PortfolioEvidenceBundle((PortfolioItemEvidence(match.item, match.attention),))
        answer = (
            "Se evaluó la línea base y cada alternativa explícita de forma independiente."
            if _is_spanish_workspace_text(question) else
            "The baseline and each explicit alternative were evaluated independently."
        )
        return WorkspaceResponse(
            status=SupplyAgentStatus.COMPLETED,
            route=route,
            explanation=answer,
            portfolio_query=query_result,
            evidence=evidence,
            session_context=next_context,
            scenario_comparison=comparison,
            scenario_set_result=result,
        )

    def _scenario_set_needs_input(self, question, route, query_result, message, reason,
                                  focused_item_id=None) -> WorkspaceResponse:
        matches = query_result.matches if query_result is not None else ()
        evidence_items = tuple(
            PortfolioItemEvidence(match.item, match.attention) for match in matches
        )
        old_context = self.session_context
        context = SupplyAgentSessionContext(
            selected_item_ids=tuple(match.item_id for match in matches),
            focused_item_id=focused_item_id,
            last_query=old_context.last_query,
            last_scenario_target_id=focused_item_id,
            last_intent="scenario_set_needs_input",
            last_document_scope_item_ids=(),
            last_scenario_change=old_context.last_scenario_change,
            scenario_history=old_context.scenario_history if focused_item_id == old_context.focused_item_id else (),
        )
        return WorkspaceResponse(
            status=SupplyAgentStatus.NEEDS_INPUT,
            route=route,
            explanation=message,
            portfolio_query=query_result,
            evidence=PortfolioEvidenceBundle(evidence_items),
            session_context=context,
            unsupported_questions=(reason,),
            clarification_required=True,
        )

    def _run_scenario_set_followup(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        state = self.session_context.scenario_set_state
        references = _scenario_set_followup_ids(question, self.session_context) or ()
        spanish = _is_spanish_workspace_text(question)
        comparison = compare_supply_assurance_scenario_set(
            state.result, fields=(
                (_scenario_question_semantics(question)[0],)
                if _scenario_question_semantics(question)[0] else None
            ), spanish=spanish, scenario_ids=references,
        )
        answer = (
            "La comparación factual solicitada está disponible para las alternativas indicadas."
            if spanish else "The requested factual comparison is available for the named alternatives."
        )
        by_id = {item.item_id: item for item in self.portfolio.items}
        by_attention = {item.item_id: item for item in self.attention.items}
        item = by_id.get(state.item_id)
        attention = by_attention.get(state.item_id)
        evidence = PortfolioEvidenceBundle(
            (PortfolioItemEvidence(item, attention),) if item is not None and attention is not None else (),
        )
        if self.recorder:
            event = self.recorder.start_stage(
                "scenario_set_reference_resolution", focused_item_id=state.item_id,
                scenario_ids=references,
            )
            self.recorder.complete_stage(event, clarification_required=False)
            event = self.recorder.start_stage("deterministic_scenario_set_comparison")
            self.recorder.complete_stage(
                event, scenario_ids=references,
                compared_fields=tuple(metric.field for metric in comparison.metrics),
                generation_llm_calls=0, embedding_calls=0, rag_calls=0,
            )
        next_state = replace(state, comparison=comparison)
        next_context = replace(self.session_context, scenario_set_state=next_state)
        return WorkspaceResponse(
            status=SupplyAgentStatus.COMPLETED,
            route=route,
            explanation=answer,
            portfolio_query=None,
            evidence=evidence,
            session_context=next_context,
            scenario_comparison=comparison,
            scenario_set_result=state.result,
        )

    def _scenario_set_non_ranking_response(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        state = self.session_context.scenario_set_state
        message = (
            "Puedo comparar los resultados calculados, pero no clasificar ni recomendar una alternativa."
            if _is_spanish_workspace_text(question) else
            "I can compare calculated results, but this workspace does not rank or recommend an alternative."
        )
        item = next((item for item in self.portfolio.items if item.item_id == state.item_id), None)
        attention = next((item for item in self.attention.items if item.item_id == state.item_id), None)
        evidence = PortfolioEvidenceBundle(
            (PortfolioItemEvidence(item, attention),) if item is not None and attention is not None else (),
        )
        return WorkspaceResponse(
            status=SupplyAgentStatus.NEEDS_INPUT, route=route, explanation=message,
            portfolio_query=None, evidence=evidence, session_context=self.session_context,
            scenario_comparison=state.comparison, scenario_set_result=state.result,
            unsupported_questions=("scenario_ranking_not_supported",), clarification_required=True,
        )

    def _run_scenario_comparison(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        spanish = _is_spanish_workspace_text(question)
        context = self.session_context
        portfolio_ids = {item.item_id for item in self.portfolio.items}
        attention_ids = {item.item_id for item in self.attention.items}
        if context.scenario_history and (
            not context.focused_item_id or context.focused_item_id not in portfolio_ids
            or context.focused_item_id not in attention_ids
            or any(item.item_id != context.focused_item_id for item in context.scenario_history)
        ):
            clarification = (
                "La posición asociada a estos escenarios ya no está disponible. Vuelve a seleccionar una posición."
                if spanish else
                "The position associated with these scenarios is no longer available. Select a position again."
            )
            if self.recorder:
                event = self.recorder.start_stage("scenario_reference_resolution")
                self.recorder.complete_stage(
                    event, focused_item_id=context.focused_item_id,
                    scenario_ids=tuple(item.scenario_id for item in context.scenario_history),
                    clarification_required=True, reason="stale_scenario_focus",
                )
            return WorkspaceResponse(
                status=SupplyAgentStatus.NEEDS_INPUT,
                route=WorkspaceRoute(WorkspaceIntent.FOLLOW_UP, route.capabilities),
                explanation=clarification,
                portfolio_query=None,
                evidence=PortfolioEvidenceBundle(()),
                session_context=SupplyAgentSessionContext(),
                clarification_required=True,
                unsupported_questions=("current_scenario_focus",),
            )
        entries, clarification = _resolve_scenario_references(question, context.scenario_history)
        if clarification is None and len(entries) < 2:
            clarification = (
                "Evalúa al menos una alternativa explícita antes de comparar escenarios."
                if spanish else "Evaluate at least one explicit alternative before comparing scenarios."
            )
        if clarification:
            if self.recorder:
                event = self.recorder.start_stage("scenario_reference_resolution")
                self.recorder.complete_stage(
                    event, focused_item_id=context.focused_item_id,
                    scenario_ids=tuple(item.scenario_id for item in entries),
                    clarification_required=True,
                )
            return WorkspaceResponse(
                status=SupplyAgentStatus.NEEDS_INPUT,
                route=WorkspaceRoute(WorkspaceIntent.FOLLOW_UP, route.capabilities),
                explanation=clarification,
                portfolio_query=None,
                evidence=PortfolioEvidenceBundle(()),
                session_context=context,
                clarification_required=True,
                unsupported_questions=("evaluated_scenario_history",),
            )

        field, question_kind = _scenario_question_semantics(question)
        entries = tuple(_localized_scenario(entry, spanish) for entry in entries)
        comparison = compare_scenarios(
            entries, fields=None if field is None else (field,), spanish=spanish,
        )
        answer = scenario_comparison_answer(
            comparison, question_kind=question_kind, spanish=spanish,
        )
        if self.recorder:
            event = self.recorder.start_stage("scenario_reference_resolution")
            self.recorder.complete_stage(
                event, focused_item_id=context.focused_item_id,
                scenario_ids=tuple(item.scenario_id for item in entries),
                scenario_labels=tuple(item.label for item in entries),
                clarification_required=False,
            )
            event = self.recorder.start_stage("deterministic_scenario_comparison")
            self.recorder.complete_stage(
                event, focused_item_id=context.focused_item_id,
                scenario_labels=tuple(item.label for item in entries),
                compared_fields=tuple(metric.field for metric in comparison.metrics),
                units=tuple(tuple(value.unit for value in metric.values) for metric in comparison.metrics),
                comparability=tuple(metric.comparable for metric in comparison.metrics),
                reasons=tuple(metric.reason for metric in comparison.metrics),
                values=tuple(tuple({
                    "scenario_id": value.scenario_id, "label": value.label,
                    "value": str(value.value), "unit": value.unit,
                } for value in metric.values) for metric in comparison.metrics),
            )
        by_id = {item.item_id: item for item in self.portfolio.items}
        attention_by_id = {item.item_id: item for item in self.attention.items}
        focused = context.focused_item_id
        subject_evidence = ()
        if focused in by_id and focused in attention_by_id:
            subject_evidence = (PortfolioItemEvidence(by_id[focused], attention_by_id[focused]),)
        return WorkspaceResponse(
            status=SupplyAgentStatus.COMPLETED,
            route=WorkspaceRoute(WorkspaceIntent.FOLLOW_UP, route.capabilities),
            explanation=answer,
            portfolio_query=None,
            evidence=PortfolioEvidenceBundle(subject_evidence),
            session_context=context,
            scenario_comparison=comparison,
        )

    def _run_comparison(self, question: str, route: WorkspaceRoute) -> WorkspaceResponse:
        from .supply_agent import (
            _identity_filters_from_question, _resolve_portfolio_scope,
        )
        field = comparison_field_for_question(question)
        context = self.session_context
        spanish = _is_spanish_workspace_text(question)
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
                scenario_history=(
                    context.scenario_history if focus_id == context.focused_item_id else ()
                ),
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
        spanish = _is_spanish_workspace_text(question)
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
            scenario_history=(
                () if focus_changed else tuple(
                    entry for entry in self.session_context.scenario_history
                    if entry.item_id == next_focus and entry.item_id in next_ids
                )
            ),
        )
        spanish = _is_spanish_workspace_text(question)
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


def _is_scenario_comparison_request(question: str) -> bool:
    return bool(re.search(
        r"\b(?:compare|comparar|compara|comparison|comparing)\b.{0,50}\b(?:scenarios?|escenarios?)\b|"
        r"\b(?:scenarios?|escenarios?)\b.{0,50}\b(?:compare|comparar|compara)\b|"
        r"\b(?:ambas\s+alternativas|both\s+alternatives|los\s+tres\s+escenarios|three\s+scenarios)\b",
        question, re.IGNORECASE,
    ))


def _requests_scenario_search(question: str) -> bool:
    """Block implicit date/threshold searches; only explicit alternatives are supported."""
    return bool(
        re.search(r"\b(?:cu[aá]ndo|when|what date|qu[eé] fecha)\b", question, re.I)
        and re.search(r"\b(?:entrega|delivery|llegar|llegue|arrive|arrives)\b", question, re.I)
        and re.search(r"\b(?:stock de seguridad|safety stock|mantener|mantiene|keep|maintain)\b", question, re.I)
        and not re.search(r"\b(?:contrato|contract|contractual|documentaci[oó]n|documentation)\b", question, re.I)
    )


def _is_spanish_workspace_text(question: str) -> bool:
    return bool(re.search(
        r"[¿¡ñáéíóú]"
        r"|\b(?:compara|comparar|escenarios?|entrega|inventario|d[ií]as?|antes|despu[eé]s|"
        r"posiciones?|atenci[oó]n|h[aá]blame|solo|contrato|alternativas?|"
        r"stock de seguridad|agotamiento)\b",
        question, re.IGNORECASE,
    ))


def _scenario_question_semantics(question: str) -> tuple[str | None, str]:
    folded = question.casefold()
    if re.search(r"\b(?:mantiene|mantener|meets?|keeps?|maintains?)\b.{0,35}\b(?:stock de seguridad|safety stock)\b|\b(?:stock de seguridad|safety stock)\b.{0,35}\b(?:mantiene|maintained|cumple)\b", folded):
        return "safety_stock_gap_before_delivery", "safety_maintained"
    if re.search(r"\b(?:agotamiento|stockout|sin stock|quedarse sin)\b", folded):
        return "stockout_before_delivery", "stockout"
    if re.search(r"\b(?:capacidad|capacity)\b", folded) and re.search(r"\b(?:supera|excede|exceeded|exceeds|overflow)\b", folded):
        return "capacity_exceeded", "capacity"
    if re.search(r"\b(?:mayor|m[aá]s|higher|highest|greatest|maximum)\b", folded) and re.search(r"\b(?:inventario|inventory)\b", folded):
        return "inventory_immediately_before_delivery", "highest"
    if re.search(r"\b(?:menor|menos|lower|lowest|least|minimum)\b", folded) and re.search(r"\b(?:consumo|consumption)\b", folded):
        return "consumption_until_delivery", "lowest"
    metric = comparison_field_for_question(question)
    if metric in {
        "inventory_immediately_before_delivery", "safety_stock_gap_before_delivery",
        "stockout_before_delivery", "capacity_exceeded",
    }:
        return metric, "values"
    if re.search(r"\b(?:qu[eé]\s+cambia|what\s+changes?)\b", folded):
        return None, "values"
    return None, "values"


def _is_scenario_comparison_question(
    question: str, context: SupplyAgentSessionContext,
) -> bool:
    field, _ = _scenario_question_semantics(question)
    return bool(context.scenario_history) and (
        _is_scenario_comparison_request(question)
        or field is not None
        or bool(re.search(r"\b(?:what\s+changes?|qu[eé]\s+cambia|between|entre)\b", question, re.I))
        or re.search(r"\b(?:baseline|el actual|la otra alternativa|the other alternative|both alternatives|ambas alternativas)\b", question, re.I)
    )


def _explicit_scenario_reference_offsets(question: str) -> tuple[int, ...]:
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    }
    offsets = []
    pattern = re.compile(
        r"\b(?P<n>\d+|one|two|three|four|five|un|una|uno|dos|tres|cuatro|cinco)\s*"
        r"(?P<unit>days?|d[ií]as?)\s*(?P<direction>earlier|before|sooner|antes|adelantad[oa]s?|later|after|despu[eé]s)?\b",
        re.I,
    )
    for match in pattern.finditer(question):
        direction = (match.group("direction") or "").casefold()
        # Bare "dos días" is accepted only in the named reference "la de dos días".
        if not direction and not re.search(r"\bla\s+de\s+$", question[max(0, match.start() - 12):match.start()], re.I):
            continue
        token = match.group("n").casefold()
        number = int(token) if token.isdigit() else words[token]
        offsets.append(number if direction in {"later", "after", "después"} else -number)
    return tuple(dict.fromkeys(offsets))


def _resolve_scenario_references(
    question: str, history: tuple[WorkspaceScenario, ...],
) -> tuple[tuple[WorkspaceScenario, ...], str | None]:
    if not history:
        return (), "Evalúa primero al menos una alternativa explícita para poder comparar escenarios." if _is_spanish_workspace_text(question) else "Evaluate at least one explicit alternative before comparing scenarios."
    if re.search(r"\b(?:los tres escenarios|three scenarios)\b", question, re.I) and len(history) != 3:
        return (), "No están disponibles los tres escenarios indicados." if _is_spanish_workspace_text(question) else "The three requested scenarios are not all available."
    if re.search(r"\b(?:la otra alternativa|the other alternative|la otra|the other one)\b", question, re.I):
        return (), "¿A qué alternativa te refieres?" if _is_spanish_workspace_text(question) else "Which alternative do you mean?"
    if re.search(r"\b(?:ambas alternativas|both alternatives)\b", question, re.I):
        alternatives = tuple(entry for entry in history if entry.scenario_id != "baseline")
        if len(alternatives) == 2:
            return alternatives, None
        return (), "¿Qué dos alternativas quieres comparar?" if _is_spanish_workspace_text(question) else "Which two alternatives should I compare?"

    requested: list[str] = []
    if re.search(r"\b(?:baseline|el actual|escenario actual|current scenario)\b", question, re.I):
        requested.append("baseline")
    offsets = _explicit_scenario_reference_offsets(question)
    for offset in offsets:
        requested.append(f"delivery-offset:{offset}")
    if requested:
        selected = tuple(entry for entry in history if entry.scenario_id in requested)
        if len(selected) != len(set(requested)):
            return (), "No encuentro todos los escenarios indicados en esta conversación." if _is_spanish_workspace_text(question) else "I cannot find every named scenario in this conversation."
        return selected, None
    if _is_scenario_comparison_request(question) or _scenario_question_semantics(question)[0] is not None:
        return history, None
    return (), "Aclara qué escenarios quieres comparar." if _is_spanish_workspace_text(question) else "Please clarify which scenarios to compare."


def _retain_scenario_history(
    context: SupplyAgentSessionContext,
    item_id: str,
    baseline_request: Any,
    scenario: Any,
    question: str,
) -> SupplyAgentSessionContext:
    alternative = scenario.alternative
    if alternative.id != "planned-delivery-time":
        return context
    baseline_at = baseline_request.delivery_plan.planned_delivery_at
    alternative_at = alternative.alternative_request.delivery_plan.planned_delivery_at
    difference = (alternative_at - baseline_at).total_seconds() / 86400
    if not difference.is_integer() or difference == 0:
        return context
    offset = int(difference)
    scenario_id = f"delivery-offset:{offset}"
    spanish = _is_spanish_workspace_text(question)
    amount = abs(offset)
    if spanish:
        unit = "día" if amount == 1 else "días"
        direction = "antes" if offset < 0 else "después"
    else:
        unit = "day" if amount == 1 else "days"
        direction = "earlier" if offset < 0 else "later"
    label = f"{amount} {unit} {direction}"
    history = list(context.scenario_history)
    if not any(entry.scenario_id == "baseline" for entry in history):
        history.insert(0, WorkspaceScenario(
            item_id, "baseline", "Actual" if spanish else "Baseline", None,
            scenario.baseline_result,
        ))
    if not any(entry.scenario_id == scenario_id for entry in history):
        history.append(WorkspaceScenario(item_id, scenario_id, label, offset, scenario.alternative_result))
    if len(history) > 16:
        history = history[:1] + history[-15:]
    updated = replace(context, scenario_history=tuple(history))
    return updated


def _localized_scenario(entry: WorkspaceScenario, spanish: bool) -> WorkspaceScenario:
    if entry.offset_days is None:
        label = "Actual" if spanish else "Baseline"
    else:
        amount = abs(entry.offset_days)
        if spanish:
            unit = "día" if amount == 1 else "días"
            direction = "antes" if entry.offset_days < 0 else "después"
        else:
            unit = "day" if amount == 1 else "days"
            direction = "earlier" if entry.offset_days < 0 else "later"
        label = f"{amount} {unit} {direction}"
    return replace(entry, label=label)


_SCENARIO_COMPARE = re.compile(
    r"\b(?:compare|compara|comparar|contrast|contrasta|versus|vs\.?|frente\s+a)\b",
    re.IGNORECASE,
)
_SCENARIO_NUMBER = r"(?P<number>[-+]?\d{1,3}(?:[ .]\d{3})+|[-+]?\d+(?:[.,]\d+)?)"
_SCENARIO_UNIT = (
    r"(?P<unit>Nm\s*³|Sm\s*³|m\s*³|Nm3|Sm3|m3|kg|kilos?|"
    r"kilogramos?|toneladas?|litros?|t|L)(?=\s|/|[.,;]|$)"
)
_DELIVERY_WORDS = re.compile(r"\b(?:delivery|entrega|suministro|arriv\w*|lleg\w*)\b", re.I)
_QUANTITY_WORDS = re.compile(
    r"\b(?:delivery\s+(?:quantit(?:y|ies)|volumes?|amounts?)|planned\s+delivery|"
    r"cantidades?\s+(?:de\s+)?(?:entrega|suministro)|vol[uú]menes?\s+(?:de\s+)?entrega|"
    r"cantidad\s+prevista|volumen\s+previsto)\b", re.I,
)
_CONSUMPTION_WORDS = re.compile(r"\b(?:consumption|consumo|forecast|previsi[oó]n|rate|tasa)\b", re.I)
_RATE_SUFFIX = re.compile(
    r"\s*(?:/\s*(?:day|days|d[ií]a|d[ií]as)|per\s+(?:day|days)|por\s+d[ií]a|"
    r"al\s+d[ií]a|daily)\b", re.I,
)


def _looks_like_scenario_set_request(question: str) -> bool:
    """Recognize explicit comparative requests without interpreting their values."""
    if not _SCENARIO_COMPARE.search(question):
        return False
    numeric_comparison = (
        len(numeric_lexemes(question)) >= 2
        and re.search(
            r"\b(?:delivery|entrega|suministro|consumption|consumo|quantity|cantidad|rate|tasa)\b",
            question, re.I,
        )
    )
    option_markers = bool(re.search(
        r"\b(?:alternatives?|scenarios?|alternativas?|escenarios?)\s+[A-C]\b|"
        r"\b[A-C]\s*[:=]", question, re.I,
    ))
    quantity_change_words = bool(re.search(
        r"\b(?:larger|more|higher|increase|increased|mayor|m[aá]s|aumentar|incrementar)\b.{0,35}"
        r"\b(?:delivery|entrega|quantity|cantidad|volume|volumen)\b|"
        r"\b(?:delivery|entrega|quantity|cantidad|volume|volumen)\b.{0,35}"
        r"\b(?:larger|more|higher|increase|increased|mayor|m[aá]s|aumentar|incrementar)\b",
        question, re.I,
    ))
    dimension_cues = sum((
        bool(re.search(r"\b(?:delivery|entrega)\b", question, re.I)
             and re.search(r"\b(?:days?|d[ií]as?|earlier|before|sooner|antes|adelantad\w*)\b", question, re.I)),
        bool(_QUANTITY_WORDS.search(question) or quantity_change_words),
        bool(_CONSUMPTION_WORDS.search(question)),
    ))
    return bool(numeric_comparison or (option_markers and dimension_cues > 1))


def _parse_scenario_set_syntax(question: str) -> _ScenarioSetSyntax | None:
    """Extract only explicit same-dimension numeric alternatives from a comparison request."""
    if not _looks_like_scenario_set_request(question):
        return None

    day_matches = tuple(re.finditer(
        rf"{_SCENARIO_NUMBER}\s*(?P<unit>days?|d[ií]as?)\b", question, re.I,
    ))
    delivery_days = day_matches if _DELIVERY_WORDS.search(question) else ()

    rate_matches: list[_ExplicitScenarioValue] = []
    rate_positions: list[tuple[int, int]] = []
    for match in re.finditer(rf"{_SCENARIO_NUMBER}\s*{_SCENARIO_UNIT}", question, re.I):
        suffix = question[match.end():match.end() + 40]
        if not _RATE_SUFFIX.match(suffix):
            continue
        try:
            value = parse_decimal_number(match.group("number"))
        except ValueError:
            continue
        before = question[max(0, match.start() - 28):match.start()]
        rate_matches.append(_ExplicitScenarioValue(
            value, normalize_unit_lexeme(match.group("unit"), include_words=True),
            bool(re.search(r"\b(?:current|baseline|actual|actualmente|actual|vigente)\b", before, re.I)),
            "day",
        ))
        rate_positions.append(match.span())

    quantity_matches: list[_ExplicitScenarioValue] = []
    quantity_cue = bool(
        _QUANTITY_WORDS.search(question)
        or (_DELIVERY_WORDS.search(question)
            and re.search(rf"{_SCENARIO_NUMBER}\s*{_SCENARIO_UNIT}", question, re.I))
    )
    if quantity_cue:
        # Every quantity in a detected set must carry its own unit; values may
        # not inherit one from a neighboring branch.
        for match in re.finditer(rf"{_SCENARIO_NUMBER}\s*{_SCENARIO_UNIT}", question, re.I):
            if _RATE_SUFFIX.match(question[match.end():match.end() + 40]):
                continue
            try:
                value = parse_decimal_number(match.group("number"))
            except ValueError:
                continue
            before = question[max(0, match.start() - 28):match.start()]
            quantity_matches.append(_ExplicitScenarioValue(
                value, normalize_unit_lexeme(match.group("unit"), include_words=True),
                bool(re.search(r"\b(?:current|baseline|actual|actualmente|actual|vigente)\b", before, re.I)),
            ))

    dimensions = []
    if delivery_days:
        dimensions.append("delivery_horizon")
    if rate_matches:
        dimensions.append("consumption_rate")
    if quantity_matches:
        dimensions.append("planned_delivery_quantity")
    if len(dimensions) > 1:
        return _ScenarioSetSyntax(None, issue="mixed_scenario_dimensions")
    if not dimensions:
        if quantity_cue or _CONSUMPTION_WORDS.search(question) or delivery_days:
            return _ScenarioSetSyntax(None, issue="explicit_values_require_units")
        return _ScenarioSetSyntax(None, issue="unsupported_scenario_dimension")

    dimension = dimensions[0]
    if dimension == "delivery_horizon":
        if len(day_matches) != len(numeric_lexemes(question)):
            return _ScenarioSetSyntax(dimension, issue="each_delivery_horizon_requires_explicit_day_value")
        values: list[_ExplicitScenarioValue] = []
        for match in delivery_days:
            try:
                value = parse_decimal_number(match.group("number"))
            except ValueError:
                continue
            if value != value.to_integral_value():
                return _ScenarioSetSyntax(dimension, issue="delivery_horizon_requires_whole_days")
            before = question[max(0, match.start() - 64):match.start()]
            values.append(_ExplicitScenarioValue(
                value, "day",
                bool(re.search(
                    r"\b(?:current|baseline|actual|actualmente|vigente)\s+(?:delivery|entrega)\s+(?:at|in|en|a)?\s*$",
                    before, re.I,
                )),
            ))
    elif dimension == "consumption_rate":
        values = rate_matches
        if len(values) != len(numeric_lexemes(question)) or len(values) < 2:
            return _ScenarioSetSyntax(dimension, issue="each_consumption_rate_requires_value_unit_and_time")
    else:
        values = quantity_matches
        if len(values) < 2 or len(numeric_lexemes(question)) != len(values):
            return _ScenarioSetSyntax(dimension, issue="each_delivery_quantity_requires_value_and_unit")

    if len(values) < 2:
        return _ScenarioSetSyntax(dimension, issue="at_least_two_explicit_values_required")
    if len({value.unit for value in values}) != 1:
        return _ScenarioSetSyntax(dimension, issue="scenario_units_must_match")
    return _ScenarioSetSyntax(dimension, tuple(values))


def _scenario_set_issue_message(issue: str, spanish: bool) -> str:
    if issue == "mixed_scenario_dimensions":
        return ("Evalúa una sola variable por comparación; separa el cambio de entrega, cantidad o consumo."
                if spanish else "Compare one variable at a time; separate delivery timing, quantity, or consumption changes.")
    if issue in {"explicit_values_require_units", "each_delivery_quantity_requires_value_and_unit",
                 "each_consumption_rate_requires_value_unit_and_time", "scenario_units_must_match",
                 "each_delivery_horizon_requires_explicit_day_value"}:
        return ("Indica cada alternativa con su valor y unidad explícitos, usando la misma unidad operativa."
                if spanish else "State each alternative with its explicit value and unit, using the same operational unit.")
    if issue == "delivery_horizon_requires_whole_days":
        return ("Indica cada horizonte de entrega como un número entero de días."
                if spanish else "State each delivery horizon as a whole number of days.")
    if issue in {"baseline_value_unavailable", "baseline_horizon_unavailable"}:
        return ("Falta una línea base estructurada para esa variable."
                if spanish else "The structured baseline for that variable is unavailable.")
    if issue == "stated_baseline_conflicts":
        return ("El valor indicado como actual no coincide con la línea base estructurada."
                if spanish else "The value stated as current does not match the structured baseline.")
    if issue == "no_alternative_after_baseline_deduplication":
        return ("Indica al menos una alternativa distinta de la línea base actual."
                if spanish else "State at least one alternative distinct from the current baseline.")
    if issue == "explicit scenario values cannot be negative":
        return ("Los valores explícitos de las alternativas no pueden ser negativos."
                if spanish else "Explicit alternative values cannot be negative.")
    return ("No puedo evaluar esa comparación de alternativas con los datos explícitos disponibles."
            if spanish else "I cannot evaluate that alternative comparison from the explicit values provided.")


def _build_explicit_scenario_set(question, baseline, syntax: _ScenarioSetSyntax, *, spanish):
    from .models import Quantity

    dimension = syntax.dimension
    if dimension == "delivery_horizon":
        if baseline.reference_time is None or baseline.delivery_plan is None:
            raise _ScenarioSetInputError("baseline_horizon_unavailable")
        duration = baseline.delivery_plan.planned_delivery_at - baseline.reference_time
        if duration.total_seconds() < 0 or duration.total_seconds() % 86400:
            raise _ScenarioSetInputError("baseline_horizon_unavailable")
        baseline_value = Decimal(int(duration.total_seconds() // 86400))
        unit = "day"
    elif dimension == "planned_delivery_quantity":
        if baseline.delivery_plan is None:
            raise _ScenarioSetInputError("baseline_value_unavailable")
        baseline_value = baseline.delivery_plan.planned_quantity.value
        unit = baseline.delivery_plan.planned_quantity.unit
    elif dimension == "consumption_rate":
        if baseline.consumption_forecast is None:
            raise _ScenarioSetInputError("baseline_value_unavailable")
        baseline_value = baseline.consumption_forecast.rate.value
        unit = baseline.consumption_forecast.rate.quantity_unit
        if any(value.time_unit != baseline.consumption_forecast.rate.time_unit for value in syntax.values):
            raise _ScenarioSetInputError("scenario_units_must_match")
    else:
        raise _ScenarioSetInputError("unsupported_scenario_dimension")

    unique: list[_ExplicitScenarioValue] = []
    seen: set[Decimal] = set()
    for value in syntax.values:
        if value.unit != unit:
            raise _ScenarioSetInputError("scenario_units_must_match")
        if value.baseline_reference and value.value != baseline_value:
            raise _ScenarioSetInputError("stated_baseline_conflicts")
        if value.value == baseline_value or value.value in seen:
            continue
        if value.value < 0:
            raise _ScenarioSetInputError("explicit scenario values cannot be negative")
        seen.add(value.value)
        unique.append(value)
    if not unique:
        raise _ScenarioSetInputError("no_alternative_after_baseline_deduplication")

    alternatives = []
    for index, value in enumerate(unique):
        ordinal = chr(ord("A") + index)
        slug = format(value.value.normalize(), "f").replace("-", "minus-").replace(".", "-")
        if dimension == "delivery_horizon":
            request = replace(
                baseline,
                delivery_plan=replace(
                    baseline.delivery_plan,
                    planned_delivery_at=baseline.reference_time + timedelta(days=int(value.value)),
                ),
            )
            scenario_id = f"delivery-horizon-days-{slug}"
            label = f"{ordinal} — Entrega en {value.value} días" if spanish else f"{ordinal} — Delivery in {value.value} days"
        elif dimension == "planned_delivery_quantity":
            request = replace(
                baseline,
                delivery_plan=replace(baseline.delivery_plan, planned_quantity=Quantity(value.value, unit)),
            )
            scenario_id = f"planned-delivery-quantity-{slug}-{unit}"
            label = f"{ordinal} — Entrega de {value.value} {unit}" if spanish else f"{ordinal} — Delivery quantity {value.value} {unit}"
        else:
            rate = baseline.consumption_forecast.rate
            request = replace(
                baseline,
                consumption_forecast=replace(
                    baseline.consumption_forecast,
                    rate=ConsumptionRate(value.value, unit, rate.time_unit),
                ),
            )
            scenario_id = f"consumption-rate-{slug}-{unit}-per-{rate.time_unit}"
            label = f"{ordinal} — Consumo {value.value} {unit}/{rate.time_unit}" if spanish else f"{ordinal} — Consumption {value.value} {unit}/{rate.time_unit}"
        alternatives.append(SupplyAssuranceAlternative(scenario_id, label, request))

    return SupplyAssuranceScenarioSet(baseline, tuple(alternatives))


def _scenario_set_followup_ids(question: str, context: SupplyAgentSessionContext) -> tuple[str, ...] | None:
    state = context.scenario_set_state
    if state is None or not re.search(r"\b(?:compare|compara|comparar)\b", question, re.I):
        return None
    match = re.search(r"\b([A-Z])\s+(?:and|y)\s+([A-Z])\b", question, re.I)
    if not match:
        return None
    ordinals = {chr(ord("A") + index): alternative.id
                for index, alternative in enumerate(state.scenario_set.alternatives)}
    ids = tuple(ordinals.get(letter.upper()) for letter in match.groups())
    return ids if all(ids) and ids[0] != ids[1] else None


def _scenario_set_best_request(question: str, context: SupplyAgentSessionContext) -> bool:
    return context.scenario_set_state is not None and bool(re.search(
        r"\b(?:which|what)\s+(?:(?:one|alternative|scenario)\s+)?(?:is|would be)\s+(?:best|better)|"
        r"\bcu[aá]l\s+(?:(?:es|ser[ií]a)\s+)?(?:la\s+)?(?:mejor|mejor opci[oó]n|recomendable)|"
        r"\b(?:recommend|recomienda|recomendar|which should we choose)\b", question, re.I,
    ))


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
