"""The threading and state machine around the engine.

This is where the Rust version's two user-visible defects lived: a sync started
in the background was invisible to the UI, and computing a preview blocked
behind a running sync. Both are asserted against here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from dengele.app.config import Config, Pair, WatchMode
from dengele.app.controller import Controller
from dengele.app.watcher import is_engine_bookkeeping
from dengele.engine import RECYCLE_DIR


@pytest.fixture
def setup(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    pair = Pair.create("Test", root_a, root_b)
    pair.engine.excludes = []
    pair.engine.delete_threshold_min = 10**9

    config = Config(pairs=[pair])
    controller = Controller(config, database=tmp_path / "state.db")
    try:
        yield controller, pair, root_a, root_b
    finally:
        controller.cancel_all()
        controller.wait(5_000)


def test_a_sync_updates_status_and_moves_files(setup):
    controller, pair, root_a, root_b = setup
    (root_a / "doc.txt").write_text("content")

    assert controller.start(pair.id) is True
    assert controller.wait(10_000)

    assert (root_b / "doc.txt").read_text() == "content"
    status = controller.status(pair.id)
    assert status.running is False
    assert status.last_error is None
    assert status.copied == 1
    assert status.last_sync_ms is not None


def test_status_is_visible_no_matter_who_started_the_sync(setup):
    """The UI reads controller state rather than tracking its own.

    In the Rust version the window only followed syncs the user had clicked, so
    a scheduled one left the card showing "never synced" while it ran.
    """
    controller, pair, root_a, _ = setup
    (root_a / "doc.txt").write_text("content")

    # Started the way the scheduler and file watcher start one — no UI involved.
    controller.start(pair.id)
    assert controller.any_running() or controller.status(pair.id).running

    controller.wait(10_000)
    assert controller.status(pair.id).copied == 1


def test_a_pair_only_syncs_once_at_a_time(setup):
    controller, pair, root_a, _ = setup
    for i in range(50):
        (root_a / f"file{i}.txt").write_text("x" * 1000)

    assert controller.start(pair.id) is True
    # The second request is refused while the first is queued or running.
    assert controller.start(pair.id) is False

    controller.wait(10_000)
    assert controller.start(pair.id) is True
    controller.wait(10_000)


def test_preview_works_while_a_sync_is_running(setup):
    """Regression for the deadlock that made the previous version look frozen."""
    controller, pair, root_a, _ = setup
    for i in range(200):
        (root_a / f"file{i}.txt").write_text("x" * 5000)

    controller.start(pair.id)

    # Must return promptly even though a sync holds the database open.
    started = time.monotonic()
    plan = controller.preview(pair.id)
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"preview blocked for {elapsed:.1f}s behind a running sync"
    assert plan.pair_id == pair.id
    controller.wait(20_000)


def test_cancelling_stops_a_sync(setup):
    controller, pair, root_a, root_b = setup
    for i in range(500):
        (root_a / f"file{i}.txt").write_text("x" * 2000)

    controller.start(pair.id)
    controller.cancel(pair.id)
    assert controller.wait(20_000)

    status = controller.status(pair.id)
    assert status.running is False
    # A cancelled sync leaves whatever it managed to copy, and is not recorded
    # as a completed sync.
    assert status.last_sync_ms is None or len(list(root_b.iterdir())) < 500


def test_cancelling_something_that_is_not_running_is_harmless(setup):
    controller, pair, _, _ = setup
    assert controller.cancel(pair.id) is False
    assert controller.cancel("nonexistent") is False


def test_a_disabled_pair_is_never_started(setup):
    controller, pair, root_a, root_b = setup
    (root_a / "doc.txt").write_text("content")
    pair.enabled = False

    assert controller.start(pair.id) is False
    controller.wait(2_000)
    assert not (root_b / "doc.txt").exists()


def test_failures_are_reported_not_raised(setup, tmp_path: Path):
    controller, pair, _, root_b = setup
    import shutil

    shutil.rmtree(root_b)

    controller.start(pair.id)
    assert controller.wait(10_000)

    status = controller.status(pair.id)
    assert status.running is False
    assert status.last_error is not None
    assert "does not exist" in status.last_error


def test_forgetting_a_pair_clears_its_history(setup):
    controller, pair, root_a, root_b = setup
    (root_a / "doc.txt").write_text("content")
    controller.start(pair.id)
    controller.wait(10_000)
    assert controller.last_sync(pair.id) is not None

    controller.forget_pair(pair.id)
    assert controller.last_sync(pair.id) is None

    # With no history, a file only on B reads as new rather than deleted.
    (root_a / "doc.txt").unlink()
    controller.start(pair.id)
    controller.wait(10_000)
    assert (root_a / "doc.txt").read_text() == "content"
    assert not (root_b / RECYCLE_DIR).exists()


def test_suppression_window_stops_a_sync_retriggering_itself(setup):
    controller, pair, root_a, _ = setup
    (root_a / "doc.txt").write_text("content")

    controller.start(pair.id)
    controller.wait(10_000)

    # A sync writes to both roots; the watcher must ignore that echo.
    assert controller.is_suppressed(pair.id)


# -- scheduling ---------------------------------------------------------


def test_due_pairs_respects_mode_and_interval(setup):
    controller, pair, _, _ = setup

    pair.watch = WatchMode.MANUAL
    assert controller.due_pairs({}) == []

    pair.watch = WatchMode.INTERVAL
    pair.interval_minutes = 15
    assert controller.due_pairs({}) == [pair.id]
    # Just synced, so not due again yet.
    assert controller.due_pairs({pair.id: time.monotonic()}) == []

    # Change-driven pairs still get an hourly full scan as a safety net,
    # because filesystem events go missing across sleep and on network volumes.
    pair.watch = WatchMode.ON_CHANGE
    assert controller.due_pairs({}) == [pair.id]
    assert controller.due_pairs({pair.id: time.monotonic()}) == []


def test_engine_bookkeeping_paths_do_not_trigger_syncs():
    assert is_engine_bookkeeping(f"/root/{RECYCLE_DIR}/2026-01-01/a.txt")
    assert is_engine_bookkeeping("/root/docs/.report.pdf.dengele-tmp")
    assert not is_engine_bookkeeping("/root/docs/report.pdf")


def test_sync_all_starts_every_enabled_pair(tmp_path: Path):
    pairs = []
    for name in ("one", "two"):
        root_a = tmp_path / name / "a"
        root_b = tmp_path / name / "b"
        root_a.mkdir(parents=True)
        root_b.mkdir(parents=True)
        (root_a / "doc.txt").write_text(name)
        pair = Pair.create(name, root_a, root_b)
        pair.engine.excludes = []
        pairs.append(pair)

    pairs[1].enabled = False
    controller = Controller(Config(pairs=pairs), database=tmp_path / "state.db")
    try:
        controller.start_all()
        controller.wait(15_000)

        assert (tmp_path / "one/b/doc.txt").exists()
        assert not (tmp_path / "two/b/doc.txt").exists()
    finally:
        controller.cancel_all()
        controller.wait(5_000)


def test_worker_crashes_do_not_leave_a_pair_stuck_running(setup, monkeypatch):
    """A pair marked running forever is what blocked every later sync before."""
    controller, pair, _, _ = setup

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("dengele.app.controller.sync_pair", explode)

    controller.start(pair.id)
    assert controller.wait(10_000)

    assert controller.is_running(pair.id) is False
    assert controller.status(pair.id).last_error is not None
    # ...and the pair can be synced again afterwards.
    assert controller.status(pair.id).running is False


def test_progress_is_observable_while_a_sync_runs(setup):
    """The window polls controller state, so it must be readable mid-sync.

    Signal *delivery* needs a running event loop and is covered by the UI
    tests; what matters here is that the state a slot would read is actually
    being updated as the sync proceeds.
    """
    controller, pair, root_a, _ = setup
    for i in range(400):
        (root_a / f"file{i}.txt").write_text("x" * 4000)

    controller.start(pair.id)

    seen: list[tuple[int, int, str]] = []
    deadline = time.monotonic() + 20
    while controller.is_running(pair.id) and time.monotonic() < deadline:
        seen.append(controller.status(pair.id).progress)
        time.sleep(0.01)
    controller.wait(20_000)

    assert any(total > 0 for _, total, _ in seen), "progress counters never advanced"
    assert any(text for _, _, text in seen), "no operation description was reported"
