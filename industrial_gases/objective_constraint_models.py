"""Immutable, explicit Supply Assurance criteria and result records.

These contracts neither evaluate predicates nor calculate physical facts. References
are caller-supplied identifiers, not generated IDs or executable instructions.
"""
from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum

from .factual_comparison import ScenarioPositionIdentity
from .units import unit_spec


class SupplyMetric(str, Enum):
    CONSUMPTION_UNTIL_DELIVERY = "consumption_until_delivery"
    INVENTORY_BEFORE_DELIVERY = "inventory_immediately_before_delivery"
    SAFETY_STOCK_GAP = "safety_stock_gap_before_delivery"
    STOCKOUT_BEFORE_DELIVERY = "stockout_before_delivery"
    INVENTORY_AFTER_DELIVERY = "inventory_immediately_after_delivery"
    REQUIRED_DELIVERY_VOLUME = "required_delivery_volume"
    CAPACITY_EXCEEDED = "capacity_exceeded"


class PredicateOperator(str, Enum):
    LT = "lt"
    LE = "le"
    EQ = "eq"
    GE = "ge"
    GT = "gt"


class DefinitionOrigin(str, Enum):
    USER = "user"
    BUSINESS_CONFIGURATION = "business_configuration"


class CriterionKind(str, Enum):
    OBJECTIVE = "objective"
    HARD_CONSTRAINT = "hard_constraint"


