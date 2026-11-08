"""Transport-independent use cases for the local public tender catalog."""

import sqlite3

from .models import CatalogCounts, Revision, Synchronization, Tender, TenderFilters
from .placsp import FEED_URL, PlacspClient, PlacspError
from .store import TenderStore


class TenderService:
    def __init__(self, store: TenderStore, source: PlacspClient | None = None):
        self._store = store
        self._source = source or PlacspClient()

    def refresh(self, *, max_pages: int | None = None) -> Synchronization:
        ceiling = self._source.limits.max_pages
        count = ceiling if max_pages is None else max_pages
        if not 1 <= count <= ceiling:
            raise ValueError("Invalid synchronization page limit")
        sync_id = self._store.start_sync()
        url = FEED_URL
        seen = set()
        try:
            for _ in range(count):
                seen.add(url)
                page = self._source.fetch_page(url)
                self._store.record_page(sync_id, page.entries)
                url = page.next_url
                if url in seen:
                    raise PlacspError("pagination_cycle")
                if url is None:
                    break
        except PlacspError as error:
            return self._store.finish_sync(sync_id, error=str(error))
        except sqlite3.Error:
            return self._store.finish_sync(sync_id, error="catalog_write_failed")
        return self._store.finish_sync(sync_id, has_more=url is not None)

    def search(self, filters: TenderFilters | None = None) -> tuple[Tender, ...]:
        return self._store.search(filters or TenderFilters())

    def get_tender(self, tender_id: int) -> Tender | None:
        return self._store.get_tender(tender_id)

    def catalog_counts(self) -> CatalogCounts:
        return self._store.catalog_counts()

    def revisions(self, tender_id: int) -> tuple[Revision, ...]:
        return self._store.revisions(tender_id)

    def sync_history(self, limit: int = 100) -> tuple[Synchronization, ...]:
        return self._store.sync_history(limit)

    def last_successful_sync(self) -> Synchronization | None:
        return self._store.last_successful_sync()
