"""Deterministic business projections shared by result views and clipboard."""
from dataclasses import dataclass, field
from clipboard_text import _redact_text


@dataclass(frozen=True)
class ExecutiveMetric:
    label: str
    value: str
    origin: str


@dataclass(frozen=True)
class ExecutiveEvidence:
    document: str
    section: str
    page: str
    excerpt: str = ""


@dataclass(frozen=True)
class ExecutiveProvenance:
    category: str
    label: str
    value: str
    origin: str


@dataclass(frozen=True)
class ExecutiveResultProjection:
    summary: str
    metrics: tuple[ExecutiveMetric, ...] = ()
    explanations: tuple[tuple[str, str], ...] = ()
    evidence: tuple[ExecutiveEvidence, ...] = ()
    provenance: tuple[ExecutiveProvenance, ...] = ()
    warnings: tuple[str, ...] = ()


def _fmt(value, unit=""):
    if value is None:
        return "No disponible"
    if isinstance(value, (int, float)):
        rendered = f"{value:,.0f}" if unit == "EUR" else f"{value:g}"
    else:
        rendered = str(value)
    return f"{rendered}{(' ' + unit) if unit else ''}"


def build_supervisor_executive_sections(result) -> ExecutiveResultProjection:
    """Project structured specialist results into deterministic business sections."""
    metrics, explanations, evidence, provenance, warnings = [], [], [], [], list(result.warnings)
    operating_inputs: dict[tuple[str, str], ExecutiveProvenance] = {}
    present = {item.agent_name: item for item in result.specialist_results}
    commercial = present.get("CommercialAgent")
    if commercial and commercial.result:
        value = commercial.result
        if value.comparison is not None:
            comparison = value.comparison
            comparison_names = []
            for contract in comparison.contracts:
                name = contract.customer or contract.document_name
                comparison_names.append(name)
                facts = {fact.name: fact for fact in contract.facts}
                for key in ("reference_volume_gwh", "flexibility_percent", "monthly_min_gwh",
                            "monthly_max_gwh", "take_or_pay_percent", "minimum_annual_gwh",
                            "excess_surcharge_eur_mwh"):
                    fact = facts.get(key)
                    if fact is None:
                        continue
                    label = FACT_LABELS.get(key, key)
                    value_text = _fmt(fact.value, fact.unit or "")
                    if key == "flexibility_percent":
                        value_text = f"±{_fmt(fact.value, fact.unit or '%')}"
                    elif key == "excess_surcharge_eur_mwh":
                        value_text = f"Spot + {_fmt(fact.value, 'EUR/MWh')}"
                    metrics.append(ExecutiveMetric(f"{name} · {label}", value_text,
                                                    "Calculado" if fact.origin == "deterministic_calculation" else "Documental"))
                    category = "VALOR CALCULADO" if fact.origin == "deterministic_calculation" else "HECHO DOCUMENTAL"
                    provenance.append(ExecutiveProvenance(category, f"{name} · {label}", value_text, fact.source.label))
                    evidence.append(ExecutiveEvidence(fact.source.document_name, fact.source.section or "Sin sección",
                                                       str(fact.source.page_start), fact.evidence))
            if comparison_names:
                explanations.append(("Comparación contractual", "Se compararon: " + "; ".join(comparison_names) + "."))
        calculations = value.calculations
        if calculations:
            calc = calculations[0].result
            if calc.get("contractual_excess_gwh") is not None:
                metrics.append(ExecutiveMetric("Exceso contractual", _fmt(calc["contractual_excess_gwh"], "GWh"), "Calculado"))
            if calc.get("contractual_excess_price_eur_mwh") is not None:
                metrics.append(ExecutiveMetric("Precio del exceso", _fmt(calc["contractual_excess_price_eur_mwh"], "EUR/MWh"), "Calculado"))
            for label, key, unit in (("Previsión mensual", "forecast_demand_gwh", "GWh"), ("Máximo flexible", "contractual_max_gwh", "GWh")):
                if calc.get(key) is not None:
                    category = "ENTRADA OPERATIVA" if key == "forecast_demand_gwh" else "VALOR CALCULADO"
                    item = ExecutiveProvenance(category, label, _fmt(calc[key], unit), "Consulta del usuario" if category == "ENTRADA OPERATIVA" else "Herramienta determinista")
                    if category == "ENTRADA OPERATIVA":
                        operating_inputs[(label, item.value)] = item
                    else:
                        provenance.append(item)
            for key, label, unit in (("forecast_demand_gwh", "Previsión mensual", "GWh"), ("spot_price_eur_mwh", "Precio spot", "EUR/MWh")):
                if key in calculations[0].inputs:
                    item = ExecutiveProvenance("ENTRADA OPERATIVA", label, _fmt(calculations[0].inputs[key], unit), "Consulta del usuario")
                    operating_inputs[(label, item.value)] = item
        for fact in value.contract_facts:
            provenance.append(ExecutiveProvenance("HECHO DOCUMENTAL", FACT_LABELS.get(fact.name, fact.name), _fmt(fact.value, fact.unit or ""), fact.source.label))
            source = fact.source
            evidence.append(ExecutiveEvidence(source.document_name, source.section or "Sin sección", str(source.page_start), fact.evidence))
        explanations.append(("Contrato", value.summary))
        warnings.extend(value.warnings)
    procurement = present.get("ProcurementAgent")
    if procurement and procurement.result:
        executions = procurement.result.tool_executions
        position = next((item for item in executions if item.get("name") == "calculate_supply_position"), None)
        exposure = next((item for item in executions if item.get("name") == "calculate_spot_exposure"), None)
        if position:
            value = position["result"]
            metrics.append(ExecutiveMetric("Posición de aprovisionamiento", f"{value.get('interpretation')} {_fmt(abs(value.get('position_gwh', 0)), 'GWh')}", "Calculado"))
            provenance.append(ExecutiveProvenance("VALOR CALCULADO", "Posición de aprovisionamiento", _fmt(value.get("position_gwh"), "GWh"), "calculate_supply_position"))
            args = position.get("arguments", {})
            if args.get("expected_demand_gwh") is not None:
                item = ExecutiveProvenance("ENTRADA OPERATIVA", "Demanda esperada", _fmt(args["expected_demand_gwh"], "GWh"), "Consulta del usuario")
                operating_inputs[(item.label, item.value)] = item
            if args.get("contracted_supply_gwh") is not None:
                item = ExecutiveProvenance("ENTRADA OPERATIVA", "Suministro aprovisionado", _fmt(args["contracted_supply_gwh"], "GWh"), "Consulta del usuario")
                operating_inputs[(item.label, item.value)] = item
        if exposure:
            value = exposure["result"]
            metrics.append(ExecutiveMetric("Exposición spot", _fmt(value.get("exposure_eur"), "EUR"), "Calculado"))
            provenance.append(ExecutiveProvenance("VALOR CALCULADO", "Exposición spot", _fmt(value.get("exposure_eur"), "EUR"), "calculate_spot_exposure"))
            args = exposure.get("arguments", {})
            if args.get("spot_price_eur_mwh") is not None:
                item = ExecutiveProvenance("ENTRADA OPERATIVA", "Precio spot", _fmt(args["spot_price_eur_mwh"], "EUR/MWh"), "Consulta del usuario")
                operating_inputs[(item.label, item.value)] = item
        explanations.append(("Aprovisionamiento", procurement.result.content))
        warnings.extend(getattr(procurement.result, "warnings", []))
    risk = present.get("RiskAgent")
    if risk and risk.result:
        value = risk.result
        if value.base_scenario:
            metrics.append(ExecutiveMetric("Riesgo base", f"{value.base_scenario.interpretation} {_fmt(value.base_scenario.short_position_gwh, 'GWh')}", "Calculado"))
            explanations.append(("Riesgo", value.summary))
        for scenario in value.stress_scenarios:
            if scenario.spot_exposure_eur is not None:
                metrics.append(ExecutiveMetric(f"Exposición · {scenario.name}", _fmt(scenario.spot_exposure_eur, "EUR"), "Calculado"))
        for delta in value.deltas:
            if delta.exposure_change_eur is not None:
                metrics.append(ExecutiveMetric(f"Cambio de exposición · {delta.scenario_name}", _fmt(delta.exposure_change_eur, "EUR"), "Calculado"))
        warnings.extend(value.warnings)
    for item in result.specialist_results:
        if item.result is None and item.error:
            warnings.append(f"{item.agent_name}: {item.error}")
    if commercial and procurement:
        explanations.append(("Implicación integrada", "El exceso contractual y el SHORT de aprovisionamiento son magnitudes distintas y no deben sumarse ni sustituirse."))
    conclusions = []
    if commercial and commercial.result and commercial.result.comparison is not None:
        names = [contract.customer or contract.document_name for contract in commercial.result.comparison.contracts]
        if names:
            conclusions.append("Comparación contractual completada para " + " y ".join(names) + ".")
    commercial_calc = next((item.result.calculations[0].result for item in result.specialist_results
                            if item.agent_name == "CommercialAgent" and item.result and item.result.calculations), None)
    procurement_result = next((item.result for item in result.specialist_results
                               if item.agent_name == "ProcurementAgent" and item.result), None)
    if commercial_calc and commercial_calc.get("contractual_excess_gwh") is not None:
        conclusions.append(f"Exceso contractual de {_fmt(commercial_calc['contractual_excess_gwh'], 'GWh')}.")
    if procurement_result:
        position = next((item for item in procurement_result.tool_executions if item.get("name") == "calculate_supply_position"), None)
        exposure = next((item for item in procurement_result.tool_executions if item.get("name") == "calculate_spot_exposure"), None)
        if position:
            conclusions.append(f"Posición de aprovisionamiento {position['result'].get('interpretation')} de {_fmt(abs(position['result'].get('position_gwh', 0)), 'GWh')}.")
        if exposure and exposure["result"].get("exposure_eur") is not None:
            conclusions.append(f"Exposición spot calculada de {_fmt(exposure['result']['exposure_eur'], 'EUR')}.")
    if commercial_calc and procurement_result:
        conclusions.append("El exceso contractual y el SHORT de aprovisionamiento son magnitudes distintas.")
    risk_result = next((item.result for item in result.specialist_results if item.agent_name == "RiskAgent" and item.result), None)
    if risk_result and risk_result.base_scenario:
        conclusions.append(f"Riesgo base: {risk_result.base_scenario.interpretation} de {_fmt(risk_result.base_scenario.short_position_gwh, 'GWh')}.")
    provenance.extend(operating_inputs.values())
    summary = " ".join(conclusions) if conclusions else (result.summary or "Se han completado los análisis disponibles.")
    return ExecutiveResultProjection(summary, tuple(metrics), tuple(explanations), tuple(dict.fromkeys(evidence)), tuple(provenance), tuple(dict.fromkeys(warnings)))

