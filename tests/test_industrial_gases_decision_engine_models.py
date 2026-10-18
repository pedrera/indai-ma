"""Decision record validation over real v1.18 fixtures; no decision engine."""
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal
import json

import pytest

from industrial_gases.decision_engine_models import (
    CandidateDecisionAssessment, CandidateDecisionReason as CR, CandidateEligibility as CE,
    DecisionScopeMode as SM, DecisionSelectionRule as Rule, DecisionValidationReason as VR,
    SupplyDecisionEvaluation, SupplyDecisionPolicy, SupplyDecisionReason as R,
    SupplyDecisionScope, SupplyDecisionStatus as S, SupplyDecisionValidationError,
)
from industrial_gases.objective_constraint_models import (
    CriterionDefinitionSource, DefinitionOrigin, HardConstraintOutcome as H,
    SupplyMetric, PredicateOperator,
)
from industrial_gases.supply_agent import _jsonable
from tests.test_industrial_gases_objective_constraint_evaluation import (
    constraint, evaluate, objective, retained,
)


POLICY_SOURCE = CriterionDefinitionSource(DefinitionOrigin.USER, "decision-request", "policy-definition-1")


def policy(mode=SM.EXPLICIT_ALTERNATIVES):
    return SupplyDecisionPolicy("unique-compliance", "1", Rule.UNIQUE_HARD_CONSTRAINT_COMPLIANT,
                                POLICY_SOURCE, mode)


@pytest.fixture(scope="module")
def evaluated():
    source, comparison = retained()
    return evaluate(source, comparison, constraints=(
        constraint(SupplyMetric.SAFETY_STOCK_GAP, PredicateOperator.GE, Decimal(0), "kg", id="gap"),
        constraint(SupplyMetric.CAPACITY_EXCEEDED, id="capacity"),
    ))


def scope(evaluation, selected_policy=None):
    selected_policy = selected_policy or policy()
    sources = tuple(row.source for row in evaluation.scenario_evaluations)
    ids = tuple(source.scenario_id for source in sources)
    return SupplyDecisionScope(selected_policy, selected_policy.scope_mode,
        evaluation.criteria.source_set_binding, "scenario-set-result", sources,
        ids if selected_policy.scope_mode is SM.ALL_SCENARIOS else ids[1:])


def assessment(row, eligibility, *, violations=(), unknowns=(), reasons):
    return CandidateDecisionAssessment(row, eligibility, ("gap", "capacity"), violations, unknowns, reasons)


def result(evaluation, *, selected_policy=None, selected_scope=None, **changes):
    selected_policy = selected_policy or policy()
    selected_scope = selected_scope or scope(evaluation, selected_policy)
    rows = {row.source.scenario_id: row for row in evaluation.scenario_evaluations}
    # Explicit fixture records, not an algorithm choosing a candidate.
    a = assessment(rows["A"], CE.INELIGIBLE, violations=("gap",), reasons=(CR.HARD_CONSTRAINT_VIOLATED,))
    b = assessment(rows["B"], CE.ELIGIBLE, reasons=(CR.ALL_HARD_CONSTRAINTS_SATISFIED,))
    assessments = (a, b)
    if selected_scope.mode is SM.ALL_SCENARIOS:
        assessments = (assessment(rows["baseline"], CE.INELIGIBLE, violations=("gap",),
                                  reasons=(CR.HARD_CONSTRAINT_VIOLATED,)),) + assessments
    values = dict(status=S.DECIDED, reasons=(R.UNIQUE_ELIGIBLE_CANDIDATE,),
                  criteria_evaluation=evaluation, criteria_evaluation_reference="criteria-evaluation-1",
                  engine_version="decision-engine-1", provenance_references=("criteria-evaluation-1",),
                  policy=selected_policy, scope=selected_scope,
                  candidate_assessments=assessments, selected_scenario_id="B")
    values.update(changes)
    return SupplyDecisionEvaluation(**values)


def test_valid_policy_default_scope_and_explicit_definition_origin():
    item = policy()
    assert item.scope_mode is SM.EXPLICIT_ALTERNATIVES
    assert item.selection_rule is Rule.UNIQUE_HARD_CONSTRAINT_COMPLIANT
    assert item.definition_source is POLICY_SOURCE
    assert replace(item, definition_source=replace(POLICY_SOURCE,
        origin=DefinitionOrigin.BUSINESS_CONFIGURATION)).definition_source.origin is DefinitionOrigin.BUSINESS_CONFIGURATION


