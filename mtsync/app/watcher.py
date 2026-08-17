"""Noticing filesystem changes, and the clock that backs them up."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mtsync.engine import RECYCLE_DIR
from mtsync.engine.models import TEMP_SUFFIX

from .config import WatchMode
from .controller import Controller

log = logging.getLogger(__name__)

#: Events are collected for this long before a sync is triggered, so saving a
#: folder full of files results in one sync rather than hundreds.
DEBOUNCE_SECONDS = 1.5

#: How often the scheduler checks whether any pair has come due.
TICK_SECONDS = 20.0


class Watchers:
    """Owns one filesystem observer per change-driven pair.

    Rebuilding the whole set whenever the configuration changes is both simple
    and correct: stopping an observer stops its thread, so there is nothing to
    reconcile.
    """

    def __init__(self, controller: Controller) -> None:
        self._controller = controller
        self._observer: Observer | None = None
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def rebuild(self) -> None:
        self.stop()

        pairs = [
            p for p in self._controller.config.pairs if p.enabled and p.watch is WatchMode.ON_CHANGE
        ]
        if not pairs:
            return

        observer = Observer()
        watched = False
        for pair in pairs:
            handler = _PairHandler(self, pair.id)
            for root in (pair.engine.path_a, pair.engine.path_b):
                try:
                    observer.schedule(handler, str(root), recursive=True)
                    watched = True
                except OSError as err:
                    # A missing or unreadable root is reported when the pair is
                    # synced; failing to watch it must not stop the others.
                    log.warning("cannot watch %s: %s", root, err)

        if not watched:
            return

        observer.daemon = True
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    def _schedule(self, pair_id: str) -> None:
        """Start (or restart) this pair's debounce window."""
        with self._lock:
            existing = self._timers.get(pair_id)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(DEBOUNCE_SECONDS, self._fire, args=(pair_id,))
            timer.daemon = True
            self._timers[pair_id] = timer
            timer.start()

    def _fire(self, pair_id: str) -> None:
        with self._lock:
            self._timers.pop(pair_id, None)
        if self._controller.is_suppressed(pair_id) or self._controller.is_running(pair_id):
            return
        self._controller.start(pair_id)


class _PairHandler(FileSystemEventHandler):
    def __init__(self, watchers: Watchers, pair_id: str) -> None:
        self._watchers = watchers
        self._pair_id = pair_id

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Our own writes — the recycle bin, temp files, conflict copies — must
        # not start another sync, or the two roots would keep waking each other.
        if is_engine_bookkeeping(event.src_path) and is_engine_bookkeeping(
            getattr(event, "dest_path", "") or event.src_path
        ):
            return
        self._watchers._schedule(self._pair_id)


def is_engine_bookkeeping(path: str | Path) -> bool:
    parts = Path(path).parts
    return any(part == RECYCLE_DIR or part.endswith(TEMP_SUFFIX) for part in parts)


class Scheduler:
    """Background clock that starts syncs when their schedule says so."""

    def __init__(self, controller: Controller) -> None:
        self._controller = controller
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_run: dict[str, float] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="mt-sync-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                for pair_id in self._controller.due_pairs(self._last_run):
                    self._last_run[pair_id] = time.monotonic()
                    self._controller.start(pair_id)
            except Exception:
                # The scheduler must survive anything a single pair throws, or
                # every later sync stops happening silently.
                log.exception("scheduler tick failed")
