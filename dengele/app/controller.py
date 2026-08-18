"""Orchestrating syncs: threads, state, and the signals the UI listens to."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from dengele.engine import (
    ApplyOutcome,
    EngineError,
    Op,
    Plan,
    Side,
    Snapshot,
    plan_pair,
    sync_pair,
)

from . import paths
from .config import Config, Pair, WatchMode

log = logging.getLogger(__name__)

#: Filesystem events are ignored for this long after a sync finishes, so a
#: sync's own writes do not immediately schedule another one.
SETTLE_SECONDS = 3.0

#: Progress is reported at most this often, so a large tree does not flood the
#: event loop with one signal per file.
PROGRESS_INTERVAL = 0.08


@dataclass(slots=True)
class PairStatus:
    """What the UI shows for a pair between syncs."""

    running: bool = False
    last_sync_ms: int | None = None
    last_error: str | None = None
    #: Reason the delete guard blocked the last plan, if it did.
    blocked: str | None = None
    copied: int = 0
    deleted: int = 0
    conflicts: int = 0
    #: Live progress while running: (done, total, description).
    progress: tuple[int, int, str] = (0, 0, "")


@dataclass(slots=True)
class _Running:
    cancel: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.monotonic)


class Controller(QObject):
    """Single source of truth for sync state.

    Every sync goes through here, whoever started it — the user, the file
    watcher, or the scheduler. The Rust predecessor let the UI track only the
    syncs it had started itself, so a sync kicked off in the background left the
    window looking idle while it was in fact busy. Here the UI renders
    :meth:`status`, so there is only one answer.
    """

    #: (pair_id) — status for this pair changed; re-read it.
    pair_changed = Signal(str)
    #: (pair_id, plan, outcome)
    sync_finished = Signal(str, object, object)
    #: (pair_id, message)
    sync_failed = Signal(str, str)
    #: Emitted when the pair list or global settings changed.
    config_changed = Signal()

    def __init__(self, config: Config, database: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self._snapshot = Snapshot(database or paths.database_file())
        self._pool = QThreadPool(self)
        # Syncs are I/O bound on the same disks; running them one at a time
        # keeps progress readable and avoids two pairs fighting over a drive.
        self._pool.setMaxThreadCount(1)

        self._lock = threading.Lock()
        self._running: dict[str, _Running] = {}
        self._statuses: dict[str, PairStatus] = {}
        self._suppressed: dict[str, float] = {}

    # -- state ----------------------------------------------------------

    def status(self, pair_id: str) -> PairStatus:
        with self._lock:
            return self._statuses.setdefault(pair_id, PairStatus())

    def is_running(self, pair_id: str) -> bool:
        with self._lock:
            return pair_id in self._running

    def any_running(self) -> bool:
        with self._lock:
            return bool(self._running)

    def is_suppressed(self, pair_id: str) -> bool:
        with self._lock:
            return time.monotonic() < self._suppressed.get(pair_id, 0.0)

    # -- running syncs --------------------------------------------------

    def start(self, pair_id: str, force: bool = False) -> bool:
        """Queue a sync. Returns False if this pair is already syncing."""
        pair = self.config.pair(pair_id)
        if pair is None or not pair.enabled:
            return False

        with self._lock:
            if pair_id in self._running:
                log.debug("sync already in progress for %s; ignoring", pair.name)
                return False
            state = _Running()
            self._running[pair_id] = state
            status = self._statuses.setdefault(pair_id, PairStatus())
            status.running = True
            status.progress = (0, 0, "scanning…")

        self.pair_changed.emit(pair_id)
        self._pool.start(_SyncTask(self, pair.copy(), state.cancel, force))
        return True

    def start_all(self) -> None:
        for pair in self.config.pairs:
            if pair.enabled:
                self.start(pair.id)

    def cancel(self, pair_id: str) -> bool:
        with self._lock:
            state = self._running.get(pair_id)
            if state is None:
                return False
            state.cancel.set()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            for state in self._running.values():
                state.cancel.set()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until queued syncs finish. Used on shutdown and in tests."""
        return self._pool.waitForDone(timeout_ms)

    # -- previewing -----------------------------------------------------

    def preview(self, pair_id: str) -> Plan:
        """Compute what a sync would do, without doing it.

        Safe to call while a sync is running: the snapshot is read through its
        own connection rather than a shared lock.
        """
        pair = self.config.pair(pair_id)
        if pair is None:
            raise EngineError(f"no such pair: {pair_id}")
        plan, _, _ = plan_pair(pair.engine, self._snapshot)
        return plan

    # -- config ---------------------------------------------------------

    def forget_pair(self, pair_id: str) -> None:
        """Drop a pair's history so re-adding the folders starts clean."""
        self.cancel(pair_id)
        self._snapshot.forget_pair(pair_id)
        with self._lock:
            self._statuses.pop(pair_id, None)

    def last_sync(self, pair_id: str) -> int | None:
        return self._snapshot.last_sync(pair_id)

    def due_pairs(self, last_run: dict[str, float]) -> list[str]:
        """Pairs whose schedule says they should sync now.

        Change-driven pairs are included on an hourly floor as well: filesystem
        events go missing across sleep, on network volumes, and when the event
        queue overflows, so a periodic full scan is the safety net.
        """
        now = time.monotonic()
        due = []
        for pair in self.config.pairs:
            if not pair.enabled or pair.watch is WatchMode.MANUAL:
                continue
            period = (
                max(1, pair.interval_minutes) * 60 if pair.watch is WatchMode.INTERVAL else 3600
            )
            if now - last_run.get(pair.id, -period) >= period:
                due.append(pair.id)
        return due

    # -- called from worker threads -------------------------------------

    def _on_progress(self, pair_id: str, done: int, total: int, op: Op) -> None:
        with self._lock:
            status = self._statuses.setdefault(pair_id, PairStatus())
            status.progress = (done, total, f"{_verb(op)} {op.rel}")
        self.pair_changed.emit(pair_id)

    def _on_finished(self, pair_id: str, plan: Plan, outcome: ApplyOutcome) -> None:
        with self._lock:
            self._running.pop(pair_id, None)
            self._suppressed[pair_id] = time.monotonic() + SETTLE_SECONDS
            status = self._statuses.setdefault(pair_id, PairStatus())
            status.running = False
            status.last_error = None
            status.blocked = plan.blocked
            status.copied = plan.stats.copies
            status.deleted = plan.stats.deletes
            status.conflicts = len(plan.conflicts)
            status.progress = (0, 0, "")
            if not outcome.cancelled:
                status.last_sync_ms = int(time.time() * 1000)

        self.pair_changed.emit(pair_id)
        self.sync_finished.emit(pair_id, plan, outcome)

    def _on_failed(self, pair_id: str, message: str) -> None:
        with self._lock:
            self._running.pop(pair_id, None)
            self._suppressed[pair_id] = time.monotonic() + SETTLE_SECONDS
            status = self._statuses.setdefault(pair_id, PairStatus())
            status.running = False
            status.last_error = message
            status.progress = (0, 0, "")

        self.pair_changed.emit(pair_id)
        self.sync_failed.emit(pair_id, message)


