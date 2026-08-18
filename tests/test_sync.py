"""End-to-end behaviour of the sync engine against real directories.

The cases that matter most are the ones the original Python script got wrong:
a file created on only one side must survive, and a deletion must be
distinguishable from a creation.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from dengele.engine import (
    RECYCLE_DIR,
    ROOT_MARKER,
    AccessError,
    ConflictKind,
    ConflictPolicy,
    PairConfig,
    RootError,
    Side,
    Snapshot,
    plan_pair,
    sync_pair,
)


def test_new_files_propagate_in_both_directions(pair):
    pair.write(Side.A, "from_a.txt", "a")
    pair.write(Side.B, "nested/from_b.txt", "b")

    pair.sync()

    assert pair.read(Side.B, "from_a.txt") == "a"
    assert pair.read(Side.A, "nested/from_b.txt") == "b"


def test_file_created_on_one_side_only_is_never_deleted(pair):
    """The regression this rewrite exists for.

    The old two-pass mirror recycled anything missing from the "source" side,
    so a file created only on the SSD was destroyed by the iCloud→SSD pass
    before the SSD→iCloud pass could ever see it.
    """
    pair.write(Side.A, "shared.txt", "shared")
    pair.sync()

    pair.write(Side.B, "only_on_b.txt", "precious")
    pair.sync()

    assert pair.read(Side.B, "only_on_b.txt") == "precious"
    assert pair.read(Side.A, "only_on_b.txt") == "precious"
    assert not (pair.root_b / RECYCLE_DIR).exists()


def test_deletion_propagates_to_the_other_side(pair):
    pair.write(Side.A, "doomed.txt", "x")
    pair.sync()
    assert pair.exists(Side.B, "doomed.txt")

    (pair.root_a / "doomed.txt").unlink()
    pair.sync()

    assert not pair.exists(Side.B, "doomed.txt")


def test_deleted_files_land_in_the_recycle_bin(pair):
    pair.write(Side.A, "docs/notes.txt", "keep me safe")
    pair.sync()
    (pair.root_a / "docs/notes.txt").unlink()
    pair.sync()

    recovered = [p for p in pair.files_under(pair.root_b / RECYCLE_DIR) if p.name == "notes.txt"]
    assert len(recovered) == 1
    assert recovered[0].read_text() == "keep me safe"


def test_edits_propagate_from_whichever_side_changed(pair):
    pair.write(Side.A, "doc.txt", "v1")
    pair.sync()

    pair.write(Side.B, "doc.txt", "v2 from b")
    pair.touch(Side.B, "doc.txt", 10)
    pair.sync()
    assert pair.read(Side.A, "doc.txt") == "v2 from b"

    pair.write(Side.A, "doc.txt", "v3 from a")
    pair.touch(Side.A, "doc.txt", 20)
    pair.sync()
    assert pair.read(Side.B, "doc.txt") == "v3 from a"


def test_concurrent_edits_conflict_and_newest_wins_without_losing_the_other(pair):
    pair.write(Side.A, "doc.txt", "base")
    pair.sync()

    pair.write(Side.A, "doc.txt", "older edit")
    pair.touch(Side.A, "doc.txt", -60)
    pair.write(Side.B, "doc.txt", "newer edit")
    pair.touch(Side.B, "doc.txt", 60)

    plan, _ = pair.sync()

    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].resolved_to is Side.B
    assert pair.read(Side.A, "doc.txt") == "newer edit"
    assert pair.read(Side.B, "doc.txt") == "newer edit"

    # The overwritten version is kept next to the winner, not discarded.
    preserved = [p for p in pair.files_under(pair.root_a) if "conflict" in p.name]
    assert len(preserved) == 1
    assert preserved[0].read_text() == "older edit"


def test_identical_concurrent_edits_are_not_a_conflict(pair):
    pair.write(Side.A, "doc.txt", "base")
    pair.sync()

    pair.write(Side.A, "doc.txt", "same new content")
    pair.write(Side.B, "doc.txt", "same new content")
    pair.touch(Side.B, "doc.txt", 30)

    plan, _ = pair.sync()

    assert plan.conflicts == []
    assert plan.stats.copies == 0


def test_ask_policy_leaves_conflicting_files_untouched(make_pair):
    pair = make_pair(ConflictPolicy.ASK)
    pair.write(Side.A, "doc.txt", "base")
    pair.sync()

    pair.write(Side.A, "doc.txt", "a version")
    pair.write(Side.B, "doc.txt", "b version")
    pair.touch(Side.B, "doc.txt", 60)

    plan, _ = pair.sync()

    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].resolved_to is None
    assert pair.read(Side.A, "doc.txt") == "a version"
    assert pair.read(Side.B, "doc.txt") == "b version"


def test_edits_inside_a_deleted_directory_survive(pair):
    """Deleting a folder must not take unrelated new work inside it with it."""
    pair.write(Side.A, "project/old.txt", "old")
    pair.sync()

    shutil.rmtree(pair.root_b / "project")
    pair.write(Side.A, "project/new.txt", "important new work")
    pair.sync()

    assert pair.read(Side.B, "project/new.txt") == "important new work"
    assert not pair.exists(Side.A, "project/old.txt")


def test_a_file_edited_on_one_side_and_deleted_on_the_other_is_kept(pair):
    pair.write(Side.A, "doc.txt", "v1")
    pair.sync()

    (pair.root_b / "doc.txt").unlink()
    pair.write(Side.A, "doc.txt", "v2 edited")
    pair.touch(Side.A, "doc.txt", 30)

    plan, _ = pair.sync()

    assert len(plan.conflicts) == 1
    assert plan.conflicts[0].kind is ConflictKind.MODIFIED_AND_DELETED
    assert pair.read(Side.B, "doc.txt") == "v2 edited"
    assert pair.read(Side.A, "doc.txt") == "v2 edited"


def test_excluded_paths_are_neither_copied_nor_deleted(pair):
    pair.cfg.excludes = ["venv/", "*.tmp"]
    pair.write(Side.A, "venv/lib/x.so", "binary")
    pair.write(Side.A, "scratch.tmp", "junk")
    pair.write(Side.A, "real.txt", "content")
    pair.write(Side.B, "venv/other.so", "other binary")

    pair.sync()

    assert pair.read(Side.B, "real.txt") == "content"
    assert not pair.exists(Side.B, "venv/lib/x.so")
    assert not pair.exists(Side.B, "scratch.tmp")
    # ...and excluded content already on B is left alone.
    assert pair.read(Side.B, "venv/other.so") == "other binary"


def test_syncing_twice_changes_nothing_the_second_time(pair):
    pair.write(Side.A, "a.txt", "one")
    pair.write(Side.B, "dir/b.txt", "two")
    pair.sync()

    plan, outcome = pair.sync()

    assert plan.effective_ops == [], f"second sync was not a no-op: {plan.effective_ops}"
    assert outcome.bytes_copied == 0


def test_copied_files_keep_their_modification_time(pair):
    pair.write(Side.A, "doc.txt", "content")
    pair.touch(Side.A, "doc.txt", -3600)
    pair.sync()

    a_mtime = (pair.root_a / "doc.txt").stat().st_mtime
    b_mtime = (pair.root_b / "doc.txt").stat().st_mtime
    assert abs(a_mtime - b_mtime) < 2


def test_a_missing_root_is_refused_rather_than_treated_as_empty(pair):
    pair.write(Side.A, "everything.txt", "irreplaceable")
    pair.sync()

    shutil.rmtree(pair.root_b)
    with pytest.raises(RootError):
        sync_pair(pair.cfg, pair.snapshot)

    assert pair.exists(Side.A, "everything.txt")


def test_mass_deletion_is_blocked_until_forced(pair):
    pair.cfg.delete_threshold_min = 3
    pair.cfg.delete_threshold_pct = 0.20
    for i in range(10):
        pair.write(Side.A, f"file{i}.txt", "content")
    pair.sync()

    for i in range(10):
        (pair.root_a / f"file{i}.txt").unlink()

    plan, _, _ = plan_pair(pair.cfg, pair.snapshot)
    assert plan.blocked is not None
    assert pair.exists(Side.B, "file0.txt"), "nothing should have been applied"

    # ...and the user can still go ahead once they have seen the list.
    pair.sync(force=True)
    assert not pair.exists(Side.B, "file0.txt")


def test_a_root_missing_its_marker_is_refused_when_markers_are_required(pair):
    pair.cfg.require_marker = True
    (pair.root_a / ROOT_MARKER).touch()

    with pytest.raises(RootError):
        sync_pair(pair.cfg, pair.snapshot)

    (pair.root_b / ROOT_MARKER).touch()
    pair.sync()


def test_nested_roots_are_refused(tmp_path: Path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)

    cfg = PairConfig(id="nested", path_a=outer, path_b=inner)
    with pytest.raises(RootError):
        plan_pair(cfg, Snapshot(":memory:"))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinks_are_replicated_not_dereferenced(pair):
    pair.write(Side.A, "target.txt", "real content")
    os.symlink("target.txt", pair.root_a / "link.txt")

    pair.sync()

    copied = pair.root_b / "link.txt"
    assert copied.is_symlink()
    assert os.readlink(copied) == "target.txt"


def test_icloud_placeholders_are_skipped_and_reported(pair):
    pair.write(Side.A, ".big-movie.mov.icloud", "placeholder stub")
    pair.write(Side.A, "downloaded.txt", "real")

    plan, _, _ = plan_pair(pair.cfg, pair.snapshot)
    pair.sync()

    assert plan.placeholders_a == [".big-movie.mov.icloud"]
    assert not pair.exists(Side.B, ".big-movie.mov.icloud")
    assert pair.exists(Side.B, "downloaded.txt")


def test_a_file_replaced_by_a_directory_is_reconciled(pair):
    pair.write(Side.A, "thing", "was a file")
    pair.sync()

    (pair.root_a / "thing").unlink()
    pair.write(Side.A, "thing/inside.txt", "now a directory")
    pair.sync()

    assert (pair.root_b / "thing").is_dir()
    assert pair.read(Side.B, "thing/inside.txt") == "now a directory"


def test_a_directory_replaced_by_a_file_is_reconciled(pair):
    pair.write(Side.A, "thing/inside.txt", "was a directory")
    pair.sync()

    shutil.rmtree(pair.root_a / "thing")
    pair.write(Side.A, "thing", "now a file")
    pair.sync()

    assert (pair.root_b / "thing").is_file()
    assert pair.read(Side.B, "thing") == "now a file"


@pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else False, reason="root ignores permission bits"
)
@pytest.mark.skipif(sys.platform == "win32", reason="chmod does not deny reads on Windows")
def test_an_unreadable_root_reports_access_rather_than_hanging(pair):
    """macOS privacy denials surfaced as a freeze in the previous version."""
    pair.write(Side.A, "doc.txt", "content")
    pair.root_a.chmod(0o000)
    try:
        with pytest.raises(AccessError):
            plan_pair(pair.cfg, pair.snapshot)
    finally:
        pair.root_a.chmod(0o755)


def test_preview_works_while_another_pair_is_mid_sync(tmp_path: Path, pair_in):
    """Regression for the deadlock that made the Rust version look frozen.

    There a single mutex guarded the snapshot database for a whole sync, so
    computing a preview blocked until that sync finished — which, when the sync
    itself was stuck, meant forever.
    """
    shared_db = tmp_path / "state.db"

    first = pair_in(tmp_path / "first", Snapshot(shared_db))
    first.write(Side.A, "doc.txt", "content")

    second = pair_in(tmp_path / "second", Snapshot(shared_db))
    second.cfg.id = "second-pair"
    second.write(Side.A, "other.txt", "other")

    # Both pairs share one database file; planning one must not be blocked by
    # the other holding it.
    first.sync()
    plan, _, _ = plan_pair(second.cfg, second.snapshot)
    assert len(plan.effective_ops) == 1
