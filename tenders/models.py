"""Typed records for the public tender catalog."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class TenderIdentity(Record):
    source: str
    entry_id: str


class CaseIdentity(Record):
    reference: str | None = None
    authority_ids: tuple[tuple[str | None, str], ...] = ()
    authority_name: str | None = None


class DocumentReference(Record):
    kind: Literal["legal", "technical", "additional"]
    reference: str | None = None
    published_url: str | None = None


class Amount(Record):
    kind: Literal["estimated", "total", "tax_exclusive"]
    value: Decimal
    currency: str | None = None


class Deadline(Record):
    day: date | None = None
    local_time: time | None = None


class Publication(Record):
    identity: TenderIdentity
    updated_at: datetime
    published_at: datetime | None = None
    official_url: str | None = None
    case: CaseIdentity
    title: str | None = None
    description: str | None = None
    status: str | None = None
    cpv: tuple[str, ...] = ()
    amounts: tuple[Amount, ...] = ()
    submission_deadline: Deadline | None = None
    document_deadline: Deadline | None = None
    documents: tuple[DocumentReference, ...] = ()


class Revision(Record):
    publication: Publication
    content_sha256: str
    observed_at: datetime


class Tender(Record):
    tender_id: int
    current: Revision


class TenderFilters(Record):
    text: str | None = None
    cpv: str | None = None
    status: str | None = None
    limit: int = 100
    offset: int = 0


class CatalogCounts(Record):
    cases: int
    publications: int
    revisions: int


class Synchronization(Record):
    sync_id: int
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "succeeded", "failed"]
    pages: int
    new_revisions: int
    has_more: bool | None
    error: str | None
