import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from tenders import Limits, PlacspClient, TenderService, TenderStore
from tenders.local import private_catalog_path
from tenders.models import Deadline
from tenders_ui import _amount, _date, _deadline, _official_detail, _status
from tests.test_tenders import NEXT, XML_HEADERS, entry, feed

APP = Path(__file__).resolve().parents[1] / "app.py"
DETAIL_URL = "https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=a%3D"


def _app():
    return AppTest.from_string(
        "from tenders_ui import render_tenders\nrender_tenders()", default_timeout=20,
    ).run()


def _text(app):
    return "\n".join(x.value for x in app.text)


def test_navigation_isolates_tenders_from_business_llm_and_rag(monkeypatch):
    monkeypatch.delenv("INDAI_STREAMLIT_SESSION_ONLY", raising=False)
    with patch("llm_client.get_available_models", return_value=[]):
        app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert app.radio(key="selected_mode").value == "indAI MA"
    with (patch("tenders_ui.render_tenders") as render,
          patch("business_auth.refresh_business_session", side_effect=AssertionError),
          patch("llm_client.get_available_models", side_effect=AssertionError),
          patch("vector_store.LocalVectorStore.from_environment", side_effect=AssertionError)):
        app.radio(key="selected_mode").set_value("Licitaciones").run()
        assert not app.exception
        render.assert_called_once()


def test_session_only_does_not_expose_catalog(monkeypatch):
    monkeypatch.setenv("INDAI_STREAMLIT_SESSION_ONLY", "1")
    with (patch("business_auth.render_native_session_validation") as session,
          patch("tenders_ui.render_tenders", side_effect=AssertionError)):
        app = AppTest.from_file(str(APP)).run()
    session.assert_called_once()
    assert not app.exception
    assert not app.radio


def test_catalog_view_search_detail_sync_failure_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("INDAI_TENDERS_DIR", str(tmp_path))
    requests = []
    state = {"status": "PUB", "failure": False}

    def handler(request):
        assert request.method == "GET"
        requests.append(str(request.url))
        if str(request.url) == NEXT:
            return httpx.Response(503) if state["failure"] else httpx.Response(
                200, headers=XML_HEADERS, content=feed(next_url=NEXT.replace(".atom", "_1.atom")))
        item = entry(status=state["status"]).replace(
            "</title>", f'</title><summary>&lt;script&gt;externo&lt;/script&gt;</summary>'
            f'<link href="{DETAIL_URL.replace("&", "&amp;")}"/>')
        return httpx.Response(200, headers=XML_HEADERS, content=feed(item, next_url=NEXT))

    path = tmp_path / "catalog.sqlite"

    def service(_):
        return TenderService(TenderStore(path), PlacspClient(
            Limits(retries=0), transport=httpx.MockTransport(handler)))

    with patch("tenders_ui._loopback_only", return_value=True), patch("tenders_ui._open_catalog", side_effect=service):
        app = _app()
        assert not app.exception and not requests
        assert "0 expedientes únicos · 0 publicaciones · 0 revisiones" in _text(app)
        assert "No hay expedientes" in app.info[0].value
        app.button(key="tenders_refresh").click().run()
        assert not app.exception and len(requests) == 2
        assert any("Quedan páginas" in x.value for x in app.info)
        assert "2 páginas guardadas · 1 revisiones nuevas" in _text(app)
        app.text_input(key="tenders_text").set_value("HIDRÁULICA").run()
        app.text_input(key="tenders_cpv").set_value("45232150").run()
        app.text_input(key="tenders_status").set_value("pub").run()
        assert len(app.dataframe[0].value) == 1
        assert "ID" not in app.dataframe[0].value.columns
        assert app.selectbox(key="tenders_selection").options == ["130/2026 · Obra hidráulica"]
        app.selectbox(key="tenders_selection").set_value(1).run()
        assert "Descripción: <script>externo</script>" in _text(app)
        assert "PCAP.pdf" in app.dataframe[2].value["Referencia"].tolist()
        assert "64.328,41 €" in app.dataframe[1].value["Importe"].tolist()
        assert len(app.dataframe[3].value) == 1
        assert any("referenciados, no descargados" in x.value for x in app.subheader)
        assert len(requests) == 2

        state.update(status="ANUL", failure=True)
        app.button(key="tenders_refresh").click().run()
        assert len(requests) == 4 and "http_503" in _text(app)
        assert any("Actualización incompleta" in x.value for x in app.warning)
        assert "1 páginas guardadas · 1 revisiones nuevas" in _text(app)
        restarted = _app()
        assert len(requests) == 4
        assert "Última sincronización satisfactoria: ninguna" not in _text(restarted)
        restarted.selectbox(key="tenders_selection").set_value(1).run()
        assert "Estado publicado: Anulada (ANUL)" in _text(restarted)
        assert len(restarted.dataframe[3].value) == 2
        assert len(requests) == 4 and not restarted.exception


