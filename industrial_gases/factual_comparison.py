"""Bounded deterministic comparisons of existing position facts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
import re
from typing import Any

from .portfolio_query import PortfolioQueryResult, WorkspaceScenario
from .portfolio_ui import _format_number
from .supply_scenarios import SupplyAssuranceScenarioSetResult


@dataclass(frozen=True)
class ComparisonValue:
    item_id: str
    display_label: str
    value: Decimal | bool | None
    unit: str | None = None
    comparison_value: Decimal | bool | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ComparisonRelation:
    left_item_id: str
    right_item_id: str
    relation: str


@dataclass(frozen=True)
class FactualComparison:
    """Facts and pairwise relations; deliberately has no score or selected winner."""

    field: str
    semantics: str
    values: tuple[ComparisonValue, ...]
    comparable: bool
    relations: tuple[ComparisonRelation, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class ScenarioFactValue:
    scenario_id: str
    label: str
    value: Decimal | bool | None
    unit: str | None = None
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class ScenarioMetricComparison:
    field: str
    label: str
    values: tuple[ScenarioFactValue, ...]
    comparable: bool
    reason: str | None = None
    comparable_scenario_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ScenarioPositionIdentity:
    """Structured identity shared by all branches in a Supply Assurance comparison."""

    customer_id: str | None
    site_id: str | None
    application_id: str | None
    gas_product_id: str | None
    installation_id: str | None


@dataclass(frozen=True)
class ScenarioSetComparison:
    """Structured comparison over retained, evaluated scenario results."""

    metrics: tuple[ScenarioMetricComparison, ...]
    requested_field: str | None = None
    position_identity: ScenarioPositionIdentity | None = None


_COMPARISON_INTENT = re.compile(
    r"\b(?:compare|comparar|comparaci[oó]n|mayor|menor|m[aá]s|menos|"
    r"greater|less|larger|smaller|higher|lower|most|least|either)\b.{0,60}"
    r"(?:brecha|gap|stockout|agotamiento|capacidad|capacity|inventario|inventory|"
    r"stock de seguridad|safety stock|entrega)", re.IGNORECASE,
)


def comparison_field_for_question(question: str) -> str | None:
    folded = question.casefold()
    if re.search(r"\b(?:stockout|agotamiento|sin stock|sin agotamiento|quedarse sin)\b", folded):
        return "stockout_before_delivery"
    if re.search(r"\b(?:capacidad|capacity)\b", folded) and re.search(r"\b(?:supera|exceeds?|excedid\w*|overflow|over capacity)\b", folded):
        return "capacity_exceeded"
    if re.search(r"\b(?:brecha|gap|shortfall)\b", folded) and re.search(
        r"(?:stock de seguridad|safety.stock|brecha|gap|shortfall)", folded,
    ):
        return "safety_stock_gap_before_delivery"
    if re.search(r"\b(?:inventario|inventory)\b", folded) and re.search(
        r"\b(?:compare|comparison|comparar|compara|mayor|menor|higher|lower|larger|smaller|which|cu[aá]l)\b", folded,
    ):
        return "inventory_immediately_before_delivery"
    return None


def is_factual_comparison_question(question: str) -> bool:
    return comparison_field_for_question(question) is not None and bool(_COMPARISON_INTENT.search(question))


def compare_portfolio_facts(
    query_result: PortfolioQueryResult, field: str, *, spanish: bool = False,
) -> FactualComparison:
    """Compare only complete projections and exact operational units, without conversion."""
    if field not in {
        "safety_stock_gap_before_delivery", "stockout_before_delivery",
        "capacity_exceeded", "inventory_immediately_before_delivery",
    }:
        raise ValueError("unsupported factual comparison field")

    values: list[ComparisonValue] = []
    for match in query_result.matches:
        item = match.item
        projection = item.result.projection
        request = item.request
        site = getattr(request.site, "name", None) or (
            "Ubicación no disponible" if spanish else "Site unavailable"
        )
        site = re.split(r"\s*/\s*", site, maxsplit=1)[0]
        product_id = getattr(request.gas_product, "gas_product_id", None) or item.result.gas_product_id
        product_labels = {
            "medical-oxygen": ("Medical oxygen (O₂)", "Oxígeno medicinal (O₂)"),
            "co2": ("CO₂", "CO₂"),
            "n2": ("N₂", "N₂"),
        }
        product = product_labels.get(product_id, (
            getattr(request.gas_product, "name", None) or "Product unavailable",
            getattr(request.gas_product, "name", None) or "Producto no disponible",
        ))[1 if spanish else 0]
        label = f"{site} · {product}"
        if projection is None:
            values.append(ComparisonValue(
                item.item_id, label, None,
                unavailable_reason=f"evaluation_{item.result.status.casefold()}",
            ))
            continue
        if field == "safety_stock_gap_before_delivery":
            quantity = projection.safety_stock_gap_before_delivery
            raw = quantity.value
            # A negative gap is a shortfall; positive/zero gaps have no shortfall.
            values.append(ComparisonValue(
                item.item_id, label, raw, quantity.unit,
                abs(raw) if raw < 0 else Decimal(0),
            ))
        elif field == "inventory_immediately_before_delivery":
            quantity = projection.inventory_immediately_before_delivery
            values.append(ComparisonValue(item.item_id, label, quantity.value, quantity.unit, quantity.value))
        else:
            value = getattr(projection, field)
            values.append(ComparisonValue(item.item_id, label, value, None, value))

    values_tuple = tuple(values)
    if len(values_tuple) < 2:
        return FactualComparison(field, _semantics(field), values_tuple, False, reason="at_least_two_positions_required")
    unavailable = tuple(value for value in values_tuple if value.value is None)
    if unavailable:
        return FactualComparison(field, _semantics(field), values_tuple, False, reason="projection_unavailable")
    units = {value.unit for value in values_tuple}
    if len(units) > 1:
        return FactualComparison(field, _semantics(field), values_tuple, False, reason="incompatible_operational_units")

    relations = []
    for left, right in combinations(values_tuple, 2):
        if left.comparison_value == right.comparison_value:
            relation = "equal"
        elif left.comparison_value > right.comparison_value:
            relation = "left_greater"
        else:
            relation = "right_greater"
        relations.append(ComparisonRelation(left.item_id, right.item_id, relation))
    return FactualComparison(field, _semantics(field), values_tuple, True, tuple(relations))


def comparison_answer(comparison: FactualComparison, *, spanish: bool) -> str:
    """Produce concise factual wording directly from the structured comparison."""
    if comparison.reason == "at_least_two_positions_required":
        return "Selecciona al menos dos posiciones para compararlas." if spanish else "Select at least two positions to compare."
    if not comparison.values:
        return "No hay posiciones seleccionadas para comparar." if spanish else "No positions are selected for comparison."
    if comparison.reason == "incompatible_operational_units":
        return "No se pueden comparar directamente: las posiciones usan unidades operativas distintas." if spanish else "These cannot be directly compared because the positions use different operational units."
    if comparison.reason == "projection_unavailable":
        return "No todas las posiciones tienen una proyección disponible para esta comparación." if spanish else "A projection is unavailable for at least one position in this comparison."

    if comparison.field == "stockout_before_delivery":
        affected = [value.display_label for value in comparison.values if value.value is True]
        if spanish:
            return ("Agotamiento antes de la entrega previsto para: " + ", ".join(affected)) if affected else "Ninguna de las posiciones seleccionadas prevé agotamiento antes de la entrega."
        return ("Stockout before delivery is projected for: " + ", ".join(affected)) if affected else "No selected position projects stockout before delivery."
    if comparison.field == "capacity_exceeded":
        affected = [value.display_label for value in comparison.values if value.value is True]
        if spanish:
            return ("La capacidad posterior a la entrega se supera en: " + ", ".join(affected)) if affected else "Ninguna de las posiciones seleccionadas supera la capacidad."
        return ("Post-delivery capacity is exceeded for: " + ", ".join(affected)) if affected else "No selected position exceeds capacity."

    unit = comparison.values[0].unit or ""
    rendered = []
    for value in comparison.values:
        if comparison.field == "safety_stock_gap_before_delivery":
            magnitude = value.comparison_value
            rendered.append(
                f"{value.display_label}: brecha {_decimal_text(value.value)} {unit} "
                f"(déficit comparable {_decimal_text(magnitude)} {unit})".strip()
            )
        else:
            rendered.append(f"{value.display_label}: {_decimal_text(value.value)} {unit}".strip())
    if comparison.field == "safety_stock_gap_before_delivery":
        lead = "Brecha de stock de seguridad; entre paréntesis, déficit en valor absoluto:" if spanish else "Safety-stock gap; shortfall magnitude in parentheses:"
    else:
        lead = "Inventario antes de la entrega:" if spanish else "Inventory before delivery:"
    relation_text = ""
    if len(comparison.values) == 2 and comparison.relations:
        relation = comparison.relations[0].relation
        left_label, right_label = (value.display_label for value in comparison.values)
        if comparison.field == "safety_stock_gap_before_delivery":
            if relation == "left_greater":
                relation_text = (f"El déficit en valor absoluto es mayor en {left_label}." if spanish
                                 else f"The shortfall magnitude is larger for {left_label}.")
            elif relation == "right_greater":
                relation_text = (f"El déficit en valor absoluto es mayor en {right_label}." if spanish
                                 else f"The shortfall magnitude is larger for {right_label}.")
            else:
                relation_text = "Ambas posiciones tienen el mismo déficit en valor absoluto." if spanish else "Both positions have the same shortfall magnitude."
        else:
            if relation == "left_greater":
                relation_text = (f"El valor es mayor en {left_label}." if spanish
                                 else f"The value is larger for {left_label}.")
            elif relation == "right_greater":
                relation_text = (f"El valor es mayor en {right_label}." if spanish
                                 else f"The value is larger for {right_label}.")
            else:
                relation_text = "Los valores son iguales." if spanish else "The values are equal."
    return lead + "\n" + "\n".join(rendered) + ("\n" + relation_text if relation_text else "")


_SCENARIO_FIELDS = (
    "consumption_until_delivery",
    "inventory_immediately_before_delivery",
    "safety_stock_gap_before_delivery",
    "stockout_before_delivery",
    "inventory_immediately_after_delivery",
    "required_delivery_volume",
    "capacity_exceeded",
)

_SCENARIO_FIELD_LABELS = {
    "consumption_until_delivery": ("Consumption until delivery", "Consumo hasta la entrega"),
    "inventory_immediately_before_delivery": ("Inventory before delivery", "Inventario antes de la entrega"),
    "safety_stock_gap_before_delivery": ("Safety-stock gap", "Diferencia frente al stock de seguridad"),
    "stockout_before_delivery": ("Stockout before delivery", "Agotamiento antes de la entrega"),
    "inventory_immediately_after_delivery": ("Inventory after delivery", "Inventario después de la entrega"),
    "required_delivery_volume": ("Required delivery volume", "Volumen de entrega requerido"),
    "capacity_exceeded": ("Capacity exceeded", "Capacidad superada"),
}


def compare_scenarios(
    scenarios: tuple[WorkspaceScenario, ...],
    *,
    fields: tuple[str, ...] | None = None,
    spanish: bool = False,
) -> ScenarioSetComparison:
    """Read existing projections only; never derives or converts supply values."""
    entries = tuple(
        (scenario.scenario_id, scenario.label, scenario.result)
        for scenario in scenarios
    )
    return _compare_scenario_entries(entries, fields=fields, spanish=spanish)


def compare_supply_assurance_scenario_set(
    result: SupplyAssuranceScenarioSetResult,
    *,
    fields: tuple[str, ...] | None = None,
    spanish: bool = False,
    scenario_ids: tuple[str, ...] | None = None,
) -> ScenarioSetComparison:
    """Adapt already-evaluated Supply Assurance branches to factual comparison."""
    scenarios = tuple(result.scenario_results)
    if not scenarios:
        raise ValueError("a Supply Assurance scenario set result must contain alternatives")
    if scenario_ids is not None:
        scenario_ids = tuple(dict.fromkeys(scenario_ids))
        available_ids = {scenario.alternative.id for scenario in scenarios}
        if not scenario_ids or any(scenario_id not in available_ids for scenario_id in scenario_ids):
            raise ValueError("requested scenario IDs must identify evaluated alternatives")
        scenarios = tuple(
            scenario for scenario in scenarios if scenario.alternative.id in scenario_ids
        )
        # Keep the caller's explicit reference order, not the original set order.
        by_id = {scenario.alternative.id: scenario for scenario in scenarios}
        scenarios = tuple(by_id[scenario_id] for scenario_id in scenario_ids)
    if any(scenario.baseline_result is not result.baseline_result for scenario in scenarios):
        raise ValueError("scenario branches must retain the scenario-set baseline result")

    baseline = result.baseline_result
    identity = ScenarioPositionIdentity(
        baseline.customer_id,
        baseline.site_id,
        baseline.application_id,
        baseline.gas_product_id,
        baseline.installation_id,
    )
    entries = (
        [("baseline", "Base" if spanish else "Baseline", baseline)]
        if scenario_ids is None else []
    )
    seen_ids = {"baseline"}
    for scenario in scenarios:
        if not scenario.alternative.id or scenario.alternative.id in seen_ids:
            raise ValueError("scenario IDs must be non-empty and unique, including baseline")
        seen_ids.add(scenario.alternative.id)
        alternative_result = scenario.alternative_result
        alternative_identity = ScenarioPositionIdentity(
            alternative_result.customer_id,
            alternative_result.site_id,
            alternative_result.application_id,
            alternative_result.gas_product_id,
            alternative_result.installation_id,
        )
        if alternative_identity != identity:
            raise ValueError(
                f"alternative {scenario.alternative.id!r} belongs to a different portfolio position"
            )
        entries.append((
            scenario.alternative.id,
            scenario.alternative.label,
            alternative_result,
        ))

    comparison = _compare_scenario_entries(entries, fields=fields, spanish=spanish)
    return ScenarioSetComparison(
        comparison.metrics,
        comparison.requested_field,
        position_identity=identity,
    )


def _compare_scenario_entries(
    entries: tuple[tuple[str, str, Any], ...] | list[tuple[str, str, Any]],
    *,
    fields: tuple[str, ...] | None,
    spanish: bool,
) -> ScenarioSetComparison:
    selected_fields = fields or _SCENARIO_FIELDS
    if any(field not in _SCENARIO_FIELDS for field in selected_fields):
        raise ValueError("unsupported scenario comparison field")
    metrics = []
    for field in selected_fields:
        values = []
        for scenario_id, label, result in entries:
            projection = result.projection
            raw = getattr(projection, field) if projection is not None else None
            unit = raw.unit if hasattr(raw, "unit") else None
            value = raw.value if hasattr(raw, "value") else raw
            values.append(ScenarioFactValue(
                scenario_id, label, value, unit,
                None if projection is not None else f"evaluation_{result.status.casefold()}",
            ))
        ids_by_unit: dict[str | None, list[str]] = {}
        for value in values:
            if value.value is not None:
                ids_by_unit.setdefault(value.unit, []).append(value.scenario_id)
        groups = tuple(tuple(ids) for ids in ids_by_unit.values() if len(ids) >= 2)
        units = {value.unit for value in values if value.value is not None}
        missing = any(value.value is None for value in values)
        comparable = not missing and len(units) <= 1
        reason = "projection_unavailable" if missing else (
            "incompatible_operational_units" if len(units) > 1 else None
        )
        metrics.append(ScenarioMetricComparison(
            field, _SCENARIO_FIELD_LABELS[field][1 if spanish else 0], tuple(values),
            comparable, reason, groups,
        ))
    return ScenarioSetComparison(tuple(metrics), fields[0] if fields and len(fields) == 1 else None)


def scenario_comparison_answer(
    comparison: ScenarioSetComparison,
    *,
    question_kind: str,
    spanish: bool,
) -> str:
    """Answer factual scenario questions without preference or recommendation language."""
    metric = comparison.metrics[0] if comparison.metrics else None
    if metric is None:
        return "No hay métricas disponibles para comparar." if spanish else "No metrics are available to compare."
    if not metric.comparable:
        if metric.reason == "incompatible_operational_units":
            return "No se pueden comparar directamente porque las unidades operativas son distintas." if spanish else "These values cannot be compared directly because their operational units differ."
        return "Falta una proyección válida para comparar todos los escenarios." if spanish else "A valid projection is missing for at least one scenario."
    values = metric.values
    if question_kind in {"highest", "lowest"}:
        numeric = [value for value in values if isinstance(value.value, Decimal)]
        if not numeric:
            return "La pregunta requiere una métrica numérica." if spanish else "This question requires a numeric metric."
        extreme = (max if question_kind == "highest" else min)(value.value for value in numeric)
        matches = [value for value in numeric if value.value == extreme]
        labels = ", ".join(value.label for value in matches)
        unit = numeric[0].unit or ""
        if spanish:
            return f"El valor más {('alto' if question_kind == 'highest' else 'bajo')} de {metric.label.casefold()} corresponde a {labels}: {_decimal_text(extreme)} {unit}.".replace("  ", " ")
        return f"The {question_kind} {metric.label.casefold()} is {labels}: {_decimal_text(extreme)} {unit}.".replace("  ", " ")
    if question_kind == "safety_maintained":
        qualified = [value for value in values if isinstance(value.value, Decimal) and value.value >= 0]
        if not qualified:
            return "Ningún escenario mantiene el inventario en el stock de seguridad configurado." if spanish else "No scenario keeps projected inventory at or above configured safety stock."
        rendered = "; ".join(
            f"{value.label}: {'+' if value.value > 0 else ''}{_decimal_text(value.value)} {value.unit or ''}".strip()
            for value in qualified
        )
        return ("Mantiene el stock de seguridad (brecha no negativa): " if spanish else "Meets configured safety stock (non-negative gap): ") + rendered
    if question_kind in {"stockout", "capacity"}:
        active = [value.label for value in values if value.value is True]
        if question_kind == "stockout":
            return (("Hay agotamiento previsto en: " if spanish else "Stockout is projected in: ") + ", ".join(active)) if active else ("No hay agotamiento previsto en los escenarios evaluados." if spanish else "No evaluated scenario projects stockout.")
        return (("Se supera capacidad en: " if spanish else "Capacity is exceeded in: ") + ", ".join(active)) if active else ("Ningún escenario supera la capacidad." if spanish else "No scenario exceeds capacity.")
    if comparison.requested_field is not None:
        rendered = []
        for value in values:
            if isinstance(value.value, Decimal):
                rendered.append(f"{value.label}: {_decimal_text(value.value)} {value.unit or ''}".strip())
            elif isinstance(value.value, bool):
                rendered.append(f"{value.label}: {'Sí' if value.value else 'No'}" if spanish else f"{value.label}: {'Yes' if value.value else 'No'}")
            else:
                rendered.append(f"{value.label}: {value.value}")
        return metric.label + ":\n" + "\n".join(rendered)
    return ("Escenarios comparados: " if spanish else "Compared scenarios: ") + ", ".join(value.label for value in values)


def _semantics(field: str) -> str:
    if field == "safety_stock_gap_before_delivery":
        return "shortfall_magnitude; raw signed gap is retained in each value"
    if field == "inventory_immediately_before_delivery":
        return "direct_numeric_comparison; exact operational unit required"
    return "independent_boolean_facts"


def _decimal_text(value: Any) -> str:
    return _format_number(value) if isinstance(value, Decimal) else str(value)