FACT_LABELS = {
    'reference_volume_gwh':'Referencia mensual (GWh)', 'flexibility_percent':'Flexibilidad (%)',
    'excess_surcharge_eur_mwh':'Recargo sobre spot (EUR/MWh)', 'annual_volume_gwh':'Volumen anual (GWh)',
    'take_or_pay_percent':'Take-or-pay (%)', 'sales_price_eur_mwh':'Precio de venta (EUR/MWh)',
    'supply_cost_eur_mwh':'Coste de suministro (EUR/MWh)',
}
CALC_LABELS = {
    'contractual_reference_gwh':'Referencia mensual (GWh)', 'reference_volume_gwh':'Referencia mensual (GWh)',
    'contractual_min_gwh':'Mínimo mensual flexible (GWh)', 'contractual_max_gwh':'Máximo mensual flexible (GWh)',
    'forecast_demand_gwh':'Previsión mensual (GWh)', 'contractual_excess_gwh':'Exceso contractual (GWh)',
    'contractual_excess_price_eur_mwh':'Precio del exceso (EUR/MWh)', 'flexibility_percent':'Flexibilidad (%)',
    'spot_price_eur_mwh':'Precio spot (EUR/MWh)', 'excess_surcharge_eur_mwh':'Recargo sobre spot (EUR/MWh)',
    'margin_eur_mwh':'Margen unitario (EUR/MWh)', 'total_margin_eur':'Margen total (EUR)',
}


