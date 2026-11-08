from datetime import date, datetime, time, timezone
from decimal import Decimal
from xml.sax.saxutils import escape

import httpx
import pytest

from tenders import Limits, PlacspClient, TenderFilters, TenderService, TenderStore
from tenders.placsp import FEED_URL, NS, PlacspError, parse_page

NEXT = FEED_URL.replace("Completo3.atom", "Completo3_20261106_202248.atom").replace(
    "contrataciondelsectorpublico.gob.es", "contrataciondelestado.es"
)
XML_HEADERS = {"content-type": "application/atom+xml"}


def entry(number="20853933", updated="2026-11-06T19:55:20.691+01:00", status="PUB", details=True):
    metadata = """
      <cbc:ContractFolderID>130/2026</cbc:ContractFolderID>
      <ext:LocatedContractingParty><cac:Party>
        <cac:PartyIdentification><cbc:ID schemeName="DIR3">L01260316</cbc:ID></cac:PartyIdentification>
        <cac:PartyName><cbc:Name>Órgano de prueba</cbc:Name></cac:PartyName>
      </cac:Party></ext:LocatedContractingParty>
      <cac:ProcurementProject>
        <cac:BudgetAmount>
          <cbc:EstimatedOverallContractAmount currencyID="EUR">64328.41</cbc:EstimatedOverallContractAmount>
          <cbc:TotalAmount currencyID="EUR">77837.38</cbc:TotalAmount>
        </cac:BudgetAmount>
        <cac:RequiredCommodityClassification><cbc:ItemClassificationCode>45232150</cbc:ItemClassificationCode></cac:RequiredCommodityClassification>
      </cac:ProcurementProject>
      <cac:TenderingProcess>
        <cac:DocumentAvailabilityPeriod><cbc:EndDate>2026-11-19</cbc:EndDate></cac:DocumentAvailabilityPeriod>
        <cac:TenderSubmissionDeadlinePeriod>
          <cbc:EndDate>2026-11-20</cbc:EndDate><cbc:EndTime>23:59:00</cbc:EndTime>
        </cac:TenderSubmissionDeadlinePeriod>
      </cac:TenderingProcess>
      <cac:LegalDocumentReference>
        <cbc:ID>PCAP.pdf</cbc:ID>
        <cac:Attachment><cac:ExternalReference>
          <cbc:URI>https://example.invalid/pcap.pdf</cbc:URI>
        </cac:ExternalReference></cac:Attachment>
      </cac:LegalDocumentReference>
      <cac:TechnicalDocumentReference><cbc:ID>PPT.pdf</cbc:ID></cac:TechnicalDocumentReference>
      <cac:AdditionalDocumentReference><cbc:ID>Anexo.ods</cbc:ID></cac:AdditionalDocumentReference>
    """ if details else ""
    return f"""
      <entry><id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/{number}</id>
        <updated>{updated}</updated><title>Obra hidráulica</title>
        <ext:ContractFolderStatus>
          <ext-basic:ContractFolderStatusCode>{status}</ext-basic:ContractFolderStatusCode>
          {metadata}
        </ext:ContractFolderStatus>
      </entry>
    """


def feed(*entries, next_url=None):
    link = f'<link rel="next" href="{escape(next_url)}"/>' if next_url else ""
    return (
        f'<feed xmlns="{NS["a"]}" xmlns:ext="{NS["ext"]}" xmlns:ext-basic="{NS["ext-basic"]}" '
        f'xmlns:cac="{NS["cac"]}" xmlns:cbc="{NS["cbc"]}">'
        f'<updated>2026-11-06T20:22:48+01:00</updated>{link}{"".join(entries)}</feed>'
    ).encode()


def client(handler, **limits):
    return PlacspClient(Limits(**limits), transport=httpx.MockTransport(handler))


def test_parses_atom_codice_and_keeps_unknowns():
    page = parse_page(feed(entry(), entry("2", details=False), next_url=NEXT))
    first, missing = page.entries
    assert page.next_url == NEXT
    assert first.updated_at == datetime(2026, 11, 6, 18, 55, 20, 691000, tzinfo=timezone.utc)
    assert first.case.reference == "130/2026"
    assert first.case.authority_ids == (("DIR3", "L01260316"),)
    assert first.case.authority_name == "Órgano de prueba"
    assert first.cpv == ("45232150",)
    assert [(a.kind, a.value, a.currency) for a in first.amounts] == [
        ("estimated", Decimal("64328.41"), "EUR"), ("total", Decimal("77837.38"), "EUR")
    ]
    assert first.document_deadline.day == date(2026, 11, 19)
    assert first.document_deadline.local_time is None
    assert first.submission_deadline.local_time == time(23, 59)
    assert [d.kind for d in first.documents] == ["additional", "legal", "technical"]
    assert first.documents[1].published_url == "https://example.invalid/pcap.pdf"
    assert missing.case.reference is None and missing.submission_deadline is None
    assert missing.amounts == missing.cpv == missing.documents == ()


