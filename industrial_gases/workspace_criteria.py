"""Deliberately bounded criterion sentences, not general language interpretation.

Use semicolons/newlines between complete sentences, and between a scenario-set
request and criteria. Mandatory sentences are hard constraints; Objective:/
Objetivo: explicitly changes the definition kind. All clauses must be supported.
"""
from dataclasses import dataclass
from decimal import Decimal
import re

from .objective_constraint_models import (
    CriterionDefinitionSource, DefinitionOrigin, FactualPredicate, PredicateOperator,
    ScenarioSetBinding, SupplyCriteria, SupplyHardConstraint, SupplyMetric, SupplyObjective,
)


@dataclass(frozen=True)
class ExplicitWorkspaceCriterion:
    label: str
    predicate: FactualPredicate
    objective: bool


def is_criteria_request(question: str) -> bool:
    return bool(re.search(
        r"\b(?:must|debe|deben|deber[aá]|objective|objetivo|constraint|restricci[oó]n|"
        r"make the operation safer|choose the best alternative|elige la mejor alternativa|"
        r"optimi[sz]\w*|optimizar|minimi[sz]\w*|minimizar)\b", question, re.I))


def split_criteria_request(question: str) -> tuple[str | None, tuple[ExplicitWorkspaceCriterion, ...]]:
    clauses = tuple(part.strip().rstrip(".").strip() for part in re.split(r"[;\n]+", question) if part.strip())
    scenario = None
    if clauses and re.search(r"\b(?:compare|compara|comparar)\b", clauses[0], re.I):
        scenario, clauses = clauses[0], clauses[1:]
    if not clauses:
        raise ValueError("explicit_criteria_required")
    parsed = []
    seen = set()
    for clause in clauses:
        prefix = re.match(r"^(objective|objetivo|constraint|restricci[oó]n)\s*:\s*", clause, re.I)
        objective = bool(prefix and prefix[1].casefold() in {"objective", "objetivo"})
        text = clause[prefix.end():] if prefix else clause
        numeric = re.fullmatch(
            r"(?:the\s+|la\s+|el\s+)?(safety-stock gap|safety stock gap|brecha de stock de seguridad|"
            r"brecha frente al stock de seguridad|inventory before delivery|inventario antes de la entrega) "
            r"(?:must be at least|debe ser al menos|debe ser de al menos) "
            r"(-?\d+(?:\.\d+)?)\s+(kg|t|L|m3|Nm3|Sm3)", text, re.I)
        if numeric:
            metric = (SupplyMetric.INVENTORY_BEFORE_DELIVERY if numeric[1].casefold().startswith(
                ("inventory", "inventario")) else SupplyMetric.SAFETY_STOCK_GAP)
            # Unit spelling is exact even though sentence matching ignores case.
            predicate = FactualPredicate(metric, PredicateOperator.GE, Decimal(numeric[2]), numeric[3])
        elif re.fullmatch(r"(?:there must be (?:no )?stockout before delivery|"
                          r"(?:no )?debe haber agotamiento antes de la entrega)", text, re.I):
            expected = not bool(re.search(r"\bno\b", text, re.I))
            predicate = FactualPredicate(SupplyMetric.STOCKOUT_BEFORE_DELIVERY, PredicateOperator.EQ, expected)
        elif re.fullmatch(r"(?:capacity must (?:not )?be exceeded|"
                          r"la capacidad (?:no )?debe superarse|(?:no )?debe superarse la capacidad)", text, re.I):
            expected = not bool(re.search(r"\b(?:no|not)\b", text, re.I))
            predicate = FactualPredicate(SupplyMetric.CAPACITY_EXCEEDED, PredicateOperator.EQ, expected)
        else:
            raise ValueError("unsupported_or_incomplete_criterion")
        key = (objective, predicate)
        if key in seen:
            raise ValueError("duplicate_criterion")
        seen.add(key)
        parsed.append(ExplicitWorkspaceCriterion(clause, predicate, objective))
    return scenario, tuple(parsed)


def bind_workspace_criteria(definitions, binding: ScenarioSetBinding) -> SupplyCriteria:
    """Content-address explicit definitions; no random IDs or inferred defaults."""
    from hashlib import sha256
    revision = sha256(repr(tuple(definitions)).encode("utf-8")).hexdigest()
    source = CriterionDefinitionSource(DefinitionOrigin.USER,
                                       f"{binding.set_reference}/explicit-criteria", revision)
    objectives, constraints = [], []
    for index, definition in enumerate(definitions, 1):
        cls = SupplyObjective if definition.objective else SupplyHardConstraint
        item = cls(f"criterion-{index}", definition.label, definition.predicate, source)
        (objectives if definition.objective else constraints).append(item)
    return SupplyCriteria(binding, source, tuple(objectives), tuple(constraints))
