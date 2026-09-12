"""Local, transactional storage for repeatable procurement benchmarks."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from execution_metrics import ExecutionComparisonRecord


DEFAULT_STORE = Path(__file__).resolve().parent / ".indai_ma" / "comparisons.sqlite3"


class ComparisonStore:
    def __init__(self, path: Path = DEFAULT_STORE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS comparison_runs ("
                "operation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, "
                "payload TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def append(self, record: ExecutionComparisonRecord, *, batch_id: str,
               case_id: str, repetition: int, provider: str, model: str,
               config: dict, skipped_actions: int = 0, fallback: bool = False,
               plan_failed: bool = False):
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "batch_id": batch_id,
            "case_id": case_id,
            "repetition": repetition,
            "provider": provider,
            "model": model,
            "config": config,
            "skipped_actions": skipped_actions,
            "fallback": fallback,
            "plan_failed": plan_failed,
            "record": asdict(record),
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO comparison_runs VALUES (?, ?, ?)",
                (record.operation_id, batch_id,
                 json.dumps(payload, ensure_ascii=False, allow_nan=False)),
            )

    def read(self, batch_id: str):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM comparison_runs WHERE batch_id = ? ORDER BY rowid",
                (batch_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
