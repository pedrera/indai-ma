"""Independent demand-stress specialist. No RAG and no agent-to-agent calls."""

import json
import re
from time import perf_counter

from pydantic import ValidationError

from diagnostics import PerformanceStatus
from gas_analysis import GasAnalysisError, extract_demand_scenarios, extract_stress_percentages, parse_scenario_input
from llm_client import GenerationCancelledError, GenerationOptions, LLMTimeoutError
from risk_calculations import build_risk_finding, build_risk_scenario, calculate_risk_delta
from risk_models import RiskAgentResult, RiskInputs, RiskInterpretation, RiskStatus
from tool_registry import LocalToolRegistry


class ProviderRiskModel:
    def __init__(self, provider):
        self.provider = provider

    def interpret(self, findings, timeout_seconds):
        prompt = (
            "Eres RiskAgent. Prioriza los hallazgos deterministas por relevancia para "
            "explicar los cambios en cobertura y exposición spot. No calcules, no "
            "inventes límites de riesgo ni redactes razonamiento interno. Devuelve "
            'solo JSON {"priority_finding_ids": ["identificador existente"]}. '
            "Solo puedes seleccionar identificadores de estos hallazgos:\n"
            + json.dumps([f.model_dump() for f in findings], ensure_ascii=False)
        )
        response = self.provider.generate_response(
            [{"role": "user", "content": prompt}], timeout_seconds=timeout_seconds,
            options=GenerationOptions(tool_calling_enabled=False, max_rounds=1,
                                      trace_purpose="risk_interpretation", trace_call_number=1),
        )
        text = response.content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        return RiskInterpretation.model_validate_json(text)


