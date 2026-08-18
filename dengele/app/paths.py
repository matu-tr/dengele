"""Where the app keeps its configuration, database and logs."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

APP_NAME = "Dengele"
APP_DIR_NAME = "dengele"

#: What the directories were called before the app was renamed. An install
#: that predates the rename keeps its config and — more importantly — the
#: snapshot of what both sides last agreed on. Losing that snapshot would not
#: lose files, but it would leave the engine unable to tell a deletion from a
#: creation, so the directory is moved across rather than started fresh.
_LEGACY_APP_DIR_NAME = "mt-sync"


def _base_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _adopt_legacy(path: Path, legacy: Path) -> None:
    """Move a pre-rename directory into place, if one is there and ours is not.

    Failure is deliberately silent: a missing or unreadable legacy directory
    just means there is nothing to carry over, and the caller creates a fresh
    one immediately afterwards.
    """
    if path.exists() or not legacy.is_dir():
        return
    with contextlib.suppress(OSError):
        legacy.rename(path)


def data_dir() -> Path:
    """Per-user directory for the config file, database and logs.

    The predecessor put these in ``~/Documents/AutomationLogs``, inside a folder
    people sync — which meant the app's own bookkeeping became sync traffic.
    These locations are the platform conventions and are never inside a
    user's document folders.
    """
    base = _base_dir()
    path = base / APP_DIR_NAME
    _adopt_legacy(path, base / _LEGACY_APP_DIR_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "config.json"


def database_file() -> Path:
    return data_dir() / "state.db"


def log_dir() -> Path:
    if sys.platform == "darwin":
        logs = Path.home() / "Library" / "Logs"
        path = logs / APP_DIR_NAME
        _adopt_legacy(path, logs / _LEGACY_APP_DIR_NAME)
    else:
        path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return log_dir() / "dengele.log"


def home() -> Path:
    return Path.home()


def suggested_roots() -> list[tuple[str, Path]]:
    """Well-known folders offered as starting points when adding a pair.

    iCloud Drive sits in different places per platform — a container directory
    on macOS, the user profile on Windows — so this is the one function that
    needs to know about either.
    """
    found: list[tuple[str, Path]] = []
    user = home()

    if sys.platform == "darwin":
        icloud = user / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if icloud.is_dir():
            found.append(("iCloud Drive", icloud))
    elif sys.platform == "win32":
        for name in ("iCloudDrive", "iCloud Drive"):
            candidate = user / name
            if candidate.is_dir():
                found.append(("iCloud Drive", candidate))
                break

    for label in ("Documents", "Desktop"):
        candidate = user / label
        if candidate.is_dir():
            found.append((label, candidate))

    return found


def is_privacy_protected(path: Path) -> bool:
    """Whether macOS gates this location behind its privacy system.

    Used to warn before a pair is created, since a folder inside one of these
    needs explicit permission that an unsigned build may never be granted.
    """
    if sys.platform != "darwin":
        return False

    user = home()
    protected = [
        user / "Desktop",
        user / "Documents",
        user / "Downloads",
        Path("/Volumes"),
    ]
    resolved = path.resolve()
    return any(resolved == p or resolved.is_relative_to(p) for p in protected)
