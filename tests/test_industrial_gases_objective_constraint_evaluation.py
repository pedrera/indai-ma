"""Acceptance tests over production projections; no duplicated physical formulas."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from industrial_gases.factual_comparison import compare_supply_assurance_scenario_set
from industrial_gases.models import ConsumptionRate, Quantity
from industrial_gases.objective_constraint_evaluation import (
    EVALUATOR_VERSION, RetainedScenarioSetSource, SourceBindingError, evaluate_supply_criteria,
)
from industrial_gases.objective_constraint_models import (
    CriterionDefinitionSource, CriterionKind, CriterionOutcome as O, DefinitionOrigin,
    FactualPredicate, HardConstraintOutcome as H, PredicateOperator as P,
    ScenarioSetBinding, SupplyCriteria, SupplyHardConstraint, SupplyMetric as M, SupplyObjective,
)
from industrial_gases.service import SupplyAssuranceService
from industrial_gases.supply_scenarios import (
    SupplyAssuranceScenarioSet, evaluate_supply_assurance_scenario_set,
)
from tests.test_industrial_gases_supply_scenarios import canonical_request, alternative


DEFINITION = CriterionDefinitionSource(DefinitionOrigin.USER, "explicit-request", "criteria-1")


def objective(metric=M.SAFETY_STOCK_GAP, op=P.GE, target=Decimal(0), unit="kg", id="goal"):
    return SupplyObjective(id, id, FactualPredicate(metric, op, target, unit), DEFINITION)


def constraint(metric=M.STOCKOUT_BEFORE_DELIVERY, op=P.EQ, target=False, unit=None, id="limit"):
    return SupplyHardConstraint(id, id, FactualPredicate(metric, op, target, unit), DEFINITION)


def retained(request=None, alternatives=None):
    request = request or canonical_request()
    if alternatives is None:
        alternatives = tuple(alternative(request, id, id, delivery_plan=replace(
            request.delivery_plan, planned_delivery_at=request.reference_time + timedelta(days=days)))
            for id, days in (("A", 3), ("B", 2)))
    scenario_set = SupplyAssuranceScenarioSet(request, alternatives)
    result = evaluate_supply_assurance_scenario_set(scenario_set, SupplyAssuranceService())
    comparison = compare_supply_assurance_scenario_set(result)
    binding = ScenarioSetBinding("hospital-set", "revision-1", comparison.position_identity)
    return RetainedScenarioSetSource(binding, scenario_set, result), comparison


def evaluate(source, comparison, objectives=(), constraints=()):
    return evaluate_supply_criteria(SupplyCriteria(source.binding, DEFINITION, objectives, constraints),
                                    comparison, source)


def outcomes(result, index=0):
    return tuple(item.evaluations[index].outcome for item in result.scenario_evaluations)


def test_hospital_horizon_gap_and_independent_constraints():
    source, comparison = retained()
    result = evaluate(source, comparison, (objective(),),
                      (constraint(), constraint(M.CAPACITY_EXCEEDED, id="capacity")))
    assert outcomes(result) == (O.VIOLATED, O.VIOLATED, O.SATISFIED)
    assert outcomes(result, 1) == outcomes(result, 2) == (O.SATISFIED,) * 3
    assert tuple(item.hard_constraint_outcome for item in result.scenario_evaluations) == (H.SATISFIED,) * 3
    assert tuple(item.source.scenario_id for item in result.scenario_evaluations) == ("baseline", "A", "B")


def test_consumption_rate_stockout():
    request = canonical_request()
    alternatives = tuple(alternative(request, id, id, consumption_forecast=replace(
        request.consumption_forecast, rate=ConsumptionRate(rate, "kg")))
        for id, rate in (("A", 800), ("B", 900)))
    source, comparison = retained(request, alternatives)
    assert outcomes(evaluate(source, comparison, constraints=(constraint(),))) == (
        O.SATISFIED, O.SATISFIED, O.VIOLATED)


@pytest.mark.parametrize("operator,expected", [(P.LT, O.VIOLATED), (P.LE, O.SATISFIED),
    (P.EQ, O.SATISFIED), (P.GE, O.SATISFIED), (P.GT, O.VIOLATED)])
def test_exact_numeric_boundaries(operator, expected):
    source, comparison = retained()
    criterion = objective(M.INVENTORY_BEFORE_DELIVERY, operator, Decimal(400))
    assert outcomes(evaluate(source, comparison, (criterion,)))[0] is expected


def test_exact_capacity_equality():
    request = canonical_request()
    a = alternative(request, "A", "A", delivery_plan=replace(
        request.delivery_plan, planned_quantity=Quantity(9600, "kg")))
    source, comparison = retained(request, (a,))
    assert source.result.scenario_results[0].alternative_result.projection.inventory_immediately_after_delivery == request.installation.capacity
    assert outcomes(evaluate(source, comparison, constraints=(constraint(M.CAPACITY_EXCEEDED),))) == (O.SATISFIED,) * 2


def test_zero_safety_with_stockout_keeps_facts_independent():
    request = replace(canonical_request(), safety_stock=Quantity(0, "kg"))
    a = alternative(request, "A", "A", consumption_forecast=replace(
        request.consumption_forecast, rate=ConsumptionRate(900, "kg")))
    source, comparison = retained(request, (a,))
    result = evaluate(source, comparison, (objective(),), (constraint(),))
    assert outcomes(result) == (O.SATISFIED,) * 2
    assert outcomes(result, 1) == (O.SATISFIED, O.VIOLATED)


def test_missing_invalid_branches_between_valid_branches():
    request = canonical_request()
    alts = (alternative(request, "A", "A"), alternative(request, "missing", "missing", delivery_plan=None),
            alternative(request, "invalid", "invalid", reference_time=request.reference_time + timedelta(days=1)),
            alternative(request, "B", "B"))
    source, comparison = retained(request, alts)
    result = evaluate(source, comparison, constraints=(constraint(),))
    assert outcomes(result) == (O.SATISFIED, O.SATISFIED, O.UNKNOWN, O.UNKNOWN, O.SATISFIED)
    assert tuple(item.evaluations[0].reason for item in result.scenario_evaluations)[2:4] == (
        "source_missing_inputs", "source_invalid")
    assert all(not metric.comparable for metric in comparison.metrics)


@pytest.mark.parametrize("source_unit,target_unit", [("kg", "t"), ("Nm3", "Sm3")])
def test_exact_units_without_conversion(source_unit, target_unit):
    request = canonical_request()
    request = replace(request, installation=replace(request.installation, capacity=Quantity(10000, source_unit)),
        inventory_snapshot=replace(request.inventory_snapshot, inventory=Quantity(3200, source_unit)),
        consumption_forecast=replace(request.consumption_forecast, rate=ConsumptionRate(700, source_unit)),
        delivery_plan=replace(request.delivery_plan, planned_quantity=Quantity(4000, source_unit)),
        safety_stock=Quantity(1500, source_unit))
    source, comparison = retained(request)
    result = evaluate(source, comparison, (objective(unit=target_unit),))
    assert outcomes(result) == (O.UNKNOWN,) * 3
    assert result.scenario_evaluations[0].evaluations[0].observed_unit == source_unit
    assert result.scenario_evaluations[0].evaluations[0].reason == "incompatible_operational_units"


def test_missing_metric_and_completed_without_projection():
    source, comparison = retained()
    subset = replace(comparison, metrics=tuple(m for m in comparison.metrics if m.field != M.SAFETY_STOCK_GAP.value))
    result = evaluate(source, subset, (objective(),))
    assert outcomes(result) == (O.UNKNOWN,) * 3
    assert result.scenario_evaluations[0].evaluations[0].reason == "metric_unavailable"
    branch = source.result.scenario_results[0]
    altered = replace(source.result, scenario_results=(replace(branch,
        alternative_result=replace(branch.alternative_result, projection=None)), source.result.scenario_results[1]))
    source = replace(source, result=altered)
    result = evaluate(source, compare_supply_assurance_scenario_set(altered), (objective(),))
    assert outcomes(result) == (O.VIOLATED, O.UNKNOWN, O.SATISFIED)
    assert result.scenario_evaluations[1].evaluations[0].reason == "projection_unavailable"


@pytest.mark.parametrize("change", ["identity", "revision", "order", "projection", "baseline_reference"])
def test_invalid_global_bindings_fail_without_result(change):
    source, comparison = retained()
    criteria = SupplyCriteria(source.binding, DEFINITION, (objective(),))
    if change == "identity":
        comparison = replace(comparison, position_identity=replace(comparison.position_identity, site_id="other"))
    elif change == "revision":
        source = replace(source, binding=replace(source.binding, set_revision="revision-2"))
    elif change == "order":
        source = replace(source, result=replace(source.result, scenario_results=source.result.scenario_results[::-1]))
    else:
        branch = source.result.scenario_results[0]
        if change == "projection":
            projection = replace(branch.alternative_result.projection, installation_id="other")
            branch = replace(branch, alternative_result=replace(branch.alternative_result, projection=projection))
        else:
            branch = replace(branch, baseline_result=replace(branch.baseline_result))
        source = replace(source, result=replace(source.result, scenario_results=(branch, source.result.scenario_results[1])))
    with pytest.raises(SourceBindingError):
        evaluate_supply_criteria(criteria, comparison, source)


@pytest.mark.parametrize("change", ["value", "unit", "type", "reason", "order", "duplicate_metric"])
def test_tampered_comparison_rejected(change):
    source, comparison = retained()
    metric = comparison.metrics[0]
    value = metric.values[0]
    changes = {"value": dict(value=Decimal(123)), "unit": dict(unit="t"),
               "type": dict(value=True), "reason": dict(unavailable_reason="fake")}
    if change in changes:
        metric = replace(metric, values=(replace(value, **changes[change]),) + metric.values[1:])
    elif change == "order":
        metric = replace(metric, values=metric.values[::-1])
    comparison = replace(comparison, metrics=(metric,) + comparison.metrics[1:])
    if change == "duplicate_metric":
        comparison = replace(comparison, metrics=comparison.metrics + (metric,))
    with pytest.raises(SourceBindingError):
        evaluate(source, comparison, (objective(),))


def test_partial_ab_comparison_explicitly_rejected():
    source, _ = retained()
    partial = compare_supply_assurance_scenario_set(source.result, scenario_ids=("A", "B"))
    with pytest.raises(SourceBindingError, match="incomplete_or_reordered"):
        evaluate(source, partial, (objective(),))


def test_no_constraints_and_contradictory_constraints():
    source, comparison = retained()
    assert all(item.hard_constraint_outcome is H.NOT_APPLICABLE
               for item in evaluate(source, comparison, (objective(),)).scenario_evaluations)
    result = evaluate(source, comparison, constraints=(constraint(id="no"), constraint(target=True, id="yes")))
    assert outcomes(result) == (O.SATISFIED,) * 3
    assert outcomes(result, 1) == (O.VIOLATED,) * 3
    assert all(item.hard_constraint_outcome is H.VIOLATED for item in result.scenario_evaluations)


def test_violated_precedes_unknown_and_retains_both():
    source, comparison = retained()
    comparison = replace(comparison, metrics=tuple(m for m in comparison.metrics if m.field != M.CAPACITY_EXCEEDED.value))
    result = evaluate(source, comparison, constraints=(constraint(target=True), constraint(M.CAPACITY_EXCEEDED, id="capacity")))
    assert outcomes(result) == (O.VIOLATED,) * 3
    assert outcomes(result, 1) == (O.UNKNOWN,) * 3
    assert all(item.hard_constraint_outcome is H.VIOLATED for item in result.scenario_evaluations)


def test_unknown_aggregate():
    source, comparison = retained()
    result = evaluate(source, replace(comparison, metrics=()), constraints=(constraint(),))
    assert all(item.hard_constraint_outcome is H.UNKNOWN for item in result.scenario_evaluations)


def test_repeatable_immutable_complete_provenance_and_labels_not_identity():
    source, comparison = retained()
    original_result, original_projection = source.result, source.result.baseline_result.projection
    original_provenance = original_projection.provenance
    result = evaluate(source, comparison, (objective(),))
    assert result == evaluate(source, comparison, (objective(),))
    renamed = replace(comparison, metrics=tuple(replace(m, label="irrelevant", values=tuple(
        replace(v, label="same label") for v in m.values)) for m in comparison.metrics))
    assert result == evaluate(source, renamed, (objective(),))
    assert source.result is original_result
    assert source.result.baseline_result.projection is original_projection
    assert original_projection.provenance is original_provenance
    cell = result.scenario_evaluations[0].evaluations[0]
    assert cell.criterion_kind is CriterionKind.OBJECTIVE
    assert cell.predicate == objective().predicate and cell.definition_source is DEFINITION
    assert cell.source.source_set_binding is source.binding
    assert cell.source.scenario_id == "baseline"
    assert cell.source.result_reference == "hospital-set/revision-1/baseline/result"
    assert cell.source.projection_reference == "hospital-set/revision-1/baseline/projection"
    assert len(cell.source.provenance_references) == len(original_provenance)
    assert cell.evaluator_version == EVALUATOR_VERSION
    assert cell.observed_value == Decimal(-1100) and cell.observed_unit == "kg"


@pytest.mark.parametrize("change,reason", [("missing", "provenance_unavailable"),
                                         ("altered", "provenance_fact_mismatch")])
def test_missing_or_altered_provenance_is_unknown(change, reason):
    source, _ = retained()
    projection = source.result.baseline_result.projection
    provenance = tuple(p for p in projection.provenance if p.field != M.SAFETY_STOCK_GAP.value)
    if change == "altered":
        gap = next(p for p in projection.provenance if p.field == M.SAFETY_STOCK_GAP.value)
        provenance += (replace(gap, value=Decimal(999)),)
    baseline = replace(source.result.baseline_result, projection=replace(projection, provenance=provenance))
    result = replace(source.result, baseline_result=baseline, scenario_results=tuple(
        replace(branch, baseline_result=baseline) for branch in source.result.scenario_results))
    source = replace(source, result=result)
    evaluated = evaluate(source, compare_supply_assurance_scenario_set(result), (objective(),))
    assert outcomes(evaluated)[0] is O.UNKNOWN
    assert evaluated.scenario_evaluations[0].evaluations[0].reason == reason


@pytest.mark.parametrize("field,value,reason", [
    ("stockout_before_delivery", 0, "invalid_fact_type"),
    ("stockout_before_delivery", None, "fact_unavailable"),
    ("safety_stock_gap_before_delivery", None, "fact_unavailable"),
])
def test_unavailable_or_wrong_type_retained_fact_is_unknown(field, value, reason):
    source, _ = retained()
    branch = source.result.scenario_results[0]
    projection = replace(branch.alternative_result.projection, **{field: value})
    result = replace(source.result, scenario_results=(replace(branch,
        alternative_result=replace(branch.alternative_result, projection=projection)),
        source.result.scenario_results[1]))
    source = replace(source, result=result)
    kwargs = dict(constraints=(constraint(),)) if field == "stockout_before_delivery" else dict(objectives=(objective(),))
    evaluated = evaluate(source, compare_supply_assurance_scenario_set(result), **kwargs)
    assert evaluated.scenario_evaluations[1].evaluations[0].outcome is O.UNKNOWN
    assert evaluated.scenario_evaluations[1].evaluations[0].reason == reason


@pytest.mark.parametrize("where", ["baseline", "alternative", "request", "focused_identity"])
def test_all_source_position_identities_are_verified(where):
    source, comparison = retained()
    if where == "baseline":
        source = replace(source, result=replace(source.result,
            baseline_result=replace(source.result.baseline_result, site_id="other")))
    elif where == "alternative":
        branch = source.result.scenario_results[0]
        branch = replace(branch, alternative_result=replace(branch.alternative_result, site_id="other"))
        source = replace(source, result=replace(source.result,
            scenario_results=(branch, source.result.scenario_results[1])))
    elif where == "request":
        request = source.scenario_set.baseline_request
        new_set = replace(source.scenario_set, baseline_request=replace(request, customer_id="other"),
            alternatives=tuple(replace(alt, alternative_request=replace(alt.alternative_request, customer_id="other"))
                               for alt in source.scenario_set.alternatives))
        source = replace(source, scenario_set=new_set)
    else:
        source = replace(source, binding=replace(source.binding,
            position_identity=replace(source.binding.position_identity, site_id=None)))
    with pytest.raises(SourceBindingError):
        evaluate(source, comparison, (objective(),))


def test_provenance_references_address_original_records_verbatim():
    source, comparison = retained()
    result = evaluate(source, comparison, (objective(),))
    branches = (source.result.baseline_result,) + tuple(
        branch.alternative_result for branch in source.result.scenario_results)
    for scenario, branch in zip(result.scenario_evaluations, branches):
        for reference, original in zip(scenario.source.provenance_references, branch.projection.provenance):
            index = int(reference.rsplit("/", 1)[1])
            assert branch.projection.provenance[index] is original
        assert scenario.evaluations[0].source is scenario.source


def test_no_service_calculator_provider_embedding_or_rag_calls():
    source, comparison = retained()
    forbidden = AssertionError("evaluation must only read retained facts")
    with patch("industrial_gases.service.SupplyAssuranceService.assess", side_effect=forbidden), \
         patch("industrial_gases.service.build_supply_projection", side_effect=forbidden), \
         patch("industrial_gases.calculations.build_supply_projection", side_effect=forbidden), \
         patch("industrial_gases.calculations.calculate_days_of_supply", side_effect=forbidden), \
         patch("industrial_gases.calculations.project_inventory", side_effect=forbidden), \
         patch("industrial_gases.calculations.calculate_required_delivery_volume", side_effect=forbidden), \
         patch("llm_client.LMStudioProvider.generate_response", side_effect=forbidden), \
         patch("llm_client.OpenAIProvider.generate_response", side_effect=forbidden), \
         patch("embeddings.LMStudioEmbeddingProvider.embed_query", side_effect=forbidden), \
         patch("embeddings.LMStudioEmbeddingProvider.embed_documents", side_effect=forbidden), \
         patch("rag_service.RAGService.retrieve", side_effect=forbidden):
        assert outcomes(evaluate(source, comparison, (objective(),))) == (O.VIOLATED, O.VIOLATED, O.SATISFIED)
