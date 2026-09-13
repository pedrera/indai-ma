import streamlit as st

from commercial_models import CommercialAgentResult


LABELS = {
    "reference_volume_gwh": "Referencia mensual (GWh)",
    "contractual_reference_gwh": "Referencia mensual (GWh)",
    "flexibility_percent": "Flexibilidad (%)",
    "excess_surcharge_eur_mwh": "Recargo sobre spot (EUR/MWh)",
    "annual_volume_gwh": "Volumen anual (GWh)",
    "take_or_pay_percent": "Take-or-pay (%)",
    "sales_price_eur_mwh": "Precio de venta (EUR/MWh)",
    "supply_cost_eur_mwh": "Coste de suministro (EUR/MWh)",
    "contractual_min_gwh": "Mínimo mensual flexible (GWh)",
    "contractual_max_gwh": "Máximo mensual flexible (GWh)",
    "forecast_demand_gwh": "Previsión mensual (GWh)",
    "contractual_excess_gwh": "Exceso contractual (GWh)",
    "spot_price_eur_mwh": "Precio spot (EUR/MWh)",
    "contractual_excess_price_eur_mwh": "Precio del exceso (EUR/MWh)",
    "volume_gwh": "Volumen del margen (GWh)",
    "margin_eur_mwh": "Margen unitario (EUR/MWh)",
    "total_margin_eur": "Margen total (EUR)",
}


def render_commercial_result(result: CommercialAgentResult):
    st.subheader("Resultado comercial")
    st.caption(f"Estado: {result.status.value} · Cliente: {result.customer or 'Sin identificar'}")
    st.write(result.summary)
    for warning in result.warnings:
        st.warning(warning)
    if result.contract_facts:
        st.markdown("**Hechos contractuales**")
        st.dataframe([{"Hecho": LABELS.get(f.name, f.name), "Valor": str(f.value),
                       "Fuente": f.source.label, "Evidencia": f.evidence}
                      for f in result.contract_facts], hide_index=True)
    if result.calculations:
        st.markdown("**Cálculos deterministas**")
        for calculation in result.calculations:
            label = "Exceso y flexibilidad mensual" if calculation.name == "calculate_contractual_volume_impact" else "Margen comercial"
            with st.expander(label, expanded=True):
                st.caption("Resultado calculado por Python; las fuentes respaldan las entradas, no la operación aritmética.")
                st.dataframe([{"Concepto": LABELS.get(key, key),
                               "Resultado": f"{value:g}" if value is not None else "No disponible"}
                              for key, value in calculation.result.items()], hide_index=True)
    if result.commercial_findings:
        st.markdown("**Cláusulas comerciales relevantes**")
        for finding in result.commercial_findings:
            st.caption(finding.source.label)
            st.text(finding.evidence)
    with st.expander("Fuentes consultadas"):
        for source in result.sources:
            st.caption(f"{source.label} · Similitud: {source.score:.3f}")
    st.download_button("Descargar análisis comercial", result.model_dump_json(indent=2),
                       "commercial-analysis.json", "application/json", key="commercial_download")
