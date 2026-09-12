import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_models import (
    AgentDecision,
    AgentObservation,
    AgentPolicy,
    AgentRunResult,
    AgentStatus,
)
from diagnostics import PerformanceRecorder
from llm_client import GenerationOptions, LLMProvider
from procurement_common import (
    eligible_procurement_tools,
    extract_procurement_context,
    missing_procurement_evidence,
    procurement_tool_skip_reason,
)
from procurement_response import validate_procurement_final_response
from tool_registry import ToolRegistry, action_fingerprint, procurement_tool_registry


class DecisionModel(Protocol):
    def decide(
        self,
        goal: str,
        state: dict[str, Any],
        tools: list[dict[str, Any]],
        timeout_seconds: float | None = None,
    ) -> AgentDecision: ...


class ProviderDecisionModel:
    """Provider-neutral structured decision adapter; it never executes tools."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.call_count = 0

    def decide(
        self,
        goal: str,
        state: dict[str, Any],
        tools: list[dict[str, Any]],
        timeout_seconds: float | None = None,
    ) -> AgentDecision:
        self.call_count += 1
        prompt = _decision_prompt(goal, state, tools)
        response = self.provider.generate_response(
            [{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
            options=GenerationOptions(
                tool_calling_enabled=False,
                max_rounds=1,
                trace_purpose="agent_decision",
                trace_call_number=self.call_count,
                trace_tool_schema_character_count=len(
                    json.dumps(tools, ensure_ascii=False)
                ),
            ),
        )
        decision = parse_agent_decision(response.content)
        if decision.action == "finish" and self.provider.recorder is not None:
            self.provider.recorder.update_latest_stage(
                "llm_call", purpose="agent_final_synthesis"
            )
        return decision


@dataclass
class ProcurementAgentState:
    goal: str
    original_request: str
    known_facts: dict[str, Any]
    unresolved_inputs: list[str]
    observations: list[AgentObservation] = field(default_factory=list)
    tool_executions: list[dict[str, Any]] = field(default_factory=list)
    attempted_actions: set[str] = field(default_factory=set)
    decision_count: int = 0
    invalid_decisions: int = 0

    def model_view(self) -> dict[str, Any]:
        return {
            "original_request": self.original_request,
            "known_facts": self.known_facts,
            "unresolved_inputs": self.unresolved_inputs,
            "observations": [
                {
                    "step": item.step_number,
                    "kind": item.kind,
                    "tool_name": item.tool_name,
                    "arguments": item.arguments,
                    "result": item.result,
                    "message": item.message,
                }
                for item in self.observations
            ],
        }


class ProcurementAgent:
    name = "ProcurementAgent"

    def __init__(
        self,
        decision_model: DecisionModel,
        registry: ToolRegistry | None = None,
        policy: AgentPolicy | None = None,
        recorder: PerformanceRecorder | None = None,
    ) -> None:
        self.decision_model = decision_model
        self.registry = registry or procurement_tool_registry()
        self.policy = policy or AgentPolicy()
        self.recorder = recorder

    def run(
        self, request: str, timeout_seconds: float | None = None
    ) -> AgentRunResult:
        state = self._initial_state(request)
        self._record("agent_start", goal=state.goal, agent_name=self.name)
        context = extract_procurement_context(request)
        tools = eligible_procurement_tools(self.registry, context)

        while state.decision_count < self.policy.max_decisions:
            state.decision_count += 1
            try:
                decision = self.decision_model.decide(
                    state.goal, state.model_view(), tools, timeout_seconds
                )
                self._validate_decision(decision)
            except Exception as error:
                state.invalid_decisions += 1
                self._record(
                    "agent_decision",
                    round=state.decision_count,
                    action="invalid",
                    decision_summary="The model returned an invalid decision.",
                    error=str(error),
                )
                if state.invalid_decisions >= self.policy.max_invalid_decisions:
                    return self._finish(
                        state,
                        AgentStatus.FAILED,
                        "No se pudo obtener una decisión válida del agente.",
                        "invalid_decision_limit",
                    )
                state.observations.append(
                    AgentObservation(
                        state.decision_count, "decision_error", message=str(error)
                    )
                )
                continue

            self._record(
                "agent_decision",
                round=state.decision_count,
                action=decision.action,
                tool_name=decision.tool_name,
                decision_summary=decision.decision_summary,
            )
            if decision.action == "finish":
                missing_evidence = missing_procurement_evidence(
                    context, state.tool_executions
                )
                if missing_evidence:
                    observation = AgentObservation(
                        state.decision_count,
                        "incomplete_analysis",
                        message=(
                            "Cannot finish; required deterministic observations "
                            "are missing: " + ", ".join(missing_evidence)
                        ),
                    )
                    state.observations.append(observation)
                    self._record_observation(observation)
                    if state.decision_count >= self.policy.max_decisions:
                        return self._finish(
                            state,
                            AgentStatus.FAILED,
                            "El agente no completó todos los cálculos solicitados.",
                            "incomplete_evidence",
                        )
                    continue
                validated_response = validate_procurement_final_response(
                    decision.answer or "", state.tool_executions
                )
                self._record(
                    "final_response_validation",
                    round=state.decision_count,
                    validation_status=(
                        "accepted" if validated_response.is_valid else "fallback"
                    ),
                    deterministic_fallback=validated_response.fallback_used,
                    validation_reasons=list(validated_response.reasons),
                )
                return self._finish(
                    state,
                    AgentStatus.COMPLETED,
                    validated_response.content,
                    "final_answer",
                )
            if decision.action == "request_information":
                content = decision.question or (
                    "Faltan datos: " + ", ".join(decision.missing_fields)
                )
                return self._finish(
                    state, AgentStatus.NEEDS_INPUT, content, "missing_information"
                )
            skip_reason = procurement_tool_skip_reason(
                decision.tool_name or "", state.tool_executions
            )
            if skip_reason:
                observation = AgentObservation(
                    state.decision_count,
                    "tool_skipped",
                    decision.tool_name,
                    decision.arguments,
                    message=skip_reason,
                )
                state.observations.append(observation)
                self._record_observation(observation)
                continue
            if len(state.tool_executions) >= self.policy.max_tool_calls:
                return self._finish(
                    state,
                    AgentStatus.BUDGET_EXHAUSTED,
                    "El agente alcanzó el límite de herramientas sin completar el análisis.",
                    "tool_budget_exhausted",
                )

            fingerprint = action_fingerprint(
                decision.tool_name or "", decision.arguments
            )
            if fingerprint in state.attempted_actions:
                observation = AgentObservation(
                    state.decision_count,
                    "duplicate_tool_call",
                    decision.tool_name,
                    decision.arguments,
                    message="La misma herramienta ya se ejecutó con estos argumentos.",
                )
                state.observations.append(observation)
                self._record_observation(observation)
                continue
            state.attempted_actions.add(fingerprint)

            event = self._start_tool(state.decision_count, decision)
            try:
                execution = self.registry.execute(
                    decision.tool_name or "", decision.arguments
                )
            except Exception as error:
                if self.recorder and event:
                    self.recorder.fail_stage(
                        event, tool_arguments=decision.arguments, error=str(error)
                    )
                observation = AgentObservation(
                    state.decision_count,
                    "tool_error",
                    decision.tool_name,
                    decision.arguments,
                    message=str(error),
                )
                state.observations.append(observation)
                self._record_observation(observation)
                continue
            if self.recorder and event:
                self.recorder.complete_stage(
                    event,
                    tool_call_count=1,
                    tool_seconds=execution.elapsed_seconds,
                    tool_arguments=execution.arguments,
                    tool_result=execution.result,
                )
            state.tool_executions.append(execution.as_dict())
            observation = AgentObservation(
                state.decision_count,
                "tool_result",
                execution.name,
                execution.arguments,
                execution.result,
            )
            state.observations.append(observation)
            self._record_observation(observation)

        return self._finish(
            state,
            AgentStatus.BUDGET_EXHAUSTED,
            "El agente alcanzó el límite de decisiones sin completar el análisis.",
            "decision_budget_exhausted",
        )

    def _initial_state(self, request: str) -> ProcurementAgentState:
        context = extract_procurement_context(request)
        facts = context.facts()
        return ProcurementAgentState(
            goal=(
                "Analizar la posición de aprovisionamiento de gas y determinar "
                "qué herramientas son necesarias para evaluar cobertura y exposición."
            ),
            original_request=request.strip(),
            known_facts=facts,
            unresolved_inputs=list(context.ambiguities),
        )

    @staticmethod
    def _validate_decision(decision: AgentDecision) -> None:
        if decision.action not in {"call_tool", "finish", "request_information"}:
            raise ValueError("Unknown agent action.")
        if not decision.decision_summary.strip():
            raise ValueError("decision_summary is required.")
        if decision.action == "call_tool" and not decision.tool_name:
            raise ValueError("tool_name is required for call_tool.")
        if decision.action == "finish" and not (decision.answer or "").strip():
            raise ValueError("answer is required for finish.")
        if decision.action == "request_information" and not (
            decision.question or decision.missing_fields
        ):
            raise ValueError("Missing-information details are required.")

    def _start_tool(self, step: int, decision: AgentDecision) -> str | None:
        return (
            self.recorder.start_stage(
                "tool_execution",
                round=step,
                agent_name=self.name,
                tool_name=decision.tool_name,
            )
            if self.recorder
            else None
        )

    def _record_observation(self, observation: AgentObservation) -> None:
        self._record(
            "agent_observation",
            round=observation.step_number,
            observation_type=observation.kind,
            tool_name=observation.tool_name,
            observation_result=observation.result,
            message=observation.message,
        )

    def _record(self, stage: str, round: int | None = None, **metadata: Any) -> None:
        if self.recorder:
            self.recorder.record_stage(stage, round=round, **metadata)

    def _finish(
        self,
        state: ProcurementAgentState,
        status: AgentStatus,
        content: str,
        reason: str,
    ) -> AgentRunResult:
        self._record(
            "agent_final",
            agent_name=self.name,
            agent_status=status.value,
            termination_reason=reason,
            response_chars=len(content),
        )
        return AgentRunResult(
            status,
            content,
            tuple(state.tool_executions),
            tuple(state.observations),
            state.decision_count,
            reason,
        )


def parse_agent_decision(content: str) -> AgentDecision:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("Agent decision is not valid JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("Agent decision must be a JSON object.")
    arguments = value.get("arguments", {})
    missing_fields = value.get("missing_fields", [])
    if not isinstance(arguments, dict) or not isinstance(missing_fields, list):
        raise ValueError("Agent decision fields have invalid types.")
    return AgentDecision(
        action=str(value.get("action", "")),
        decision_summary=str(value.get("decision_summary", "")),
        tool_name=value.get("tool_name"),
        arguments=arguments,
        answer=value.get("answer"),
        missing_fields=tuple(str(item) for item in missing_fields),
        question=value.get("question"),
    )


def _decision_prompt(
    goal: str, state: dict[str, Any], tools: list[dict[str, Any]]
) -> str:
    return f"""
Eres ProcurementAgent. Persigue el objetivo usando solo el estado y las
herramientas deterministas disponibles. Decide una sola acción. No hagas tú
los cálculos cubiertos por herramientas. Los resultados observados son
autoritativos. No reveles razonamiento interno: decision_summary debe ser una
justificación operativa breve. Al finalizar, responde sólo al objetivo y a lo
solicitado en original_request. No solicites ni menciones datos opcionales que
el usuario no haya pedido. Indica que falta un dato únicamente cuando impida
completar el análisis solicitado. No inventes escenarios de demanda.

Devuelve exclusivamente un objeto JSON con uno de estos formatos:
{{"action":"call_tool","tool_name":"...","arguments":{{...}},"decision_summary":"..."}}
{{"action":"finish","answer":"...","decision_summary":"..."}}
{{"action":"request_information","missing_fields":["..."],"question":"...","decision_summary":"..."}}

OBJETIVO:
{goal}

ESTADO:
{json.dumps(state, ensure_ascii=False, indent=2)}

HERRAMIENTAS PERMITIDAS:
{json.dumps(tools, ensure_ascii=False, indent=2)}
""".strip()
