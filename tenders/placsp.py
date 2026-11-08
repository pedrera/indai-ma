"""Bounded reader for the official PLACSP hosted-profile Atom distribution."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from time import monotonic, sleep
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

from .models import Amount, CaseIdentity, Deadline, DocumentReference, Publication, TenderIdentity

SOURCE = "placsp_hosted_profiles_excluding_minor_contracts"
HOSTS = frozenset({"contrataciondelsectorpublico.gob.es", "contrataciondelestado.es"})
FEED_PATH = "/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3"
FEED_URL = f"https://contrataciondelsectorpublico.gob.es{FEED_PATH}.atom"
PAGE_PATH = re.compile(re.escape(FEED_PATH) + r"(?:_\d{8}_\d{6}(?:_\d+)?)?\.atom", re.ASCII)
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
    "ext-basic": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
}


class PlacspError(RuntimeError):
    """A classified failure safe to record without external response content."""


@dataclass(frozen=True)
class Limits:
    max_pages: int = 2
    max_bytes: int = 32 * 1024 * 1024
    timeout_seconds: float = 30
    retries: int = 1

    def __post_init__(self):
        if not (1 <= self.max_pages <= 100 and 1 <= self.max_bytes <= 64 * 1024 * 1024
                and 0 < self.timeout_seconds <= 120 and 0 <= self.retries <= 3):
            raise ValueError("Invalid PLACSP limits")


@dataclass(frozen=True)
class SourcePage:
    entries: tuple[Publication, ...]
    next_url: str | None
    updated_at: datetime | None


def validate_page_url(url: str) -> None:
    if not url.isascii() or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise PlacspError("invalid_page_url")
    try:
        parsed = urlsplit(url)
        allowed_netlocs = HOSTS | {f"{host}:443" for host in HOSTS}
        valid = (parsed.scheme == "https" and parsed.netloc in allowed_netlocs
                 and PAGE_PATH.fullmatch(parsed.path) and "?" not in url and "#" not in url)
    except ValueError:
        valid = False
    if not valid:
        raise PlacspError("invalid_page_url")


def _text(node, path):
    value = node.findtext(path, namespaces=NS)
    return (value.strip() or None) if value is not None else None


def _instant(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Missing offset")
    return parsed.astimezone(timezone.utc)


def _deadline(node, period):
    path = f"cac:TenderingProcess/cac:{period}"
    day = _text(node, f"{path}/cbc:EndDate")
    clock = _text(node, f"{path}/cbc:EndTime")
    if day is None and clock is None:
        return None
    return Deadline(day=date.fromisoformat(day) if day else None,
                    local_time=time.fromisoformat(clock) if clock else None)


def _entry(node):
    entry_id = _text(node, "a:id")
    updated = _instant(_text(node, "a:updated"))
    folder = node.find("ext:ContractFolderStatus", NS)
    if not entry_id or updated is None or folder is None:
        raise PlacspError("missing_entry_identity_or_folder")
    party_path = "ext:LocatedContractingParty/cac:Party"
    authority_ids = {((x.get("schemeName") or "").strip() or None, x.text.strip())
                     for x in folder.findall(f"{party_path}/cac:PartyIdentification/cbc:ID", NS)
                     if x.text and x.text.strip()}
    project_path = "cac:ProcurementProject"
    amounts = []
    for field, kind in (("EstimatedOverallContractAmount", "estimated"),
                        ("TotalAmount", "total"), ("TaxExclusiveAmount", "tax_exclusive")):
        element = folder.find(f"{project_path}/cac:BudgetAmount/cbc:{field}", NS)
        if element is not None and element.text and element.text.strip():
            value = Decimal(element.text.strip())
            if not value.is_finite():
                raise ValueError("Non-finite amount")
            amounts.append(Amount(kind=kind, value=value, currency=element.get("currencyID")))
    cpv_paths = (project_path, "cac:ProcurementProjectLot/cac:ProcurementProject")
    cpv = {x.text.strip() for path in cpv_paths for x in folder.findall(
        f"{path}/cac:RequiredCommodityClassification/cbc:ItemClassificationCode", NS)
        if x.text and x.text.strip()}
    documents = set()
    for field, kind in (("LegalDocumentReference", "legal"), ("TechnicalDocumentReference", "technical"),
                        ("AdditionalDocumentReference", "additional"), ("AditionalDocumentReference", "additional")):
        for doc in folder.findall(f"cac:{field}", NS):
            reference = _text(doc, "cbc:ID")
            url = _text(doc, "cac:Attachment/cac:ExternalReference/cbc:URI")
            if reference is not None or url is not None:
                documents.add((kind, reference, url))
    official_url = next((link.get("href") for link in node.findall("a:link", NS)
                         if link.get("rel", "alternate") == "alternate" and link.get("href")), None)
    return Publication(
        identity=TenderIdentity(source=SOURCE, entry_id=entry_id),
        updated_at=updated, published_at=_instant(_text(node, "a:published")), official_url=official_url,
        case=CaseIdentity(reference=_text(folder, "cbc:ContractFolderID"),
                          authority_ids=tuple(sorted(authority_ids, key=lambda x: (x[0] or "", x[1]))),
                          authority_name=_text(folder, f"{party_path}/cac:PartyName/cbc:Name")),
        title=_text(node, "a:title") or _text(folder, f"{project_path}/cbc:Name"),
        description=_text(folder, f"{project_path}/cbc:Description") or _text(node, "a:summary"),
        status=(_text(folder, "ext-basic:ContractFolderStatusCode")
                or _text(folder, "cbc:ContractFolderStatusCode")),
        cpv=tuple(sorted(cpv)), amounts=tuple(amounts),
        submission_deadline=_deadline(folder, "TenderSubmissionDeadlinePeriod"),
        document_deadline=_deadline(folder, "DocumentAvailabilityPeriod"),
        documents=tuple(DocumentReference(kind=kind, reference=ref, published_url=url)
                        for kind, ref, url in sorted(documents, key=lambda x: (x[0], x[1] or "", x[2] or ""))),
    )


def parse_page(body: bytes, *, max_bytes: int = Limits().max_bytes) -> SourcePage:
    if len(body) > max_bytes:
        raise PlacspError("response_too_large")
    try:
        text = body.decode("utf-8-sig")
        if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise PlacspError("unsafe_xml")
        root = ET.fromstring(text)
        if root.tag != f"{{{NS['a']}}}feed":
            raise PlacspError("not_atom_feed")
        links = [x.get("href") for x in root.findall("a:link", NS) if x.get("rel") == "next"]
        if len(links) > 1 or (links and not links[0]):
            raise PlacspError("invalid_next_link")
        if links:
            validate_page_url(links[0])
        entries = tuple(_entry(node) for node in root.findall("a:entry", NS))
        return SourcePage(entries, links[0] if links else None, _instant(_text(root, "a:updated")))
    except (ET.ParseError, ValueError, InvalidOperation, RecursionError) as error:
        raise PlacspError("invalid_atom_data") from error


class PlacspClient:
    def __init__(self, limits: Limits | None = None, *, transport: httpx.BaseTransport | None = None):
        self.limits = limits or Limits()
        self._transport = transport

    def fetch_page(self, url: str = FEED_URL) -> SourcePage:
        validate_page_url(url)
        deadline = monotonic() + self.limits.timeout_seconds
        for attempt in range(self.limits.retries + 1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PlacspError("timeout")
            try:
                return self._fetch(url, remaining, deadline)
            except httpx.TimeoutException:
                failure = "timeout"
            except httpx.TransportError:
                failure = "transport_error"
            except _TransientStatus as error:
                failure = str(error)
            if attempt == self.limits.retries:
                raise PlacspError(failure)
            sleep(min(0.25 * (2 ** attempt), max(0, deadline - monotonic())))
        raise AssertionError("Unreachable retry state")

    def _fetch(self, url: str, timeout: float, deadline: float) -> SourcePage:
        # Each attempt has its own cookie jar and never sends cookies from prior responses.
        with httpx.Client(verify=True, trust_env=False, follow_redirects=False,
                          timeout=timeout, transport=self._transport) as client:
            with client.stream("GET", url, headers={
                "Accept": "application/atom+xml, application/xml",
                "Accept-Encoding": "identity", "User-Agent": "indAI-MA/PLACSP",
            }) as response:
                status = response.status_code
                if status in {408, 429, 500, 502, 503, 504}:
                    raise _TransientStatus(f"http_{status}")
                if status != 200:
                    raise PlacspError(f"http_{status}")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime not in {"application/atom+xml", "application/xml", "text/xml"}:
                    raise PlacspError("unexpected_content_type")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise PlacspError("unexpected_content_encoding")
                length = response.headers.get("content-length")
                if length is not None and (not length.isascii() or not length.isdecimal()
                                           or len(length) > 10 or int(length) > self.limits.max_bytes):
                    raise PlacspError("response_too_large")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() >= deadline:
                        raise PlacspError("timeout")
                    if len(body) + len(chunk) > self.limits.max_bytes:
                        raise PlacspError("response_too_large")
                    body.extend(chunk)
                if monotonic() >= deadline:
                    raise PlacspError("timeout")
        return parse_page(bytes(body), max_bytes=self.limits.max_bytes)


class _TransientStatus(RuntimeError):
    pass
