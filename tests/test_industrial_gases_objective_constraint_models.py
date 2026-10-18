"""Contract validation only: no physical or predicate evaluation."""
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal

import pytest

from industrial_gases.factual_comparison import ScenarioPositionIdentity, _SCENARIO_FIELDS
from industrial_gases.objective_constraint_models import (
    CriterionDefinitionSource, CriterionEvaluation, CriterionKind, CriterionOutcome,
    DefinitionOrigin, FactualPredicate, PredicateOperator, ScenarioCriteriaEvaluation,
    ScenarioFactSource, ScenarioSetBinding, ScenarioSetCriteriaEvaluation,
    SupplyCriteria, SupplyHardConstraint, SupplyMetric, SupplyObjective,
)


IDENTITY = ScenarioPositionIdentity("customer", "site", "application", "gas", "installation")
BINDING = ScenarioSetBinding("explicit-set", "revision-1", IDENTITY)
DEFINITION = CriterionDefinitionSource(DefinitionOrigin.USER, "request-1", "1")
PREDICATE = FactualPredicate(SupplyMetric.SAFETY_STOCK_GAP, PredicateOperator.GE,
                             Decimal("0"), "kg")
OBJECTIVE = SupplyObjective("target", "Maintain safety stock", PREDICATE, DEFINITION)
CONSTRAINT = SupplyHardConstraint(
    "limit", "No stockout",
    FactualPredicate(SupplyMetric.STOCKOUT_BEFORE_DELIVERY, PredicateOperator.EQ, False),
    DEFINITION,
)
SOURCE = ScenarioFactSource(BINDING, "A", "result-A", "projection-A", ("gap-provenance",))


def record(**changes):
    values = dict(criterion_id="target", criterion_kind=CriterionKind.OBJECTIVE,
                  outcome=CriterionOutcome.UNKNOWN, reason="unavailable",
                  predicate=PREDICATE, definition_source=DEFINITION, source=SOURCE,
                  evaluator_version="1")
    values.update(changes)
    return CriterionEvaluation(**values)


def test_metric_whitelist_matches_existing_comparison():
    assert tuple(metric.value for metric in SupplyMetric) == _SCENARIO_FIELDS


@pytest.mark.parametrize("metric", [m for m in SupplyMetric if m not in {
    SupplyMetric.STOCKOUT_BEFORE_DELIVERY, SupplyMetric.CAPACITY_EXCEEDED,
}])
@pytest.mark.parametrize("operator", list(PredicateOperator))
def test_numeric_metrics_and_strict_inclusive_operators(metric, operator):
    predicate = FactualPredicate(metric, operator, Decimal("2.5"), "kg")
    assert predicate.operator is operator
    assert predicate.target == Decimal("2.5")


@pytest.mark.parametrize("metric", [SupplyMetric.STOCKOUT_BEFORE_DELIVERY,
                                    SupplyMetric.CAPACITY_EXCEEDED])
@pytest.mark.parametrize("target", [True, False])
def test_actual_boolean_targets(metric, target):
    assert FactualPredicate(metric, PredicateOperator.EQ, target).target is target


def test_signed_gap_and_separate_criterion_definitions():
    signed = replace(PREDICATE, target=Decimal("-15"))
    assert replace(OBJECTIVE, predicate=signed).predicate.target == Decimal("-15")
    assert CONSTRAINT.predicate.target is False
    assert type(OBJECTIVE) is not type(CONSTRAINT)


@pytest.mark.parametrize("changes", [
    {"metric": "cost"}, {"metric": "safety_stock_gap_before_delivery"},
    {"operator": "ge"}, {"operator": "between"},
    {"target": 1}, {"target": 1.0}, {"target": "1"}, {"target": True},
    {"target": None}, {"unit": None}, {"unit": "%"}, {"unit": "kg/day"},
    {"unit": " KG "},
])
def test_malformed_numeric_predicates_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(PREDICATE, **changes)


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_non_finite_decimal_rejected(value):
    with pytest.raises(ValueError):
        replace(PREDICATE, target=Decimal(value))


@pytest.mark.parametrize("metric", [SupplyMetric.CONSUMPTION_UNTIL_DELIVERY,
    SupplyMetric.INVENTORY_BEFORE_DELIVERY, SupplyMetric.INVENTORY_AFTER_DELIVERY,
    SupplyMetric.REQUIRED_DELIVERY_VOLUME])