class RiskAgent:
    name = "RiskAgent"

    def __init__(self, model=None, recorder=None, registry=None):
        self.model = model
        self.recorder = recorder
        self.registry = registry or LocalToolRegistry((
            "calculate_demand_scenario", "calculate_supply_position", "calculate_spot_exposure"))

    def _record(self, stage, **metadata):
        if self.recorder:
            self.recorder.record_stage(stage, **metadata)

    def run(self, request: str | RiskInputs, timeout_seconds=None):
        started = perf_counter()
        result = RiskAgentResult(status=RiskStatus.COMPLETED,
            summary="Análisis de cobertura y exposición con suministro y precio spot constantes entre escenarios.")
        self._record("agent_start", agent_name=self.name)
        event = self.recorder.start_stage("input_parsing") if self.recorder else None
        try:
            if isinstance(request, RiskInputs):
                inputs = RiskInputs.model_validate(request.model_dump())
            else:
                parsed = parse_scenario_input(request)
                if len(parsed.base_demand_candidates) != 1 or len(parsed.contracted_supply_candidates) != 1 or len(parsed.spot_price_candidates) > 1:
                    result.status = RiskStatus.NEEDS_INPUT
                    result.summary = "Indica una demanda y un suministro inequívocos en GWh, y un único precio spot si está disponible."
                    if event:
                        self.recorder.fail_stage(event, missing_or_ambiguous_inputs=True)
                    return self._finish(result)
                demand_stress, price_stress = extract_stress_percentages(request)
                if not price_stress:
                    # Preserve the legacy strict validation for malformed demand scenarios.
                    extract_demand_scenarios(request, use_defaults=False, strict=True)
                inputs = RiskInputs(demand_gwh=parsed.base_demand_candidates[0],
                    supply_gwh=parsed.contracted_supply_candidates[0],
                    spot_price_eur_mwh=parsed.spot_price_candidates[0] if parsed.spot_price_candidates else None,
                    stress_percentages=[v for v in demand_stress if v != 0],
                    demand_stress_percentages=[v for v in demand_stress if v != 0],
                    price_stress_percentages=[v for v in price_stress if v != 0])
            # Legacy stress_percentages remains a demand alias; explicit fields win.
            demand_variations = inputs.demand_stress_percentages or inputs.stress_percentages
            price_variations = inputs.price_stress_percentages
            variations = list(dict.fromkeys(v for v in demand_variations if v != 0))
            price_variations = list(dict.fromkeys(v for v in price_variations if v != 0))
            if event:
                self.recorder.complete_stage(event, risk_inputs=inputs.model_dump(),
                scenario_count=len(variations) + len(price_variations) + 1)
        except (ValidationError, GasAnalysisError, ValueError):
            result.status = RiskStatus.FAILED
            result.summary = "Entradas de riesgo inválidas. Usa volúmenes y spot no negativos y finitos, y porcentajes de demanda desde -100 %. Máximo 20 escenarios explícitos."
            if event:
                self.recorder.fail_stage(event, validation_error="invalid_risk_inputs")
            return self._finish(result)
        if inputs.spot_price_eur_mwh is None:
            result.warnings.append("Falta precio spot: se calculan posiciones y volúmenes, pero no importes de exposición ni deltas monetarios.")
        calculation_started = perf_counter()
        try:
            result.base_scenario = self._scenario("Base", 0, inputs, result, scenario_type="BASE")
            result.risk_findings.append(build_risk_finding(result.base_scenario))
            for index, variation in enumerate(variations, 1):
                scenario = self._scenario(f"Demanda {variation:+g} %", variation, inputs, result,
                                          scenario_type="DEMAND")
                result.stress_scenarios.append(scenario)
                before = perf_counter()
                delta = calculate_risk_delta(result.base_scenario, scenario)
                result.deltas.append(delta)
                self._record("risk_delta", scenario_name=scenario.name, delta=delta.model_dump(),
                             calculation_seconds=perf_counter() - before)
                result.risk_findings.append(build_risk_finding(result.base_scenario, scenario, delta, f"stress_{index}"))
            offset = len(variations)
            for index, variation in enumerate(price_variations, 1):
                scenario = self._scenario(f"Precio spot {variation:+g} %", variation, inputs, result,
                                          scenario_type="PRICE")
                result.stress_scenarios.append(scenario)
                delta = calculate_risk_delta(result.base_scenario, scenario)
                result.deltas.append(delta)
                self._record("risk_delta", scenario_name=scenario.name, delta=delta.model_dump(),
                             calculation_seconds=0)
                result.risk_findings.append(build_risk_finding(result.base_scenario, scenario, delta,
                                                                f"stress_{offset + index}"))
        except (ValueError, OverflowError):
            result.status = RiskStatus.FAILED
            result.summary = "No se pudieron completar los cálculos; revisa los valores de entrada."
            return self._finish(result)
        self._record("risk_calculations", calculation_wall_seconds=perf_counter() - calculation_started)
        if self.model is not None:
            self._interpret(result, timeout_seconds, started)
        elif self.recorder:
            self.recorder.record_stage("risk_interpretation", status=PerformanceStatus.SKIPPED,
                                       reason="Modo determinista: no se requiere generación LLM.")
        result.status = RiskStatus.PARTIAL if result.warnings else RiskStatus.COMPLETED
        return self._finish(result)

    def _execute(self, tool, arguments, scenario, result):
        event = self.recorder.start_stage("tool_execution", tool_name=tool, scenario_name=scenario,
                                         tool_arguments=arguments) if self.recorder else None
        try:
            execution = self.registry.execute(tool, arguments)
        except Exception:
            if event:
                self.recorder.fail_stage(event)
            raise
        result.tool_executions.append({**execution.as_dict(), "scenario_name": scenario})
        if tool not in result.tools_used:
            result.tools_used.append(tool)
        if event:
            self.recorder.complete_stage(event, tool_call_count=1, tool_seconds=execution.elapsed_seconds,
                                         tool_result=execution.result)
        return execution.result

    def _scenario(self, name, variation, inputs, result, *, scenario_type="DEMAND"):
        self._record("base_scenario" if variation == 0 else "stress_scenario",
                     scenario_name=name, demand_variation_percent=variation)
        demand = inputs.demand_gwh
        if scenario_type == "DEMAND" and variation != 0:
            demand = self._execute("calculate_demand_scenario", {
                "base_demand_gwh": demand, "variation_percent": variation}, name, result)["scenario_demand_gwh"]
            demand = round(demand, 12)
        arguments = {"expected_demand_gwh": demand, "contracted_supply_gwh": inputs.supply_gwh}
        position = self._execute("calculate_supply_position", arguments, name, result)
        exposure = None
        spot_price = inputs.spot_price_eur_mwh
        if scenario_type == "PRICE" and spot_price is not None:
            spot_price = round(spot_price * (1 + variation / 100), 12)
        if spot_price is not None:
            exposure = self._execute("calculate_spot_exposure", {
                **arguments, "spot_price_eur_mwh": spot_price}, name, result)
        elif self.recorder:
            self.recorder.record_stage("tool_execution", status=PerformanceStatus.SKIPPED,
                tool_name="calculate_spot_exposure", scenario_name=name, missing_inputs=["spot_price_eur_mwh"])
        return build_risk_scenario(name, variation, demand, inputs.supply_gwh, position,
                                   spot_price, exposure, scenario_type=scenario_type,
                                   stress_percent=variation)

    def _interpret(self, result, timeout_seconds, started):
        event = self.recorder.start_stage("risk_interpretation") if self.recorder else None
        try:
            remaining = None if timeout_seconds is None else timeout_seconds - (perf_counter() - started)
            if remaining is not None and remaining <= 0:
                raise LLMTimeoutError("Tiempo disponible agotado.")
            interpretation = RiskInterpretation.model_validate(self.model.interpret(result.risk_findings, remaining))
            known = {f.finding_id: f for f in result.risk_findings}
            order = interpretation.priority_finding_ids
            if any(key not in known for key in order) or len(set(order)) != len(order):
                raise ValueError("Selección de hallazgos inválida.")
            result.risk_findings = [known[key] for key in order] + [f for f in result.risk_findings if f.finding_id not in order]
            result.interpretation_mode = "llm_prioritized"
            if event:
                self.recorder.complete_stage(event, priority_finding_ids=order)
        except GenerationCancelledError:
            if event:
                self.recorder.fail_stage(event)
            raise
        except Exception:
            result.interpretation_mode = "deterministic_fallback"
            result.warnings.append("La interpretación LLM no está disponible o no es válida; se conserva el análisis determinista completo.")
            if event:
                self.recorder.fail_stage(event, fallback=True)

    def _finish(self, result):
        names = ([result.base_scenario.name] if result.base_scenario else []) + [s.name for s in result.stress_scenarios]
        self._record("risk_result", structured_result=result.model_dump(mode="json"),
                     scenario_count=len(names), scenario_names=names, result_status=result.status.value)
        self._record("agent_final", agent_name=self.name, agent_status=result.status.value,
                     scenario_count=len(names), scenario_names=names, termination_reason=result.status.value)
        return result
