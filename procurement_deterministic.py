from typing import Protocol

from agent_models import AgentRunResult, AgentStatus
from diagnostics import PerformanceRecorder
from llm_client import GenerationOptions, LLMProvider
from procurement_common import (
    execute_procurement_tool,
    extract_procurement_context,
    synthesis_prompt,
)
from tool_registry import ToolRegistry, procurement_tool_registry
from procurement_response import validate_procurement_final_response


class SynthesisModel(Protocol):
    def synthesize(
        self,
        request: str,
        executions: list[dict],
        timeout_seconds: float | None,
    ) -> str: ...


class ProviderSynthesisModel:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def synthesize(
        self,
        request: str,
        executions: list[dict],
        timeout_seconds: float | None,
    ) -> str:
        prompt = synthesis_prompt(
            "ProcurementDeterministicWorkflow", request, executions
        )
        return self.provider.generate_response(
            [{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
            options=GenerationOptions(
                tool_calling_enabled=False,
                max_rounds=1,
                trace_purpose="deterministic_final_synthesis",
                trace_call_number=1,
            ),
        ).content


class ProcurementDeterministicWorkflow:
    name = "ProcurementDeterministicWorkflow"

    def __init__(
        self,
        model: SynthesisModel,
        registry: ToolRegistry | None = None,
        recorder: PerformanceRecorder | None = None,
    ) -> None:
        self.model = model
        self.registry = registry or procurement_tool_registry()
        self.recorder = recorder

    def run(self, request: str, timeout_seconds: float | None = None) -> AgentRunResult:
        context = extract_procurement_context(request)
        self._record("deterministic_start", orchestration_mode="deterministic")
        if context.demand_gwh is None or context.supply_gwh is None:
            return self._finish(
                AgentStatus.NEEDS_INPUT,
                "Faltan una demanda o un suministro no ambiguos.",
                [],
                [],
                "missing_information",
            )
        executions = []
        observations = []
        step = 0
        for variation in context.scenario_variations:
            step += 1
            execution, observation = execute_procurement_tool(
                self.registry,
                self.recorder,
                "calculate_demand_scenario",
                {
                    "base_demand_gwh": context.demand_gwh,
                    "variation_percent": variation,
                },
                step,
                self.name,
            )
            executions.append(execution)
            observations.append(observation)
        step += 1
        position, observation = execute_procurement_tool(
            self.registry,
            self.recorder,
            "calculate_supply_position",
            {
                "expected_demand_gwh": context.demand_gwh,
                "contracted_supply_gwh": context.supply_gwh,
            },
            step,
            self.name,
        )
        executions.append(position)
        observations.append(observation)
        if (
            position["result"]["interpretation"] == "SHORT"
            and context.spot_price_eur_mwh is not None
        ):
            step += 1
            exposure, observation = execute_procurement_tool(
                self.registry,
                self.recorder,
                "calculate_spot_exposure",
                {
                    "expected_demand_gwh": context.demand_gwh,
                    "contracted_supply_gwh": context.supply_gwh,
                    "spot_price_eur_mwh": context.spot_price_eur_mwh,
                },
                step,
                self.name,
            )
            executions.append(exposure)
            observations.append(observation)
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

    def _record(self, stage: str, **metadata) -> None:
        if self.recorder:
            try:
                self.recorder.record_stage(stage, **metadata)
            except Exception:
                pass

    def _finish(self, status, content, executions, observations, reason):
        self._record(
            "deterministic_final",
            orchestration_mode="deterministic",
            termination_reason=reason,
            response_chars=len(content),
        )
        return AgentRunResult(
            status,
            content,
            tuple(executions),
            tuple(observations),
            1 if status == AgentStatus.COMPLETED else 0,
            reason,
        )