def test_spanish_formatting_preserves_precision_and_missing_values():
    assert _amount(Decimal("5551393.82"), "EUR") == "5.551.393,82 €"
    assert _amount(Decimal("-1012.345"), "USD") == "-1.012,345 USD"
    assert _amount(Decimal("12"), None) == "12,00 moneda sin informar"
    assert _date(datetime(2026, 11, 8, 8, 50, tzinfo=timezone.utc)) == "08/11/2026 09:50"
    assert _date(datetime(2026, 7, 8, 8, 50, tzinfo=timezone.utc)) == "08/07/2026 10:50"
    assert _date(None) == _deadline(None) == "Sin informar"
    assert _deadline(Deadline()) == "Desconocida"
    assert _deadline(Deadline(day=date(2026, 11, 8), local_time=time(9, 50))) == (
        "08/11/2026 09:50 (zona sin informar)"
    )
    assert _deadline(Deadline(day=date(2026, 11, 8), local_time=time(8, 50, tzinfo=timezone.utc))) == (
        "08/11/2026 09:50"
    )
    assert _status("PUB") == "Publicada (PUB)"
    assert _status("FUTURO") == "FUTURO"
    assert _status(None) == "Sin informar"


def test_existing_catalog_counts_cases_publications_and_revisions(tmp_path):
    path = tmp_path / "legacy.sqlite"
    page = feed(entry(), entry("2"), entry("3").replace("L01260316", "L99999999"),
                entry("4", details=False), entry("5", details=False),
                entry(updated="2026-11-07T17:00:00+01:00", status="ANUL"))
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers=XML_HEADERS, content=page))
    service = TenderService(TenderStore(path), PlacspClient(transport=transport))
    assert service.refresh().new_revisions == 6
    # Reproduce payloads from before description was introduced, without migrating the schema.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE revision SET payload = json_remove(payload, '$.description')")
    reopened = TenderService(TenderStore(path), PlacspClient(transport=transport))
    assert reopened.catalog_counts().model_dump() == {"cases": 4, "publications": 5, "revisions": 6}
    assert reopened.search()[0].current.publication.description is None
    assert reopened.search()[0].current.publication.amounts[0].value == Decimal("64328.41")
    assert reopened.refresh().new_revisions == 0


@pytest.mark.parametrize(("loopback", "directory", "message"), [
    (False, "any", "--server.address"),
    (True, "", "INDAI_TENDERS_DIR"),
    (True, str(APP.parent), "catálogo privado"),
])
def test_configuration_fails_closed(monkeypatch, loopback, directory, message):
    monkeypatch.setenv("INDAI_TENDERS_DIR", directory)
    with patch("tenders_ui._loopback_only", return_value=loopback):
        app = _app()
    assert message in app.error[0].value
    assert not app.button
    assert not app.exception


def test_catalog_path_requires_existing_external_directory(tmp_path):
    with pytest.raises(ValueError):
        private_catalog_path(str(APP.parent))
    with pytest.raises(FileNotFoundError):
        private_catalog_path(str(tmp_path / "missing"))
    if os.name == "nt":
        with patch("tenders.local._verify_windows_acl", side_effect=ValueError("ACL")):
            with pytest.raises(ValueError, match="ACL"):
                private_catalog_path(str(tmp_path))
    else:
        tmp_path.chmod(0o755)
        with pytest.raises(ValueError, match="permisos"):
            private_catalog_path(str(tmp_path))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", [
    "http://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&idEvl=a",
    DETAIL_URL.replace("contrataciondelestado.es", "contrataciondelestado.es@evil.invalid"),
    DETAIL_URL.replace("/wps/poc", "/redirect"),
    DETAIL_URL + "&url=https://evil.invalid",
    DETAIL_URL + "#fragment",
])
def test_official_link_validation(url):
    assert _official_detail(DETAIL_URL) == DETAIL_URL
    assert _official_detail(url) is None
