"""Offline persistent audit integration: no requests to the pilot."""
import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from api.app import create_app
from tests.test_security_http import environment, signing_key, signed, assert_no_execution
from security_audit import LocalAudit

@pytest.fixture
def audited(environment, tmp_path, monkeypatch):
    path = tmp_path/'audit.jsonl'
    path.touch(mode=0o600)
    monkeypatch.setenv('INDAI_LOCAL_SECURITY_AUDIT','1')
    monkeypatch.setenv('INDAI_SECURITY_AUDIT_FILE',str(path))
    app = create_app(environment[1], environment[0].app.state.runtime_config, security=environment[5])
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client,path

@pytest.mark.parametrize('subject,tenant,kind',[
    ('viewer','tenant-a','PermissionDenied'),
    ('viewer','tenant-b','TenantAccessDenied'),
    ('outsider','tenant-a','TenantAccessDenied'),
    ('outsider','tenant-b','TenantAccessDenied')])
def test_real_boundaries_persist_denial(audited,environment,signing_key,subject,tenant,kind):
    client,path=audited
    supervisor=Mock(side_effect=AssertionError('Supervisor constructed'))
    environment[1]._supervisor_factory=supervisor
    token=signed(signing_key,subject)
    response=client.post('/api/v1/analysis',json={'text':'PRIVATE_BUSINESS_SENTINEL'},headers={
        'Authorization':'Bearer '+token,'X-Tenant-ID':tenant,'X-Validation-ID':'a'*32,
        'Cookie':'PRIVATE_COOKIE_SENTINEL'})
    assert response.status_code==403
    raw=path.read_text()
    records=[json.loads(line) for line in raw.splitlines()]
    assert {r['correlation_id'] for r in records}=={'a'*32}
    assert records[0]['event']=='request_received' and records[-1]['http_status']==403
    denied=[r for r in records if r['event'].endswith('_denied')]
    assert len(denied)==1 and denied[0]['exception_type']==kind
    assert not any(r['event'] in {'analysis_service_entered','supervisor_construction_started','supervisor_construction_completed'} for r in records)
    assert token not in raw and subject not in raw and 'PRIVATE_' not in raw and 'Bearer' not in raw
    assert_no_execution(environment)
    supervisor.assert_not_called()


def test_audit_failure_does_not_change_denial(audited,environment,signing_key):
    client,path=audited
    path.unlink()
    response=client.post('/api/v1/analysis',json={'text':'test'},headers={
        'Authorization':'Bearer '+signed(signing_key,'viewer'),'X-Tenant-ID':'tenant-a'})
    assert response.status_code==403 and not path.exists()
    assert_no_execution(environment)


def test_untrusted_correlation_and_auth_failure(audited):
    client,path=audited
    response=client.post('/api/v1/analysis',json={'text':'test'},headers={'X-Validation-ID':'PRIVATE_ID'})
    assert response.status_code==401
    records=[json.loads(line) for line in path.read_text().splitlines()]
    assert len({r['correlation_id'] for r in records})==1
    assert len(records[0]['correlation_id'])==32
    assert [r['event'] for r in records]==['request_received','authentication_denied','request_completed']
    assert 'PRIVATE_ID' not in path.read_text()


def test_authorized_execution_events_are_real(audited,signing_key):
    client,path=audited
    response=client.post('/api/v1/analysis',json={'text':'La demanda prevista es de 120 GWh, tenemos 95 GWh aprovisionados y el precio spot es de 42 EUR/MWh. Analiza nuestra posición de aprovisionamiento.'},headers={
        'Authorization':'Bearer '+signed(signing_key),'X-Tenant-ID':'tenant-a'})
    assert response.status_code==200
    records=[json.loads(line) for line in path.read_text().splitlines()]
    events=[r['event'] for r in records]
    assert events==['request_received','authentication_succeeded','tenant_authorization_succeeded',
        'permission_authorization_succeeded','analysis_service_entered','supervisor_construction_started',
        'supervisor_construction_completed','request_completed']
    assert records[-1]['http_status']==200


def test_reject_repository_and_symlink_paths(tmp_path):
    from pathlib import Path
    with pytest.raises(ValueError):
        LocalAudit(Path(__file__).resolve().parents[1]/'unsafe.jsonl')
    target=tmp_path/'target'
    target.touch(mode=0o600)
    link=tmp_path/'link'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Symlinks unavailable')
    sink=LocalAudit(link)
    sink.write({'event':'test'})
    assert sink.write_failed and not target.read_text()


def test_correlation_reaches_service_context(audited,environment,signing_key):
    client,path=audited
    client.post('/api/v1/analysis',json={'text':'test'},headers={
        'Authorization':'Bearer '+signed(signing_key),'X-Tenant-ID':'tenant-a','X-Validation-ID':'b'*32})
    context=environment[1].analyze.call_args.args[1]
    assert context.correlation_id=='b'*32
    assert {json.loads(line)['correlation_id'] for line in path.read_text().splitlines()}=={'b'*32}


def test_disabled_by_default(tmp_path,monkeypatch):
    from security_audit import configured_audit
    monkeypatch.delenv('INDAI_LOCAL_SECURITY_AUDIT',raising=False)
    path=tmp_path/'not-created.jsonl'
    monkeypatch.setenv('INDAI_SECURITY_AUDIT_FILE',str(path))
    assert configured_audit() is None and not path.exists()


def test_concurrent_request_scopes_are_isolated(audited,signing_key):
    from concurrent.futures import ThreadPoolExecutor
    client,path=audited
    def call(index):
        return client.post('/api/v1/analysis',json={'text':'test'},headers={
            'Authorization':'Bearer '+signed(signing_key,'viewer' if index==1 else 'outsider'),
            'X-Tenant-ID':'tenant-a','X-Validation-ID':str(index)*32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call,[1,2]))==[403,403]
    records=[json.loads(line) for line in path.read_text().splitlines()]
    for index,kind in [(1,'PermissionDenied'),(2,'TenantAccessDenied')]:
        group=[r for r in records if r['correlation_id']==str(index)*32]
        assert group[0]['event']=='request_received' and group[-1]['event']=='request_completed'
        assert [r['exception_type'] for r in group if 'exception_type' in r]==[kind]
