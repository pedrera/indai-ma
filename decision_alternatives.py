"""Deterministic alternatives attached to existing decision steps."""
from dataclasses import replace

from decision_plan import DecisionAlternative, DecisionPlan


def _alternatives(step):
    if step.readiness == "BLOCKED":
        return ()
    source = step.id
    templates = {
        "operational-short": (
            ("operational-short-full", "Evaluar cobertura completa", "Evaluar la cobertura completa del SHORT operativo."),
            ("operational-short-partial", "Evaluar cobertura parcial", "Evaluar una cobertura parcial del SHORT operativo."),
            ("operational-short-maintain", "Mantener exposición", "Mantener temporalmente la exposición al SHORT para su revisión posterior."),
        ),
        "operational-long": (
            ("operational-long-maintain", "Mantener posición", "Mantener la posición LONG actual."),
            ("operational-long-reallocation", "Evaluar venta o reasignación", "Evaluar si existe posibilidad de venta o reasignación."),
            ("operational-long-reduce-future", "Evaluar reducción de aprovisionamiento futuro", "Evaluar si existe posibilidad de reducir el aprovisionamiento futuro."),
        ),
        "contractual-take-or-pay": (
            ("top-review-consumption", "Evaluar incremento de consumo", "Evaluar si existe capacidad real para incrementar el consumo."),
            ("top-review-contract", "Evaluar opciones contractuales", "Evaluar las opciones contractuales disponibles."),
            ("top-maintain-forecast", "Mantener previsión y revisar", "Mantener la previsión y revisar la situación antes del cierre."),
        ),
        "contractual-monthly-excess": (
            ("excess-manage-volume", "Evaluar gestión del exceso", "Evaluar la gestión del volumen excedentario."),
            ("excess-review-contract", "Revisar implicación contractual", "Revisar la implicación contractual del exceso."),
        ),
    }
    if step.id.startswith("risk-price-stress"):
        templates[step.id] = (
            ("price-maintain-exposure", "Mantener exposición", "Mantener la exposición al escenario de precio para su seguimiento."),
            ("price-reduce-exposure", "Evaluar reducción de exposición", "Evaluar una reducción de la exposición."),
            ("price-review-coverage", "Evaluar cobertura", "Evaluar opciones de cobertura disponibles."),
        )
    return tuple(DecisionAlternative(item_id, label, description, source) for item_id, label, description in templates.get(step.id, ()))


def compose_decision_alternatives(decision_plan: DecisionPlan | None) -> DecisionPlan | None:
    """Enrich existing steps; deterministic order is presentation order, not ranking."""
    if decision_plan is None:
        return None
    return replace(decision_plan, steps=tuple(replace(step, alternatives=_alternatives(step)) for step in decision_plan.steps))
