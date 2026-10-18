"""Pure evaluation of explicit criteria over verified retained scenario facts.

Partial scenario comparisons are rejected, never silently treated as complete.
Missing metrics remain UNKNOWN. No service, physical calculator or provider is
called. The caller owns authoritative set-reference/revision assignment: this
in-memory boundary cannot discover a newer revision in an external store.
"""
from dataclasses import dataclass
from decimal import Decimal
from operator import lt, le, eq, ge, gt
from urllib.parse import quote

from .factual_comparison import ScenarioPositionIdentity, ScenarioSetComparison
from .models import SupplyProjection
from .objective_constraint_models import (
    CriterionEvaluation, CriterionKind, CriterionOutcome, PredicateOperator,
    ScenarioCriteriaEvaluation, ScenarioFactSource, ScenarioSetBinding,
    ScenarioSetCriteriaEvaluation, SupplyCriteria, SupplyMetric,
)
from .service import SupplyAssuranceResult
from .supply_scenarios import SupplyAssuranceScenarioSet, SupplyAssuranceScenarioSetResult
from .units import unit_spec


EVALUATOR_VERSION = "supply-criteria-v1"
_OPERATORS = {PredicateOperator.LT: lt, PredicateOperator.LE: le,
              PredicateOperator.EQ: eq, PredicateOperator.GE: ge,
              PredicateOperator.GT: gt}
_BOOLEAN_FIELDS = {SupplyMetric.STOCKOUT_BEFORE_DELIVERY.value,
                   SupplyMetric.CAPACITY_EXCEEDED.value}


class SourceBindingError(ValueError):
    """Global source integrity failure; no compliance result can be emitted."""


@dataclass(frozen=True)
class RetainedScenarioSetSource:
    """Authoritative caller binding to the original ordered set and its results."""

    binding: ScenarioSetBinding
    scenario_set: SupplyAssuranceScenarioSet
    result: SupplyAssuranceScenarioSetResult

    def __post_init__(self) -> None:
        for value, expected in ((self.binding, ScenarioSetBinding),
                                (self.scenario_set, SupplyAssuranceScenarioSet),
                                (self.result, SupplyAssuranceScenarioSetResult)):
            if not isinstance(value, expected):
                raise TypeError(f"retained source requires {expected.__name__}")


def _result_identity(result) -> ScenarioPositionIdentity:
    return ScenarioPositionIdentity(result.customer_id, result.site_id,
                                    result.application_id, result.gas_product_id,
                                    result.installation_id)


def _request_identity(request) -> ScenarioPositionIdentity:
    return ScenarioPositionIdentity(request.customer_id, getattr(request.site, "site_id", None),
                                    getattr(request.application, "application_id", None),
                                    getattr(request.gas_product, "gas_product_id", None),
                                    getattr(request.installation, "installation_id", None))


def _fact(projection, field):
    raw = getattr(projection, field, None) if projection is not None else None
    return (raw.value, raw.unit) if hasattr(raw, "value") and hasattr(raw, "unit") else (raw, None)


def _same_value(left, right) -> bool:
    # bool/Decimal equality is insufficient: False == Decimal(0).
    if type(left) is not type(right):
        return False
    if isinstance(left, Decimal) and not left.is_finite():
        return str(left) == str(right)
    return left == right


def _validate_source(criteria, comparison, source):
    if not isinstance(criteria, SupplyCriteria) or not isinstance(comparison, ScenarioSetComparison):
        raise TypeError("criteria and comparison must use existing domain contracts")
    if not isinstance(source, RetainedScenarioSetSource):
        raise TypeError("source_binding must be RetainedScenarioSetSource")
    binding = source.binding
    if criteria.source_set_binding != binding:
        raise SourceBindingError("criteria_set_binding_mismatch")
    identity = binding.position_identity
    if any(value is None for value in (
        identity.customer_id, identity.site_id, identity.application_id,
        identity.gas_product_id, identity.installation_id,
    )):
        raise SourceBindingError("focused_position_identity_required")
    if comparison.position_identity != identity:
        raise SourceBindingError("comparison_position_identity_mismatch")
    scenario_set, result = source.scenario_set, source.result
    if (len(result.scenario_results) != len(scenario_set.alternatives)
            or tuple(branch.alternative for branch in result.scenario_results) != scenario_set.alternatives):
        raise SourceBindingError("scenario_set_result_order_or_definition_mismatch")
    entries = [("baseline", scenario_set.baseline_request, result.baseline_result)]
    for branch in result.scenario_results:
        if branch.baseline_result is not result.baseline_result:
            raise SourceBindingError("baseline_result_reference_mismatch")
        entries.append((branch.alternative.id, branch.alternative.alternative_request,
                        branch.alternative_result))
    ids = tuple(entry[0] for entry in entries)
    if len(set(ids)) != len(ids):
        raise SourceBindingError("duplicate_or_reserved_scenario_id")
    for _, request, branch in entries:
        if not isinstance(branch, SupplyAssuranceResult):
            raise SourceBindingError("invalid_result_contract")
        if _request_identity(request) != identity or _result_identity(branch) != identity:
            raise SourceBindingError("source_position_identity_mismatch")
        projection = branch.projection
        if projection is not None:
            if not isinstance(projection, SupplyProjection):
                raise SourceBindingError("invalid_projection_contract")
            if (projection.installation_id != identity.installation_id
                    or projection.gas_product_id != identity.gas_product_id
                    or projection.reference_time != request.reference_time):
                raise SourceBindingError("projection_identity_or_reference_time_mismatch")
    metrics = {}
    for metric in comparison.metrics:
        if metric.field not in {field.value for field in SupplyMetric} or metric.field in metrics:
            raise SourceBindingError("unsupported_or_duplicate_comparison_metric")
        if tuple(value.scenario_id for value in metric.values) != ids:
            raise SourceBindingError("incomplete_or_reordered_scenario_comparison")
        for value, (_, _, branch) in zip(metric.values, entries):
            observed, unit = _fact(branch.projection, metric.field)
            reason = None if branch.projection is not None else f"evaluation_{branch.status.casefold()}"
            if (not _same_value(value.value, observed) or value.unit != unit
                    or value.unavailable_reason != reason):
                raise SourceBindingError("comparison_fact_mismatch")
        metrics[metric.field] = metric
    return entries, metrics


