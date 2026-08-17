"""Snapshot-based bidirectional folder synchronization.

The engine compares three states for every relative path: side A, side B, and
the *snapshot* — what both sides agreed on at the end of the last successful
sync. That third input is what lets it tell "deleted over there" apart from
"newly created over here", which a plain two-pass mirror cannot do.

Work happens in two phases. :func:`plan_pair` produces a :class:`Plan` without
touching the filesystem; :func:`apply` carries it out. Callers can inspect or
refuse a plan in between, which is what makes the delete guard useful rather
than merely obstructive.

Nothing in this package imports Qt, so all of it is testable without a GUI.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .apply import ApplyOutcome, ProgressCallback, apply
from .errors import AccessError, Cancelled, EngineError, PlanBlocked, RootError
from .exclude import ExcludeError, ExcludeSet, validate_patterns
from .hashing import files_equal, hash_file
from .models import (
    MTIME_TOLERANCE_MS,
    RECYCLE_DIR,
    ROOT_MARKER,
    Conflict,
    ConflictKind,
    ConflictPolicy,
    Entry,
    EntryKind,
    Op,
    OpKind,
    PairConfig,
    Plan,
    PlanStats,
    Side,
    SnapRecord,
    default_excludes,
)
from .planner import conflict_name, entry_changed, plan, validate_roots
from .recycle import prune, recycle, session_name
from .scan import Scan, scan_root
from .snapshot import Snapshot

__all__ = [
    "MTIME_TOLERANCE_MS",
    "RECYCLE_DIR",
    "ROOT_MARKER",
    "AccessError",
    "ApplyOutcome",
    "Cancelled",
    "Conflict",
    "ConflictKind",
    "ConflictPolicy",
    "EngineError",
    "Entry",
    "EntryKind",
    "ExcludeError",
    "ExcludeSet",
    "Op",
    "OpKind",
    "PairConfig",
    "Plan",
    "PlanBlocked",
    "PlanStats",
    "ProgressCallback",
    "RootError",
    "Scan",
    "Side",
    "SnapRecord",
    "Snapshot",
    "apply",
    "check_access",
    "conflict_name",
    "default_excludes",
    "entry_changed",
    "files_equal",
    "hash_file",
    "plan",
    "plan_pair",
    "prune",
    "recycle",
    "scan_root",
    "session_name",
    "sync_pair",
    "validate_patterns",
    "validate_roots",
]


def check_access(root: Path) -> None:
    """Confirm the operating system will actually let us read ``root``.

    macOS gates Desktop, Documents, Downloads and removable volumes behind its
    privacy system. An app that has not been granted access does not get a
    clean error from every API — the first read can simply block while the
    consent prompt is pending, which is precisely how the previous version of
    this app appeared to freeze. Probing one directory listing up front turns
    that into an error the UI can explain.
    """
    try:
        with os.scandir(root) as it:
            next(iter(it), None)
    except PermissionError as err:
        raise AccessError(root) from err
    except FileNotFoundError as err:
        raise RootError(f"folder does not exist: {root}", root) from err


def plan_pair(cfg: PairConfig, snapshot: Snapshot) -> tuple[Plan, Scan, Scan]:
    """Scan both roots and compute what would change, touching nothing.

    The scans come back alongside the plan because :func:`apply` needs them:
    they are the metadata the plan was decided from, and re-reading the disk in
    between would open a window for inconsistency.

    The snapshot is read once, up front, and the lengthy scanning happens
    without holding any database handle open — so a preview stays responsive
    even while another pair is mid-sync.
    """
    validate_roots(cfg)
    for side in (Side.A, Side.B):
        check_access(cfg.root(side))

    excludes = ExcludeSet(cfg.excludes)
    recorded = snapshot.load(cfg.id)

    a = scan_root(cfg.path_a, excludes, cfg.skip_cloud_placeholders)
    b = scan_root(cfg.path_b, excludes, cfg.skip_cloud_placeholders)

    return plan(cfg, a, b, recorded), a, b


def sync_pair(
    cfg: PairConfig,
    snapshot: Snapshot,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    force: bool = False,
) -> tuple[Plan, ApplyOutcome]:
    """Plan a sync and immediately carry it out.

    ``force`` overrides the delete guard; callers should only pass it after the
    user has seen :attr:`Plan.blocked` and confirmed.
    """
    computed, a, b = plan_pair(cfg, snapshot)
    outcome = apply(cfg, computed, a, b, snapshot, progress, cancel, force)
    return computed, outcome
