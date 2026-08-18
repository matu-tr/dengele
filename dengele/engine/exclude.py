"""Deciding which paths the user does not want synchronized."""

from __future__ import annotations

import pathspec

from .models import RECYCLE_DIR, ROOT_MARKER, TEMP_SUFFIX


def _pattern_style() -> str:
    """The gitignore dialect this pathspec version prefers.

    pathspec renamed ``gitwildmatch`` to ``gitignore`` and warns on the old
    name, but the new one does not exist in older releases that are still
    perfectly serviceable.
    """
    try:
        pathspec.PathSpec.from_lines("gitignore", [])
    except Exception:
        return "gitwildmatch"
    return "gitignore"


_PATTERN_STYLE = _pattern_style()


class ExcludeError(ValueError):
    """A pattern the user typed could not be compiled."""


class ExcludeSet:
    """Compiled matcher for exclude patterns.

    Patterns use gitignore syntax and match the forward-slash relative path
    (``docs/notes/a.txt``), so both ``*.tmp`` and ``build/`` behave the way
    people expect from a ``.gitignore``. The predecessor compared whole path
    components against a flat list of names and could express neither.
    """

    __slots__ = ("_spec",)

    def __init__(self, patterns: list[str] | tuple[str, ...] = ()) -> None:
        cleaned = [p.strip() for p in patterns if p.strip()]
        try:
            self._spec = pathspec.PathSpec.from_lines(_PATTERN_STYLE, cleaned)
        except Exception as err:  # pathspec raises a variety of types
            raise ExcludeError(str(err)) from err

    def is_excluded(self, rel: str, is_dir: bool = False) -> bool:
        """Whether ``rel`` should be skipped. Engine bookkeeping always is."""
        if self._is_bookkeeping(rel):
            return True
        # gitignore semantics distinguish `build/` from `build`, and pathspec
        # needs the trailing slash to apply directory-only patterns.
        candidate = f"{rel}/" if is_dir and not rel.endswith("/") else rel
        return self._spec.match_file(candidate)

    @staticmethod
    def _is_bookkeeping(rel: str) -> bool:
        first = rel.split("/", 1)[0]
        return first == RECYCLE_DIR or rel == ROOT_MARKER or rel.endswith(TEMP_SUFFIX)


def validate_patterns(patterns: list[str]) -> None:
    """Raise :class:`ExcludeError` if any pattern is unusable.

    Called when settings are saved so a typo is rejected while the user is
    still looking at it, rather than silently failing at scan time.
    """
    ExcludeSet(patterns)
