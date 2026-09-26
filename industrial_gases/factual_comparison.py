"""Bounded deterministic comparisons of existing position facts."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
import re
from typing import Any

from .portfolio_query import PortfolioQueryResult
from .portfolio_ui import _format_number


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


def _semantics(field: str) -> str:
    if field == "safety_stock_gap_before_delivery":
        return "shortfall_magnitude; raw signed gap is retained in each value"
    if field == "inventory_immediately_before_delivery":
        return "direct_numeric_comparison; exact operational unit required"
    return "independent_boolean_facts"


def _decimal_text(value: Any) -> str:
    return _format_number(value) if isinstance(value, Decimal) else str(value)
