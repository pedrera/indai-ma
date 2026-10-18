"""Bounded explicit selection authorization; no natural-language inference."""
from hashlib import sha256
import re

from .decision_engine import ENGINE_VERSION
from .decision_engine_models import (
    DecisionScopeMode, DecisionSelectionRule, SupplyDecisionPolicy, SupplyDecisionReason,
)
from .objective_constraint_evaluation import EVALUATOR_VERSION
from .objective_constraint_models import CriterionDefinitionSource, DefinitionOrigin


def selection_scope(question: str) -> DecisionScopeMode | None:
    text = re.sub(r"\s+", " ", question.strip()).removesuffix(".").casefold()
    if text in {
        "aplica la política de selección estricta a las alternativas",
        "selecciona la única alternativa que cumpla todas las restricciones",
        "apply strict selection to the alternatives",
        "select the unique alternative satisfying all hard constraints",
    }:
        return DecisionScopeMode.EXPLICIT_ALTERNATIVES
    if text in {
        "aplica la política de selección estricta incluyendo el escenario base",
        "apply strict selection including the baseline scenario",
    }:
        return DecisionScopeMode.ALL_SCENARIOS
    return None


def is_selection_request(question: str) -> bool:
    """Recognize attempts at this bounded command so suffixes are never ignored."""
    return bool(re.match(
        r"^(?:aplica la pol[ií]tica de selecci[oó]n|selecciona la [uú]nica alternativa|"
        r"apply strict selection|select the unique alternative)\b", question.strip(), re.I))


def explicit_selection_policy(question, binding, mode):
    revision = sha256(question.strip().encode("utf-8")).hexdigest()
    origin = CriterionDefinitionSource(DefinitionOrigin.USER,
        f"{binding.set_reference}/explicit-selection", revision)
    return SupplyDecisionPolicy("workspace-unique-hard-compliance", "1",
        DecisionSelectionRule.UNIQUE_HARD_CONSTRAINT_COMPLIANT, origin, mode)


def selection_request_issue(question):
    """Distinguish a supported command with invalid scope from another policy."""
    if re.match(r"^(?:aplica la pol[ií]tica de selecci[oó]n estricta\b|"
                r"apply strict selection\b|selecciona la [uú]nica alternativa\b|"
                r"select the unique alternative\b)", question.strip(), re.I):
        return "ambiguous_or_unsupported_decision_scope"
    return "unsupported_decision_policy"


def decision_reuse_key(state, policy):
    # Session-local object identities preserve v1.18's baseline-reference check:
    # an equal-valued replacement is not the original verified retained source.
    dependencies = (id(state.scenario_set), id(state.result),
                    state.source_set_binding, state.scenario_set, state.result,
                    state.criteria, state.criteria_evaluation, policy,
                    ENGINE_VERSION, EVALUATOR_VERSION)
    return sha256(repr(dependencies).encode("utf-8")).hexdigest()


def selection_explanation(evaluation, scenario_set, *, spanish):
    """Format recorded reasons only; eligibility belongs exclusively to the engine."""
    labels = {"baseline": "Base" if spanish else "Baseline"}
    labels.update((item.id, item.label) for item in scenario_set.alternatives)
    participants = ", ".join(labels[id] for id in evaluation.candidate_ids)
    reasons = {
        SupplyDecisionReason.UNIQUE_ELIGIBLE_CANDIDATE: (
            "Existe una única candidata elegible y las demás son no elegibles.",
            "Exactly one candidate is eligible and all others are ineligible."),
        SupplyDecisionReason.NO_HARD_CONSTRAINTS: (
            "No hay restricciones obligatorias explícitas para establecer elegibilidad.",
            "There are no explicit hard constraints to establish eligibility."),
        SupplyDecisionReason.NO_ELIGIBLE_CANDIDATE: (
            "Ninguna candidata cumple todas las restricciones obligatorias.",
            "No candidate satisfies all hard constraints."),
        SupplyDecisionReason.MULTIPLE_ELIGIBLE_CANDIDATES: (
            "Varias candidatas cumplen las restricciones; esta política no desempata.",
            "Multiple candidates satisfy the constraints; this policy does not break ties."),
        SupplyDecisionReason.ELIGIBILITY_UNRESOLVED: (
            "Hay candidatas cuya elegibilidad no puede determinarse por hechos UNKNOWN.",
            "Some candidates have unresolved eligibility because facts are UNKNOWN."),
    }
    reason = reasons[evaluation.reasons[0]][0 if spanish else 1]
    text = (f"Política UNIQUE_HARD_CONSTRAINT_COMPLIANT; escenarios: {participants}. "
            f"Estado: {evaluation.status.value}. {reason}" if spanish else
            f"Policy UNIQUE_HARD_CONSTRAINT_COMPLIANT; scenarios: {participants}. "
            f"Status: {evaluation.status.value}. {reason}")
    if evaluation.selected_scenario_id is not None:
        label = labels[evaluation.selected_scenario_id]
        text += (f" {label}: seleccionada bajo la política de cumplimiento único de restricciones explícitas."
                 if spanish else f" {label}: selected under the unique explicit hard-constraint compliance policy.")
    return text
