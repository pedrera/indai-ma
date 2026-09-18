"""Local validation of input domains and output provenance; no business arithmetic."""
import math
import re
from typing import Literal
from pydantic import BaseModel, Field
from gas_analysis import parse_scenario_input, extract_demand_scenarios

MAX_QUERY_CHARS = 12000


class Violation(BaseModel):
    code: str
    message: str
    severity: Literal['error', 'warning'] = 'error'


class GuardrailResult(BaseModel):
    passed: bool = True
    violations: list[Violation] = Field(default_factory=list)
    warnings: list[Violation] = Field(default_factory=list)

    def reject(self, code, message):
        self.passed = False
        self.violations.append(Violation(code=code, message=message))


def validate_input(query):
    result = GuardrailResult()
    if not isinstance(query, str):
        result.reject('invalid_input_type', 'La consulta debe ser texto.')
        return result
    if not query.strip():
        result.reject('empty_query', 'Escribe una consulta.')
    if len(query) > MAX_QUERY_CHARS:
        result.reject('query_too_long', f'La consulta supera {MAX_QUERY_CHARS} caracteres.')
    if not result.passed:
        return result
    if re.search(r'(?:ignore|ignora|bypass|omite).{0,60}(?:rules|reglas|validation|validaci[oó]n)', query, re.I) and re.search(
            r'fabricat|fabrica|invent', query, re.I):
        result.reject('instruction_override', 'No se permite desactivar validaciones para fabricar hechos o resultados.')
    # Negative percentages are legitimate stress inputs; negative volumes are not.
    if re.search(r'(?<![\w\d])[−-]\s*\d+(?:[.,]\d+)?\s*GWh\b', query, re.I):
        result.reject('negative_volume', 'Demanda y suministro deben ser volúmenes no negativos.')
    if re.search(r'\b(?:nan|inf|infinity)\s*(?:GWh|EUR/MWh)\b', query, re.I):
        result.reject('nonfinite_value', 'Los valores deben ser finitos.')
    parsed = parse_scenario_input(query)
    fields = (parsed.base_demand_candidates, parsed.contracted_supply_candidates, parsed.spot_price_candidates)
    if any(len(values) > 1 for values in fields):
        result.reject('conflicting_inputs', 'Hay valores contradictorios; indica una única base, suministro y precio spot.')
    if any(not math.isfinite(v) for values in fields for v in values):
        result.reject('nonfinite_value', 'Los valores deben ser finitos.')
    scenarios = extract_demand_scenarios(query, use_defaults=False)
    if any(not math.isfinite(v) or v < -100 for _, v in scenarios) or len(scenarios) > 20:
        result.reject('invalid_scenario', 'La variación no puede reducir la demanda más del 100 %; máximo 20 escenarios.')
    if re.search(r'\bescenario\s+(?:abc|nan|inf)\s*%', query, re.I):
        result.reject('invalid_scenario', 'El porcentaje del escenario no es válido.')
    return result


def validate_output(output, events):
    result = GuardrailResult()
    executed = {e.metadata.get('agent_name') for e in events if e.stage == 'specialist_execution'}
    if executed - set(output.routing.selected_agents):
        result.reject('unselected_agent', 'Se ejecutó un especialista no seleccionado.')
    for item in output.specialist_results:
        data = item.result
        if data is None:
            continue
        own_events = [e for e in events if e.metadata.get('supervisor_agent') == item.agent_name]
        for tool in data.tool_executions:
            matching = [e for e in own_events if e.stage == 'tool_execution'
                        and e.metadata.get('tool_name') == tool['name']
                        and e.metadata.get('tool_arguments') == tool['arguments']
                        and e.metadata.get('tool_result') == tool['result']]
            if not matching:
                result.reject('tool_provenance_mismatch', 'Un resultado no coincide con su ejecución registrada.')
        if item.agent_name == 'ProcurementAgent':
            tools = {t['name']: t for t in data.tool_executions}
            position, exposure = tools.get('calculate_supply_position'), tools.get('calculate_spot_exposure')
            if position and exposure and any(position['arguments'][key] != exposure['arguments'][key]
                    for key in ('expected_demand_gwh', 'contracted_supply_gwh')):
                result.reject('inconsistent_position_inputs', 'Posición y exposición no usan la misma demanda y suministro.')
            from procurement_response import validate_procurement_final_response
            if not validate_procurement_final_response(data.content, list(data.tool_executions)).is_valid:
                result.reject('unsupported_narrative', 'La respuesta no coincide con las herramientas.')
            if position and position['result']['interpretation'] == 'SHORT' and not exposure:
                result.warnings.append(Violation(code='spot_cost_unavailable', message='El coste spot no está disponible.', severity='warning'))
        if item.agent_name == 'CommercialAgent':
            evidence = [e.metadata for e in own_events if e.stage == 'commercial_evidence']
            excerpts = evidence[-1].get('excerpts', []) if evidence else []
            for fact in [*data.contract_facts, *data.commercial_findings]:
                source = fact.source
                if not source.document_name or source.page_start < 1 or not fact.evidence.strip() or not any(
                        fact.evidence in x['text'] and source.model_dump() == x['source'] for x in excerpts):
                    result.reject('unsupported_contract_fact', 'Un hecho contractual carece de evidencia y fuente verificables.')
            for calculation in data.calculations:
                if not any(t['name'] == calculation.name and t['arguments'] == calculation.inputs
                           and t['result'] == calculation.result for t in data.tool_executions):
                    result.reject('contract_calculation_mismatch', 'El cálculo contractual no coincide con la herramienta.')
        if item.agent_name == 'RiskAgent' and data.base_scenario:
            for scenario in [data.base_scenario, *data.stress_scenarios]:
                tools = [t for t in data.tool_executions if t.get('scenario_name') == scenario.name]
                for tool in tools:
                    if tool['name'] == 'calculate_supply_position' and (
                        not math.isclose(scenario.position_gwh, tool['result']['position_gwh'], abs_tol=1e-9)
                        or scenario.interpretation != tool['result']['interpretation']):
                        result.reject('risk_result_mismatch', 'La posición de riesgo no coincide con su herramienta.')
                    if tool['name'] == 'calculate_spot_exposure' and (scenario.spot_exposure_eur is None or
                        not math.isclose(scenario.spot_exposure_eur, tool['result']['exposure_eur'], abs_tol=1e-6)):
                        result.reject('risk_result_mismatch', 'La exposición de riesgo no coincide con su herramienta.')
    return result
