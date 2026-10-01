"""Offline-testable real-provider acceptance runner for the Conversational Workspace."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter
from typing import Callable
from uuid import uuid4

# Direct script execution places ``scripts/`` on sys.path, not the repository
# root. Add the existing source root before importing the application's modules.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from diagnostics import PerformanceRecorder, PerformanceSnapshot, PerformanceStatus, safe_failure_category
from execution_metrics import build_operation_metrics
from execution_view import ExecutionView, clipboard_payloads
from generation import GenerationCancelledError
from llm_client import LLMTimeoutError


CANONICAL_TURNS = (
    ("Attention positions", "¿Qué posiciones requieren atención?"),
    ("Focus Hospital", "Háblame solo de la del hospital."),
    ("Delivery -1 day", "¿Y si la entrega llegara un día antes?"),
    ("Delivery -2 days", "¿Y dos días antes?"),
    ("Compare scenarios", "Compara los tres escenarios."),
    ("Highest inventory", "¿Cuál tiene mayor inventario antes de la entrega?"),
    ("Safety stock", "¿En cuál se mantiene el stock de seguridad?"),
    ("Contract delivery", "¿Qué dice el contrato sobre la entrega?"),
    ("Compare again", "Compara otra vez los escenarios."),
)

class FailureCategory(str, Enum):
    PASS = "PASS"
    ASSERTION_FAILURE = "ASSERTION_FAILURE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    CITATION_VALIDATION_FAILURE = "CITATION_VALIDATION_FAILURE"
    SEMANTIC_GUARD_FAILURE = "SEMANTIC_GUARD_FAILURE"
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"


@dataclass(frozen=True)
class TurnResult:
    number: int
    label: str
    question: str
    operation_id: str | None
    status: str
    duration_seconds: float
    category: FailureCategory
    failed_checks: tuple[str, ...]
    llm_calls: int
    retrieval_seconds: float
    retrieval_status: str
    citation_status: str
    semantic_guard_status: str
    response: object | None = None
    snapshot: PerformanceSnapshot | None = None


@dataclass(frozen=True)
class AcceptanceResult:
    turns: tuple[TurnResult, ...]
    execution_views: dict[str, ExecutionView]
    conversation_context: object
    summary_checks: tuple[tuple[str, bool], ...]
    stopped_early: bool = False

    @property
    def passed(self) -> bool:
        return (
            not self.stopped_early
            and len(self.turns) == len(CANONICAL_TURNS)
            and all(turn.category is FailureCategory.PASS for turn in self.turns)
            and all(passed for _, passed in self.summary_checks)
        )

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def classify_failure(response=None, snapshot: PerformanceSnapshot | None = None, error=None) -> FailureCategory:
    """Classify only from known exception types and recorded structured stages."""
    if error is not None:
        if isinstance(error, (LLMTimeoutError, TimeoutError)):
            return FailureCategory.TIMEOUT
        if isinstance(error, GenerationCancelledError):
            return FailureCategory.PROVIDER_FAILURE
        if type(error).__name__ == "IndustrialKnowledgeOperationalError":
            return FailureCategory.RETRIEVAL_FAILURE
        if type(error).__name__ in {"SupplyAgentProviderError", "SupplyAgentProtocolError"}:
            return FailureCategory.PROVIDER_FAILURE
        return FailureCategory.UNEXPECTED_EXCEPTION

    events = snapshot.events if snapshot is not None else ()
    if snapshot is not None and snapshot.status in {"timed_out", "timeout"}:
        return FailureCategory.TIMEOUT
    errors = getattr(response, "operational_errors", ()) or ()
    if any(str(item).split(":", 1)[0] in {"LLMTimeoutError", "TimeoutError"} for item in errors):
        return FailureCategory.TIMEOUT
    if any(
        event.stage in {"portfolio_knowledge_retrieval", "query_embedding", "vector_search", "retrieved_context"}
        and event.status is PerformanceStatus.FAILED
        for event in events
    ):
        return FailureCategory.RETRIEVAL_FAILURE
    if any(
        event.stage == "citation_validation"
        and (event.status is PerformanceStatus.FAILED or event.metadata.get("valid") is False)
        for event in events
    ):
        return FailureCategory.CITATION_VALIDATION_FAILURE
    if any(
        event.stage == "workspace_response_projection"
        and event.metadata.get("semantic_guard_applied") is True
        for event in events
    ) or getattr(response, "semantic_guard_applied", False):
        return FailureCategory.SEMANTIC_GUARD_FAILURE
    if getattr(getattr(response, "status", None), "value", None) == "provider_error":
        return FailureCategory.PROVIDER_FAILURE
    if any(event.stage == "llm_call" and event.status is PerformanceStatus.FAILED for event in events):
        return FailureCategory.PROVIDER_FAILURE
    if snapshot is not None:
        category = safe_failure_category(snapshot)
        if category == "timeout":
            return FailureCategory.TIMEOUT
        if category == "retrieval_error":
            return FailureCategory.RETRIEVAL_FAILURE
        if category == "citation_validation":
            return FailureCategory.CITATION_VALIDATION_FAILURE
        if category == "semantic_guard":
            return FailureCategory.SEMANTIC_GUARD_FAILURE
        if category in {"provider_error", "protocol_error"}:
            return FailureCategory.PROVIDER_FAILURE
    return FailureCategory.PASS


def _projection_values(projection) -> dict[str, object]:
    if projection is None:
        return {}
    fields = (
        "consumption_until_delivery", "inventory_immediately_before_delivery",
        "safety_stock_gap_before_delivery", "stockout_before_delivery",
        "inventory_immediately_after_delivery", "required_delivery_volume", "capacity_exceeded",
    )
    return {
        field: getattr(projection, field).value
        if hasattr(getattr(projection, field), "value") else getattr(projection, field)
        for field in fields
    }


def _scenario(response):
    scenarios = getattr(response, "scenario_analyses", ())
    return scenarios[0][1] if len(scenarios) == 1 else None


def _values_for_metric(comparison, field: str):
    metric = next((item for item in comparison.metrics if item.field == field), None)
    return tuple(metric.values) if metric is not None else ()


def _check_turn(number: int, response, snapshot: PerformanceSnapshot, prior_responses: dict[int, object],
                views: dict[str, ExecutionView]) -> tuple[str, ...]:
    failed: list[str] = []
    def expect(ok: bool, code: str) -> None:
        if not ok:
            failed.append(code)

    ids = tuple(getattr(response, "selected_item_ids", ()))
    context = response.session_context
    if number == 1:
        expect(ids == ("hospital-costa-sur-o2", "alimentos-sur-malaga-co2"), "q1_attention_selection")
        attention_by_item = dict(response.attention_facts)
        expect(
            tuple(fact.source_finding.code for fact in attention_by_item.get("hospital-costa-sur-o2", ()))
            == ("safety_stock_breach",),
            "q1_hospital_attention_finding",
        )
        expect(
            tuple(fact.source_finding.code for fact in attention_by_item.get("alimentos-sur-malaga-co2", ()))
            == ("safety_stock_breach",),
            "q1_malaga_attention_finding",
        )
        expect(build_operation_metrics(snapshot).llm_call_count == 0, "q1_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), "q1_no_retrieval")
    elif number == 2:
        expect(ids == ("hospital-costa-sur-o2",), "q2_hospital_focus")
        expect(context.focused_item_id == "hospital-costa-sur-o2", "q2_unique_focus")
        expect(context.selected_item_ids == prior_responses[1].session_context.selected_item_ids,
               "q2_selected_set_preserved")
        expect(build_operation_metrics(snapshot).llm_call_count == 0, "q2_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), "q2_no_retrieval")
    elif number in {3, 4}:
        scenario = _scenario(response)
        expected_offset = -1 if number == 3 else -2
        expect(scenario is not None, f"q{number}_one_scenario")
        if scenario is not None:
            history = context.scenario_history
            expected_id = f"delivery-offset:{expected_offset}"
            expect(history[-1].scenario_id == expected_id, f"q{number}_offset")
            expect(history[-1].offset_days == expected_offset, f"q{number}_history_offset")
            base = _projection_values(scenario.baseline_result.projection)
            alt = _projection_values(scenario.alternative_result.projection)
            expected_base = (2800, 400, -1100, False, 4400, 1100, False)
            base_order = tuple(base.get(key) for key in (
                "consumption_until_delivery", "inventory_immediately_before_delivery",
                "safety_stock_gap_before_delivery", "stockout_before_delivery",
                "inventory_immediately_after_delivery", "required_delivery_volume", "capacity_exceeded",
            ))
            expect(base_order == expected_base, f"q{number}_original_baseline")
            expected_alt = (
                (2100, 1100, -400, False, 5100, 400, False)
                if number == 3 else (1400, 1800, 300, False, 5800, 0, False)
            )
            alt_order = tuple(alt.get(key) for key in (
                "consumption_until_delivery", "inventory_immediately_before_delivery",
                "safety_stock_gap_before_delivery", "stockout_before_delivery",
                "inventory_immediately_after_delivery", "required_delivery_volume", "capacity_exceeded",
            ))
            expect(alt_order == expected_alt, f"q{number}_expected_projection")
        expect(build_operation_metrics(snapshot).llm_call_count == 0, f"q{number}_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), f"q{number}_no_retrieval")
    elif number in {5, 9}:
        comparison = response.scenario_comparison
        expect(comparison is not None, f"q{number}_comparison_exists")
        expected_ids = ("baseline", "delivery-offset:-1", "delivery-offset:-2")
        expected_labels = ("Actual", "1 día antes", "2 días antes")
        if comparison is not None:
            inventory = _values_for_metric(comparison, "inventory_immediately_before_delivery")
            expect(tuple(value.scenario_id for value in inventory) == expected_ids,
                   f"q{number}_scenario_order")
            expect(tuple(value.label for value in inventory) == expected_labels,
                   f"q{number}_scenario_labels")
            expect(tuple(value.value for value in inventory) == (400, 1100, 1800),
                   f"q{number}_inventory_comparison")
            expected_by_field = {
                "consumption_until_delivery": (2800, 2100, 1400),
                "inventory_immediately_before_delivery": (400, 1100, 1800),
                "safety_stock_gap_before_delivery": (-1100, -400, 300),
                "stockout_before_delivery": (False, False, False),
                "inventory_immediately_after_delivery": (4400, 5100, 5800),
                "required_delivery_volume": (1100, 400, 0),
                "capacity_exceeded": (False, False, False),
            }
            for field, values in expected_by_field.items():
                result_values = _values_for_metric(comparison, field)
                expect(tuple(value.value for value in result_values) == values, f"q{number}_{field}")
        expect(build_operation_metrics(snapshot).llm_call_count == 0, f"q{number}_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), f"q{number}_no_retrieval")
        if number == 9:
            before = prior_responses.get(4)
            expect(before is not None and context.scenario_history == before.session_context.scenario_history,
                   "q9_scenario_history_after_documentary")
            q8_id = prior_responses.get("q8_operation_id")
            q8_view = views.get(q8_id) if isinstance(q8_id, str) else None
            expect(q8_view is not None and q8_view.snapshot.operation_id == q8_id,
                   "q9_q8_historical_view_retained")
            if q8_view is not None:
                expect(any(event.stage == "portfolio_knowledge_retrieval" for event in q8_view.snapshot.events),
                       "q9_q8_retrieval_stages_retained")
                eligibility = next((event for event in q8_view.snapshot.events
                                    if event.stage == "deterministic_documentary_eligibility"), None)
                response_event = next((event for event in q8_view.snapshot.events
                                       if event.stage == "deterministic_documentary_response"), None)
                expect(eligibility is not None and eligibility.metadata.get(
                    "deterministic_documentary_eligibility") is True,
                    "q9_q8_deterministic_eligibility_retained")
                expect(response_event is not None and response_event.metadata.get(
                    "deterministic_documentary_response_used") is True,
                    "q9_q8_deterministic_response_retained")
                expect(tuple(q8_view.snapshot.events) == tuple(prior_responses.get("q8_events", ())),
                       "q9_q8_snapshot_not_replaced")
    elif number == 6:
        comparison = response.scenario_comparison
        values = _values_for_metric(comparison, "inventory_immediately_before_delivery") if comparison else ()
        expect(tuple(value.value for value in values) == (400, 1100, 1800), "q6_inventory_values")
        expect(values and values[-1].scenario_id == "delivery-offset:-2", "q6_highest_inventory_is_minus_2")
        expect(build_operation_metrics(snapshot).llm_call_count == 0, "q6_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), "q6_no_retrieval")
    elif number == 7:
        comparison = response.scenario_comparison
        values = _values_for_metric(comparison, "safety_stock_gap_before_delivery") if comparison else ()
        expect(tuple((value.scenario_id, value.value) for value in values) == (
            ("baseline", -1100), ("delivery-offset:-1", -400), ("delivery-offset:-2", 300),
        ), "q7_safety_stock_values")
        expect(tuple(value.scenario_id for value in values if value.value is not None and value.value >= 0)
               == ("delivery-offset:-2",), "q7_only_minus_2_meets_safety_stock")
        expect(build_operation_metrics(snapshot).llm_call_count == 0, "q7_no_llm")
        expect(not any(event.stage in _RAG_STAGES for event in snapshot.events), "q7_no_retrieval")
    elif number == 8:
        expect(getattr(response.route.intent, "value", response.route.intent) == "documentary",
               "q8_documentary_intent")
        expect(response.session_context.focused_item_id == "hospital-costa-sur-o2", "q8_hospital_focus")
        expect(ids == ("hospital-costa-sur-o2",), "q8_single_hospital_position")
        retrieval_events = [event for event in snapshot.events if event.stage == "portfolio_knowledge_retrieval"]
        expect(bool(retrieval_events) and all(event.status is PerformanceStatus.COMPLETED for event in retrieval_events),
               "q8_scoped_retrieval_completed")
        scoped = response.documentary_sources
        expect(bool(scoped), "q8_position_sources_present")
        for source in scoped:
            metadata = source.source.chunk.metadata
            expect(source.item_ids == ("hospital-costa-sur-o2",), "q8_source_scoped_to_hospital")
            expect(metadata.get("gas_product_id") not in {"co2", "n2"}, "q8_no_cross_gas_source")
            if metadata.get("scope") != "global":
                expect(metadata.get("customer_id") == "hospital-costa-sur", "q8_source_customer_identity")
                expect(metadata.get("gas_product_id") == "medical-oxygen", "q8_source_o2_identity")
        for source in response.evidence.global_sources:
            expect(source.source.chunk.metadata.get("scope") == "global", "q8_global_source_scope")
            expect(source.source.chunk.metadata.get("applicable_domain") == "industrial_gases",
                   "q8_global_source_domain")
        metrics = build_operation_metrics(snapshot)
        eligibility = next((event for event in snapshot.events
                            if event.stage == "deterministic_documentary_eligibility"), None)
        deterministic_response = next((event for event in snapshot.events
                                       if event.stage == "deterministic_documentary_response"), None)
        expect(eligibility is not None and eligibility.metadata.get(
            "deterministic_documentary_eligibility") is True,
            "q8_deterministic_documentary_eligible")
        expect(eligibility is not None and eligibility.metadata.get(
            "eligibility_reason") == "single_direct_extractable_fact",
            "q8_single_direct_documentary_fact")
        expect(deterministic_response is not None and deterministic_response.metadata.get(
            "deterministic_documentary_response_used") is True,
            "q8_deterministic_documentary_response_used")
        expect(deterministic_response is not None and deterministic_response.metadata.get(
            "llm_calls_avoided") is True,
            "q8_generation_llm_avoided")
        expect(metrics.llm_call_count == 0, "q8_no_llm_call")
        citation = [event for event in snapshot.events if event.stage == "citation_validation"]
        expect(bool(citation) and any(
            event.status is PerformanceStatus.COMPLETED and event.metadata.get("valid") is True
            for event in citation
        ), "q8_citation_scope_validation_passed")
        expect(response.status.value == "completed", "q8_documentary_response_completed")
        expect(response.semantic_guard_applied is False, "q8_semantic_guard_not_applied")
        projection = [event for event in snapshot.events if event.stage == "workspace_response_projection"]
        expect(bool(projection) and all(event.metadata.get("semantic_guard_applied") is False for event in projection),
               "q8_semantic_guard_stage_clear")
        expect(not any(event.status is PerformanceStatus.FAILED and event.stage == "llm_call"
                       for event in snapshot.events), "q8_no_llm_timeout_or_failure")
    return tuple(failed)


_RAG_STAGES = frozenset({
    "portfolio_knowledge_retrieval", "query_embedding", "vector_search", "retrieved_context",
})
_SAFE_SEMANTIC_GUARD_REASONS = frozenset({
    "safety_stock_capacity_conflation", "safety_stock_stockout_conflation",
    "projection_unavailable_for_physical_claim", "unsupported_stockout_claim",
    "unsupported_capacity_claim",
})


def _semantic_guard_status(snapshot: PerformanceSnapshot) -> str:
    events = [event for event in snapshot.events if event.stage == "workspace_response_projection"]
    applied = [event for event in events if event.metadata.get("semantic_guard_applied") is True]
    if not applied:
        return "clear" if events else "not_recorded"
    reasons = tuple(dict.fromkeys(
        reason if isinstance(reason, str) and reason in _SAFE_SEMANTIC_GUARD_REASONS else "other"
        for event in applied
        for reason in (event.metadata.get("semantic_guard_reason"),)
        if isinstance(reason, str)
    ))
    if not reasons:
        reasons = ("other",)
    return "applied:" + ",".join(reasons)


def run_workspace_acceptance(
    *, provider_name: str, model_name: str, timeout_seconds: int,
    provider_factory: Callable[[PerformanceRecorder], object],
    decision_model_factory: Callable[[object, PerformanceRecorder], object],
    knowledge_factory: Callable[[PerformanceRecorder], object],
    portfolio_factory: Callable[[], tuple[object, object]],
    progress: Callable[[int, str, str, TurnResult | None], None] | None = None,
) -> AcceptanceResult:
    """Run the canonical turns through the real Workspace orchestrator path."""
    from industrial_gases.conversational_workspace import ConversationalWorkspaceOrchestrator
    from industrial_gases.portfolio_query import SupplyAgentSessionContext

    context = SupplyAgentSessionContext()
    responses: dict[int, object] = {}
    response_ids: dict[int, str] = {}
    views: dict[str, ExecutionView] = {}
    turns: list[TurnResult] = []
    stopped_early = False

    for number, (label, question) in enumerate(CANONICAL_TURNS, 1):
        if progress:
            progress(number, label, "start", None)
        operation_id = uuid4().hex[:8]
        recorder = PerformanceRecorder(operation_id, provider_name, model_name, "conversational_workspace")
        started = perf_counter()
        response = None
        error = None
        provider = None
        try:
            # The Streamlit app builds the demo portfolio, provider and lazy
            # knowledge service for each submitted turn in this same way.
            portfolio, attention = portfolio_factory()
            provider = provider_factory(recorder)
            decision_model = decision_model_factory(provider, recorder)
            orchestrator = ConversationalWorkspaceOrchestrator(
                portfolio=portfolio,
                attention=attention,
                knowledge=lambda current=recorder: knowledge_factory(current),
                decision_model=decision_model,
                session_context=context,
                recorder=recorder,
            )
            response = orchestrator.run(question, timeout_seconds)
        except Exception as caught:  # The report records only its safe category/type.
            error = caught

        elapsed = perf_counter() - started
        if response is None:
            recorder.finish("timed_out" if isinstance(error, (LLMTimeoutError, TimeoutError)) else "failed")
        else:
            recorder.finish(getattr(response.status, "value", str(response.status)))
            context = response.session_context
            responses[number] = response
            response_ids[number] = operation_id
        snapshot = recorder.snapshot()
        view = ExecutionView.capture(
            "indAI MA", response, {"provider": provider_name, "model": model_name,
                                    "timeout_seconds": timeout_seconds},
            snapshot, getattr(response, "explanation", None) or "",
            getattr(response, "tool_executions", ()),
        )
        views[operation_id] = view
        failure = classify_failure(response, snapshot, error)
        failed_checks = (
            ("turn_execution",) if response is None else
            _check_turn(number, response, snapshot, responses, views)
        )
        if failure is not FailureCategory.PASS:
            category = failure
        elif failed_checks:
            category = FailureCategory.ASSERTION_FAILURE
        else:
            category = FailureCategory.PASS
        metrics = build_operation_metrics(snapshot)
        retrieval_events = [event for event in snapshot.events if event.stage in _RAG_STAGES]
        retrieval_status = (
            "failed" if any(event.status is PerformanceStatus.FAILED for event in retrieval_events)
            else "completed" if retrieval_events else "not_used"
        )
        citation_events = [event for event in snapshot.events if event.stage == "citation_validation"]
        citation_status = (
            "pass" if any(event.metadata.get("valid") is True for event in citation_events)
            else "fail" if citation_events else "not_reached"
        )
        semantic_status = _semantic_guard_status(snapshot)
        item = TurnResult(
            number, label, question, operation_id, snapshot.status, elapsed, category,
            tuple(failed_checks), metrics.llm_call_count, metrics.retrieval_time_total,
            retrieval_status, citation_status, semantic_status, response, snapshot,
        )
        turns.append(item)
        if number == 8 and response is not None:
            responses["q8_operation_id"] = operation_id
            responses["q8_events"] = snapshot.events
        if progress:
            progress(number, label, "finish", item)
        if response is None:
            stopped_early = True
            for later_number, (later_label, later_question) in enumerate(CANONICAL_TURNS[number:], number + 1):
                turns.append(TurnResult(
                    later_number, later_label, later_question, None, "not_run", 0.0,
                    FailureCategory.ASSERTION_FAILURE, ("prior_turn_interrupted_conversation",),
                    0, 0.0, "not_run", "not_reached", "not_recorded",
                ))
            break

    summary_checks: tuple[tuple[str, bool], ...] = ()
    if len(turns) == len(CANONICAL_TURNS) and all(turn.response is not None for turn in turns):
        q8 = turns[7]
        turn_has_check = lambda turn, check: check not in turn.failed_checks
        summary_checks = (
            ("conversation_continuity", all(turn.response is not None for turn in turns)),
            ("scenario_history_preserved", turn_has_check(turns[8], "q9_scenario_history_after_documentary")),
            ("deterministic_comparison", all(
                turn_has_check(turn, f"q{turn.number}_no_llm")
                and turn_has_check(turn, f"q{turn.number}_no_retrieval")
                for turn in (turns[4], turns[5], turns[6], turns[8])
            )),
            ("document_grounding", turn_has_check(q8, "q8_scoped_retrieval_completed")
             and turn_has_check(q8, "q8_source_scoped_to_hospital")),
            ("citation_validation", turn_has_check(q8, "q8_citation_scope_validation_passed")),
            ("semantic_guard", turn_has_check(q8, "q8_semantic_guard_not_applied")),
            ("historical_execution_retention", turn_has_check(turns[8], "q9_q8_historical_view_retained")
             and turn_has_check(turns[8], "q9_q8_snapshot_not_replaced")),
            ("all_turns_have_unique_operation_ids",
             all(turn.operation_id for turn in turns)
             and len({turn.operation_id for turn in turns}) == 9),
            ("all_historical_views_retained", all(turn.operation_id in views for turn in turns)),
            ("q8_isolated_from_q9_recorder", views[response_ids[8]].snapshot.operation_id == response_ids[8]
             and views[response_ids[8]].snapshot.operation_id != response_ids[9]),
        )
        # Q8's exact snapshot is retained as a snapshot value, never reconstructed
        # from the active recorder for a later question.
        q9_failures = list(turns[8].failed_checks)
        if not all(ok for _, ok in summary_checks):
            q9_failures.append("conversation_execution_history")
            turns[8] = TurnResult(
                **{**turns[8].__dict__, "category": FailureCategory.ASSERTION_FAILURE,
                   "failed_checks": tuple(q9_failures)},
            )
    return AcceptanceResult(tuple(turns), views, context, summary_checks, stopped_early)


def render_safe_report(result: AcceptanceResult, *, provider: str, model: str, timeout: int,
                       timestamp: datetime | None = None, baseline: str | None = None) -> str:
    stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "indAI MA Workspace Real-Provider Acceptance",
        f"Timestamp: {stamp}", f"Provider: {provider}", f"Model: {model}",
        f"Effective timeout: {timeout} s",
    ]
    if baseline:
        lines.append(f"Release baseline: {baseline}")
    lines.extend(("", "Turns:"))
    for turn in result.turns:
        outcome = "PASS" if turn.category is FailureCategory.PASS else "FAIL"
        lines.append(
            f"Q{turn.number} {turn.label}: {outcome}; status={turn.status}; "
            f"duration={turn.duration_seconds:.2f}s; operation={turn.operation_id or 'not-created'}; "
            f"category={turn.category.value}; llm_calls={turn.llm_calls}; "
            f"retrieval={turn.retrieval_status} ({turn.retrieval_seconds:.3f}s); "
            f"citation={turn.citation_status}; semantic_guard={turn.semantic_guard_status}"
        )
        lines.append(f"  Question: {turn.question}")
        if turn.snapshot is not None:
            eligibility_event = next((
                event for event in turn.snapshot.events
                if event.stage == "deterministic_documentary_eligibility"
            ), None)
            response_event = next((
                event for event in turn.snapshot.events
                if event.stage == "deterministic_documentary_response"
            ), None)
            if eligibility_event is not None or response_event is not None:
                eligible = (eligibility_event.metadata.get("eligibility_decision", "unavailable")
                            if eligibility_event is not None else "unavailable")
                reason = (eligibility_event.metadata.get("eligibility_reason", "unavailable")
                          if eligibility_event is not None else "unavailable")
                chunk_ids = (response_event.metadata.get("selected_supporting_chunk_ids", ())
                             if response_event is not None else ())
                chunk_summary = ",".join(
                    item if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", item)
                    else "[redacted-id]"
                    for item in chunk_ids[:8]
                ) or "none"
                used = (response_event.metadata.get("deterministic_documentary_response_used", False)
                        if response_event is not None else False)
                avoided = (response_event.metadata.get("llm_calls_avoided", False)
                           if response_event is not None else False)
                lines.append(
                    f"  Deterministic documentary: eligible={eligible}; reason={reason}; "
                    f"supporting_chunks={chunk_summary}; deterministic_response_used={used}; "
                    f"llm_calls_avoided={avoided}"
                )
            prompt_event = next((
                event for event in reversed(turn.snapshot.events)
                if event.stage == "prompt_build"
                and isinstance(event.metadata.get("prompt_component_character_counts"), dict)
            ), None)
            if prompt_event is not None:
                counts = prompt_event.metadata["prompt_component_character_counts"]
                percentages = prompt_event.metadata.get("prompt_component_percentages", {})
                total_chars = prompt_event.metadata.get("prompt_character_count", 0)
                delta = prompt_event.metadata.get("prompt_composition_accounting_delta", "unavailable")
                lines.append(f"  Prompt composition: {total_chars} chars; accounting delta={delta}")
                for component, characters in counts.items():
                    percent = percentages.get(component)
                    percent_text = f"{percent:.2f}%" if isinstance(percent, (int, float)) else "unavailable"
                    lines.append(f"    {component}: {characters} chars ({percent_text})")
        if turn.failed_checks:
            lines.append(f"  Failed checks: {', '.join(turn.failed_checks)}")
    lines.append("")
    for name, passed in result.summary_checks:
        lines.append(f"{name.replace('_', ' ').title()}: {'PASS' if passed else 'FAIL'}")
    lines.append(f"RESULT: {'PASS' if result.passed else 'FAIL'}")
    return "\n".join(lines) + "\n"


def write_safe_report(path: str | Path, report: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    return target


def _git_value(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v1.15 Workspace real-provider acceptance conversation.")
    parser.add_argument("--report", help="Safe text report path (defaults to artifacts/workspace_acceptance_<UTC timestamp>.txt).")
    parser.add_argument("--verbose", action="store_true", help="Print structured assertion codes for each turn.")
    args = parser.parse_args(argv)

    from embeddings import LMStudioEmbeddingProvider, get_embedding_model_name
    from industrial_gases.industrial_knowledge import demo_knowledge_service
    from industrial_gases.portfolio_ui import evaluate_demo_supply_portfolio
    from industrial_gases.supply_agent import ProviderSupplyDecisionModel
    from llm_client import get_available_models, get_default_model_name, get_default_provider_name, get_llm_provider
    from runtime_config import LLMRuntimeConfig

    timestamp = datetime.now(timezone.utc)
    runtime = LLMRuntimeConfig.from_environment()
    provider_name = get_default_provider_name()
    model_name = get_default_model_name(provider_name)
    try:
        available = get_available_models(provider_name)
        if not model_name or model_name not in available:
            if not available:
                raise ValueError("no model configured")
            model_name = available[0]
    except Exception:
        print("Provider configuration unavailable; no acceptance turns were run.", file=sys.stderr)
        report = (
            "indAI MA Workspace Real-Provider Acceptance\n"
            f"Timestamp: {timestamp.isoformat(timespec='seconds')}\n"
            f"Provider: {provider_name}\nModel: {model_name or 'unresolved'}\n"
            f"Effective timeout: {runtime.timeout_seconds} s\nRESULT: FAIL\n"
            "Failure category: PROVIDER_FAILURE\n"
        )
        path = Path(args.report) if args.report else Path("artifacts") / f"workspace_acceptance_{timestamp:%Y%m%dT%H%M%SZ}.txt"
        print(f"Report: {write_safe_report(path, report)}")
        return 1

    print("indAI MA Workspace Real-Provider Acceptance\n===========================================")
    print(f"Provider: {provider_name}\nModel: {model_name}\nTimeout: {runtime.timeout_seconds} s")
    head = _git_value("rev-parse", "--short", "HEAD")
    tag = _git_value("rev-parse", "--short", "v1.14.0")
    baseline = f"HEAD {head or 'unknown'}; v1.14.0 {tag or 'unknown'}"
    def progress(number, label, phase, turn):
        if phase == "start":
            print(f"[{number}/9] {label:<28} RUNNING", flush=True)
        else:
            suffix = (
                f"{turn.category.value} {turn.duration_seconds:7.2f} s"
                f" · LLM {turn.llm_calls} · RAG {turn.retrieval_status}"
            )
            print(f"[{number}/9] {label:<28} {suffix}", flush=True)
            if args.verbose and turn.failed_checks:
                print("    checks: " + ", ".join(turn.failed_checks), flush=True)

    result = run_workspace_acceptance(
        provider_name=provider_name,
        model_name=model_name,
        timeout_seconds=runtime.timeout_seconds,
        provider_factory=lambda recorder: get_llm_provider(
            provider_name, model_name, recorder=recorder, runtime_config=runtime,
        ),
        decision_model_factory=lambda provider, recorder: ProviderSupplyDecisionModel(provider),
        knowledge_factory=lambda recorder: demo_knowledge_service(
            LMStudioEmbeddingProvider(model=get_embedding_model_name()), recorder,
        ),
        portfolio_factory=evaluate_demo_supply_portfolio,
        progress=progress,
    )
    report = render_safe_report(
        result, provider=provider_name, model=model_name, timeout=runtime.timeout_seconds,
        timestamp=timestamp, baseline=baseline,
    )
    path = Path(args.report) if args.report else Path("artifacts") / f"workspace_acceptance_{timestamp:%Y%m%dT%H%M%SZ}.txt"
    saved = write_safe_report(path, report)
    print(f"Report: {saved}")
    print(f"RESULT: {'PASS' if result.passed else 'FAIL'}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
