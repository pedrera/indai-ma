"""Pure selection of the unique explicitly hard-constraint-compliant candidate.

The caller supplies the authoritative current source revision. Verification does
not discover unseen revisions or authenticate business authorization. References
derived here are stable snapshot locators, not persistent audit identifiers.
"""
from dataclasses import fields
from urllib.parse import quote

from .decision_engine_models import (
    CandidateDecisionAssessment, CandidateDecisionReason as CR, CandidateEligibility as CE,
    DecisionScopeMode, DecisionSelectionRule, DecisionValidationReason as VR,
    SupplyDecisionEvaluation, SupplyDecisionPolicy, SupplyDecisionReason as DR,
    SupplyDecisionScope, SupplyDecisionStatus as DS, SupplyDecisionValidationError,
)
from .factual_comparison import compare_supply_assurance_scenario_set
from .objective_constraint_evaluation import (
    EVALUATOR_VERSION, RetainedScenarioSetSource, SourceBindingError, evaluate_supply_criteria,
)
from .objective_constraint_models import (
    CriterionKind, CriterionOutcome, HardConstraintOutcome as H, ScenarioSetCriteriaEvaluation,
)


ENGINE_VERSION = "supply-decision-v1"


def _reject(reason, detail):
    raise SupplyDecisionValidationError(reason) from ValueError(detail)


def _verify(evaluation, source):
    binding = evaluation.criteria.source_set_binding
    if (binding.set_reference != source.binding.set_reference
            or binding.position_identity != source.binding.position_identity):
        _reject(VR.SOURCE_BINDING_MISMATCH, "criteria_set_binding_mismatch")
    if binding.set_revision != source.binding.set_revision:
        _reject(VR.STALE_SOURCE_REVISION, "criteria_set_revision_mismatch")
    if any(not isinstance(getattr(binding.position_identity, field.name), str)
           or not getattr(binding.position_identity, field.name).strip()
           for field in fields(binding.position_identity)):
        _reject(VR.SOURCE_BINDING_MISMATCH, "focused_position_identity_required")
    expected_ids = ("baseline",) + tuple(item.id for item in source.scenario_set.alternatives)
    actual_ids = tuple(row.source.scenario_id for row in evaluation.scenario_evaluations)
    if len(set(expected_ids)) != len(expected_ids) or actual_ids != expected_ids:
        _reject(VR.INCOMPLETE_EVIDENCE, "incomplete_duplicate_or_reordered_scenario_manifest")
    criterion_ids = tuple(item.id for item in
                          evaluation.criteria.objectives + evaluation.criteria.constraints)
    for row in evaluation.scenario_evaluations:
        if tuple(item.criterion_id for item in row.evaluations) != criterion_ids:
            _reject(VR.INCOMPLETE_EVIDENCE, "incomplete_duplicate_or_reordered_criteria")
    if evaluation.evaluator_version != EVALUATOR_VERSION:
        _reject(VR.SOURCE_FACT_MISMATCH, "unsupported_criteria_evaluator_version")
    try:
        comparison = compare_supply_assurance_scenario_set(source.result)
        canonical = evaluate_supply_criteria(evaluation.criteria, comparison, source)
    except (SourceBindingError, ValueError, TypeError, AttributeError) as error:
        raise SupplyDecisionValidationError(VR.SOURCE_BINDING_MISMATCH) from error
    if canonical != evaluation:
        _reject(VR.SOURCE_FACT_MISMATCH, "canonical_criteria_evaluation_mismatch")


def _assess(row):
    hard = tuple(item for item in row.evaluations
                 if item.criterion_kind is CriterionKind.HARD_CONSTRAINT)
    violated = tuple(item.criterion_id for item in hard if item.outcome is CriterionOutcome.VIOLATED)
    unknown = tuple(item.criterion_id for item in hard if item.outcome is CriterionOutcome.UNKNOWN)
    eligibility, reasons = {
        H.SATISFIED: (CE.ELIGIBLE, (CR.ALL_HARD_CONSTRAINTS_SATISFIED,)),
        H.VIOLATED: (CE.INELIGIBLE, (CR.HARD_CONSTRAINT_VIOLATED,) +
                     ((CR.HARD_CONSTRAINT_UNKNOWN,) if unknown else ())),
        H.UNKNOWN: (CE.UNRESOLVED, (CR.HARD_CONSTRAINT_UNKNOWN,)),
        H.NOT_APPLICABLE: (None, (CR.NO_HARD_CONSTRAINTS,)),
    }[row.hard_constraint_outcome]
    return CandidateDecisionAssessment(row, eligibility, tuple(item.criterion_id for item in hard),
                                       violated, unknown, reasons)


