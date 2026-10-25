"""Public Streamlit auth API doubles, session isolation and real API JWT checks."""
import ast
from dataclasses import replace
from pathlib import Path
from time import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import business_auth as auth
from api_client import AnalysisApiCredentialsError, AnalysisApiConnectionError
from security.models import PrincipalId
from security.permissions import Permission
from tests.test_security_http import environment, signing_key, headers, signed


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class User(dict):
    __getattr__ = dict.__getitem__


def native_user(subject="person", access="private-access-token", expiry=None, logged=True):
    now = time()
    return User(iss="https://identity.example", sub=subject, iat=now - 30,
                exp=expiry or now + 300, is_logged_in=logged, tokens={"access": access})


def metadata(user, tenants=None):
    return {"issuer": user["iss"], "subject": user["sub"], "expires_at": time() + 200,
            "tenants": tenants if tenants is not None else [
                {"tenant_id": "a", "permissions": ["analysis:run"]},
                {"tenant_id": "b", "permissions": ["analysis:run"]},
            ]}


@pytest.fixture
def ui(monkeypatch):
    st = SimpleNamespace(user=native_user(), session_state=State(), login=Mock(), logout=Mock(),
                         secrets={"auth": {"client_id": "public-client-id", "client_secret": "not-a-real-secret",
                                           "cookie_secret": "not-a-real-secret", "server_metadata_url": "https://identity.example/config",
                                           "expose_tokens": ["access"]}},
                         button=Mock(return_value=False), warning=Mock(), info=Mock(), selectbox=Mock())
    client = Mock()
    client.session.side_effect = lambda **kw: metadata(st.user)
    monkeypatch.setattr(auth, "AnalysisApiClient", Mock(return_value=client))
    return st, client


def activate(st, tenant="a"):
    auth.refresh_business_session(st)
    st.session_state["business_tenant_selection"] = tenant
    auth.select_business_tenant(st)
    return st.session_state["business_session"]


def test_anonymous_cannot_capture_business_and_native_login_is_offered(ui):
    st, client = ui
    st.user = User(is_logged_in=False)
    assert auth.refresh_business_session(st) is None
    client.session.assert_not_called()
    st.button.return_value = True
    assert auth.render_business_auth(st) is False
    st.login.assert_called_once_with()
    with pytest.raises(AnalysisApiCredentialsError):
        auth.capture_business_request(st, "q")


def test_native_login_missing_configuration_stays_disabled(ui):
    st, _ = ui
    st.user = User(is_logged_in=False)
    st.secrets = {}
    auth.render_business_auth(st)
    assert st.button.call_args.kwargs["disabled"] is True
    st.login.assert_not_called()


def test_authenticated_identity_is_bound_to_backend_and_requires_explicit_tenant(ui):
    st, client = ui
    session = auth.refresh_business_session(st)
    assert session.principal == PrincipalId(st.user["iss"], st.user["sub"])
    assert session.tenant_id is None
    assert not session.can_analyze
    with pytest.raises(AnalysisApiCredentialsError):
        auth.capture_business_request(st, "q")
    activate(st)
    request, binding = auth.capture_business_request(st, "q")
    assert request.access_token == "private-access-token"
    assert request.tenant_id == "a"
    assert auth.business_result_is_current(st.session_state, binding)
    assert "private-access-token" not in repr(st.session_state)
    assert "private-access-token" not in repr(request)
    client.close.assert_called()


@pytest.mark.parametrize("fault", ["no_access", "no_subject", "id_expired", "access_expired", "identity_mismatch", "id_instead_access", "backend_down", "unknown_permission"])
def test_invalid_sessions_never_enable_business(ui, fault):
    st, client = ui
    if fault == "no_access":
        st.user["tokens"] = {"id": "identity-token"}
    elif fault == "no_subject":
        del st.user["sub"]
    elif fault == "id_expired":
        st.user["exp"] = time() - 1
    elif fault == "access_expired":
        client.session.side_effect = lambda **kw: {**metadata(st.user), "expires_at": time() - 1}
    elif fault == "identity_mismatch":
        client.session.side_effect = lambda **kw: {**metadata(st.user), "subject": "other"}
    elif fault == "id_instead_access":
        client.session.side_effect = AnalysisApiCredentialsError("not-access-token")
    elif fault == "backend_down":
        client.session.side_effect = AnalysisApiConnectionError("private backend detail")
    else:
        client.session.side_effect = lambda **kw: metadata(st.user, [{"tenant_id": "a", "permissions": ["*"]}])
    assert auth.refresh_business_session(st) is None
    assert st.session_state["business_session"] is None
    assert "private backend detail" not in st.session_state["business_auth_error"]
    with pytest.raises(AnalysisApiCredentialsError):
        auth.capture_business_request(st, "q")


