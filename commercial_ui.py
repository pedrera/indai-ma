import streamlit as st
from business_output import commercial_rows, commercial_sources


def render_commercial_result(result, *, embedded=False):
    st.subheader("Resultado comercial")
    st.write(result.summary)
    if result.comparison is not None:
        from commercial_comparison import comparison_rows
        rows = comparison_rows(result)
        sources = list(dict.fromkeys(f.source.label for c in result.comparison.contracts for f in c.facts))
    else:
        rows = commercial_rows(result)
        sources = commercial_sources(result)
    if rows:
        st.dataframe(rows, hide_index=True)
    for source in sources:
        st.caption(source)
    for warning in result.warnings:
        st.warning(warning)
    if not embedded:
        with st.expander("Evidencia contractual y detalles técnicos"):
            for finding in result.commercial_findings:
                st.caption(finding.source.label)
                st.text(finding.evidence)
            st.json(result.model_dump(mode="json"))
            st.download_button("Descargar análisis comercial", result.model_dump_json(indent=2),
                               "commercial-analysis.json", "application/json", key="commercial_download")
