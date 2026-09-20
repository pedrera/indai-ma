"""Deterministic consequences for the small v1.7 alternative-evaluation MVP."""
import math
from dataclasses import dataclass, replace
from typing import Literal

from decision_plan import DecisionPlan

EvaluationStatus = Literal["EVALUATED", "PARTIALLY_EVALUATED", "NOT_EVALUATED"]


@dataclass(frozen=True)
class AlternativeEvaluationInputs:
    coverage_volume_gwh: float | None = None

    def __post_init__(self):
        if self.coverage_volume_gwh is not None and (
            not isinstance(self.coverage_volume_gwh, (int, float))
            or isinstance(self.coverage_volume_gwh, bool)
            or not math.isfinite(self.coverage_volume_gwh)
            or self.coverage_volume_gwh < 0
        ):
            raise ValueError("coverage_volume_gwh must be a finite non-negative number")


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
    if interpretation != "SHORT" or short is None:
        return decision_plan
    exposure_value = exposure_result.get("exposure_eur")

    def evaluate(alternative):
        if alternative.id == "operational-short-full":
            evaluation = AlternativeEvaluation(
                alternative.id, "EVALUATED",
                (AlternativeOutcome("covered_volume_gwh", float(short), "GWh", "structured_result"),
                 AlternativeOutcome("remaining_short_gwh", 0.0, "GWh", "derived")),
            )
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
                evaluation = AlternativeEvaluation(alternative.id, "NOT_EVALUATED", (), ("coverage_volume_gwh",))
            elif coverage > short:
                raise ValueError("coverage_volume_gwh cannot exceed the SHORT volume")
            else:
                evaluation = AlternativeEvaluation(
                    alternative.id, "EVALUATED",
                    (AlternativeOutcome("covered_volume_gwh", float(coverage), "GWh", "scenario_input"),
                     AlternativeOutcome("remaining_short_gwh", float(short - coverage), "GWh", "derived")),
                )
        else:
            return alternative
        return replace(alternative, evaluation=evaluation)

    steps = tuple(replace(step, alternatives=tuple(evaluate(item) for item in step.alternatives))
                  for step in decision_plan.steps)
    return replace(decision_plan, steps=steps)