def test_no_tenants_or_no_permission_has_no_implicit_default(ui):
    st, client = ui
    for tenants in ([], [{"tenant_id": "a", "permissions": ["portfolio:read"]}]):
        client.session.side_effect = lambda **kw: metadata(st.user, tenants)
        session = auth.refresh_business_session(st)
        assert session.tenant_id is None
        assert auth.render_business_auth(st) is False
        with pytest.raises(AnalysisApiCredentialsError):
            auth.capture_business_request(st, "q")


def test_unauthorized_tenant_is_denied(ui):
    st, _ = ui
    auth.refresh_business_session(st)
    st.session_state["business_tenant_selection"] = "not-a-member"
    auth.select_business_tenant(st)
    assert st.session_state["business_session"] is None


@pytest.mark.parametrize("change", ["tenant", "user", "logout", "revocation", "expiry"])
def test_changed_scope_cancels_jobs_erases_results_and_rejects_old_binding(ui, change):
    st, client = ui
    original = activate(st)
    job = Mock()
    st.session_state.update(generation_kind="business_api", generation_job=job,
                            business_api_result={"summary": "private"}, business_operation_ids=("old",),
                            execution_views={"old": object(), "workspace": object()},
                            execution_mode_ids={"Multi-Agent Supervisor": "old", "indAI MA": "workspace"},
                            business_request="private question")
    if change == "tenant":
        st.session_state["business_tenant_selection"] = "b"
        auth.select_business_tenant(st)
    elif change == "user":
        st.user = native_user("other-user")
        auth.refresh_business_session(st)
    elif change == "logout":
        auth.logout_business(st)
        st.logout.assert_called_once()
    elif change == "revocation":
        client.session.side_effect = lambda **kw: metadata(st.user, [])
        auth.refresh_business_session(st)
    else:
        st.user["exp"] = time() - 1
        auth.refresh_business_session(st)
    job.cancel.assert_called_once()
    assert st.session_state["generation_job"] is None
    assert st.session_state["business_api_result"] is None
    assert "business_request" not in st.session_state
    assert "old" not in st.session_state["execution_views"]
    assert "workspace" in st.session_state["execution_views"]
    assert not auth.business_result_is_current(st.session_state, original.binding)


def test_same_subject_new_native_login_invalidates_binding(ui):
    st, _ = ui
    original = activate(st)
    st.user["iat"] += 1
    auth.refresh_business_session(st)
    assert not auth.business_result_is_current(st.session_state, original.binding)


def test_public_token_missing_or_replaced_while_selecting_is_denied(ui):
    st, client = ui
    activate(st)
    st.user["tokens"] = {}
    with pytest.raises(AnalysisApiCredentialsError):
        auth.capture_business_request(st, "q")


def test_sessions_and_simultaneous_jobs_have_independent_bindings(ui):
    first, _ = ui
    one = activate(first)
    second = SimpleNamespace(**{**vars(first), "session_state": State()})
    two = activate(second, "b")
    request_a, binding_a = auth.capture_business_request(first, "A")
    request_b, binding_b = auth.capture_business_request(second, "B")
    assert request_a.tenant_id == "a" and request_b.tenant_id == "b"
    assert one.epoch != two.epoch
    assert auth.business_result_is_current(first.session_state, binding_a)
    assert not auth.business_result_is_current(second.session_state, binding_a)
    assert not auth.business_result_is_current(first.session_state, binding_b)


def test_old_business_completion_is_discarded_before_rendering(ui):
    st, _ = ui
    session = activate(st)
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "finish_generation")
    namespace = {"st": st, "GenerationResult": object, "business_result_is_current": auth.business_result_is_current}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    st.session_state.update(generation_kind="business_api", generation_job=Mock())
    namespace["finish_generation"](object(), business_binding=(session.principal, "different-tenant", "old-epoch"))
    assert st.session_state["business_api_result"] is None


