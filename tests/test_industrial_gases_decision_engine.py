"""Decision acceptance over real retained v1.18 fixtures and production evaluation."""
from dataclasses import FrozenInstanceError, replace
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import patch

import pytest

from industrial_gases.decision_engine import ENGINE_VERSION, evaluate_supply_decision
from industrial_gases.decision_engine_models import (
    CandidateEligibility as CE, DecisionScopeMode as SM, DecisionValidationReason as VR,
    SupplyDecisionValidationError,
)
from industrial_gases.factual_comparison import compare_supply_assurance_scenario_set
from industrial_gases.objective_constraint_models import (
    CriterionOutcome as O, HardConstraintOutcome as H, PredicateOperator as P, SupplyMetric as M,
)
from tests.test_industrial_gases_decision_engine_models import policy
from tests.test_industrial_gases_objective_constraint_evaluation import (
    constraint, evaluate, objective, retained,
)
from tests.test_industrial_gases_supply_scenarios import alternative, canonical_request


def gap(target=0, op=P.GE, id="gap"):
    return constraint(M.SAFETY_STOCK_GAP, op, Decimal(target), "kg", id)


def capacity():
    return constraint(M.CAPACITY_EXCEEDED, id="capacity")


def run(source=None, constraints=None, objectives=(), mode=SM.EXPLICIT_ALTERNATIVES):
    if source is None:
        source, _ = retained()
    comparison = compare_supply_assurance_scenario_set(source.result)
    evaluation = evaluate(source, comparison, objectives,
                          (gap(), capacity()) if constraints is None else constraints)
    return evaluate_supply_decision(evaluation, policy(mode), source)


def without_provenance(source, scenario_id, field):
    branches = []
    for branch in source.result.scenario_results:
        if branch.alternative.id == scenario_id:
            result = branch.alternative_result
            projection = replace(result.projection, provenance=tuple(
                item for item in result.projection.provenance if item.field != field))
            branch = replace(branch, alternative_result=replace(result, projection=projection))
        branches.append(branch)
    return replace(source, result=replace(source.result, scenario_results=tuple(branches)))


def test_hospital_unique_b_full_snapshot():
    result = run()
    assert result.selected_scenario_id == "B"
    assert result.status.value == "DECIDED"
    assert result.reasons[0].value == "UNIQUE_ELIGIBLE_CANDIDATE"
    assert result.candidate_ids == ("A", "B")
    assert tuple(row.source.scenario_id for row in result.criteria_evaluation.scenario_evaluations) == (
        "baseline", "A", "B")
    assert tuple(item.eligibility for item in result.candidate_assessments) == (CE.INELIGIBLE, CE.ELIGIBLE)


@pytest.mark.parametrize("objectives", [(), (objective(),)])
def test_capacity_and_stockout_do_not_rank(objectives):
    result = run(constraints=(constraint(), capacity()), objectives=objectives)
    assert result.reasons[0].value == "MULTIPLE_ELIGIBLE_CANDIDATES"
    assert result.selected_scenario_id is None


@pytest.mark.parametrize("constraints", [(gap(10000),), (gap(), gap(0, P.LT, "negative"))])
def test_none_or_contradictory(constraints):
    assert run(constraints=constraints).reasons[0].value == "NO_ELIGIBLE_CANDIDATE"


@pytest.mark.parametrize("objectives", [(), (objective(),)])
def test_no_hard_constraints(objectives):
    result = run(constraints=(), objectives=objectives)
    assert result.reasons[0].value == "NO_HARD_CONSTRAINTS"
    assert all(item.eligibility is None and item.hard_constraint_outcome is H.NOT_APPLICABLE
               for item in result.candidate_assessments)


def test_one_eligible_one_unknown():
    source, _ = retained()
    source = without_provenance(source, "A", M.CAPACITY_EXCEEDED.value)
    result = run(source, (capacity(),))
    assert result.status.value == "INSUFFICIENT_EVIDENCE"
    assert result.selected_scenario_id is None
    assert result.candidate_assessments[0].unknown_references == ("capacity",)


def test_violation_with_unknown_is_definitively_ineligible():
    source, _ = retained()
    result = run(without_provenance(source, "A", M.CAPACITY_EXCEEDED.value))
    assert result.selected_scenario_id == "B"
    a = result.candidate_assessments[0]
    assert a.eligibility is CE.INELIGIBLE
    assert a.violation_references == ("gap",)
    assert a.unknown_references == ("capacity",)
    assert len(a.reasons) == 2


def test_objective_unknown_does_not_affect_selection():
    source, _ = retained()
    source = without_provenance(source, "B", M.STOCKOUT_BEFORE_DELIVERY.value)
    result = run(source, objectives=(objective(M.STOCKOUT_BEFORE_DELIVERY, P.EQ, False, None),))
    assert result.selected_scenario_id == "B"
    assert result.criteria_evaluation.scenario_evaluations[-1].evaluations[0].outcome is O.UNKNOWN


