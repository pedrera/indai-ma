"""Deterministic readiness enrichment for existing decision-plan steps."""
from dataclasses import replace
from typing import Literal

from decision_plan import DecisionPlan, DecisionStep

ReadinessStatus = Literal["READY", "PARTIALLY_READY", "BLOCKED"]


def _specialists(supervisor_result):
    return {item.agent_name: item for item in getattr(supervisor_result, "specialist_results", ())}


def _execution(result, name):
    return next((item for item in getattr(result, "tool_executions", ()) if item.get("name") == name), None)


def _missing(result, *names):
    available = set(getattr(result, "missing_inputs", ())) if result is not None else set()
    return tuple(name for name in names if name in available)


def _step_readiness(step: DecisionStep, specialists):
    commercial = getattr(specialists.get("CommercialAgent"), "result", None)
    procurement = getattr(specialists.get("ProcurementAgent"), "result", None)
    risk = getattr(specialists.get("RiskAgent"), "result", None)
    if step.id == "operational-short":
        position = _execution(procurement, "calculate_supply_position")
        exposure = _execution(procurement, "calculate_spot_exposure")
        if position and position.get("result", {}).get("interpretation") == "SHORT":
            missing = _missing(procurement, "spot_price_eur_mwh")
            return ("PARTIALLY_READY", missing) if missing and exposure is None else ("READY", ())
    elif step.id == "operational-long":
        return "READY", ()
    elif step.id == "contractual-take-or-pay":
        return ("READY", ()) if getattr(commercial, "take_or_pay_projection", None) is not None else ("BLOCKED", _missing(commercial, "cumulative_consumption_gwh", "remaining_forecast_consumption_gwh"))
    elif step.id == "contractual-monthly-excess":
        calculations = getattr(commercial, "calculations", ())
        if calculations and calculations[0].result.get("contractual_excess_gwh") is not None:
            return "READY", ()
    elif step.id.startswith("risk-price-stress"):
        scenarios = [item for item in getattr(risk, "stress_scenarios", ()) if getattr(item, "scenario_type", None) == "PRICE"]
        if scenarios and all(item.spot_price_eur_mwh is not None and item.spot_exposure_eur is not None for item in scenarios):
            return "READY", ()
        return "BLOCKED", _missing(risk, "spot_price_eur_mwh")
    elif step.id.startswith("risk-demand-stress"):
        return "READY", ()
    return step.readiness or "READY", step.missing_information


def compose_decision_readiness(supervisor_result, recommendation, decision_plan) -> DecisionPlan | None:
    """Enrich existing steps from structured specialist inputs only."""
    if decision_plan is None or not decision_plan.steps:
        return decision_plan
    specialists = _specialists(supervisor_result)
    enriched = []
    for step in decision_plan.steps:
        readiness, missing = _step_readiness(step, specialists)
        enriched.append(replace(step, readiness=readiness, missing_information=tuple(dict.fromkeys(missing))))
    statuses = [step.readiness for step in enriched]
    plan_status = "BLOCKED" if "BLOCKED" in statuses else "PARTIALLY_READY" if "PARTIALLY_READY" in statuses else "READY"
    return replace(decision_plan, steps=tuple(enriched), readiness=plan_status)