@pytest.mark.parametrize("changes", [
    {"selection_rule": "UNIQUE_HARD_CONSTRAINT_COMPLIANT"}, {"selection_rule": "weighted"},
    {"selection_rule": None}, {"scope_mode": "ALL_SCENARIOS"}, {"scope_mode": "subset"},
    {"policy_id": ""}, {"policy_version": " "}, {"policy_version": 1},
    {"definition_source": None},
])
def test_invalid_policy_metadata_and_unknown_rules(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(policy(), **changes)


@pytest.mark.parametrize("changes", [{"reference": ""}, {"revision": " "}, {"origin": None}])
def test_empty_policy_definition_reference_revision_or_origin(changes):
    with pytest.raises((ValueError, TypeError)):
        policy_source = replace(POLICY_SOURCE, **changes)
        replace(policy(), definition_source=policy_source)


def test_policy_rejects_additional_unrepresentable_requirements():
    with pytest.raises(TypeError):
        SupplyDecisionPolicy("p", "1", Rule.UNIQUE_HARD_CONSTRAINT_COMPLIANT, POLICY_SOURCE, weights={})
    with pytest.raises(TypeError):
        SupplyDecisionPolicy("p", "1", Rule.UNIQUE_HARD_CONSTRAINT_COMPLIANT, POLICY_SOURCE, preference="earliest")


@pytest.mark.parametrize("mode,ids", [(SM.EXPLICIT_ALTERNATIVES, ("A", "B")),
                                    (SM.ALL_SCENARIOS, ("baseline", "A", "B"))])
def test_scope_resolves_exact_candidates_without_reordering(evaluated, mode, ids):
    item = scope(evaluated, policy(mode))
    assert item.candidate_ids == ids
    assert item.source_set_binding is evaluated.criteria.source_set_binding
    assert item.source_reference == "scenario-set-result"
    assert item.scenario_sources[0].scenario_id == "baseline"


@pytest.mark.parametrize("ids", [("baseline", "A", "B"), ("B", "A"), ("A",), ("A", "A"), (), ("A", "missing")])
def test_explicit_scope_rejects_baseline_subsets_duplicates_and_reordering(evaluated, ids):
    with pytest.raises(ValueError):
        replace(scope(evaluated), candidate_ids=ids)


def test_all_scope_requires_baseline_and_original_source_order(evaluated):
    item = scope(evaluated, policy(SM.ALL_SCENARIOS))
    with pytest.raises(ValueError):
        replace(item, candidate_ids=("A", "B"))
    with pytest.raises(ValueError):
        replace(item, candidate_ids=("A", "baseline", "B"))


@pytest.mark.parametrize("changes", [{"policy": None}, {"mode": SM.ALL_SCENARIOS},
    {"source_reference": ""}, {"candidate_ids": ["A", "B"]}, {"candidate_ids": {"A", "B"}},
    {"scenario_sources": []}, {"source_set_binding": None}])
def test_scope_requires_explicit_policy_and_immutable_metadata(evaluated, changes):
    with pytest.raises((ValueError, TypeError)):
        replace(scope(evaluated), **changes)


@pytest.mark.parametrize("field", ["customer_id", "site_id", "application_id", "gas_product_id", "installation_id"])
def test_scope_rejects_each_incomplete_position_identity(evaluated, field):
    item = scope(evaluated)
    incomplete = replace(item.source_set_binding.position_identity, **{field: None})
    with pytest.raises(ValueError):
        replace(item, source_set_binding=replace(item.source_set_binding, position_identity=incomplete))


def test_scope_rejects_mixed_revisions_duplicate_sources_and_wrong_baseline_order(evaluated):
    item = scope(evaluated)
    other = replace(item.scenario_sources[1], source_set_binding=replace(item.source_set_binding, set_revision="other"))
    for sources in ((item.scenario_sources[0], other, item.scenario_sources[2]),
                    (item.scenario_sources[0], item.scenario_sources[1], item.scenario_sources[1]),
                    item.scenario_sources[::-1], item.scenario_sources[1:]):
        with pytest.raises(ValueError):
            replace(item, scenario_sources=sources)


def test_candidate_records_reuse_v118_results_and_aggregate(evaluated):
    item = result(evaluated)
    a, b = item.candidate_assessments
    assert a.scenario_id == "A" and b.scenario_id == "B"
    assert a.hard_constraint_outcome is H.VIOLATED
    assert b.hard_constraint_outcome is H.SATISFIED
    assert a.scenario_evaluation is evaluated.scenario_evaluations[1]
    assert a.criterion_references == ("gap", "capacity")
    assert a.violation_references == ("gap",) and not a.unknown_references


@pytest.mark.parametrize("changes", [{"eligibility": CE.ELIGIBLE}, {"eligibility": "INELIGIBLE"},
    {"eligibility": None}, {"criterion_references": ("capacity", "gap")},
    {"criterion_references": ("gap", "gap")}, {"violation_references": ()},
    {"unknown_references": ("gap",)}, {"reasons": (CR.HARD_CONSTRAINT_UNKNOWN,)},
    {"reasons": (CR.HARD_CONSTRAINT_VIOLATED,) * 2}, {"criterion_references": ["gap", "capacity"]}])
def test_candidate_evidence_eligibility_and_reasons_must_agree(evaluated, changes):
    item = result(evaluated).candidate_assessments[0]
    with pytest.raises((ValueError, TypeError)):
        replace(item, **changes)


def test_violation_and_unknown_remain_ineligible_with_both_evidences():
    source, comparison = retained()
    evaluation = evaluate(source, comparison, constraints=(
        constraint(SupplyMetric.SAFETY_STOCK_GAP, PredicateOperator.GE, Decimal(0), "kg", id="gap"),
        constraint(SupplyMetric.INVENTORY_BEFORE_DELIVERY, PredicateOperator.GE, Decimal(0), "t", id="unit"),
    ))
    item = CandidateDecisionAssessment(evaluation.scenario_evaluations[1], CE.INELIGIBLE,
        ("gap", "unit"), ("gap",), ("unit",), (CR.HARD_CONSTRAINT_VIOLATED, CR.HARD_CONSTRAINT_UNKNOWN))
    assert item.hard_constraint_outcome is H.VIOLATED
    assert item.unknown_references == ("unit",)
    assert item.scenario_evaluation.evaluations[1].observed_unit == "kg"


def test_unresolved_and_insufficient_evidence_records():
    source, comparison = retained()
    evaluation = evaluate(source, comparison, constraints=(
        constraint(SupplyMetric.SAFETY_STOCK_GAP, PredicateOperator.GE, Decimal(0), "t", id="gap"),))
    item_scope = scope(evaluation)
    candidates = tuple(CandidateDecisionAssessment(row, CE.UNRESOLVED, ("gap",), (), ("gap",),
        (CR.HARD_CONSTRAINT_UNKNOWN,)) for row in evaluation.scenario_evaluations[1:])
    item = SupplyDecisionEvaluation(S.INSUFFICIENT_EVIDENCE, (R.ELIGIBILITY_UNRESOLVED,),
        evaluation, "evaluation", "1", ("evaluation",), item_scope.policy, item_scope, candidates)
    assert item.selected_scenario_id is None
    assert all(candidate.hard_constraint_outcome is H.UNKNOWN for candidate in item.candidate_assessments)


def test_objectives_do_not_enter_eligibility_and_no_constraints_stays_unassessed():
    source, comparison = retained()
    evaluation = evaluate(source, comparison, objectives=(objective(),))
    item_scope = scope(evaluation)
    candidates = tuple(CandidateDecisionAssessment(row, None, (), (), (), (CR.NO_HARD_CONSTRAINTS,))
                       for row in evaluation.scenario_evaluations[1:])
    item = SupplyDecisionEvaluation(S.UNDECIDABLE, (R.NO_HARD_CONSTRAINTS,), evaluation,
        "evaluation", "1", ("evaluation",), item_scope.policy, item_scope, candidates)
    assert all(candidate.hard_constraint_outcome is H.NOT_APPLICABLE
               and candidate.eligibility is None for candidate in item.candidate_assessments)
    with pytest.raises(ValueError):
        replace(candidates[0], eligibility=CE.ELIGIBLE)


def test_valid_decided_record_preserves_all_source_links(evaluated):
    item = result(evaluated)
    assert item.selected_scenario_id == "B"
    assert item.criteria_evaluation is evaluated
    assert item.source_set_binding is evaluated.criteria.source_set_binding
    assert item.position_identity is item.source_set_binding.position_identity
    assert item.policy.policy_version == "1" and item.engine_version == "decision-engine-1"
    assert item.candidate_ids == ("A", "B")
    source = evaluated.scenario_evaluations[2].source
    refs = ("criteria-evaluation-1", source.result_reference, source.projection_reference,
            source.provenance_references[0], POLICY_SOURCE.reference)
    assert replace(item, provenance_references=refs).provenance_references == refs


@pytest.mark.parametrize("selected", [None, "", "outside", "baseline", "A"])
def test_decided_requires_eligible_selected_candidate_in_scope(evaluated, selected):
    with pytest.raises(ValueError):
        result(evaluated, selected_scenario_id=selected)


@pytest.mark.parametrize("status,reason", [(S.UNDECIDABLE, R.NO_ELIGIBLE_CANDIDATE),
    (S.INSUFFICIENT_EVIDENCE, R.ELIGIBILITY_UNRESOLVED)])
def test_non_decided_cannot_select(evaluated, status, reason):
    with pytest.raises(ValueError):
        result(evaluated, status=status, reasons=(reason,))


@pytest.mark.parametrize("status,reason", [(S.DECIDED, R.NO_ELIGIBLE_CANDIDATE),
    (S.UNDECIDABLE, R.UNIQUE_ELIGIBLE_CANDIDATE), (S.UNDECIDABLE, R.ELIGIBILITY_UNRESOLVED),
    (S.INSUFFICIENT_EVIDENCE, R.MULTIPLE_ELIGIBLE_CANDIDATES)])
def test_incompatible_status_reason_rejected(evaluated, status, reason):
    with pytest.raises(ValueError):
        result(evaluated, status=status, reasons=(reason,), selected_scenario_id=None)


@pytest.mark.parametrize("reason", [R.NO_ELIGIBLE_CANDIDATE,
                                  R.MULTIPLE_ELIGIBLE_CANDIDATES, R.UNSUPPORTED_PREFERENCE])
def test_valid_non_decision_reason_records_without_executing_policy(evaluated, reason):
    # Records do not establish the candidate counts implied by these declarations.
    # TASK 2 is responsible for deciding which reason the original evidence justifies.
    item = result(evaluated, status=S.UNDECIDABLE, reasons=(reason,), selected_scenario_id=None)
    assert item.status is S.UNDECIDABLE


@pytest.mark.parametrize("reason", [R.POLICY_REQUIRED, R.UNSUPPORTED_POLICY])
def test_missing_or_rejected_policy_is_recorded_without_implicit_scope(evaluated, reason):
    item = SupplyDecisionEvaluation(S.UNDECIDABLE, (reason,), evaluated, "criteria", "1", ("criteria",))
    assert item.policy is item.scope is item.selected_scenario_id is None
    assert item.candidate_ids == ()
    with pytest.raises(ValueError):
        replace(item, scope=scope(evaluated))
    with pytest.raises(ValueError):
        replace(item, reasons=(R.NO_ELIGIBLE_CANDIDATE,))


@pytest.mark.parametrize("reasons", [(R.NO_HARD_CONSTRAINTS,),
    (R.NO_ELIGIBLE_CANDIDATE, R.MULTIPLE_ELIGIBLE_CANDIDATES),
    (R.POLICY_REQUIRED, R.UNSUPPORTED_POLICY)])
def test_contradictory_definition_and_reason_metadata_rejected(evaluated, reasons):
    with pytest.raises(ValueError):
        result(evaluated, status=S.UNDECIDABLE, reasons=reasons, selected_scenario_id=None)


def test_policy_scope_snapshot_and_candidate_assessment_consistency(evaluated):
    item = result(evaluated)
    for changes in ({"policy": replace(item.policy, policy_version="2")},
                    {"candidate_assessments": item.candidate_assessments[::-1]},
                    {"candidate_assessments": item.candidate_assessments[:1]},
                    {"candidate_assessments": (item.candidate_assessments[0],) * 2}):
        with pytest.raises(ValueError):
            replace(item, **changes)
    altered = replace(item.candidate_assessments[1].scenario_evaluation, evaluator_version="other", evaluations=())
    fake = CandidateDecisionAssessment(altered, None, (), (), (), (CR.NO_HARD_CONSTRAINTS,))
    with pytest.raises(ValueError):
        replace(item, candidate_assessments=(item.candidate_assessments[0], fake))


def test_all_scenarios_preserves_baseline_assessment(evaluated):
    item = result(evaluated, selected_policy=policy(SM.ALL_SCENARIOS))
    assert item.candidate_ids == ("baseline", "A", "B")
    assert item.candidate_assessments[0].scenario_id == "baseline"


@pytest.mark.parametrize("changes", [{"status": "DECIDED"}, {"engine_version": ""},
    {"criteria_evaluation_reference": " "}, {"provenance_references": ()},
    {"provenance_references": ("criteria-evaluation-1",) * 2},
    {"provenance_references": ("unbound-source",)}, {"reasons": ()},
    {"reasons": (R.UNIQUE_ELIGIBLE_CANDIDATE,) * 2}, {"reasons": [R.UNIQUE_ELIGIBLE_CANDIDATE]},
    {"candidate_assessments": []}, {"provenance_references": {"criteria-evaluation-1"}}])
def test_malformed_final_records_and_mutable_collections_rejected(evaluated, changes):
    with pytest.raises((ValueError, TypeError)):
        result(evaluated, **changes)


def test_incomplete_criteria_snapshot_is_not_a_complete_decision_record(evaluated):
    row = evaluated.scenario_evaluations[1]
    missing = replace(row, evaluations=row.evaluations[:1])
    for snapshot in (replace(evaluated, scenario_evaluations=evaluated.scenario_evaluations[1:]),
                     replace(evaluated, scenario_evaluations=(evaluated.scenario_evaluations[0], missing,
                                                            evaluated.scenario_evaluations[2]))):
        with pytest.raises(ValueError):
            result(evaluated, criteria_evaluation=snapshot)


def test_duplicate_source_provenance_references_rejected(evaluated):
    item = scope(evaluated)
    source = replace(item.scenario_sources[1], provenance_references=("same", "same"))
    with pytest.raises(ValueError):
        replace(item, scenario_sources=(item.scenario_sources[0], source, item.scenario_sources[2]))


def test_all_records_frozen_and_no_mutable_defaults(evaluated):
    item = result(evaluated)
    for value, field, replacement in ((item, "engine_version", "2"), (item.policy, "policy_id", "other"),
        (item.scope, "candidate_ids", ()), (item.candidate_assessments[0], "eligibility", CE.ELIGIBLE)):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
        for definition in fields(value):
            assert not isinstance(definition.default, (dict, list, set))
    assert result(evaluated) == result(evaluated)


def test_existing_json_projection_is_stable_and_retains_enum_versions_and_references(evaluated):
    item = result(evaluated)
    encoded = json.dumps(_jsonable(item), sort_keys=True)
    assert encoded == json.dumps(_jsonable(result(evaluated)), sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["status"] == "DECIDED" and decoded["selected_scenario_id"] == "B"
    assert decoded["policy"]["selection_rule"] == "UNIQUE_HARD_CONSTRAINT_COMPLIANT"
    assert decoded["scope"]["candidate_ids"] == ["A", "B"]
    assert decoded["criteria_evaluation_reference"] == "criteria-evaluation-1"


def test_source_validation_failures_are_not_business_decision_outcomes(evaluated):
    error = SupplyDecisionValidationError(VR.STALE_SOURCE_REVISION)
    assert error.reason is VR.STALE_SOURCE_REVISION
    assert isinstance(error, ValueError)
    with pytest.raises(TypeError):
        SupplyDecisionValidationError("STALE_SOURCE_REVISION")
    with pytest.raises(TypeError):
        result(evaluated, reasons=(VR.SOURCE_FACT_MISMATCH,), status=S.UNDECIDABLE, selected_scenario_id=None)
