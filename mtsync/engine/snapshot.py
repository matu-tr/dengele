"""Persistent record of what both sides last agreed on.

This is the planner's third input. Unlike the predecessor's hash table — which
was written on every run and never read — deleting a row here changes
behaviour, so rows are only written after an operation actually succeeds.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from .models import EntryKind, SnapRecord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    pair_id     TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime_ms    INTEGER NOT NULL,
    link_target TEXT,
    PRIMARY KEY (pair_id, rel_path)
);
CREATE TABLE IF NOT EXISTS pair_meta (
    pair_id      TEXT PRIMARY KEY,
    last_sync_ms INTEGER NOT NULL
);
"""


class Snapshot:
    """Per-pair sync state, backed by SQLite.

    A connection is opened per thread rather than shared behind one lock. The
    Rust predecessor guarded a single connection with a global mutex, which
    meant computing a preview blocked for as long as a sync was running — the
    app looked frozen. WAL mode lets readers and the writer proceed together,
    so a preview stays responsive while a sync is in progress.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # An in-memory database lives only as long as its connection, so it has
        # to be shared; tests are single-threaded, which makes that safe.
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._shared = self._new_connection()
        else:
            self._initialise(self._connection())

    # -- connections ----------------------------------------------------

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        self._initialise(conn)
        return conn

    def _connection(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    @staticmethod
    def _initialise(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()

    def close(self) -> None:
        for conn in (self._shared, getattr(self._local, "conn", None)):
            if conn is not None:
                conn.close()
        self._shared = None
        self._local = threading.local()

    # -- reads ----------------------------------------------------------

    def load(self, pair_id: str) -> dict[str, SnapRecord]:
        """Every recorded path for a pair."""
        rows = self._connection().execute(
            "SELECT rel_path, kind, size, mtime_ms, link_target FROM sync_state WHERE pair_id = ?",
            (pair_id,),
        )
        return {
            row["rel_path"]: SnapRecord(
                kind=EntryKind(row["kind"]),
                size=row["size"],
                mtime_ms=row["mtime_ms"],
                link_target=row["link_target"],
            )
            for row in rows
        }

    def last_sync(self, pair_id: str) -> int | None:
        row = (
            self._connection()
            .execute("SELECT last_sync_ms FROM pair_meta WHERE pair_id = ?", (pair_id,))
            .fetchone()
        )
        return None if row is None else row["last_sync_ms"]

    # -- writes ---------------------------------------------------------

    def commit(
        self,
        pair_id: str,
        upserts: Iterable[tuple[str, SnapRecord]] = (),
        deletes: Iterable[str] = (),
    ) -> None:
        """Write upserts and deletions in one transaction.

        A partially applied snapshot is worse than a stale one: it would make
        the planner believe a path was reconciled when it was not.
        """
        conn = self._connection()
        with conn:
            conn.executemany(
                "INSERT INTO sync_state"
                " (pair_id, rel_path, kind, size, mtime_ms, link_target)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(pair_id, rel_path) DO UPDATE SET"
                "   kind = excluded.kind,"
                "   size = excluded.size,"
                "   mtime_ms = excluded.mtime_ms,"
                "   link_target = excluded.link_target",
                [
                    (pair_id, rel, rec.kind.value, rec.size, rec.mtime_ms, rec.link_target)
                    for rel, rec in upserts
                ],
            )
            conn.executemany(
                "DELETE FROM sync_state WHERE pair_id = ? AND rel_path = ?",
                [(pair_id, rel) for rel in deletes],
            )

    def set_last_sync(self, pair_id: str, when_ms: int) -> None:
        conn = self._connection()
        with conn:
            conn.execute(
                "INSERT INTO pair_meta (pair_id, last_sync_ms) VALUES (?, ?)"
                " ON CONFLICT(pair_id) DO UPDATE SET last_sync_ms = excluded.last_sync_ms",
                (pair_id, when_ms),
            )

    def forget_pair(self, pair_id: str) -> None:
        """Drop all history for a pair, e.g. when the user removes it."""
        conn = self._connection()
        with conn:
            conn.execute("DELETE FROM sync_state WHERE pair_id = ?", (pair_id,))
            conn.execute("DELETE FROM pair_meta WHERE pair_id = ?", (pair_id,))
