"""Session lifecycle tests: doubles and local transports only, no pilot POST."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from time import time
import httpx
import pytest
import business_auth as auth
from api_client import AnalysisApiClient,AnalysisApiCredentialsError
from tests.test_streamlit_auth import ui,native_user,metadata,activate

@pytest.mark.parametrize('before,after,grants',[
    ('analyst-a','viewer-a',[{'tenant_id':'a','permissions':['documents:read','portfolio:read']}]),
    ('viewer-a','outsider',[]),
    ('outsider','analyst-a',[{'tenant_id':'a','permissions':['analysis:run']}])])
def test_identity_matrix_drops_all_previous_context(ui,before,after,grants):
    st,client=ui
    st.user=native_user(before)
    previous=activate(st)
    job=Mock()
    st.session_state.update(generation_kind='business_api',generation_job=job,
        business_api_result='OLD_RESULT',business_request='OLD_REQUEST',
        business_operation_ids=('old',),execution_views={'old':SimpleNamespace(mode='Business')},
        execution_mode_ids={'Business':'old'},business_first_run_result=(previous.binding,'OLD_RESULT'))
    st.user=native_user(after,access='NEW_TEST_TOKEN')
    client.session.side_effect=lambda **kw: metadata(st.user,grants)
    current=auth.refresh_business_session(st)
    assert current.principal.subject==after and current.tenant_id is None
    assert current.epoch!=previous.epoch and not current.can_analyze
    assert not auth.business_result_is_current(st.session_state,previous.binding)
    assert st.session_state['generation_job'] is None
    assert st.session_state['business_api_result'] is None
    assert not st.session_state['execution_views']
    assert 'OLD_' not in repr(st.session_state)
    job.cancel.assert_called_once()
    client.close.assert_called()
    assert client.session.call_args.kwargs=={'access_token':'NEW_TEST_TOKEN'}


def test_anonymous_residual_state_is_cleaned_without_session_object(ui):
    st,_=ui
    st.user=native_user(logged=False)
    job=Mock()
    st.session_state.update(generation_kind='business_api',generation_job=job,
        business_tenant_selection='a',business_api_result='OLD_RESULT',business_request='OLD_TOKEN')
    assert auth.refresh_business_session(st) is None
    assert st.session_state['generation_job'] is None
    assert st.session_state['business_api_result'] is None
    assert 'business_tenant_selection' not in st.session_state
    assert 'OLD_' not in repr(st.session_state)


def completion(st):
    tree=ast.parse(Path('app.py').read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='finish_generation')
    namespace={'st':st,'GenerationResult':object,'business_result_is_current':auth.business_result_is_current}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),namespace)
    return namespace['finish_generation']

@pytest.mark.parametrize('change',['principal','tenant','session','logout','replaced_job'])
def test_late_completion_cannot_publish_or_erase_new_job(ui,change):
    st,_=ui
    old=activate(st)
    old_job=Mock()
    if change=='principal':
        st.user=native_user('new-person');auth.refresh_business_session(st)
    elif change=='tenant':
        st.session_state['business_tenant_selection']='b';auth.select_business_tenant(st)
    elif change=='session':
        st.user['iat']+=1;auth.refresh_business_session(st)
    elif change=='logout':
        auth.logout_business(st)
    new_job=Mock()
    st.session_state.update(generation_kind='business_api',generation_job=new_job,business_api_result='NEW_RESULT')
    completion(st)(object(),business_binding=old.binding,source_job=old_job)
    assert st.session_state['generation_job'] is new_job
    assert st.session_state['business_api_result']=='NEW_RESULT'


def test_stale_business_callback_after_mode_change_cannot_touch_state(ui):
    st,_=ui
    old=activate(st)
    st.session_state.update(generation_kind='chat',generation_job=Mock(),business_api_result=None)
    before=dict(st.session_state)
    completion(st)(object(),business_binding=old.binding)
    assert st.session_state==before


def test_http401_invalidates_context_and_closes_per_operation_client(ui,monkeypatch):
    st,_=ui
    original=activate(st)
    st.session_state.update(business_api_result='OLD_RESULT')
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(401,json={'detail':'PRIVATE_SENTINEL'})
    client=AnalysisApiClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth,'AnalysisApiClient',lambda **kw:client)
    assert auth.refresh_business_session(st) is None
    assert client._client.is_closed
    assert not auth.business_result_is_current(st.session_state,original.binding)
    assert st.session_state['business_api_result'] is None
    assert 'business_tenant_selection' not in st.session_state
    assert 'PRIVATE_SENTINEL' not in st.session_state['business_auth_error']
    assert len(requests)==1 and requests[0].method=='GET'


def test_analysis_401_is_typed_credentials_failure_without_sensitive_body():
    client=AnalysisApiClient(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'secret':'PRIVATE_SENTINEL'})))
    try:
        with pytest.raises(AnalysisApiCredentialsError) as exc:
            client.analyze('offline test',access_token='test-token',tenant_id='a')
        assert 'PRIVATE_SENTINEL' not in str(exc.value)
    finally:
        client.close()

@pytest.mark.parametrize('change',['principal','tenant','session'])
def test_pending_http_work_finishes_but_old_result_is_not_published(ui,change,monkeypatch):
    from threading import Event
    from business_api_adapter import create_business_api_job
    import business_api_adapter as adapter
    from generation import GenerationStatus
    from api_client_models import ApiAnalysisResult
    from tests.test_api_client import PAYLOAD
    st,_=ui
    old=activate(st)
    request,binding=auth.capture_business_request(st,'offline deterministic input')
    entered,released=Event(),Event()
    backend_finished=Event()
    def analyze(*args,**kwargs):
        assert kwargs['access_token']=='private-access-token' and kwargs['tenant_id']=='a'
        entered.set()
        assert released.wait(5)
        backend_finished.set()
        return ApiAnalysisResult.model_validate(PAYLOAD)
    client=Mock()
    client.config.analysis_timeout=30
    client.analyze.side_effect=analyze
    monkeypatch.setattr(adapter,'AnalysisApiClient',lambda:client)
    job=create_business_api_job(request)
    job.business_binding=binding
    st.session_state.update(generation_kind='business_api',generation_job=job,business_api_result=None)
    job.start()
    try:
        assert entered.wait(5)
        if change=='principal':
            st.user=native_user('other');auth.refresh_business_session(st)
        elif change=='tenant':
            st.session_state['business_tenant_selection']='b';auth.select_business_tenant(st)
        else:
            st.user['iat']+=1;auth.refresh_business_session(st)
        assert job.cancel_requested and st.session_state['generation_job'] is None
        assert not backend_finished.is_set()  # Local cancel has NOT stopped the server work.
    finally:
        released.set()
        job._thread.join(5)
    assert not job._thread.is_alive() and backend_finished.is_set()
    assert job.poll().status==GenerationStatus.CANCELLED
    assert job.request is None
    client.close.assert_called_once()
    current_job=Mock()
    st.session_state.update(generation_kind='business_api',generation_job=current_job,business_api_result=None)
    completion(st)(job.poll(),business_binding=binding,source_job=job)
    assert st.session_state['generation_job'] is current_job
    assert st.session_state['business_api_result'] is None
