import streamlit as st
from evals.runner import load_cases, EvaluationRunner, summarize
from evals.offline import OfflineEnvironment


def render_evaluation():
    st.subheader('Evaluation')
    st.caption('Evaluación local con contrato de prueba y embeddings fijos. No requiere LLM ni modifica tu índice.')
    category = st.selectbox('Categoría', ['Todas','procurement','commercial','risk','supervisor','guardrails'], key='evaluation_category')
    if st.button('Run evaluation', key='evaluation_run'):
        cases = load_cases(category=None if category == 'Todas' else category)
        with st.spinner('Ejecutando casos de evaluación…'), OfflineEnvironment() as env:
            st.session_state.evaluation_results = EvaluationRunner(env.supervisor).run(cases)
    results = st.session_state.get('evaluation_results')
    if results is None:
        return
    summary = summarize(results)
    st.write(f'Casos: {summary.total_cases} · Aprobados: {summary.passed_cases} · Fallidos: {summary.failed_cases}')
    st.json(summary.model_dump())
    st.dataframe([{'case_id': r.case_id, 'passed': r.passed, 'status': r.execution_status,
                   'latency_ms': r.latency_ms, 'errors': '; '.join(r.errors)} for r in results], hide_index=True)
    failed = [r for r in results if not r.passed]
    st.write('Casos fallidos: ' + (', '.join(r.case_id for r in failed) or 'ninguno'))
    selected = st.selectbox('Inspeccionar caso', [r.case_id for r in failed] + [r.case_id for r in results if r.passed])
    result = next((r for r in results if r.case_id == selected), None)
    if result:
        st.json(result.model_dump())
        st.download_button('Descargar caso JSON', result.model_dump_json(indent=2), f'{result.case_id}.json', 'application/json')
