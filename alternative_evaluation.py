"""Deterministic consequences for the small v1.7 alternative-evaluation MVP."""
import math
from dataclasses import dataclass, replace
from typing import Literal

from decision_plan import DecisionPlan

EvaluationStatus = Literal["EVALUATED", "PARTIALLY_EVALUATED", "NOT_EVALUATED"]


@dataclass(frozen=True)
class AlternativeEvaluationInputs:
    coverage_volume_gwh: float | None = None
    coverage_price_eur_mwh: float | None = None
    revised_remaining_forecast_consumption_gwh: float | None = None

    def __post_init__(self):
        if self.coverage_volume_gwh is not None and (
            not isinstance(self.coverage_volume_gwh, (int, float))
            or isinstance(self.coverage_volume_gwh, bool)
            or not math.isfinite(self.coverage_volume_gwh)
            or self.coverage_volume_gwh < 0
        ):
            raise ValueError("coverage_volume_gwh must be a finite non-negative number")
        if self.coverage_price_eur_mwh is not None and (
            not isinstance(self.coverage_price_eur_mwh, (int, float))
            or isinstance(self.coverage_price_eur_mwh, bool)
            or not math.isfinite(self.coverage_price_eur_mwh)
            or self.coverage_price_eur_mwh < 0
        ):
            raise ValueError("coverage_price_eur_mwh must be a finite non-negative number")
        value = self.revised_remaining_forecast_consumption_gwh
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or value < 0):
            raise ValueError("revised_remaining_forecast_consumption_gwh must be a finite non-negative number")


@dataclass(frozen=True)
class AlternativeOutcome:
    metric: str
    value: float
    unit: str
    origin: str


@dataclass(frozen=True)
class AlternativeEvaluation:
    alternative_id: str
    status: EvaluationStatus
    outcomes: tuple[AlternativeOutcome, ...] = ()
    missing_inputs: tuple[str, ...] = ()
    inputs: AlternativeEvaluationInputs | None = None


def _execution(result, name):
    return next((item for item in getattr(result, "tool_executions", ())
                 if item.get("name") == name), None)