def test_tokens_never_appear_in_ui_logs_or_session_metadata(ui, caplog):
    st, _ = ui
    activate(st)
    auth.render_business_auth(st)
    request, binding = auth.capture_business_request(st, "question")
    assert "private-access-token" not in caplog.text
    assert "private-access-token" not in repr(st.session_state)
    for component in (st.warning, st.info, st.selectbox):
        assert "private-access-token" not in repr(component.call_args_list)


def test_real_api_session_lists_only_current_principal_memberships(environment, signing_key):
    client = environment[0]
    supplied = headers(signing_key)
    supplied.pop("X-Tenant-ID")
    response = client.get("/api/v1/session", headers=supplied)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["subject"] == "analyst"
    assert {item["tenant_id"] for item in payload["tenants"]} == {"tenant-a", "tenant-b"}
    assert "analysis:run" not in next(item for item in payload["tenants"] if item["tenant_id"] == "tenant-b")["permissions"]
    assert signed(signing_key) not in response.text
    environment[1].analyze.assert_not_called()
    assert client.get("/api/v1/session").status_code == 401


def test_frontend_capture_calls_real_protected_analysis(environment, signing_key, monkeypatch):
    import httpx
    from api_client import AnalysisApiClient
    from business_api_adapter import ApiJobAdapter
    from tests.test_security_http import PROCUREMENT
    backend = environment[0]
    def transport(request):
        response = backend.request(request.method, request.url.path, headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, json=response.json())
    def client_factory(**kwargs):
        return AnalysisApiClient("http://test", transport=httpx.MockTransport(transport))
    monkeypatch.setattr(auth, "AnalysisApiClient", client_factory)
    user = native_user(subject="analyst", access=signed(signing_key))
    user["iss"] = "https://http-security.example"
    st = SimpleNamespace(user=user, session_state=State())
    activate(st, "tenant-a")
    request, binding = auth.capture_business_request(st, PROCUREMENT)
    client = client_factory()
    try:
        result = ApiJobAdapter(client).run(request, 10)
    finally:
        client.close()
    assert "25 GWh" in result.summary
    assert auth.business_result_is_current(st.session_state, binding)
    assert result.diagnostics.llm_calls == 0
    environment[1].analyze.assert_called_once()


def test_legacy_business_inspector_data_is_erased_on_new_login(ui):
    st, _ = ui
    st.session_state.update(execution_views={"legacy": SimpleNamespace(mode="Business"),
                                            "workspace": SimpleNamespace(mode="indAI MA")},
                            execution_mode_ids={"Multi-Agent Supervisor": "legacy"},
                            business_api_result={"summary": "previous user"})
    activate(st)
    assert "legacy" not in st.session_state["execution_views"]
    assert "workspace" in st.session_state["execution_views"]
    assert st.session_state["business_api_result"] is None


def test_actual_business_ui_requires_tenant_and_resets_on_user_change(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit
    user = native_user()
    client = Mock()
    client.session.side_effect = lambda **kw: metadata(user)
    monkeypatch.setattr(streamlit, "user", user)
    monkeypatch.setattr(auth, "AnalysisApiClient", Mock(return_value=client))
    monkeypatch.setattr("llm_client.get_available_models", lambda *args: [])
    app = AppTest.from_file(str(Path("app.py").resolve()), default_timeout=20).run()
    app.radio(key="selected_mode").set_value("Business").run()
    assert not app.exception
    assert app.button(key="business_start").disabled
    app.selectbox(key="business_tenant_selection").select("a").run()
    app.text_area(key="business_request").set_value("private first user question").run()
    assert not app.exception
    assert not app.button(key="business_start").disabled
    original = app.session_state["business_session"].binding
    user["sub"] = "different-user"
    app.run()
    assert not app.exception
    assert app.button(key="business_start").disabled
    assert app.selectbox(key="business_tenant_selection").value is None
    assert "private first user question" not in app.text_area(key="business_request").value
    assert app.session_state["business_session"].binding != original


def test_real_api_session_rejects_id_token_and_expired_access_token(environment, signing_key):
    import jwt
    for encoded in (signed(signing_key, exp=1), jwt.encode({"sub": "analyst"}, signing_key, algorithm="RS256", headers={"kid": "test-key", "typ": "JWT"})):
        response = environment[0].get("/api/v1/session", headers={"Authorization": "Bearer " + encoded})
        assert response.status_code == 401
    environment[1].analyze.assert_not_called()