def evaluate_supply_decision(
    criteria_evaluation: ScenarioSetCriteriaEvaluation,
    decision_policy: SupplyDecisionPolicy | None,
    source_binding: RetainedScenarioSetSource,
) -> SupplyDecisionEvaluation:
    """Verify full retained evidence before applying an explicit selection policy.

    Unsupported policy objects and preference arguments are rejected, never
    ignored. None records POLICY_REQUIRED without creating a policy or scope.
    DECIDED means unique compliance in this scope, not the best business choice.
    """
    if not isinstance(criteria_evaluation, ScenarioSetCriteriaEvaluation):
        raise TypeError("criteria_evaluation must be ScenarioSetCriteriaEvaluation")
    if not isinstance(source_binding, RetainedScenarioSetSource):
        raise TypeError("source_binding must be RetainedScenarioSetSource")
    if decision_policy is not None:
        if not isinstance(decision_policy, SupplyDecisionPolicy):
            raise TypeError("decision_policy must be SupplyDecisionPolicy or None")
        if decision_policy.selection_rule is not DecisionSelectionRule.UNIQUE_HARD_CONSTRAINT_COMPLIANT:
            raise ValueError("unsupported decision policy")
        if not isinstance(decision_policy.scope_mode, DecisionScopeMode):
            raise ValueError("unsupported decision scope")
    _verify(criteria_evaluation, source_binding)
    binding = source_binding.binding
    prefix = "/".join(quote(value, safe="") for value in (binding.set_reference, binding.set_revision))
    definition = criteria_evaluation.criteria.definition_source
    evaluation_ref = "/".join((prefix, "criteria", quote(definition.reference, safe=""),
                               quote(definition.revision, safe=""), EVALUATOR_VERSION))
    references = [evaluation_ref, binding.set_reference, definition.reference]
    references.extend(item.definition_source.reference for item in
                      criteria_evaluation.criteria.objectives + criteria_evaluation.criteria.constraints)
    sources = tuple(row.source for row in criteria_evaluation.scenario_evaluations)
    for source in sources:
        references.append(source.result_reference)
        if source.projection_reference is not None:
            references.append(source.projection_reference)
        references.extend(source.provenance_references)
    scope, assessments, selected = None, (), None
    status, reason = DS.UNDECIDABLE, DR.POLICY_REQUIRED
    if decision_policy is not None:
        rows = criteria_evaluation.scenario_evaluations
        candidates = rows if decision_policy.scope_mode is DecisionScopeMode.ALL_SCENARIOS else rows[1:]
        scope = SupplyDecisionScope(decision_policy, decision_policy.scope_mode, binding,
                                    prefix + "/result", sources,
                                    tuple(row.source.scenario_id for row in candidates))
        references.extend((decision_policy.definition_source.reference, scope.source_reference))
        assessments = tuple(_assess(row) for row in candidates)
        eligible = tuple(item for item in assessments if item.eligibility is CE.ELIGIBLE)
        unresolved = sum(item.eligibility is CE.UNRESOLVED for item in assessments)
        if not criteria_evaluation.criteria.constraints:
            reason = DR.NO_HARD_CONSTRAINTS
        elif len(eligible) >= 2:
            reason = DR.MULTIPLE_ELIGIBLE_CANDIDATES
        elif unresolved:
            status, reason = DS.INSUFFICIENT_EVIDENCE, DR.ELIGIBILITY_UNRESOLVED
        elif len(eligible) == 1:
            status, reason, selected = DS.DECIDED, DR.UNIQUE_ELIGIBLE_CANDIDATE, eligible[0].scenario_id
        else:
            reason = DR.NO_ELIGIBLE_CANDIDATE
    return SupplyDecisionEvaluation(status, (reason,), criteria_evaluation, evaluation_ref,
                                    ENGINE_VERSION, tuple(dict.fromkeys(references)),
                                    decision_policy, scope, assessments, selected)
