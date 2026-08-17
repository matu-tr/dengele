"""Carrying out a plan."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import recycle
from .errors import PlanBlocked
from .models import (
    TEMP_SUFFIX,
    Entry,
    EntryKind,
    Op,
    OpKind,
    PairConfig,
    Plan,
    Side,
    SnapRecord,
)
from .scan import Scan
from .snapshot import Snapshot

log = logging.getLogger(__name__)

#: Called with (completed, total, current op). Must be cheap and thread-safe.
ProgressCallback = Callable[[int, int, Op], None]


@dataclass(slots=True)
class ApplyOutcome:
    applied: int = 0
    bytes_copied: int = 0
    cancelled: bool = False
    #: Operations that failed, as (relative path, reason).
    failures: list[tuple[str, str]] = field(default_factory=list)


def apply(
    cfg: PairConfig,
    plan: Plan,
    a: Scan,
    b: Scan,
    snapshot: Snapshot,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    force: bool = False,
) -> ApplyOutcome:
    """Execute ``plan``, advancing the snapshot as operations succeed.

    Failures are collected rather than raised: one unreadable file should not
    abandon the remaining work. The snapshot only advances for operations that
    actually succeeded, so a failed path is simply retried next run.
    """
    if plan.blocked and not force:
        raise PlanBlocked(plan.blocked)

    outcome = ApplyOutcome()
    upserts: list[tuple[str, SnapRecord]] = []
    deletes: list[str] = []

    started_ms = int(time.time() * 1000)
    session = recycle.session_name(started_ms)
    total = len(plan.ops)

    for index, op in enumerate(plan.ops):
        if cancel is not None and cancel.is_set():
            outcome.cancelled = True
            break
        if progress is not None:
            progress(index, total, op)

        try:
            change = _run_op(cfg, op, a, b, session)
        except OSError as err:
            outcome.failures.append((op.rel, err.strerror or str(err)))
            continue

        outcome.applied += 1
        outcome.bytes_copied += op.size if op.kind is OpKind.COPY else 0
        if change is _DELETE_ROW:
            deletes.append(op.rel)
        elif change is not None:
            upserts.append((op.rel, change))

    snapshot.commit(cfg.id, upserts, deletes)
    if not outcome.cancelled and not outcome.failures:
        snapshot.set_last_sync(cfg.id, started_ms)

    for side in (Side.A, Side.B):
        try:
            recycle.prune(cfg.root(side), cfg.recycle_retention_days)
        except OSError as err:
            log.warning("could not prune recycle bin on side %s: %s", side.label, err)

    return outcome


#: Sentinel distinguishing "drop this snapshot row" from "write this record".
_DELETE_ROW = object()


def _run_op(cfg: PairConfig, op: Op, a: Scan, b: Scan, session: str):
    match op.kind:
        case OpKind.MKDIR:
            (cfg.root(op.side) / op.rel).mkdir(parents=True, exist_ok=True)
            return SnapRecord(kind=EntryKind.DIR, size=0, mtime_ms=0)

        case OpKind.COPY:
            entry = (a if op.side is Side.A else b).entries.get(op.rel)
            if entry is None:
                raise FileNotFoundError(f"{op.rel} vanished between scan and apply")
            _copy_entry(cfg.root(op.side) / op.rel, cfg.root(op.side.other) / op.rel, entry)
            return SnapRecord.from_entry(entry)

        case OpKind.DELETE:
            # Already gone is the outcome we wanted anyway.
            with contextlib.suppress(FileNotFoundError):
                recycle.recycle(cfg.root(op.side), op.rel, session)
            return _DELETE_ROW

        case OpKind.PRESERVE_LOSER:
            root = cfg.root(op.side)
            destination = root / op.renamed_to
            destination.parent.mkdir(parents=True, exist_ok=True)
            (root / op.rel).rename(destination)
            # Deliberately not recorded: next run reads the renamed copy as a
            # new file and propagates it to the other side.
            return None

        case OpKind.RECORD:
            entry = a.entries.get(op.rel) or b.entries.get(op.rel)
            if entry is None:
                raise FileNotFoundError(f"nothing to record for {op.rel}")
            return SnapRecord.from_entry(entry)

        case OpKind.DROP_RECORD:
            return _DELETE_ROW

    raise AssertionError(f"unhandled op kind: {op.kind}")


def _copy_entry(source: Path, destination: Path, entry: Entry) -> None:
    """Replicate one entry onto the other side, replacing whatever is there."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    if entry.kind is EntryKind.DIR:
        destination.mkdir(parents=True, exist_ok=True)
        return

    if entry.kind is EntryKind.SYMLINK:
        _remove_any(destination)
        os.symlink(entry.link_target, destination)
        return

    # Write to a sibling temp file and rename over the destination, so an
    # interrupted copy never leaves a truncated file where the real one was.
    temp = destination.with_name(f".{destination.name}{TEMP_SUFFIX}")
    _remove_any(temp)
    try:
        shutil.copyfile(source, temp)
        # Give the copy the source's timestamp, or the next run would see it as
        # changed and the metadata fast path would never hit.
        os.utime(temp, (entry.mtime_ms / 1000, entry.mtime_ms / 1000))
        _remove_any(destination)
        os.replace(temp, destination)
    except OSError:
        _remove_any(temp)
        raise


def _remove_any(path: Path) -> None:
    """Remove a file, directory or symlink if it is there."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        pass