def _source_reference(binding, scenario_id, result):
    prefix = "/".join(quote(value, safe="") for value in
                      (binding.set_reference, binding.set_revision, scenario_id))
    projection = result.projection
    return ScenarioFactSource(
        binding, scenario_id, f"{prefix}/result",
        f"{prefix}/projection" if projection is not None else None,
        tuple(f"{prefix}/projection/provenance/{index}" for index, _ in
              enumerate(projection.provenance)) if projection is not None else (),
    )


def _evaluate(criterion, kind, metric, branch, source):
    predicate = criterion.predicate
    field = predicate.metric.value
    observed, unit = None, None
    reason = None
    if branch.status != "COMPLETED":
        reason = {"INVALID": "source_invalid", "MISSING_INPUTS": "source_missing_inputs"}.get(
            branch.status, "source_not_completed")
    elif branch.projection is None:
        reason = "projection_unavailable"
    elif metric is None:
        reason = "metric_unavailable"
    else:
        observed, unit = _fact(branch.projection, field)
        if observed is None:
            reason = "fact_unavailable"
        elif field in _BOOLEAN_FIELDS:
            if type(observed) is not bool or unit is not None:
                reason = "invalid_fact_type"
        elif not isinstance(observed, Decimal) or not observed.is_finite():
            reason = "invalid_fact_type"
        else:
            try:
                unit_spec(unit)
            except ValueError:
                reason = "invalid_fact_unit"
            if predicate.metric is not SupplyMetric.SAFETY_STOCK_GAP and observed < 0:
                reason = "invalid_fact_value"
        if reason is not None:
            # TASK 1 records only well-typed observed facts; malformed source data
            # remains referenced through the original projection, never coerced.
            observed, unit = None, None
        else:
            provenance = tuple(item for item in branch.projection.provenance
                               if getattr(item, "field", None) == field)
            if not provenance:
                reason = "provenance_unavailable"
            elif any(not _same_value(_fact_value(item.value), observed)
                     or _fact_unit(item.value) != unit for item in provenance):
                reason = "provenance_fact_mismatch"
            elif unit != predicate.unit:
                reason = "incompatible_operational_units"
    outcome = CriterionOutcome.UNKNOWN
    if reason is None:
        satisfied = _OPERATORS[predicate.operator](observed, predicate.target)
        outcome = CriterionOutcome.SATISFIED if satisfied else CriterionOutcome.VIOLATED
        reason = "predicate_satisfied" if satisfied else "predicate_violated"
    return CriterionEvaluation(criterion.id, kind, outcome, reason, predicate,
                               criterion.definition_source, source, EVALUATOR_VERSION,
                               observed, unit)


def _fact_value(value):
    return value.value if hasattr(value, "value") else value


def _fact_unit(value):
    return value.unit if hasattr(value, "unit") else None


def evaluate_supply_criteria(
    criteria: SupplyCriteria,
    comparison: ScenarioSetComparison,
    source_binding: RetainedScenarioSetSource,
) -> ScenarioSetCriteriaEvaluation:
    """Verify global binding first, then evaluate every retained branch in order.

    Labels/comparability metadata are never used as fact or identity authority.
    SourceBindingError aborts global mismatches; individual unavailable facts yield
    UNKNOWN. Original result/projection/provenance objects are never modified.
    """
    entries, metrics = _validate_source(criteria, comparison, source_binding)
    scenarios = []
    definitions = tuple((item, CriterionKind.OBJECTIVE) for item in criteria.objectives) + tuple(
        (item, CriterionKind.HARD_CONSTRAINT) for item in criteria.constraints)
    for scenario_id, _, branch in entries:
        source = _source_reference(source_binding.binding, scenario_id, branch)
        evaluations = tuple(_evaluate(item, kind, metrics.get(item.predicate.metric.value),
                                      branch, source) for item, kind in definitions)
        scenarios.append(ScenarioCriteriaEvaluation(source, EVALUATOR_VERSION, evaluations))
    return ScenarioSetCriteriaEvaluation(criteria, EVALUATOR_VERSION, tuple(scenarios))