class CriterionOutcome(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"


class HardConstraintOutcome(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


_BOOLEAN_METRICS = frozenset({
    SupplyMetric.STOCKOUT_BEFORE_DELIVERY, SupplyMetric.CAPACITY_EXCEEDED,
})


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _typed(value, expected, name: str) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{name} must be {expected.__name__}")


def _identity(value: ScenarioPositionIdentity) -> None:
    _typed(value, ScenarioPositionIdentity, "position_identity")
    for field in fields(value):
        component = getattr(value, field.name)
        if component is not None:
            _text(component, field.name)


def _ordered(value, expected, name: str) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} must be an ordered tuple or list")
    result = tuple(value)
    for item in result:
        _typed(item, expected, name)
    return result


@dataclass(frozen=True)
class FactualPredicate:
    """A target over exactly one existing comparison metric; no conversions."""

    metric: SupplyMetric
    operator: PredicateOperator
    target: Decimal | bool
    unit: str | None = None

    def __post_init__(self) -> None:
        _typed(self.metric, SupplyMetric, "metric")
        _typed(self.operator, PredicateOperator, "operator")
        if self.metric in _BOOLEAN_METRICS:
            if type(self.target) is not bool:
                raise TypeError("Boolean metrics require an actual bool target")
            if self.operator is not PredicateOperator.EQ or self.unit is not None:
                raise ValueError("Boolean predicates require eq and no unit")
        else:
            if not isinstance(self.target, Decimal):
                raise TypeError("numeric targets must be Decimal")
            if not self.target.is_finite():
                raise ValueError("numeric targets must be finite")
            if self.metric is not SupplyMetric.SAFETY_STOCK_GAP and self.target < 0:
                raise ValueError("quantity targets must be nonnegative")
            unit_spec(self.unit)


@dataclass(frozen=True)
class CriterionDefinitionSource:
    """Explicit definition origin, reference and revision; never inferred."""

    origin: DefinitionOrigin
    reference: str
    revision: str

    def __post_init__(self) -> None:
        _typed(self.origin, DefinitionOrigin, "origin")
        _text(self.reference, "definition reference")
        _text(self.revision, "definition revision")


def _definition(value) -> None:
    _text(value.id, "criterion ID")
    _text(value.label, "criterion label")
    _typed(value.predicate, FactualPredicate, "predicate")
    _typed(value.definition_source, CriterionDefinitionSource, "definition_source")


@dataclass(frozen=True)
class SupplyObjective:
    """An explicitly measurable business target, separate from hard constraints."""

    id: str
    label: str
    predicate: FactualPredicate
    definition_source: CriterionDefinitionSource

    def __post_init__(self) -> None:
        _definition(self)


@dataclass(frozen=True)
class SupplyHardConstraint:
    """An explicitly mandatory condition; no automatic operational defaults."""

    id: str
    label: str
    predicate: FactualPredicate
    definition_source: CriterionDefinitionSource

    def __post_init__(self) -> None:
        _definition(self)


@dataclass(frozen=True)
class ScenarioSetBinding:
    """One caller-identified set revision and its existing position identity."""

    set_reference: str
    set_revision: str
    position_identity: ScenarioPositionIdentity

    def __post_init__(self) -> None:
        _text(self.set_reference, "set_reference")
        _text(self.set_revision, "set_revision")
        _identity(self.position_identity)


@dataclass(frozen=True)
class SupplyCriteria:
    source_set_binding: ScenarioSetBinding
    definition_source: CriterionDefinitionSource
    objectives: tuple[SupplyObjective, ...] = ()
    constraints: tuple[SupplyHardConstraint, ...] = ()

    def __post_init__(self) -> None:
        _typed(self.source_set_binding, ScenarioSetBinding, "source_set_binding")
        _typed(self.definition_source, CriterionDefinitionSource, "definition_source")
        objectives = _ordered(self.objectives, SupplyObjective, "objectives")
        constraints = _ordered(self.constraints, SupplyHardConstraint, "constraints")
        ids = tuple(item.id for item in objectives + constraints)
        if len(set(ids)) != len(ids):
            raise ValueError("criterion IDs must be unique across objectives and constraints")
        object.__setattr__(self, "objectives", objectives)
        object.__setattr__(self, "constraints", constraints)


@dataclass(frozen=True)
class ScenarioFactSource:
    """References to retained source facts; absent projections remain explicit.

    projection_reference is None when no projection is available. A source result
    reference is still required, including for INVALID/MISSING_INPUTS results.
    References preserve traceability but do not prove correspondence to live data.
    """

    source_set_binding: ScenarioSetBinding
    scenario_id: str
    result_reference: str
    projection_reference: str | None
    provenance_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _typed(self.source_set_binding, ScenarioSetBinding, "source_set_binding")
        _text(self.scenario_id, "scenario_id")
        _text(self.result_reference, "result_reference")
        if self.projection_reference is not None:
            _text(self.projection_reference, "projection_reference")
        refs = _ordered(self.provenance_references, str, "provenance_references")
        for reference in refs:
            _text(reference, "provenance reference")
        object.__setattr__(self, "provenance_references", refs)


@dataclass(frozen=True)
class CriterionEvaluation:
    """A recorded outcome, not an evaluator or a physical calculation.

    UNKNOWN can retain a typed observed fact with an incompatible unit. It never
    implies satisfaction. Known outcomes require a compatible available fact.
    """

    criterion_id: str
    criterion_kind: CriterionKind
    outcome: CriterionOutcome
    reason: str
    predicate: FactualPredicate
    definition_source: CriterionDefinitionSource
    source: ScenarioFactSource
    evaluator_version: str
    observed_value: Decimal | bool | None = None
    observed_unit: str | None = None

    def __post_init__(self) -> None:
        _text(self.criterion_id, "criterion_id")
        _typed(self.criterion_kind, CriterionKind, "criterion_kind")
        _typed(self.outcome, CriterionOutcome, "outcome")
        _text(self.reason, "reason")
        _typed(self.predicate, FactualPredicate, "predicate")
        _typed(self.definition_source, CriterionDefinitionSource, "definition_source")
        _typed(self.source, ScenarioFactSource, "source")
        _text(self.evaluator_version, "evaluator_version")
        value = self.observed_value
        if value is None:
            if self.observed_unit is not None:
                raise ValueError("an absent fact cannot carry an observed unit")
        elif self.predicate.metric in _BOOLEAN_METRICS:
            if type(value) is not bool or self.observed_unit is not None:
                raise ValueError("observed Boolean facts require bool and no unit")
        else:
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("observed numeric facts require finite Decimal")
            unit_spec(self.observed_unit)
            if self.predicate.metric is not SupplyMetric.SAFETY_STOCK_GAP and value < 0:
                raise ValueError("observed quantity facts must be nonnegative")
        if value is not None and self.source.projection_reference is None:
            raise ValueError("an observed fact requires a projection reference")
        if self.outcome is not CriterionOutcome.UNKNOWN:
            if value is None or self.observed_unit != self.predicate.unit:
                raise ValueError("known outcomes require an available exact-unit fact")


@dataclass(frozen=True)
class ScenarioCriteriaEvaluation:
    source: ScenarioFactSource
    evaluator_version: str
    evaluations: tuple[CriterionEvaluation, ...] = ()

    @property
    def hard_constraint_outcome(self) -> HardConstraintOutcome:
        """Aggregate recorded hard constraints only; no overall safety claim."""
        outcomes = tuple(item.outcome for item in self.evaluations
                         if item.criterion_kind is CriterionKind.HARD_CONSTRAINT)
        if not outcomes:
            return HardConstraintOutcome.NOT_APPLICABLE
        if CriterionOutcome.VIOLATED in outcomes:
            return HardConstraintOutcome.VIOLATED
        if CriterionOutcome.UNKNOWN in outcomes:
            return HardConstraintOutcome.UNKNOWN
        return HardConstraintOutcome.SATISFIED

    def __post_init__(self) -> None:
        _typed(self.source, ScenarioFactSource, "source")
        _text(self.evaluator_version, "evaluator_version")
        evaluations = _ordered(self.evaluations, CriterionEvaluation, "evaluations")
        if len({item.criterion_id for item in evaluations}) != len(evaluations):
            raise ValueError("scenario criterion IDs must be unique")
        if any(item.source != self.source or item.evaluator_version != self.evaluator_version
               for item in evaluations):
            raise ValueError("criterion evaluations must share their scenario source and version")
        object.__setattr__(self, "evaluations", evaluations)


@dataclass(frozen=True)
class ScenarioSetCriteriaEvaluation:
    criteria: SupplyCriteria
    evaluator_version: str
    scenario_evaluations: tuple[ScenarioCriteriaEvaluation, ...] = ()

    def __post_init__(self) -> None:
        _typed(self.criteria, SupplyCriteria, "criteria")
        _text(self.evaluator_version, "evaluator_version")
        scenarios = _ordered(self.scenario_evaluations, ScenarioCriteriaEvaluation,
                             "scenario_evaluations")
        if len({item.source.scenario_id for item in scenarios}) != len(scenarios):
            raise ValueError("scenario IDs must be unique")
        definitions = {
            item.id: (CriterionKind.OBJECTIVE, item) for item in self.criteria.objectives
        }
        definitions.update({
            item.id: (CriterionKind.HARD_CONSTRAINT, item) for item in self.criteria.constraints
        })
        for scenario in scenarios:
            if (scenario.source.source_set_binding != self.criteria.source_set_binding
                    or scenario.evaluator_version != self.evaluator_version):
                raise ValueError("scenario evaluations must share the criteria set binding and version")
            for evaluation in scenario.evaluations:
                definition = definitions.get(evaluation.criterion_id)
                if definition is None:
                    raise ValueError("evaluation criterion must exist in criteria")
                kind, criterion = definition
                if (evaluation.criterion_kind is not kind
                        or evaluation.predicate != criterion.predicate
                        or evaluation.definition_source != criterion.definition_source):
                    raise ValueError("evaluation must retain its criterion definition snapshot")
        object.__setattr__(self, "scenario_evaluations", scenarios)