class _SyncTask(QRunnable):
    """Runs one sync off the UI thread.

    Results come back through the controller's signals, which Qt delivers to
    the main thread — the only thread allowed to touch widgets.
    """

    def __init__(
        self,
        controller: Controller,
        pair: Pair,
        cancel: threading.Event,
        force: bool,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._pair = pair
        self._cancel = cancel
        self._force = force
        self._last_emit = 0.0

    def run(self) -> None:
        pair_id = self._pair.id
        try:
            plan, outcome = sync_pair(
                self._pair.engine,
                self._controller._snapshot,
                progress=self._progress,
                cancel=self._cancel,
                force=self._force,
            )
        except EngineError as err:
            self._controller._on_failed(pair_id, str(err))
        except OSError as err:
            self._controller._on_failed(pair_id, err.strerror or str(err))
        except Exception:
            # A crash in a worker must not take the app down or, worse, leave
            # the pair marked as permanently running.
            log.exception("unexpected failure syncing %s", self._pair.name)
            self._controller._on_failed(pair_id, "unexpected error; see the log for details")
        else:
            self._controller._on_finished(pair_id, plan, outcome)

    def _progress(self, done: int, total: int, op: Op) -> None:
        now = time.monotonic()
        # Always report the first and last operation so the bar starts and
        # finishes cleanly, whatever the throttle says.
        if done and done + 1 != total and now - self._last_emit < PROGRESS_INTERVAL:
            return
        self._last_emit = now
        self._controller._on_progress(self._pair.id, done, total, op)


def _verb(op: Op) -> str:
    from dengele.engine import OpKind

    return {
        OpKind.MKDIR: "creating",
        OpKind.COPY: "copying",
        OpKind.DELETE: "removing",
        OpKind.PRESERVE_LOSER: "preserving",
        OpKind.RECORD: "recording",
        OpKind.DROP_RECORD: "recording",
    }.get(op.kind, "syncing")


def describe_side(side: Side) -> str:
    return f"folder {side.label}"
