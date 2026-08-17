"""Errors the engine raises, phrased so the UI can show them verbatim."""

from __future__ import annotations

from pathlib import Path


class EngineError(Exception):
    """Base class for anything the engine refuses to do."""


class RootError(EngineError):
    """A pair's folder is missing, unusable, or overlaps the other one."""

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class AccessError(EngineError):
    """A folder exists but the operating system will not let us read it.

    On macOS this is what the privacy system produces for Desktop, Documents,
    Downloads and removable volumes until the app has been granted access. It
    is called out separately from :class:`RootError` so the UI can point at
    System Settings instead of suggesting the folder is missing.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"no permission to read {path}")
        self.path = path


class PlanBlocked(EngineError):
    """A plan tripped the delete guard and was not applied."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Cancelled(EngineError):
    """The user stopped a sync while it was running."""
