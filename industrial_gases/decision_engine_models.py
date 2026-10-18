"""Immutable records for explicit Industrial Gases decision policies.

No candidate is selected here. Validation checks declared structure, snapshots and
references, not whether observations, outcomes or authorization are authentic.
TASK 2 must verify these records against the original retained scenario results.
"""
from dataclasses import dataclass, fields
from enum import Enum

from .factual_comparison import ScenarioPositionIdentity
from .objective_constraint_models import (
    CriterionDefinitionSource, CriterionKind, CriterionOutcome, HardConstraintOutcome,
    ScenarioCriteriaEvaluation, ScenarioFactSource, ScenarioSetBinding,
    ScenarioSetCriteriaEvaluation,
)


class DecisionSelectionRule(str, Enum):
    UNIQUE_HARD_CONSTRAINT_COMPLIANT = "UNIQUE_HARD_CONSTRAINT_COMPLIANT"


class DecisionScopeMode(str, Enum):
    EXPLICIT_ALTERNATIVES = "EXPLICIT_ALTERNATIVES"
    ALL_SCENARIOS = "ALL_SCENARIOS"


class CandidateEligibility(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNRESOLVED = "UNRESOLVED"


class CandidateDecisionReason(str, Enum):
    ALL_HARD_CONSTRAINTS_SATISFIED = "ALL_HARD_CONSTRAINTS_SATISFIED"
    HARD_CONSTRAINT_VIOLATED = "HARD_CONSTRAINT_VIOLATED"
    HARD_CONSTRAINT_UNKNOWN = "HARD_CONSTRAINT_UNKNOWN"
    NO_HARD_CONSTRAINTS = "NO_HARD_CONSTRAINTS"


class SupplyDecisionStatus(str, Enum):
    DECIDED = "DECIDED"
    UNDECIDABLE = "UNDECIDABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class SupplyDecisionReason(str, Enum):
    UNIQUE_ELIGIBLE_CANDIDATE = "UNIQUE_ELIGIBLE_CANDIDATE"
    NO_HARD_CONSTRAINTS = "NO_HARD_CONSTRAINTS"
    NO_ELIGIBLE_CANDIDATE = "NO_ELIGIBLE_CANDIDATE"
    MULTIPLE_ELIGIBLE_CANDIDATES = "MULTIPLE_ELIGIBLE_CANDIDATES"
    ELIGIBILITY_UNRESOLVED = "ELIGIBILITY_UNRESOLVED"
    UNSUPPORTED_PREFERENCE = "UNSUPPORTED_PREFERENCE"
    POLICY_REQUIRED = "POLICY_REQUIRED"
    UNSUPPORTED_POLICY = "UNSUPPORTED_POLICY"


class DecisionValidationReason(str, Enum):
    STALE_SOURCE_REVISION = "STALE_SOURCE_REVISION"
    SOURCE_BINDING_MISMATCH = "SOURCE_BINDING_MISMATCH"
    SOURCE_FACT_MISMATCH = "SOURCE_FACT_MISMATCH"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"


class SupplyDecisionValidationError(ValueError):
    """Invalid source evidence is an error, never an UNDECIDABLE business result."""

    def __init__(self, reason: DecisionValidationReason):
        _typed(reason, DecisionValidationReason, "validation reason")
        self.reason = reason
        super().__init__(reason.value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _typed(value, expected, name):
    if not isinstance(value, expected):
        raise TypeError(f"{name} must be {expected.__name__}")


def _tuple(value, expected, name, *, nonempty=False):
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an immutable ordered tuple")
    if nonempty and not value:
        raise ValueError(f"{name} must not be empty")
    for item in value:
        _typed(item, expected, name)


def _references(value, name, *, nonempty=False):
    _tuple(value, str, name, nonempty=nonempty)
    for item in value:
        _text(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must be unique")


def _binding(value):
    _typed(value, ScenarioSetBinding, "source_set_binding")
    _typed(value.position_identity, ScenarioPositionIdentity, "position_identity")
    _text(value.set_reference, "set_reference")
    _text(value.set_revision, "set_revision")
    for field in fields(value.position_identity):
        _text(getattr(value.position_identity, field.name), field.name)


def _source(value):
    _typed(value, ScenarioFactSource, "scenario source")
    _binding(value.source_set_binding)
    _text(value.scenario_id, "scenario_id")
    _text(value.result_reference, "result_reference")
    if value.projection_reference is not None:
        _text(value.projection_reference, "projection_reference")
    _references(value.provenance_references, "provenance_references")


def _sources(values, binding):
    _tuple(values, ScenarioFactSource, "scenario_sources", nonempty=True)
    ids = tuple(source.scenario_id for source in values)
    _references(ids, "scenario IDs")
    if len(ids) < 2 or ids[0] != "baseline":
        raise ValueError("source manifest must contain baseline first and explicit alternatives")
    for source in values:
        _source(source)
        if source.source_set_binding != binding:
            raise ValueError("scenario sources must share the complete set binding")
    _references(tuple(source.result_reference for source in values), "result references")
    _references(tuple(source.projection_reference for source in values
                      if source.projection_reference is not None), "projection references")
    return ids


@dataclass(frozen=True)
class SupplyDecisionPolicy:
    """Explicit policy metadata; presence is not proof of business authorization."""

    policy_id: str
    policy_version: str
    selection_rule: DecisionSelectionRule
    definition_source: CriterionDefinitionSource
    scope_mode: DecisionScopeMode = DecisionScopeMode.EXPLICIT_ALTERNATIVES

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id")
        _text(self.policy_version, "policy_version")
        _typed(self.selection_rule, DecisionSelectionRule, "selection_rule")
        _typed(self.scope_mode, DecisionScopeMode, "scope_mode")
        _typed(self.definition_source, CriterionDefinitionSource, "definition_source")


@dataclass(frozen=True)
class SupplyDecisionScope:
    """Resolved candidates against a complete ordered v1.18 source manifest.

    This validates resolution against the manifest, not its correspondence to
    original physical results; that source verification belongs to TASK 2.
    """

    policy: SupplyDecisionPolicy
    mode: DecisionScopeMode
    source_set_binding: ScenarioSetBinding
    source_reference: str
    scenario_sources: tuple[ScenarioFactSource, ...]
    candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _typed(self.policy, SupplyDecisionPolicy, "policy")
        _typed(self.mode, DecisionScopeMode, "mode")
        _binding(self.source_set_binding)
        _text(self.source_reference, "source_reference")
        if self.mode is not self.policy.scope_mode:
            raise ValueError("scope mode must match its explicit policy")
        ids = _sources(self.scenario_sources, self.source_set_binding)
        _references(self.candidate_ids, "candidate_ids", nonempty=True)
        expected = ids if self.mode is DecisionScopeMode.ALL_SCENARIOS else ids[1:]
        if self.candidate_ids != expected:
            raise ValueError("candidate IDs must exactly match the declared mode and source order")


@dataclass(frozen=True)
class CandidateDecisionAssessment:
    """Declared eligibility linked to original v1.18 criterion records.

    With NOT_APPLICABLE, eligibility is None: no hard constraints authorize an
    eligibility claim. Objectives never enter the hard-constraint references.
    """

    scenario_evaluation: ScenarioCriteriaEvaluation
    eligibility: CandidateEligibility | None
    criterion_references: tuple[str, ...]
    violation_references: tuple[str, ...]
    unknown_references: tuple[str, ...]
    reasons: tuple[CandidateDecisionReason, ...]

    @property
    def scenario_id(self) -> str:
        return self.scenario_evaluation.source.scenario_id

    @property
    def hard_constraint_outcome(self) -> HardConstraintOutcome:
        return self.scenario_evaluation.hard_constraint_outcome

    def __post_init__(self) -> None:
        _typed(self.scenario_evaluation, ScenarioCriteriaEvaluation, "scenario_evaluation")
        _source(self.scenario_evaluation.source)
        _references(self.criterion_references, "criterion_references")
        _references(self.violation_references, "violation_references")
        _references(self.unknown_references, "unknown_references")
        _tuple(self.reasons, CandidateDecisionReason, "candidate reasons", nonempty=True)
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("candidate reasons must be unique")
        hard = tuple(item for item in self.scenario_evaluation.evaluations
                     if item.criterion_kind is CriterionKind.HARD_CONSTRAINT)
        if self.criterion_references != tuple(item.criterion_id for item in hard):
            raise ValueError("criterion references must cover exactly the recorded hard constraints")
        for refs, outcome in ((self.violation_references, CriterionOutcome.VIOLATED),
                              (self.unknown_references, CriterionOutcome.UNKNOWN)):
            if refs != tuple(item.criterion_id for item in hard if item.outcome is outcome):
                raise ValueError("evidence references must match the recorded outcomes in order")
        expected = {
            HardConstraintOutcome.SATISFIED: (CandidateEligibility.ELIGIBLE,
                (CandidateDecisionReason.ALL_HARD_CONSTRAINTS_SATISFIED,)),
            HardConstraintOutcome.VIOLATED: (CandidateEligibility.INELIGIBLE,
                (CandidateDecisionReason.HARD_CONSTRAINT_VIOLATED,) +
                ((CandidateDecisionReason.HARD_CONSTRAINT_UNKNOWN,) if self.unknown_references else ())),
            HardConstraintOutcome.UNKNOWN: (CandidateEligibility.UNRESOLVED,
                (CandidateDecisionReason.HARD_CONSTRAINT_UNKNOWN,)),
            HardConstraintOutcome.NOT_APPLICABLE: (None, (CandidateDecisionReason.NO_HARD_CONSTRAINTS,)),
        }[self.hard_constraint_outcome]
        if self.eligibility is not None:
            _typed(self.eligibility, CandidateEligibility, "eligibility")
        if (self.eligibility, self.reasons) != expected:
            raise ValueError("eligibility and reasons must agree with recorded hard-constraint compliance")


def _criteria_snapshot(value):
    _typed(value, ScenarioSetCriteriaEvaluation, "criteria_evaluation")
    _binding(value.criteria.source_set_binding)
    scenarios = value.scenario_evaluations
    sources = tuple(item.source for item in scenarios)
    _sources(sources, value.criteria.source_set_binding)
    expected_ids = tuple(item.id for item in value.criteria.objectives + value.criteria.constraints)
    for scenario in scenarios:
        if tuple(item.criterion_id for item in scenario.evaluations) != expected_ids:
            raise ValueError("criteria snapshot must cover every definition in its original order")
    return sources


@dataclass(frozen=True)
class SupplyDecisionEvaluation:
    """A declared decision result, not a proof of eligibility or uniqueness.

    No selection rule is executed. TASK 2 must verify original facts and establish
    candidate counts before emitting DECIDED. A missing/unsupported policy can be
    recorded without creating a scope that would implicitly authorize selection.
    """

    status: SupplyDecisionStatus
    reasons: tuple[SupplyDecisionReason, ...]
    criteria_evaluation: ScenarioSetCriteriaEvaluation
    criteria_evaluation_reference: str
    engine_version: str
    provenance_references: tuple[str, ...]
    policy: SupplyDecisionPolicy | None = None
    scope: SupplyDecisionScope | None = None
    candidate_assessments: tuple[CandidateDecisionAssessment, ...] = ()
    selected_scenario_id: str | None = None

    @property
    def source_set_binding(self) -> ScenarioSetBinding:
        return self.criteria_evaluation.criteria.source_set_binding

    @property
    def position_identity(self) -> ScenarioPositionIdentity:
        return self.source_set_binding.position_identity

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return self.scope.candidate_ids if self.scope is not None else ()

    def __post_init__(self) -> None:
        _typed(self.status, SupplyDecisionStatus, "status")
        _tuple(self.reasons, SupplyDecisionReason, "decision reasons", nonempty=True)
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("decision reasons must be unique")
        allowed = {
            SupplyDecisionStatus.DECIDED: {SupplyDecisionReason.UNIQUE_ELIGIBLE_CANDIDATE},
            SupplyDecisionStatus.UNDECIDABLE: {
                SupplyDecisionReason.NO_HARD_CONSTRAINTS, SupplyDecisionReason.NO_ELIGIBLE_CANDIDATE,
                SupplyDecisionReason.MULTIPLE_ELIGIBLE_CANDIDATES, SupplyDecisionReason.UNSUPPORTED_PREFERENCE,
                SupplyDecisionReason.POLICY_REQUIRED, SupplyDecisionReason.UNSUPPORTED_POLICY},
            SupplyDecisionStatus.INSUFFICIENT_EVIDENCE: {SupplyDecisionReason.ELIGIBILITY_UNRESOLVED},
        }[self.status]
        if not set(self.reasons) <= allowed:
            raise ValueError("decision reasons are incompatible with status")
        counts_reasons = {SupplyDecisionReason.NO_HARD_CONSTRAINTS,
                         SupplyDecisionReason.NO_ELIGIBLE_CANDIDATE,
                         SupplyDecisionReason.MULTIPLE_ELIGIBLE_CANDIDATES}
        if len(set(self.reasons) & counts_reasons) > 1:
            raise ValueError("decision reasons must not make mutually exclusive declarations")
        if self.status is SupplyDecisionStatus.DECIDED:
            _text(self.selected_scenario_id, "selected_scenario_id")
        elif self.selected_scenario_id is not None:
            raise ValueError("only DECIDED can carry a selected scenario ID")
        _text(self.criteria_evaluation_reference, "criteria_evaluation_reference")
        _text(self.engine_version, "engine_version")
        _references(self.provenance_references, "provenance_references", nonempty=True)
        sources = _criteria_snapshot(self.criteria_evaluation)
        if (SupplyDecisionReason.NO_HARD_CONSTRAINTS in self.reasons
                and self.criteria_evaluation.criteria.constraints):
            raise ValueError("NO_HARD_CONSTRAINTS requires an empty hard-constraint definition set")
        if (not self.criteria_evaluation.criteria.constraints and set(self.reasons) & {
                SupplyDecisionReason.UNIQUE_ELIGIBLE_CANDIDATE,
                SupplyDecisionReason.NO_ELIGIBLE_CANDIDATE,
                SupplyDecisionReason.MULTIPLE_ELIGIBLE_CANDIDATES,
                SupplyDecisionReason.ELIGIBILITY_UNRESOLVED}):
            raise ValueError("eligibility-based reasons require explicit hard constraints")
        _tuple(self.candidate_assessments, CandidateDecisionAssessment, "candidate_assessments")
        policy_missing_reasons = {SupplyDecisionReason.POLICY_REQUIRED, SupplyDecisionReason.UNSUPPORTED_POLICY}
        if len(set(self.reasons) & policy_missing_reasons) > 1:
            raise ValueError("a policy cannot be declared both missing and unsupported")
        if self.policy is None:
            if (not set(self.reasons) <= policy_missing_reasons or self.scope is not None
                    or self.candidate_assessments):
                raise ValueError("absent policy requires a policy reason and no implicit scope or assessments")
        else:
            _typed(self.policy, SupplyDecisionPolicy, "policy")
            _typed(self.scope, SupplyDecisionScope, "scope")
            if set(self.reasons) & policy_missing_reasons:
                raise ValueError("an existing supported policy cannot be reported missing or unsupported")
            if (self.scope.policy != self.policy or self.scope.source_set_binding != self.source_set_binding
                    or self.scope.scenario_sources != sources):
                raise ValueError("policy, scope and criteria source snapshots must match")
            if tuple(item.scenario_id for item in self.candidate_assessments) != self.scope.candidate_ids:
                raise ValueError("assessments must cover all candidates exactly in scope order")
            by_id = {item.source.scenario_id: item for item in self.criteria_evaluation.scenario_evaluations}
            if any(item.scenario_evaluation != by_id[item.scenario_id] for item in self.candidate_assessments):
                raise ValueError("candidate assessments must retain the original criterion snapshot")
            if self.selected_scenario_id is not None:
                selected = next((item for item in self.candidate_assessments
                                 if item.scenario_id == self.selected_scenario_id), None)
                if selected is None or selected.eligibility is not CandidateEligibility.ELIGIBLE:
                    raise ValueError("selected scenario must be an eligible candidate in the declared scope")
        references = {self.criteria_evaluation_reference, self.source_set_binding.set_reference,
                      self.criteria_evaluation.criteria.definition_source.reference}
        references.update(item.definition_source.reference for item in
                          self.criteria_evaluation.criteria.objectives + self.criteria_evaluation.criteria.constraints)
        for source in sources:
            references.add(source.result_reference)
            if source.projection_reference is not None:
                references.add(source.projection_reference)
            references.update(source.provenance_references)
        if self.policy is not None:
            references.update((self.policy.definition_source.reference, self.scope.source_reference))
        if not set(self.provenance_references) <= references:
            raise ValueError("provenance references must resolve within the supplied snapshots")
