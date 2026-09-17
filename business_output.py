"""Business text shared by result views and clipboard; no calculations or LLM."""
from clipboard_text import _redact_text

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
    formatters = {'CommercialAgent':commercial_text, 'ProcurementAgent':procurement_text, 'RiskAgent':risk_text}
    labels = {'CommercialAgent':'Contrato', 'ProcurementAgent':'Aprovisionamiento', 'RiskAgent':'Escenarios de riesgo'}
    lines = [result.summary]
    for item in result.specialist_results:
        lines += [labels[item.agent_name], formatters[item.agent_name](item.result) if item.result is not None
                  else item.error or 'Sin resultado disponible.']
    if result.combined_findings:
        lines += ['Implicaciones conjuntas', *result.combined_findings]
    lines += ['Aviso: '+w for w in result.warnings]
    return _redact_text('\n\n'.join(lines))
