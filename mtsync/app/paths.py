"""Where the app keeps its configuration, database and logs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "MT Sync"
APP_DIR_NAME = "mt-sync"


def data_dir() -> Path:
    """Per-user directory for the config file, database and logs.

    The predecessor put these in ``~/Documents/AutomationLogs``, inside a folder
    people sync — which meant the app's own bookkeeping became sync traffic.
    These locations are the platform conventions and are never inside a
    user's document folders.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")

    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "config.json"


def database_file() -> Path:
    return data_dir() / "state.db"


def log_dir() -> Path:
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Logs" / APP_DIR_NAME
    else:
        path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return log_dir() / "mt-sync.log"


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
