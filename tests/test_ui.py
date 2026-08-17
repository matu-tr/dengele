"""Smoke tests for the widgets.

These need Qt's GUI libraries, which a bare CI container or a headless server
may not have. They skip rather than fail in that case, so the engine tests
still run everywhere; CI installs the libraries so this file actually executes
there.

They do not check appearance — they check that every screen can be built,
opened and driven without raising, which is what catches the mistakes that
make a UI unusable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as err:  # pragma: no cover - depends on the host
    # `pytest.importorskip` only skips when a module is missing; Qt's widget
    # modules import fine as Python but fail to load their shared libraries
    # (libGL and friends) on a machine with no graphics stack.
    pytest.skip(f"Qt GUI libraries unavailable: {err}", allow_module_level=True)

from mtsync.app.config import Config, Pair, Theme
from mtsync.app.controller import Controller
from mtsync.app.watcher import Watchers
from mtsync.engine import ConflictPolicy, Side


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def wired(qt_app, tmp_path: Path, monkeypatch):
    """A controller and window backed by throwaway folders."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "doc.txt").write_text("content")

    pair = Pair.create("Test pair", root_a, root_b)
    pair.engine.excludes = []
    config = Config(pairs=[pair])

    # Never write to the real config file from a test.
    monkeypatch.setattr("mtsync.app.config.save", lambda *a, **k: None)
    monkeypatch.setattr("mtsync.app.autostart.set_enabled", lambda *a, **k: None)

    controller = Controller(config, database=tmp_path / "state.db")
    watchers = Watchers(controller)

    from mtsync.ui.main_window import MainWindow

    window = MainWindow(controller, watchers)
    try:
        yield window, controller, pair
    finally:
        controller.cancel_all()
        controller.wait(5_000)
        watchers.stop()
        window.close()
        window.deleteLater()


def test_the_window_builds_and_shows_its_pairs(wired):
    window, _, pair = wired
    window.show()

    assert window.isVisible()
    assert pair.id in window._cards
    assert window._cards[pair.id].pair.name == "Test pair"


def test_the_window_is_frameless_but_movable(wired):
    from PySide6.QtCore import Qt

    window, _, _ = wired
    window.show()

    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    # Its own title bar stands in for the system one.
    assert window.title_bar.isVisible()
    assert window.title_bar.height() > 0


def test_switching_tabs_changes_the_page(wired):
    window, _, _ = wired
    window.show()

    assert window._pages.currentIndex() == 0
    window._tab_settings.click()
    assert window._pages.currentIndex() == 1
    window._tab_pairs.click()
    assert window._pages.currentIndex() == 0


@pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK, Theme.SYSTEM])
def test_every_theme_produces_a_usable_stylesheet(wired, theme):
    window, controller, _ = wired
    controller.config.theme = theme
    window.apply_theme()

    assert window.styleSheet(), "theme produced an empty stylesheet"
    assert "{" in window.styleSheet()


def test_a_card_reflects_a_finished_sync(wired, qt_app):
    window, controller, pair = wired
    window.show()

    controller.start(pair.id)
    controller.wait(10_000)
    window.refresh_statuses()

    card = window._cards[pair.id]
    assert "Last sync" in card._summary.text()
    assert card._progress.isHidden()


def test_the_preview_dialog_opens_and_lists_work(wired):
    from mtsync.ui.plan_preview import PlanPreview

    window, controller, pair = wired
    plan = controller.preview(pair.id)
    assert plan.effective_ops, "expected the first sync to have work to do"

    dialog = PlanPreview(plan, pair.name, window)
    dialog.show()
    try:
        assert dialog.isVisible()
    finally:
        dialog.close()


def test_the_preview_dialog_handles_an_empty_plan(wired):
    from mtsync.ui.plan_preview import PlanPreview

    window, controller, pair = wired
    controller.start(pair.id)
    controller.wait(10_000)

    plan = controller.preview(pair.id)
    assert not plan.effective_ops

    dialog = PlanPreview(plan, pair.name, window)
    dialog.show()
    try:
        assert dialog.isVisible()
    finally:
        dialog.close()


def test_the_editor_round_trips_a_pair(wired):
    from mtsync.ui.pair_editor import PairEditor

    window, _, pair = wired
    editor = PairEditor(pair, is_new=False, parent=window)
    editor.show()

    saved: list[Pair] = []
    editor.saved.connect(saved.append)

    editor._name.setText("Renamed")
    editor._policy.setCurrentIndex(
        next(
            i
            for i in range(editor._policy.count())
            if editor._policy.itemData(i) is ConflictPolicy.A_WINS
        )
    )
    editor._excludes.setPlainText("*.tmp\nbuild/")
    editor._threshold_pct.setValue(35)
    editor._on_save()

    assert len(saved) == 1
    assert saved[0].name == "Renamed"
    assert saved[0].engine.conflict_policy is ConflictPolicy.A_WINS
    assert saved[0].engine.excludes == ["*.tmp", "build/"]
    assert saved[0].engine.delete_threshold_pct == pytest.approx(0.35)


def test_the_editor_refuses_an_unusable_exclude_pattern(wired):
    from mtsync.ui.pair_editor import PairEditor

    window, _, pair = wired
    editor = PairEditor(pair, is_new=False, parent=window)

    saved: list[Pair] = []
    editor.saved.connect(saved.append)

    editor._excludes.setPlainText("!")
    editor._on_save()

    assert saved == [], "an unparseable pattern must not be saved"
    # `isVisible` is False for a child of a window that was never shown;
    # `isHidden` reflects the widget's own state, which is what we set.
    assert not editor._error.isHidden()


def test_the_editor_refuses_two_identical_folders(wired, tmp_path: Path):
    from mtsync.ui.pair_editor import PairEditor

    window, _, pair = wired
    editor = PairEditor(pair, is_new=False, parent=window)

    saved: list[Pair] = []
    editor.saved.connect(saved.append)

    editor._path_b.setText(editor._path_a.text())
    editor._on_save()

    assert saved == []
    assert not editor._error.isHidden()


def test_the_tray_menu_builds(wired):
    from mtsync.ui.tray import Tray

    window, controller, _ = wired
    tray = Tray(controller, window)
    try:
        menu = tray.contextMenu()
        captions = [action.text() for action in menu.actions() if action.text()]
        assert "Open MT Sync" in captions
        assert "Sync all now" in captions
        assert "Quit" in captions
    finally:
        tray.hide()


def test_the_icon_renders_at_every_size(qt_app):
    from mtsync.ui.icon import app_icon, tray_icon

    icon = app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(64, 64).isNull()

    tray = tray_icon()
    assert not tray.isNull()
    assert tray.isMask(), "the menu-bar icon must be a template image on macOS"


def test_closing_the_window_hides_it_when_configured_to(wired):
    window, controller, _ = wired
    controller.config.close_to_tray = True
    window.show()

    window.close()
    assert not window.isVisible(), "window should hide rather than quit"


def test_an_empty_config_shows_the_getting_started_screen(qt_app, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mtsync.app.config.save", lambda *a, **k: None)
    from mtsync.ui.main_window import MainWindow

    controller = Controller(Config(), database=tmp_path / "state.db")
    window = MainWindow(controller, Watchers(controller))
    try:
        window.show()
        assert window._cards == {}
        assert not window._sync_all.isVisible()
    finally:
        window.close()
        window.deleteLater()


def test_side_labels_are_stable():
    """The UI prints these; changing them silently would confuse a user."""
    assert Side.A.label == "A"
    assert Side.B.label == "B"
