"""Registering the app to launch when the user logs in."""

from __future__ import annotations

import contextlib
import logging
import plistlib
import sys
from pathlib import Path

from .paths import APP_NAME

log = logging.getLogger(__name__)

_LAUNCH_AGENT_ID = "tr.matu.dengele"

#: How the login item was identified before the app was renamed. macOS keys the
#: agent by its label and Windows by the value name, so a registration made
#: under the old name is invisible to the code below: it would keep launching
#: the app at login while the settings screen showed the switch as off, and
#: turning the switch on and off again would never remove it.
_LEGACY_LAUNCH_AGENT_ID = "tr.matu.mtsync"
_LEGACY_APP_NAME = "MT Sync"


def migrate_legacy() -> None:
    """Re-register a pre-rename login item under the current name.

    Called once at startup. Doing nothing is always safe — the cost of a
    failure here is a stale login item, not a broken app — so every platform
    error is logged and swallowed.
    """
    try:
        if sys.platform == "darwin":
            legacy = Path.home() / "Library" / "LaunchAgents" / f"{_LEGACY_LAUNCH_AGENT_ID}.plist"
            if not legacy.exists():
                return
            legacy.unlink()
            _set_launch_agent(True)
        elif sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ
            ) as key:
                try:
                    winreg.QueryValueEx(key, _LEGACY_APP_NAME)
                except FileNotFoundError:
                    return
                winreg.DeleteValue(key, _LEGACY_APP_NAME)
            _set_run_key(True)
        else:
            return
    except OSError as err:
        log.warning("could not migrate the previous autostart registration: %s", err)
    else:
        log.info("moved the login item from %r to %r", _LEGACY_APP_NAME, APP_NAME)


def is_enabled() -> bool:
    try:
        if sys.platform == "darwin":
            return _launch_agent_path().exists()
        if sys.platform == "win32":
            return _read_run_key() is not None
    except OSError as err:
        log.warning("could not read autostart registration: %s", err)
    return False


def set_enabled(enabled: bool) -> None:
    """Add or remove the login item, swallowing platform failures.

    Failing to register is an inconvenience, not a reason to refuse the rest of
    the settings the user just saved.
    """
    try:
        if sys.platform == "darwin":
            _set_launch_agent(enabled)
        elif sys.platform == "win32":
            _set_run_key(enabled)
    except OSError as err:
        log.warning("could not update autostart registration: %s", err)


def _executable() -> list[str]:
    """The command that starts this app, frozen or from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "dengele"]


# -- macOS --------------------------------------------------------------


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_ID}.plist"


def _set_launch_agent(enabled: bool) -> None:
    path = _launch_agent_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": _LAUNCH_AGENT_ID,
        "ProgramArguments": _executable(),
        "RunAtLoad": True,
        # The app manages its own lifetime; relaunching it on quit would make
        # "Quit" impossible.
        "KeepAlive": False,
    }
    with open(path, "wb") as handle:
        plistlib.dump(plist, handle)


# -- Windows ------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _read_run_key() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value
    except FileNotFoundError:
        return None


def _set_run_key(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = " ".join(f'"{part}"' for part in _executable())
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, APP_NAME)
