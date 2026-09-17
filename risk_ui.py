import streamlit as st

from risk_models import RiskAgentResult


def render_risk_result(result: RiskAgentResult, *, embedded=False):
    st.subheader("Resultado de riesgo")
    st.write(result.summary)
    for warning in result.warnings:
        st.warning(warning)
    scenarios = ([result.base_scenario] if result.base_scenario else []) + result.stress_scenarios
    if scenarios:
        st.dataframe([{
            "Escenario": s.name, "Demanda (GWh)": s.demand_gwh, "Suministro (GWh)": s.supply_gwh,
            "Posición (GWh)": s.position_gwh, "Estado": s.interpretation,
            "Volumen descubierto (GWh)": s.short_position_gwh,
            "Volumen descubierto (MWh)": s.short_position_mwh,
            "Spot (EUR/MWh)": s.spot_price_eur_mwh, "Exposición spot (EUR)": s.spot_exposure_eur,
        } for s in scenarios], hide_index=True)
    if result.deltas:
        st.markdown("**Cambios respecto a la base**")
        st.dataframe([{
            "Escenario": d.scenario_name, "Cambio demanda (GWh)": d.demand_change_gwh,
            "Cambio posición (GWh)": d.position_change_gwh,
            "Cambio descubierto (GWh)": d.short_position_change_gwh,
            "Cambio exposición (EUR)": d.exposure_change_eur,
        } for d in result.deltas], hide_index=True)
    for finding in result.risk_findings:
        st.write(finding.text)
    if not embedded:
        with st.expander("Detalles técnicos del riesgo"):
            st.json(result.model_dump(mode="json"))
            st.download_button("Descargar análisis de riesgo", result.model_dump_json(indent=2),
                               "risk-analysis.json", "application/json", key="risk_download")