@pytest.mark.parametrize("mode,ids", [(SM.EXPLICIT_ALTERNATIVES, ("A", "B")),
                                    (SM.ALL_SCENARIOS, ("baseline", "A", "B"))])
def test_scope_and_baseline(mode, ids):
    result = run(constraints=(gap(-1000, P.LT),), mode=mode)
    assert result.candidate_ids == ids
    assert result.selected_scenario_id == ("baseline" if mode is SM.ALL_SCENARIOS else None)


def test_identical_alternatives_no_order_tiebreak():
    request = canonical_request()
    source, _ = retained(request, tuple(alternative(request, id, id) for id in ("Z", "A")))
    result = run(source, (capacity(),))
    assert result.candidate_ids == ("Z", "A")
    assert result.reasons[0].value == "MULTIPLE_ELIGIBLE_CANDIDATES"


def evidence():
    source, comparison = retained()
    return source, evaluate(source, comparison, constraints=(gap(), capacity()))


@pytest.mark.parametrize("change", ["reason", "outcome", "observed_value"])
def test_forged_cell_rejected(change):
    source, evaluation = evidence()
    row = evaluation.scenario_evaluations[1]
    cell = row.evaluations[0]
    value = {"reason": "forged", "outcome": O.SATISFIED, "observed_value": Decimal(5)}[change]
    row = replace(row, evaluations=(replace(cell, **{change: value}), row.evaluations[1]))
    evaluation = replace(evaluation, scenario_evaluations=(evaluation.scenario_evaluations[0], row,
                                                         evaluation.scenario_evaluations[2]))
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(evaluation, policy(), source)
    assert caught.value.reason is VR.SOURCE_FACT_MISMATCH
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize("change", ["missing_scenario", "reordered_scenario", "missing_criterion"])
def test_incomplete_evidence(change):
    source, evaluation = evidence()
    rows = evaluation.scenario_evaluations
    if change == "missing_scenario":
        rows = rows[1:]
    elif change == "reordered_scenario":
        rows = rows[::-1]
    else:
        rows = (replace(rows[0], evaluations=rows[0].evaluations[:1]),) + rows[1:]
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(replace(evaluation, scenario_evaluations=rows), policy(), source)
    assert caught.value.reason is VR.INCOMPLETE_EVIDENCE


@pytest.mark.parametrize("duplicate", ["scenario", "criterion"])
def test_duplicate_contract_rejected(duplicate):
    _, evaluation = evidence()
    row = evaluation.scenario_evaluations[0]
    with pytest.raises(ValueError, match="unique"):
        if duplicate == "scenario":
            replace(evaluation, scenario_evaluations=(row, row))
        else:
            replace(row, evaluations=(row.evaluations[0],) * 2)


@pytest.mark.parametrize("field,reason", [("set_revision", VR.STALE_SOURCE_REVISION),
    ("set_reference", VR.SOURCE_BINDING_MISMATCH), ("position_identity", VR.SOURCE_BINDING_MISMATCH)])
def test_source_binding_mismatch(field, reason):
    source, evaluation = evidence()
    value = replace(source.binding.position_identity, site_id="other") if field == "position_identity" else "other"
    source = replace(source, binding=replace(source.binding, **{field: value}))
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(evaluation, policy(), source)
    assert caught.value.reason is reason


@pytest.mark.parametrize("field", ["result_reference", "projection_reference", "provenance_references"])
def test_forged_references(field):
    source, evaluation = evidence()
    row = evaluation.scenario_evaluations[1]
    value = ("forged",) if field == "provenance_references" else "forged"
    row_source = replace(row.source, **{field: value})
    row = replace(row, source=row_source, evaluations=tuple(replace(cell, source=row_source)
                                                          for cell in row.evaluations))
    rows = (evaluation.scenario_evaluations[0], row, evaluation.scenario_evaluations[2])
    with pytest.raises(SupplyDecisionValidationError):
        evaluate_supply_decision(replace(evaluation, scenario_evaluations=rows), policy(), source)


def test_unsupported_evaluator_version():
    source, evaluation = evidence()
    rows = tuple(replace(row, evaluator_version="future", evaluations=tuple(
        replace(cell, evaluator_version="future") for cell in row.evaluations))
        for row in evaluation.scenario_evaluations)
    evaluation = replace(evaluation, evaluator_version="future", scenario_evaluations=rows)
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(evaluation, policy(), source)
    assert "unsupported_criteria_evaluator_version" in str(caught.value.__cause__)


def test_partial_display_never_limits_source():
    source, evaluation = evidence()
    display = compare_supply_assurance_scenario_set(source.result, scenario_ids=("A", "B"))
    assert len(display.metrics[0].values) == 2
    result = evaluate_supply_decision(evaluation, policy(SM.ALL_SCENARIOS), source)
    assert result.candidate_ids == ("baseline", "A", "B")
    with pytest.raises(TypeError):
        evaluate_supply_decision(evaluation, policy(), source, comparison=display)


