"""Moving deleted items aside instead of destroying them."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .models import RECYCLE_DIR
from .planner import format_timestamp


def session_name(now_ms: int | None = None) -> str:
    """Folder name for one sync run's deletions."""
    return format_timestamp(now_ms if now_ms is not None else int(time.time() * 1000))


def recycle(root: Path, rel: str, session: str) -> Path:
    """Move ``rel`` into its root's recycle bin.

    Every run gets its own timestamped folder and the original structure is
    preserved inside it, so undoing a mistaken sync is a matter of restoring
    one directory.
    """
    source = root / rel
    destination = _unique(root / RECYCLE_DIR / session / rel)
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        source.rename(destination)
    except OSError:
        # rename cannot cross filesystems. The bin lives inside the same root
        # so this is rare, but bind mounts and junctions can still trigger it.
        shutil.move(str(source), str(destination))
    return destination


def prune(root: Path, retention_days: int) -> int:
    """Delete recycle sessions older than ``retention_days``. 0 keeps them all."""
    if retention_days <= 0:
        return 0

    bin_path = root / RECYCLE_DIR
    if not bin_path.is_dir():
        return 0

    cutoff = time.time() - retention_days * 86_400
    removed = 0
    for session in bin_path.iterdir():
        if not session.is_dir():
            continue
        try:
            if session.stat().st_mtime < cutoff:
                shutil.rmtree(session)
                removed += 1
        except OSError:
            # Housekeeping must never turn a successful sync into a failure.
            continue
    return removed


def _unique(path: Path) -> Path:
    """A path that does not exist yet, suffixing if needed.

    Two deletions of the same relative path within one session would otherwise
    clobber each other.
    """
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path
