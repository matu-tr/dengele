"""Unit tests for the pieces the end-to-end tests exercise only indirectly."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mtsync.engine import (
    MTIME_TOLERANCE_MS,
    RECYCLE_DIR,
    ROOT_MARKER,
    Entry,
    EntryKind,
    ExcludeError,
    ExcludeSet,
    Side,
    SnapRecord,
    Snapshot,
    conflict_name,
    entry_changed,
    recycle,
    scan_root,
)
from mtsync.engine.recycle import prune, session_name

# -- excludes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("venv/", "venv/lib/x.py", True),
        ("venv/", "project/venv/lib/x.py", True),
        ("venv/", "venv-notes.md", False),
        ("*.tmp", "a/b/scratch.tmp", True),
        ("*.tmp", "a/b/scratch.tmpx", False),
        ("cache/**", "cache/deep/file", True),
        ("/build/", "build/out", True),
        ("/build/", "sub/build/out", False),
    ],
)
def test_exclude_patterns_follow_gitignore_semantics(pattern, path, expected):
    excludes = ExcludeSet([pattern])
    assert excludes.is_excluded(path) is expected


def test_engine_bookkeeping_is_always_excluded():
    excludes = ExcludeSet([])
    assert excludes.is_excluded(RECYCLE_DIR)
    assert excludes.is_excluded(f"{RECYCLE_DIR}/2026-01-01/a.txt")
    assert excludes.is_excluded(ROOT_MARKER)
    assert excludes.is_excluded("docs/.report.pdf.mt-sync-tmp")
    assert not excludes.is_excluded("docs/report.pdf")


def test_unusable_patterns_are_rejected():
    """gitignore syntax is permissive, but not infinitely so.

    Most typos are legal patterns that simply match nothing, which is why the
    settings screen cannot promise to catch mistakes — only genuinely
    unparseable input is refused.
    """
    with pytest.raises(ExcludeError):
        ExcludeSet(["!"])
    with pytest.raises(ExcludeError):
        ExcludeSet(["\\"])

    # A stray bracket is valid gitignore and must not be rejected.
    ExcludeSet(["[unclosed"])


# -- change detection ---------------------------------------------------


def _file(size: int = 10, mtime_ms: int = 1_000_000) -> Entry:
    return Entry(kind=EntryKind.FILE, size=size, mtime_ms=mtime_ms)


def test_mtime_drift_within_tolerance_is_not_a_change():
    record = SnapRecord(kind=EntryKind.FILE, size=10, mtime_ms=1_000_000)

    assert not entry_changed(_file(mtime_ms=1_000_000 + MTIME_TOLERANCE_MS - 1), record)
    assert entry_changed(_file(mtime_ms=1_000_000 + MTIME_TOLERANCE_MS + 1), record)
    assert entry_changed(_file(size=11), record)
    assert entry_changed(_file(), None)


def test_a_symlink_changes_when_its_target_does():
    record = SnapRecord(kind=EntryKind.SYMLINK, size=0, mtime_ms=0, link_target="a.txt")
    same = Entry(kind=EntryKind.SYMLINK, size=0, mtime_ms=0, link_target="a.txt")
    moved = Entry(kind=EntryKind.SYMLINK, size=0, mtime_ms=0, link_target="b.txt")

    assert not entry_changed(same, record)
    assert entry_changed(moved, record)


# -- conflict naming ----------------------------------------------------


def test_conflict_names_keep_the_extension():
    when = int(time.mktime((2026, 8, 17, 14, 13, 20, 0, 0, -1)) * 1000)

    assert conflict_name("notes/todo.txt", Side.A, when).startswith("notes/todo (conflict A ")
    assert conflict_name("notes/todo.txt", Side.A, when).endswith(".txt")
    assert conflict_name("README", Side.B, when).startswith("README (conflict B ")
    # A leading dot is part of the name, not an extension separator.
    assert ".gitignore (conflict A" in conflict_name(".gitignore", Side.A, when)


def test_conflict_names_are_legal_windows_filenames():
    assert ":" not in session_name(int(time.time() * 1000))


# -- snapshot -----------------------------------------------------------


def _record(size: int) -> SnapRecord:
    return SnapRecord(kind=EntryKind.FILE, size=size, mtime_ms=1_700_000_000_000)


def test_snapshot_round_trips_upserts_and_deletes():
    snapshot = Snapshot(":memory:")
    snapshot.commit("p1", [("a.txt", _record(1)), ("b.txt", _record(2))])

    loaded = snapshot.load("p1")
    assert len(loaded) == 2
    assert loaded["b.txt"].size == 2

    snapshot.commit("p1", [("a.txt", _record(9))], ["b.txt"])
    loaded = snapshot.load("p1")
    assert list(loaded) == ["a.txt"]
    assert loaded["a.txt"].size == 9


def test_snapshot_keeps_pairs_isolated():
    snapshot = Snapshot(":memory:")
    snapshot.commit("p1", [("shared.txt", _record(1))])
    snapshot.commit("p2", [("shared.txt", _record(2))])

    assert snapshot.load("p1")["shared.txt"].size == 1
    assert snapshot.load("p2")["shared.txt"].size == 2

    snapshot.forget_pair("p1")
    assert snapshot.load("p1") == {}
    assert len(snapshot.load("p2")) == 1


def test_snapshot_survives_reopening(tmp_path: Path):
    path = tmp_path / "state.db"
    first = Snapshot(path)
    first.commit("p1", [("a.txt", _record(5))])
    first.set_last_sync("p1", 1234)
    first.close()

    second = Snapshot(path)
    assert second.load("p1")["a.txt"].size == 5
    assert second.last_sync("p1") == 1234


# -- recycle bin --------------------------------------------------------


def test_recycle_preserves_structure(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.txt").write_text("bye")

    moved = recycle(tmp_path, "docs/a.txt", "sess1")

    assert not (tmp_path / "docs/a.txt").exists()
    assert moved.read_text() == "bye"
    assert moved == tmp_path / RECYCLE_DIR / "sess1/docs/a.txt"


def test_second_delete_of_the_same_path_does_not_clobber_the_first(tmp_path: Path):
    (tmp_path / "a.txt").write_text("first")
    first = recycle(tmp_path, "a.txt", "sess1")
    (tmp_path / "a.txt").write_text("second")
    second = recycle(tmp_path, "a.txt", "sess1")

    assert first != second
    assert first.read_text() == "first"
    assert second.read_text() == "second"


def test_prune_keeps_recent_sessions_and_zero_means_forever(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    recycle(tmp_path, "a.txt", "recent")

    assert prune(tmp_path, 30) == 0
    assert (tmp_path / RECYCLE_DIR / "recent").exists()
    # 0 means "never prune", not "prune everything".
    assert prune(tmp_path, 0) == 0
    assert (tmp_path / RECYCLE_DIR / "recent").exists()


# -- scanning -----------------------------------------------------------


def test_scan_records_files_dirs_and_reports_placeholders(tmp_path: Path):
    (tmp_path / "sub/deep").mkdir(parents=True)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub/deep/b.txt").write_text("world!")
    (tmp_path / ".movie.mp4.icloud").write_text("stub")

    scan = scan_root(tmp_path, ExcludeSet([]))

    assert scan.entries["a.txt"].kind is EntryKind.FILE
    assert scan.entries["a.txt"].size == 5
    assert scan.entries["sub"].kind is EntryKind.DIR
    assert scan.entries["sub/deep/b.txt"].size == 6
    assert scan.placeholders == [".movie.mp4.icloud"]
    assert scan.errors == []


def test_scan_does_not_descend_into_excluded_directories(tmp_path: Path):
    (tmp_path / "venv/lib").mkdir(parents=True)
    (tmp_path / "venv/lib/big.so").write_text("x")
    (tmp_path / "keep.txt").write_text("x")

    scan = scan_root(tmp_path, ExcludeSet(["venv/"]))

    assert "keep.txt" in scan.entries
    assert not any(key.startswith("venv") for key in scan.entries)
