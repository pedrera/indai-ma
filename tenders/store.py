"""SQLite catalog with transactional page writes and immutable revisions."""

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import CatalogCounts, Publication, Revision, Synchronization, Tender, TenderFilters

SCHEMA = """
CREATE TABLE IF NOT EXISTS tender (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    UNIQUE (source, entry_id)
);
CREATE TABLE IF NOT EXISTS revision (
    id INTEGER PRIMARY KEY,
    tender_id INTEGER NOT NULL REFERENCES tender(id),
    updated_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    search_text TEXT NOT NULL,
    UNIQUE (tender_id, updated_at, content_sha256)
);
CREATE INDEX IF NOT EXISTS revision_latest ON revision(tender_id, updated_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS synchronization (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    pages INTEGER NOT NULL DEFAULT 0,
    new_revisions INTEGER NOT NULL DEFAULT 0,
    has_more INTEGER,
    error TEXT
);
"""

CURRENT = """
SELECT t.id AS tender_id, r.*
FROM tender AS t
JOIN revision AS r ON r.id = (
    SELECT latest.id FROM revision AS latest WHERE latest.tender_id = t.id
    ORDER BY latest.updated_at DESC, latest.id DESC LIMIT 1
)
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _content_hash(publication: Publication) -> str:
    excluded = {"identity", "updated_at"}
    if publication.description is None:
        excluded.add("description")
    payload = publication.model_dump(mode="json", exclude=excluded)
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _revision(row) -> Revision:
    return Revision(publication=Publication.model_validate_json(row["payload"]),
                    content_sha256=row["content_sha256"], observed_at=row["observed_at"])


def _synchronization(row) -> Synchronization:
    return Synchronization(sync_id=row["id"], **{key: row[key] for key in row.keys() if key != "id"})


class TenderStore:
    """Use an existing private directory outside the repository and web roots.

    The caller provisions the directory and its Windows ACL before use.
    """

    def __init__(self, path: str | Path):
        candidate = Path(path)
        self.path = candidate.resolve()
        restricted = [Path(__file__).resolve().parents[1]]
        if os.name == "nt":
            restricted.extend(Path(value).resolve() for value in (
                os.environ.get("PUBLIC"), os.environ.get("ONEDRIVE"),
            ) if value)
        if (candidate.is_symlink() or any(self.path.is_relative_to(root) for root in restricted)
                or not self.path.parent.is_dir()):
            raise ValueError("Catalog requires an existing private directory outside the repository")
        if os.name != "nt" and self.path.parent.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("Catalog directory must restrict access to its owner")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not self.path.is_file():
                raise ValueError("Catalog path must be a regular file") from None
        else:
            os.close(fd)
        with self._connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def start_sync(self) -> int:
        with self._connection() as connection:
            return connection.execute(
                "INSERT INTO synchronization (started_at, status) VALUES (?, 'running')", (_now(),)
            ).lastrowid

    def record_page(self, sync_id: int, entries: tuple[Publication, ...]) -> None:
        observed_at = _now()
        with self._connection() as connection:
            count = 0
            for item in entries:
                identity = (item.identity.source, item.identity.entry_id)
                connection.execute(
                    "INSERT INTO tender(source, entry_id) VALUES (?, ?) ON CONFLICT DO NOTHING", identity
                )
                tender_id = connection.execute(
                    "SELECT id FROM tender WHERE source = ? AND entry_id = ?", identity
                ).fetchone()["id"]
                updated = item.updated_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
                search_text = " ".join(filter(None, (item.title, item.case.reference, item.case.authority_name)))
                cursor = connection.execute(
                    """INSERT INTO revision(tender_id, updated_at, content_sha256, observed_at, payload, search_text)
                       VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                    (tender_id, updated, _content_hash(item), observed_at,
                     item.model_dump_json(), search_text.casefold()),
                )
                count += cursor.rowcount
            connection.execute(
                "UPDATE synchronization SET pages = pages + 1, new_revisions = new_revisions + ? WHERE id = ?",
                (count, sync_id),
            )

    def finish_sync(self, sync_id: int, *, has_more: bool | None = None,
                    error: str | None = None) -> Synchronization:
        status = "succeeded" if error is None else "failed"
        with self._connection() as connection:
            row = connection.execute(
                """UPDATE synchronization SET finished_at = ?, status = ?, has_more = ?, error = ?
                   WHERE id = ? RETURNING *""",
                (_now(), status, has_more, error, sync_id),
            ).fetchone()
            return _synchronization(row)

    def sync_history(self, limit: int = 100) -> tuple[Synchronization, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("Invalid synchronization history limit")
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM synchronization ORDER BY id DESC LIMIT ?", (limit,))
            return tuple(_synchronization(row) for row in rows)

    def last_successful_sync(self) -> Synchronization | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM synchronization WHERE status = 'succeeded' ORDER BY finished_at DESC, id DESC LIMIT 1"
            ).fetchone()
            return _synchronization(row) if row else None

    def get_tender(self, tender_id: int) -> Tender | None:
        with self._connection() as connection:
            row = connection.execute(CURRENT + " WHERE t.id = ?", (tender_id,)).fetchone()
            return Tender(tender_id=tender_id, current=_revision(row)) if row else None

    def catalog_counts(self) -> CatalogCounts:
        # Group only when both the case reference and a structured authority ID exist.
        with self._connection() as connection:
            row = connection.execute("""
                WITH current_publications AS (""" + CURRENT + """),
                identities AS (
                    SELECT tender_id, source, nullif(json_extract(payload, '$.case.reference'), '') AS reference,
                           (SELECT json_array(json_extract(value, '$[0]'), json_extract(value, '$[1]'))
                            FROM json_each(payload, '$.case.authority_ids')
                            WHERE json_extract(value, '$[0]') IN ('DIR3', 'ID_PLATAFORMA', 'ID_OC_PLAT')
                              AND nullif(json_extract(value, '$[1]'), '') IS NOT NULL
                            ORDER BY CASE json_extract(value, '$[0]') WHEN 'DIR3' THEN 0
                                     WHEN 'ID_PLATAFORMA' THEN 1 ELSE 2 END, key LIMIT 1) AS authority
                    FROM current_publications JOIN tender ON tender.id = tender_id
                )
                SELECT count(DISTINCT CASE WHEN reference IS NOT NULL AND authority IS NOT NULL
                           THEN json_array(source, authority, reference)
                           ELSE json_array(tender_id) END) AS cases,
                       count(*) AS publications, (SELECT count(*) FROM revision) AS revisions
                FROM identities
            """).fetchone()
            return CatalogCounts(**dict(row))

    def revisions(self, tender_id: int) -> tuple[Revision, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM revision WHERE tender_id = ? ORDER BY updated_at DESC, id DESC", (tender_id,)
            )
            return tuple(_revision(row) for row in rows)

    def search(self, filters: TenderFilters) -> tuple[Tender, ...]:
        if not (1 <= filters.limit <= 500 and filters.offset >= 0):
            raise ValueError("Invalid search page")
        sql = CURRENT + """
        WHERE (? IS NULL OR instr(r.search_text, ?) > 0)
          AND (? IS NULL OR json_extract(r.payload, '$.status') = ?)
          AND (? IS NULL OR EXISTS (SELECT 1 FROM json_each(r.payload, '$.cpv') WHERE value = ?))
        ORDER BY r.updated_at DESC, t.id
        LIMIT ? OFFSET ?
        """
        term = filters.text.casefold() if filters.text else None
        params = (term, term, filters.status, filters.status, filters.cpv, filters.cpv,
                  filters.limit, filters.offset)
        with self._connection() as connection:
            rows = connection.execute(sql, params)
            return tuple(Tender(tender_id=row["tender_id"], current=_revision(row)) for row in rows)
