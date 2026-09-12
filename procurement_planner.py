import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_models import AgentRunResult, AgentStatus
from diagnostics import PerformanceRecorder
from llm_client import GenerationOptions, LLMProvider
from procurement_common import (
    PROCUREMENT_GOAL,
    ProcurementContext,
    eligible_procurement_tools,
    execute_procurement_tool,
    extract_procurement_context,
    procurement_tool_skip_reason,
    synthesis_prompt,
)
from tool_registry import ToolRegistry, action_fingerprint, procurement_tool_registry
from procurement_response import validate_procurement_final_response


class PlannedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any]


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[PlannedAction] = Field(default_factory=list)
    final_synthesis_required: bool = True


class PlannerModel(Protocol):
    def create_plan(
        self,
        goal: str,
        context: ProcurementContext,
        tools: list[dict[str, Any]],
        timeout_seconds: float | None,
    ) -> AgentPlan: ...

    def synthesize(
        self,
        request: str,
        executions: list[dict[str, Any]],
        timeout_seconds: float | None,
    ) -> str: ...


class ProviderPlannerModel:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def create_plan(
        self,
        goal: str,
        context: ProcurementContext,
        tools: list[dict[str, Any]],
        timeout_seconds: float | None,
    ) -> AgentPlan:
        prompt = _plan_prompt(goal, context, tools)
        response = self.provider.generate_response(
            [{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
            options=GenerationOptions(
                tool_calling_enabled=False,
                max_rounds=1,
                trace_purpose="agent_plan",
                trace_call_number=1,
                trace_tool_schema_character_count=len(
                    json.dumps(tools, ensure_ascii=False)
                ),
            ),
        )
        return parse_agent_plan(response.content)

    def synthesize(
        self,
        request: str,
        executions: list[dict[str, Any]],
        timeout_seconds: float | None,
    ) -> str:
        prompt = synthesis_prompt("ProcurementAgentPlanner", request, executions)
        return self.provider.generate_response(
            [{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
            options=GenerationOptions(
                tool_calling_enabled=False,
                max_rounds=1,
                trace_purpose="agent_final_synthesis",
                trace_call_number=2,
            ),
        ).content


class ProcurementAgentPlanner:
    name = "ProcurementAgentPlanner"

    def __init__(
        self,
        model: PlannerModel,
        registry: ToolRegistry | None = None,
        recorder: PerformanceRecorder | None = None,
        max_planned_actions: int = 5,
    ) -> None:
        self.model = model
        self.registry = registry or procurement_tool_registry()
        self.recorder = recorder
        self.max_planned_actions = max_planned_actions

    def run(self, request: str, timeout_seconds: float | None = None) -> AgentRunResult:
        context = extract_procurement_context(request)
        self._record("agent_start", agent_name=self.name, goal=PROCUREMENT_GOAL)
        tools = eligible_procurement_tools(self.registry, context)
        try:
            plan = self.model.create_plan(
                PROCUREMENT_GOAL, context, tools, timeout_seconds
            )
            self._record(
                "agent_plan_created",
                agent_name=self.name,
                plan_actions=[action.model_dump() for action in plan.actions],
                planned_action_count=len(plan.actions),
            )
            validated = self.validate_plan(plan, context)
            self._record(
                "plan_validation",
                agent_name=self.name,
                validated_action_count=len(validated),
            )
        except Exception as error:
            self._record(
                "plan_validation",
                agent_name=self.name,
                validation_error=str(error),
                status="failed",
            )
            return self._finish(
                AgentStatus.FAILED,
                f"El plan de aprovisionamiento no es válido: {error}",
                [],
                [],
                "plan_validation_failed",
            )

        executions: list[dict[str, Any]] = []
        observations = []
        try:
            for index, action in enumerate(validated, 1):
                skip_reason = procurement_tool_skip_reason(
                    action.tool_name, executions
                )
                if skip_reason:
                    self._record(
                        "agent_observation",
                        round=index,
                        observation_type="tool_skipped",
                        tool_name=action.tool_name,
                        message=skip_reason,
                    )
                    continue
                execution, observation = execute_procurement_tool(
                    self.registry,
                    self.recorder,
                    action.tool_name,
                    action.arguments,
                    index,
                    self.name,
                )
                executions.append(execution)
                observations.append(observation)
        except Exception as error:
            return self._finish(
                AgentStatus.FAILED,
                f"No se pudo ejecutar el plan: {error}",
                executions,
                observations,
                "plan_execution_failed",
            )

        try:
            self.validate_execution_evidence(context, executions)
            self._record(
                "execution_evidence_validation",
                agent_name=self.name,
                status="completed",
            )
        except Exception as error:
            self._record(
                "execution_evidence_validation",
                agent_name=self.name,
                validation_error=str(error),
                status="failed",
            )
            return self._finish(
                AgentStatus.FAILED,
                f"El plan no produjo todas las observaciones necesarias: {error}",
                executions,
                observations,
                "incomplete_plan",
            )

        try:
            answer = self.model.synthesize(request, executions, timeout_seconds)
        except Exception as error:
            return self._finish(
                AgentStatus.FAILED,
                f"No se pudo sintetizar el resultado: {error}",
                executions,
                observations,
                "final_synthesis_failed",
            )
        validated_response = validate_procurement_final_response(answer, executions)
        self._record(
            "final_response_validation",
            agent_name=self.name,
            validation_status=(
                "accepted" if validated_response.is_valid else "fallback"
            ),
            deterministic_fallback=validated_response.fallback_used,
            validation_reasons=list(validated_response.reasons),
        )
        return self._finish(
            AgentStatus.COMPLETED,
            validated_response.content,
            executions,
            observations,
            "final_answer",
        )

    def validate_plan(
        self, plan: AgentPlan, context: ProcurementContext
    ) -> list[PlannedAction]:
        if not plan.final_synthesis_required:
            raise ValueError("Planner final synthesis is required.")
        if len(plan.actions) > self.max_planned_actions:
            raise ValueError(
                f"Plan exceeds maximum of {self.max_planned_actions} actions."
            )
        seen: set[str] = set()
        validated: list[PlannedAction] = []
        for action in plan.actions:
            arguments = self.registry.validate(action.tool_name, action.arguments)
            fingerprint = action_fingerprint(action.tool_name, arguments)
            if fingerprint in seen:
                raise ValueError("Plan contains a duplicate identical action.")
            seen.add(fingerprint)
            self._validate_provenance(action.tool_name, arguments, context)
            if (
                action.tool_name == "calculate_spot_exposure"
                and not any(
                    previous.tool_name == "calculate_supply_position"
                    for previous in validated
                )
            ):
                raise ValueError(
                    "calculate_spot_exposure requires a preceding "
                    "calculate_supply_position action."
                )
            validated.append(
                PlannedAction(tool_name=action.tool_name, arguments=arguments)
            )
        self._validate_plan_completeness(validated, context)
        return validated

    @staticmethod
    def _validate_plan_completeness(
        actions: list[PlannedAction], context: ProcurementContext
    ) -> None:
        action_names = [action.tool_name for action in actions]
        if context.demand_gwh is not None and context.supply_gwh is not None:
            if "calculate_supply_position" not in action_names:
                raise ValueError(
                    "The plan must assess the supply position when demand and "
                    "contracted supply are available."
                )
            if (
                context.demand_gwh > context.supply_gwh
                and context.spot_price_eur_mwh is not None
                and "calculate_spot_exposure" not in action_names
            ):
                raise ValueError(
                    "A SHORT position with an available spot price requires "
                    "calculate_spot_exposure."
                )
        planned_variations = {
            action.arguments["variation_percent"]
            for action in actions
            if action.tool_name == "calculate_demand_scenario"
        }
        missing_variations = set(context.scenario_variations) - planned_variations
        if missing_variations:
            raise ValueError(
                "The plan omits requested demand scenarios: "
                f"{sorted(missing_variations)}."
            )

    @staticmethod
    def validate_execution_evidence(
        context: ProcurementContext, executions: list[dict[str, Any]]
    ) -> None:
        if context.demand_gwh is None or context.supply_gwh is None:
            return
        supply_position = next(
            (
                execution
                for execution in executions
                if execution["name"] == "calculate_supply_position"
            ),
            None,
        )
        if supply_position is None:
            raise ValueError("The supply-position observation is missing.")
        if (
            supply_position["result"].get("interpretation") == "SHORT"
            and context.spot_price_eur_mwh is not None
            and not any(
                execution["name"] == "calculate_spot_exposure"
                for execution in executions
            )
        ):
            raise ValueError("The spot-exposure observation is missing.")

    @staticmethod
    def _validate_provenance(
        tool_name: str,
        arguments: dict[str, float],
        context: ProcurementContext,
    ) -> None:
        expected = {
            "expected_demand_gwh": context.demand_gwh,
            "contracted_supply_gwh": context.supply_gwh,
            "spot_price_eur_mwh": context.spot_price_eur_mwh,
            "base_demand_gwh": context.demand_gwh,
        }
        for name, expected_value in expected.items():
            if name not in arguments:
                continue
            if expected_value is None or arguments[name] != expected_value:
                raise ValueError(
                    f"{tool_name}.{name} is not grounded in the user input."
                )
        if tool_name == "calculate_demand_scenario" and (
            arguments["variation_percent"] not in context.scenario_variations
        ):
            raise ValueError(
                "Demand variation "
                f"{arguments['variation_percent']:g}% was not requested by the user."
            )

    def _record(self, stage: str, **metadata: Any) -> None:
        if self.recorder:
            try:
                status = metadata.pop("status", None)
                if status == "failed":
                    event = self.recorder.start_stage(stage, **metadata)
                    self.recorder.fail_stage(event)
                else:
                    self.recorder.record_stage(stage, **metadata)
            except Exception:
                pass

    def _finish(self, status, content, executions, observations, reason):
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
            tuple(executions),
            tuple(observations),
            2 if status == AgentStatus.COMPLETED else 1,
            reason,
        )


def parse_agent_plan(content: str) -> AgentPlan:
    text = content.strip()
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(value)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            return AgentPlan.model_validate(candidate)
        except ValidationError:
            continue
    raise ValueError("Planner response does not contain a valid AgentPlan JSON object.")


def _plan_prompt(goal, context, tools):
    return f"""
Eres ProcurementAgentPlanner. Crea antes de ejecutar un plan completo usando
solo las herramientas permitidas y los valores presentes en los hechos. No
calcules resultados ni inventes datos. No incluyas exposición spot si falta el
precio spot. Incluye siempre calculate_supply_position cuando haya demanda y
suministro. Si la demanda supera al suministro y hay precio spot, incluye
calculate_spot_exposure después de calculate_supply_position. Si la demanda
es igual o inferior al suministro, no incluyas calculate_spot_exposure porque
no existe déficit que cubrir. Incluye cada
escenario de demanda solicitado. Si demand_variations_percent no aparece o
está vacío, no incluyas calculate_demand_scenario ni inventes escenarios. Usa
únicamente las herramientas incluidas en HERRAMIENTAS. La síntesis final sólo podrá usar resultados
de herramientas ejecutadas, nunca calcular resultados omitidos por el plan.
Devuelve exclusivamente JSON válido con esta forma:
{{"actions":[{{"tool_name":"...","arguments":{{...}}}}],"final_synthesis_required":true}}

OBJETIVO:
{goal}

HECHOS:
{json.dumps(context.facts(), ensure_ascii=False, indent=2)}

HERRAMIENTAS:
{json.dumps(tools, ensure_ascii=False, indent=2)}
""".strip()