def compose_alternative_evaluations(supervisor_result, decision_plan: DecisionPlan | None,
                                    evaluation_inputs: AlternativeEvaluationInputs | None = None) -> DecisionPlan | None:
    """Attach only the explicitly supported SHORT evaluations to existing alternatives."""
    if decision_plan is None:
        return None
    specialists = {item.agent_name: item for item in getattr(supervisor_result, "specialist_results", ())}
    procurement = specialists.get("ProcurementAgent")
    procurement_result = getattr(procurement, "result", None)
    position = _execution(procurement_result, "calculate_supply_position")
    exposure = _execution(procurement_result, "calculate_spot_exposure")
    position_result = (position or {}).get("result", {})
    exposure_result = (exposure or {}).get("result", {})
    interpretation = position_result.get("interpretation")
    short = position_result.get("short_position_gwh")
    if short is None and interpretation == "SHORT":
        short = abs(position_result.get("position_gwh", 0))
    top_projection = getattr(getattr(specialists.get("CommercialAgent"), "result", None), "take_or_pay_projection", None)
    if interpretation == "LONG":
        long_volume = position_result.get("position_gwh")
        if long_volume is None or float(long_volume) < 0:
            return decision_plan

        def evaluate_long(alternative):
            if alternative.id != "operational-long-maintain":
                return alternative
            return replace(alternative, evaluation=AlternativeEvaluation(
                alternative.id,
                "EVALUATED",
                (AlternativeOutcome("remaining_long_gwh", float(long_volume), "GWh", "structured_result"),),
            ))

        return replace(decision_plan, steps=tuple(
            replace(step, alternatives=tuple(evaluate_long(item) for item in step.alternatives))
            for step in decision_plan.steps
        ))
    if interpretation != "SHORT" and top_projection is None:
        return decision_plan
    exposure_value = exposure_result.get("exposure_eur")
    coverage_price = evaluation_inputs.coverage_price_eur_mwh if evaluation_inputs else None
    revised_forecast = evaluation_inputs.revised_remaining_forecast_consumption_gwh if evaluation_inputs else None
    full_inputs = (AlternativeEvaluationInputs(coverage_price_eur_mwh=coverage_price)
                   if coverage_price is not None else None)
    short_inputs = (
        AlternativeEvaluationInputs(
            coverage_volume_gwh=evaluation_inputs.coverage_volume_gwh,
            coverage_price_eur_mwh=evaluation_inputs.coverage_price_eur_mwh,
        )
        if evaluation_inputs is not None else None
    )

    def evaluate(alternative):
        if alternative.id == "top-review-consumption":
            if top_projection is None:
                return alternative
            if revised_forecast is None:
                return replace(alternative, evaluation=AlternativeEvaluation(
                    alternative.id, "NOT_EVALUATED", (), ("revised_remaining_forecast_consumption_gwh",),
                    AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=None)))
            from take_or_pay import calculate_take_or_pay_projection
            scenario = calculate_take_or_pay_projection(
                top_projection.take_or_pay_minimum_gwh,
                top_projection.cumulative_consumption_gwh,
                revised_forecast,
            )
            return replace(alternative, evaluation=AlternativeEvaluation(
                alternative.id, "EVALUATED",
                (AlternativeOutcome("projected_consumption_gwh", scenario.projected_annual_consumption_gwh, "GWh", "derived"),
                 AlternativeOutcome("projected_top_deficit_gwh", scenario.projected_take_or_pay_deficit_gwh, "GWh", "derived")),
                inputs=AlternativeEvaluationInputs(revised_remaining_forecast_consumption_gwh=revised_forecast)))
        if alternative.id == "top-maintain-forecast":
            if top_projection is None:
                return alternative
            evaluation = AlternativeEvaluation(
                alternative.id,
                "EVALUATED",
                (AlternativeOutcome("projected_consumption_gwh", float(top_projection.projected_annual_consumption_gwh), "GWh", "structured_result"),
                 AlternativeOutcome("projected_top_deficit_gwh", float(top_projection.projected_take_or_pay_deficit_gwh), "GWh", "structured_result")),
            )
            return replace(alternative, evaluation=evaluation)
        if alternative.id == "operational-short-full":
            evaluation = AlternativeEvaluation(
                alternative.id, "EVALUATED",
                (AlternativeOutcome("covered_volume_gwh", float(short), "GWh", "structured_result"),
                 AlternativeOutcome("remaining_short_gwh", 0.0, "GWh", "derived")),
                inputs=full_inputs,
            )
            if coverage_price is not None:
                evaluation = replace(evaluation, outcomes=evaluation.outcomes + (
                    AlternativeOutcome("coverage_cost_eur", float(short) * 1000 * coverage_price, "EUR", "derived"),
                ))
        elif alternative.id == "operational-short-maintain":
            outcomes = (
                AlternativeOutcome("covered_volume_gwh", 0.0, "GWh", "derived"),
                AlternativeOutcome("remaining_short_gwh", float(short), "GWh", "structured_result"),
            )
            if exposure_value is not None:
                outcomes += (AlternativeOutcome("spot_exposure_eur", float(exposure_value), "EUR", "structured_result"),)
            evaluation = AlternativeEvaluation(alternative.id, "EVALUATED", outcomes)
        elif alternative.id == "operational-short-partial":
            coverage = evaluation_inputs.coverage_volume_gwh if evaluation_inputs else None
            if coverage is None:
                evaluation = AlternativeEvaluation(alternative.id, "NOT_EVALUATED", (), ("coverage_volume_gwh",), short_inputs)
            elif coverage > short:
                raise ValueError("coverage_volume_gwh cannot exceed the SHORT volume")
            else:
                evaluation = AlternativeEvaluation(
                    alternative.id, "EVALUATED",
                    (AlternativeOutcome("covered_volume_gwh", float(coverage), "GWh", "scenario_input"),
                     AlternativeOutcome("remaining_short_gwh", float(short - coverage), "GWh", "derived")),
                    inputs=short_inputs,
                )
                if coverage_price is not None:
                    evaluation = replace(evaluation, outcomes=evaluation.outcomes + (
                        AlternativeOutcome("coverage_cost_eur", float(coverage) * 1000 * coverage_price, "EUR", "derived"),
                    ))
        else:
            return alternative
        return replace(alternative, evaluation=evaluation)

    steps = tuple(replace(step, alternatives=tuple(evaluate(item) for item in step.alternatives))
                  for step in decision_plan.steps)
    return replace(decision_plan, steps=steps)