def test_negative_quantity_targets_rejected(metric):
    with pytest.raises(ValueError):
        FactualPredicate(metric, PredicateOperator.GE, Decimal("-1"), "kg")


@pytest.mark.parametrize("changes", [{"target": 0}, {"target": Decimal(0)},
    {"operator": PredicateOperator.LT}, {"operator": PredicateOperator.GE}, {"unit": "kg"}])
def test_invalid_boolean_definitions(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(CONSTRAINT.predicate, **changes)


@pytest.mark.parametrize("unit", ["kg", "t", "L", "m3", "Nm3", "Sm3"])
def test_catalog_units_retained_exactly(unit):
    predicate = replace(PREDICATE, unit=unit)
    assert predicate.unit == unit
    assert predicate.target is PREDICATE.target


@pytest.mark.parametrize("changes", [{"origin": None}, {"origin": "user"},
    {"origin": "service_finding"}, {"reference": ""}, {"reference": None},
    {"revision": " "}, {"revision": 1}])
def test_invalid_or_missing_definition_origin_reference_revision(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(DEFINITION, **changes)


@pytest.mark.parametrize("cls", [SupplyObjective, SupplyHardConstraint])
@pytest.mark.parametrize("changes", [{"id": " "}, {"label": ""},
    {"predicate": None}, {"definition_source": None}])
def test_malformed_definitions(cls, changes):
    values = dict(id="id", label="label", predicate=PREDICATE, definition_source=DEFINITION)
    values.update(changes)
    with pytest.raises((TypeError, ValueError)):
        cls(**values)


def test_ordered_collections_copied_and_frozen():
    objectives = [OBJECTIVE, replace(OBJECTIVE, id="second")]
    constraints = [CONSTRAINT]
    criteria = SupplyCriteria(BINDING, DEFINITION, objectives, constraints)
    objectives.clear()
    constraints.clear()
    assert tuple(item.id for item in criteria.objectives) == ("target", "second")
    assert criteria.constraints == (CONSTRAINT,)
    with pytest.raises(FrozenInstanceError):
        criteria.objectives = ()
    with pytest.raises(FrozenInstanceError):
        OBJECTIVE.predicate.target = Decimal(1)


@pytest.mark.parametrize("objectives,constraints", [
    ((OBJECTIVE, OBJECTIVE), ()), ((), (CONSTRAINT, CONSTRAINT)),
    ((OBJECTIVE,), (replace(CONSTRAINT, id="target"),)),
])
def test_duplicate_ids_across_both_kinds(objectives, constraints):
    with pytest.raises(ValueError):
        SupplyCriteria(BINDING, DEFINITION, objectives, constraints)


def test_collection_types_and_set_metadata_required():
    for changes in ({"objectives": {OBJECTIVE}}, {"constraints": (OBJECTIVE,)},
                    {"source_set_binding": None}, {"definition_source": None}):
        with pytest.raises(TypeError):
            replace(SupplyCriteria(BINDING, DEFINITION), **changes)


@pytest.mark.parametrize("changes", [{"set_reference": ""}, {"set_revision": None},
    {"set_revision": " "}, {"position_identity": None},
    {"position_identity": replace(IDENTITY, customer_id=" ")}])
def test_explicit_binding_and_revision_validation(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(BINDING, **changes)


def test_unknown_retains_absence_and_source_identity():
    source = replace(SOURCE, projection_reference=None, provenance_references=())
    evaluation = record(source=source)
    assert evaluation.outcome is CriterionOutcome.UNKNOWN
    assert evaluation.observed_value is None
    assert evaluation.source.source_set_binding.position_identity is IDENTITY
    assert evaluation.source.result_reference == "result-A"
    assert evaluation.predicate is PREDICATE
    assert evaluation.definition_source is DEFINITION


def test_incompatible_observed_unit_only_allowed_for_unknown():
    evaluation = record(observed_value=Decimal(2), observed_unit="t", reason="incompatible_unit")
    assert evaluation.observed_unit == "t"
    for outcome in (CriterionOutcome.SATISFIED, CriterionOutcome.VIOLATED):
        with pytest.raises(ValueError):
            replace(evaluation, outcome=outcome)


@pytest.mark.parametrize("outcome", list(CriterionOutcome))
def test_outcome_records_preserve_snapshot_without_evaluating(outcome):
    evaluation = record(outcome=outcome, observed_value=Decimal("-1"), observed_unit="kg")
    assert evaluation.outcome is outcome
    assert evaluation.source.provenance_references == ("gap-provenance",)
    assert evaluation.evaluator_version == "1"


@pytest.mark.parametrize("changes", [{"outcome": "UNKNOWN"}, {"criterion_kind": "objective"},
    {"reason": ""}, {"evaluator_version": ""}, {"criterion_id": ""},
    {"observed_value": True}, {"observed_value": Decimal("NaN"), "observed_unit": "kg"},
    {"observed_unit": "kg"}, {"outcome": CriterionOutcome.SATISFIED}])
def test_malformed_result_records(changes):
    with pytest.raises((TypeError, ValueError)):
        record(**changes)


def test_observed_boolean_false_is_available():
    evaluation = record(criterion_id="limit", criterion_kind=CriterionKind.HARD_CONSTRAINT,
        predicate=CONSTRAINT.predicate, observed_value=False, outcome=CriterionOutcome.SATISFIED)
    assert evaluation.observed_value is False
    with pytest.raises(ValueError):
        replace(evaluation, observed_value=0)


def test_fact_requires_projection_reference():
    with pytest.raises(ValueError):
        record(source=replace(SOURCE, projection_reference=None),
               observed_value=Decimal(0), observed_unit="kg")


def test_source_provenance_collection_is_immutable_and_validated():
    refs = ["field-reference"]
    source = replace(SOURCE, provenance_references=refs)
    refs.clear()
    assert source.provenance_references == ("field-reference",)
    for changes in ({"scenario_id": ""}, {"result_reference": " "},
                    {"projection_reference": ""}, {"provenance_references": ("",)}):
        with pytest.raises(ValueError):
            replace(source, **changes)


def test_nested_results_preserve_order_and_definition_binding():
    criteria = SupplyCriteria(BINDING, DEFINITION, (OBJECTIVE,))
    a = ScenarioCriteriaEvaluation(SOURCE, "1", [record()])
    b_source = replace(SOURCE, scenario_id="B", result_reference="result-B")
    b = ScenarioCriteriaEvaluation(b_source, "1", [record(source=b_source)])
    results = [b, a]
    result = ScenarioSetCriteriaEvaluation(criteria, "1", results)
    results.clear()
    assert tuple(item.source.scenario_id for item in result.scenario_evaluations) == ("B", "A")
    assert isinstance(a.evaluations, tuple)
    for invalid in (replace(a, source=replace(SOURCE, source_set_binding=replace(BINDING, set_revision="2")),
                            evaluations=()), replace(a, evaluator_version="2", evaluations=())):
        with pytest.raises(ValueError):
            replace(result, scenario_evaluations=(invalid,))
    with pytest.raises(ValueError):
        replace(result, scenario_evaluations=(a, a))


def test_nested_result_rejects_mismatched_snapshots_and_sources():
    criteria = SupplyCriteria(BINDING, DEFINITION, (OBJECTIVE,))
    for changes in ({"criterion_id": "missing"}, {"criterion_kind": CriterionKind.HARD_CONSTRAINT},
                    {"predicate": replace(PREDICATE, target=Decimal(1))},
                    {"definition_source": replace(DEFINITION, revision="2")}):
        scenario = ScenarioCriteriaEvaluation(SOURCE, "1", (record(**changes),))
        with pytest.raises(ValueError):
            ScenarioSetCriteriaEvaluation(criteria, "1", (scenario,))
    with pytest.raises(ValueError):
        ScenarioCriteriaEvaluation(SOURCE, "1", (record(evaluator_version="2"),))
    with pytest.raises(ValueError):
        ScenarioCriteriaEvaluation(SOURCE, "1", (record(), record()))


def test_no_mutable_defaults_or_generated_ids():
    classes = (FactualPredicate, CriterionDefinitionSource, SupplyObjective, SupplyHardConstraint,
               ScenarioSetBinding, SupplyCriteria, ScenarioFactSource, CriterionEvaluation,
               ScenarioCriteriaEvaluation, ScenarioSetCriteriaEvaluation)
    for cls in classes:
        for field in fields(cls):
            assert not isinstance(field.default, (list, dict, set))
    assert SupplyCriteria(BINDING, DEFINITION) == SupplyCriteria(BINDING, DEFINITION)