def number(value):
    return f'{value:g}' if isinstance(value, (int,float)) else str(value)


def commercial_rows(result):
    rows = []
    for fact in result.contract_facts:
        if fact.name in FACT_LABELS:
            rows.append({'Concepto': FACT_LABELS[fact.name], 'Valor': number(fact.value)})
    for calculation in result.calculations:
        for key, value in calculation.result.items():
            if key in CALC_LABELS:
                rows.append({'Concepto': CALC_LABELS[key], 'Valor': number(value) if value is not None else 'No disponible'})
    return [dict(pair) for pair in dict.fromkeys(tuple(row.items()) for row in rows)]


def commercial_sources(result):
    sources = list(dict.fromkeys(f.source.label for f in result.contract_facts))
    if not sources:
        sources = list(dict.fromkeys(f.source.label for f in result.commercial_findings))
    return sources


def commercial_text(result):
    if result.comparison is not None:
        from commercial_comparison import comparison_text
        return _redact_text(comparison_text(result))
    lines = [result.summary, *[f"{row['Concepto']}: {row['Valor']}" for row in commercial_rows(result)]]
    sources = commercial_sources(result)
    if sources:
        lines += ['Fuentes:', *sources]
    lines += ['Aviso: '+w for w in result.warnings]
    return _redact_text('\n\n'.join(lines))


def risk_text(result):
    return _redact_text('\n\n'.join([result.summary, *[f.text for f in result.risk_findings],
                                    *['Aviso: '+w for w in result.warnings]]))


def procurement_text(result):
    return _redact_text(result.content if hasattr(result, 'content') else str(result))


def supervisor_text(result):
    projection = build_supervisor_executive_sections(result)
    lines = ["Resumen ejecutivo", projection.summary]
    if projection.metrics:
        lines += ["Métricas clave", *[f"{metric.label}: {metric.value}" for metric in projection.metrics]]
    if projection.explanations:
        lines += ["Explicación de negocio", *[f"{title}: {text}" for title, text in projection.explanations]]
    if projection.evidence:
        lines += ["Evidencia", *[f"{item.document} · {item.section} · pág. {item.page}" for item in projection.evidence]]
    if projection.provenance:
        lines += ["Provenance", *[f"{item.category}: {item.label} = {item.value} ({item.origin})" for item in projection.provenance]]
    lines += ["Aviso: " + warning for warning in projection.warnings]
    return _redact_text('\n\n'.join(lines))
