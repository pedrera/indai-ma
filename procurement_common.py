import json
from dataclasses import dataclass
from typing import Any

from agent_models import AgentObservation
from diagnostics import PerformanceRecorder
from gas_analysis import extract_demand_scenarios, parse_scenario_input
from tool_registry import ToolRegistry


PROCUREMENT_GOAL = (
    "Analizar la posición de aprovisionamiento de gas y el coste de cubrir "
    "cualquier déficit usando cálculos deterministas."
)


@dataclass(frozen=True)
class ProcurementContext:
    request: str
    demand_gwh: float | None
    supply_gwh: float | None
    spot_price_eur_mwh: float | None
    scenario_variations: tuple[float, ...]
    ambiguities: tuple[str, ...]

    def facts(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "expected_demand_gwh": self.demand_gwh,
                "contracted_supply_gwh": self.supply_gwh,
                "spot_price_eur_mwh": self.spot_price_eur_mwh,
                "demand_variations_percent": list(self.scenario_variations),
            }.items()
            if value is not None and value != []
        }


def extract_procurement_context(request: str) -> ProcurementContext:
    parsed = parse_scenario_input(request)
    explicit_scenarios = extract_demand_scenarios(request, use_defaults=False)
    return ProcurementContext(
        request=request.strip(),
        demand_gwh=_single(parsed.base_demand_candidates),
        supply_gwh=_single(parsed.contracted_supply_candidates),
        spot_price_eur_mwh=_single(parsed.spot_price_candidates),
        scenario_variations=tuple(
            variation for _, variation in explicit_scenarios
        ),
        ambiguities=tuple(parsed.ambiguities),
    )


def eligible_procurement_tools(
    registry: ToolRegistry, context: ProcurementContext
) -> list[dict[str, Any]]:
    """Expose registered tools whose required optional inputs are available."""
    return [
        descriptor.as_dict()
        for descriptor in registry.descriptors()
        if not (
            descriptor.name == "calculate_demand_scenario"
            and not context.scenario_variations
        )
        and not (
            descriptor.name == "calculate_spot_exposure"
            and context.spot_price_eur_mwh is None
        )
    ]


def procurement_tool_skip_reason(
    tool_name: str, executions: list[dict[str, Any]]
) -> str | None:
    """Return a deterministic reason when a proposed action is unnecessary."""
    if tool_name != "calculate_spot_exposure":
        return None
    position = next(
        (
            execution
            for execution in reversed(executions)
            if execution.get("name") == "calculate_supply_position"
        ),
        None,
    )
    if (
        position is not None
        and position.get("result", {}).get("interpretation") != "SHORT"
    ):
        return "Spot exposure is unnecessary for a non-SHORT position."
    return None


def missing_procurement_evidence(
    context: ProcurementContext, executions: list[dict[str, Any]]
) -> list[str]:
    """List deterministic observations still required by the user facts."""
    missing: list[str] = []
    position = next(
        (
            execution
            for execution in executions
            if execution.get("name") == "calculate_supply_position"
        ),
        None,
    )
    if context.demand_gwh is not None and context.supply_gwh is not None:
        if position is None:
            missing.append("calculate_supply_position")
        elif (
            position.get("result", {}).get("interpretation") == "SHORT"
            and context.spot_price_eur_mwh is not None
            and not any(
                item.get("name") == "calculate_spot_exposure"
                for item in executions
            )
        ):
            missing.append("calculate_spot_exposure")
    completed_variations = {
        float(item.get("result", {}).get("variation_percent"))
        for item in executions
        if item.get("name") == "calculate_demand_scenario"
        and item.get("result", {}).get("variation_percent") is not None
    }
    for variation in context.scenario_variations:
        if variation not in completed_variations:
            missing.append(f"calculate_demand_scenario({variation:+g}%)")
    return missing


def execute_procurement_tool(
    registry: ToolRegistry,
    recorder: PerformanceRecorder | None,
    tool_name: str,
    arguments: dict[str, Any],
    step: int,
    agent_name: str,
) -> tuple[dict[str, Any], AgentObservation]:
    event = (
        recorder.start_stage(
            "tool_execution",
            round=step,
            agent_name=agent_name,
            tool_name=tool_name,
        )
        if recorder
        else None
    )
    try:
        execution = registry.execute(tool_name, arguments)
    except Exception as error:
        if recorder and event:
            recorder.fail_stage(event, tool_arguments=arguments, error=str(error))
        raise
    if recorder and event:
        recorder.complete_stage(
            event,
            tool_call_count=1,
            tool_seconds=execution.elapsed_seconds,
            tool_arguments=execution.arguments,
            tool_result=execution.result,
        )
    observation = AgentObservation(
        step,
        "tool_result",
        execution.name,
        execution.arguments,
        execution.result,
    )
    if recorder:
        recorder.record_stage(
            "agent_observation",
            round=step,
            observation_type="tool_result",
            tool_name=execution.name,
            observation_result=execution.result,
        )
    return execution.as_dict(), observation


def synthesis_prompt(
    agent_name: str,
    request: str,
    executions: list[dict[str, Any]],
) -> str:
    return f"""
Eres {agent_name}. Redacta una respuesta breve en español basada únicamente en
los resultados deterministas. No recalcules ni inventes valores. Si falta el
precio spot, indica que la exposición económica no puede calcularse. No
expongas razonamiento interno.

CONSULTA:
{request}

RESULTADOS DETERMINISTAS AUTORITATIVOS:
{json.dumps(executions, ensure_ascii=False, indent=2)}
""".strip()


def _single(values: list[float]) -> float | None:
    return values[0] if len(values) == 1 else None
