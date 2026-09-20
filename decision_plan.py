"""Deterministic decision-plan projection over BusinessRecommendation actions."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DecisionStep:
    id: str
    category: Literal["operational", "contractual", "risk"]
    action: str
    rationale: str | None = None
    supporting_metrics: tuple[tuple[str, str], ...] = ()
    horizon: Literal["current_period", "before_period_close", "monitoring"] | None = None
    decision_state: Literal["review_required", "monitor", "no_action", "blocked"] = "review_required"
    depends_on: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    source_action_id: str | None = None
    source_agent: str | None = None
    readiness: Literal["READY", "PARTIALLY_READY", "BLOCKED"] | None = None


@dataclass(frozen=True)
class DecisionPlan:
    steps: tuple[DecisionStep, ...]
    is_complete: bool
    warnings: tuple[str, ...] = ()
    readiness: Literal["READY", "PARTIALLY_READY", "BLOCKED"] | None = None


def compose_decision_plan(recommendation) -> DecisionPlan | None:
    """Project structured BusinessActions without parsing or recalculating."""
    actions = tuple(getattr(recommendation, "actions", ()))
    if not actions:
        return None
    operational_short = any(action.id == "operational-short" for action in actions)
    steps = []
    for action in actions:
        action_id = action.id
        if action_id is None:
            continue
        if action_id.startswith("operational-short"):
            horizon, state, dependency = "current_period", "review_required", ()
            source_agent = "ProcurementAgent"
        elif action_id.startswith("operational-long"):
            horizon, state, dependency = "current_period", "review_required", ()
            source_agent = "ProcurementAgent"
        elif action_id == "contractual-take-or-pay":
            horizon, state, dependency = "before_period_close", "review_required", ()
            source_agent = "CommercialAgent"
        elif action_id == "contractual-monthly-excess":
            horizon, state, dependency = "current_period", "review_required", ()
            source_agent = "CommercialAgent"
        elif action_id.startswith("risk-price-stress"):
            horizon, state = "monitoring", "monitor"
            dependency = ("operational-short",) if operational_short else ()
            source_agent = "RiskAgent"
        elif action_id.startswith("risk-demand-stress"):
            horizon, state, dependency = "monitoring", "monitor", ()
            source_agent = "RiskAgent"
        else:
            continue
        steps.append(DecisionStep(
            id=action_id,
            category=action.category,
            action=action.action,
            rationale=action.rationale,
            supporting_metrics=action.supporting_metrics,
            horizon=horizon,
            decision_state=state,
            depends_on=dependency,
            source_action_id=action_id,
            source_agent=source_agent,
        ))
    if not steps:
        return None
    return DecisionPlan(tuple(steps), bool(getattr(recommendation, "is_complete", False)),
                        tuple(getattr(recommendation, "warnings", ())))