def test_missing_policy_no_implicit_selection():
    source, evaluation = evidence()
    result = evaluate_supply_decision(evaluation, None, source)
    assert result.reasons[0].value == "POLICY_REQUIRED"
    assert result.scope is None and result.selected_scenario_id is None


def test_unsupported_policy_and_preference_rejected():
    source, evaluation = evidence()
    with pytest.raises(TypeError):
        evaluate_supply_decision(evaluation, "rank", source)
    with pytest.raises(TypeError):
        evaluate_supply_decision(evaluation, policy(), source, preference="earliest")


def test_determinism_immutability_and_provenance():
    source, evaluation = evidence()
    result = evaluate_supply_decision(evaluation, policy(), source)
    assert result == evaluate_supply_decision(evaluation, policy(), source)
    assert result.criteria_evaluation is evaluation
    assert result.engine_version == ENGINE_VERSION
    assert result.source_set_binding == source.binding
    assert result.scope.policy is result.policy
    for row in evaluation.scenario_evaluations:
        assert row.source.result_reference in result.provenance_references
        assert row.source.projection_reference in result.provenance_references
        assert set(row.source.provenance_references) <= set(result.provenance_references)
    with pytest.raises(FrozenInstanceError):
        result.selected_scenario_id = "A"


def test_invalid_retained_source_preserves_cause():
    source, evaluation = evidence()
    source = replace(source, result=replace(source.result, scenario_results=source.result.scenario_results[:1]))
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(evaluation, policy(), source)
    assert caught.value.__cause__ is not None


def test_no_external_or_physical_calls_and_canonical_recheck():
    source, evaluation = evidence()
    from industrial_gases.objective_constraint_evaluation import evaluate_supply_criteria
    forbidden_paths = (
        "industrial_gases.service.SupplyAssuranceService.assess",
        "industrial_gases.service.build_supply_projection",
        "industrial_gases.calculations.build_supply_projection",
        "industrial_gases.calculations.calculate_days_of_supply",
        "industrial_gases.calculations.project_inventory",
        "industrial_gases.calculations.calculate_required_delivery_volume",
        "llm_client.LMStudioProvider.generate_response",
        "llm_client.OpenAIProvider.generate_response",
        "embeddings.LMStudioEmbeddingProvider.embed_query",
        "embeddings.LMStudioEmbeddingProvider.embed_documents",
        "rag_service.RAGService.retrieve",
        "socket.socket",
    )
    with ExitStack() as stack:
        forbidden = [stack.enter_context(patch(path, side_effect=AssertionError(path)))
                     for path in forbidden_paths]
        logical = stack.enter_context(patch("industrial_gases.decision_engine.evaluate_supply_criteria",
                                             wraps=evaluate_supply_criteria))
        result = evaluate_supply_decision(evaluation, policy(), source)
    assert result.selected_scenario_id == "B"
    assert all(mock.call_count == 0 for mock in forbidden)
    assert logical.call_count == 1


def test_all_unresolved_and_missing_projection():
    source, _ = retained()
    branches = tuple(replace(branch, alternative_result=replace(branch.alternative_result,
                        projection=None)) for branch in source.result.scenario_results)
    source = replace(source, result=replace(source.result, scenario_results=branches))
    result = run(source)
    assert result.status.value == "INSUFFICIENT_EVIDENCE"
    assert all(item.eligibility is CE.UNRESOLVED for item in result.candidate_assessments)
    assert all(item.scenario_evaluation.source.projection_reference is None
               for item in result.candidate_assessments)


def test_multiple_eligible_takes_precedence_over_unknown():
    request = canonical_request()
    source, _ = retained(request, tuple(alternative(request, id, id) for id in ("A", "B", "C")))
    result = run(without_provenance(source, "C", M.CAPACITY_EXCEEDED.value), (capacity(),))
    assert result.reasons[0].value == "MULTIPLE_ELIGIBLE_CANDIDATES"
    assert result.selected_scenario_id is None


def test_no_eligible_with_unresolved_is_insufficient():
    source, _ = retained()
    source = without_provenance(source, "B", M.SAFETY_STOCK_GAP.value)
    assert run(source).status.value == "INSUFFICIENT_EVIDENCE"


def test_extra_scenario_rejected():
    source, evaluation = evidence()
    row = evaluation.scenario_evaluations[0]
    new_source = replace(row.source, scenario_id="extra")
    extra = replace(row, source=new_source, evaluations=tuple(
        replace(cell, source=new_source) for cell in row.evaluations))
    evaluation = replace(evaluation, scenario_evaluations=evaluation.scenario_evaluations + (extra,))
    with pytest.raises(SupplyDecisionValidationError) as caught:
        evaluate_supply_decision(evaluation, policy(), source)
    assert caught.value.reason is VR.INCOMPLETE_EVIDENCE


def test_missing_policy_does_not_hide_invalid_evidence():
    source, evaluation = evidence()
    with pytest.raises(SupplyDecisionValidationError):
        evaluate_supply_decision(replace(evaluation, scenario_evaluations=()), None, source)