def test_refresh_reopens_catalog_and_preserves_corrections(tmp_path):
    first_page = feed(entry(), next_url=NEXT)
    second_page = feed(entry("2"), entry())
    calls = []

    def handle(request):
        calls.append(str(request.url))
        assert request.method == "GET"
        assert request.headers.get("authorization") is None
        assert request.headers.get("cookie") is None
        return httpx.Response(200, headers={**XML_HEADERS, "set-cookie": "x=1"},
                              content=first_page if str(request.url) == FEED_URL else second_page)

    path = tmp_path / "catalog.sqlite"
    service = TenderService(TenderStore(path), client(handle))
    limited = service.refresh(max_pages=1)
    assert limited.pages == 1 and limited.new_revisions == 1 and limited.has_more
    done = service.refresh()
    assert done.pages == 2 and done.new_revisions == 1 and not done.has_more
    assert calls == [FEED_URL, FEED_URL, NEXT]
    first_id = service.search()[0].tender_id
    assert len(service.search(TenderFilters(text="HIDRÁULICA", cpv="45232150", status="PUB"))) == 2
    assert len(service.search(TenderFilters(limit=1, offset=1))) == 1
    assert service.search(TenderFilters(text="%' OR 1=1 --")) == ()
    assert service.get_tender(999) is None
    first_page = feed(entry(updated="2026-11-07T17:00:00+01:00", status="ANUL"), next_url=NEXT)
    reopened = TenderService(TenderStore(path), client(handle))
    assert reopened.refresh().new_revisions == 1
    # A same-timestamp correction is a distinct revision. Old data from page 2
    # remains in history without becoming current.
    first_page = feed(entry(updated="2026-11-07T17:00:00+01:00", status="ANUL", details=False))
    assert reopened.refresh().new_revisions == 1
    assert reopened.refresh().new_revisions == 0
    assert reopened.get_tender(first_id).current.publication.status == "ANUL"
    assert reopened.get_tender(first_id).current.publication.case.reference is None
    assert len(reopened.revisions(first_id)) == 3
    assert reopened.last_successful_sync().status == "succeeded"


def test_failed_page_keeps_committed_page_and_previous_success(tmp_path):
    fail = False

    def handle(request):
        if str(request.url) == NEXT and fail:
            return httpx.Response(503)
        return httpx.Response(200, headers=XML_HEADERS,
                              content=feed(entry(), next_url=NEXT) if str(request.url) == FEED_URL else feed())

    path = tmp_path / "catalog.sqlite"
    service = TenderService(TenderStore(path), client(handle, retries=0))
    assert service.last_successful_sync() is None
    success = service.refresh()
    fail = True
    failed = service.refresh()
    assert (failed.status, failed.error, failed.pages) == ("failed", "http_503", 1)
    reopened = TenderService(TenderStore(path), client(handle, retries=0))
    assert reopened.last_successful_sync() == success
    assert reopened.sync_history()[0] == failed
    assert len(reopened.search()) == 1


@pytest.mark.parametrize("url", [
    "http://contrataciondelestado.es/a.atom",
    "https://contrataciondelestado.es:444/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom",
    "https://contrataciondelestado.es@127.0.0.1/a.atom",
    FEED_URL + "?redirect=other",
    FEED_URL.replace("643/", "643/../"),
])
def test_next_url_is_rejected(url):
    with pytest.raises(PlacspError, match="invalid_page_url"):
        parse_page(feed(entry(), next_url=url))


@pytest.mark.parametrize(("body", "error"), [
    (b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///private">]><feed>&x;</feed>', "unsafe_xml"),
    ("<!DOCTYPE feed><feed/>".encode("utf-16"), "invalid_atom_data"),
    (b"<html/>", "not_atom_feed"),
    (b"<feed>", "invalid_atom_data"),
    (feed(entry(updated="2026-11-06")), "invalid_atom_data"),
])
def test_rejects_unsafe_or_invalid_xml(body, error):
    with pytest.raises(PlacspError, match=error):
        parse_page(body)


@pytest.mark.parametrize(("response", "error"), [
    (lambda: httpx.Response(302, headers={"location": "https://example.invalid"}), "http_302"),
    (lambda: httpx.Response(200, headers={"content-type": "text/html"}), "unexpected_content_type"),
    (lambda: httpx.Response(200, headers={**XML_HEADERS, "content-length": "1001"}), "response_too_large"),
    (lambda: httpx.Response(200, headers=XML_HEADERS, stream=httpx.ByteStream(b"x" * 1001)), "response_too_large"),
    (lambda: httpx.Response(200, headers={**XML_HEADERS, "content-encoding": "gzip"}), "unexpected_content_encoding"),
])
def test_response_restrictions(response, error):
    calls = []

    def handle(request):
        calls.append(request)
        return response()

    with pytest.raises(PlacspError, match=error):
        client(handle, max_bytes=1000).fetch_page()
    assert len(calls) == 1


def test_timeouts_retry_at_most_the_configured_count():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("non-public details", request=request)

    with pytest.raises(PlacspError, match="^timeout$"):
        client(handle, retries=1).fetch_page()
    assert len(calls) == 2


def test_pagination_cycle_is_reported_and_never_fetched(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, headers=XML_HEADERS, content=feed(next_url=FEED_URL))

    service = TenderService(TenderStore(tmp_path / "catalog.sqlite"), client(handle))
    assert service.refresh().error == "pagination_cycle"
    assert len(calls) == 1


def test_catalog_cannot_be_created_inside_repository():
    with pytest.raises(ValueError, match="outside the repository"):
        TenderStore("must-not-be-created.sqlite")
