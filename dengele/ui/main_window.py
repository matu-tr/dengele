"""The main window: the pair list, settings, and everything they open."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dengele.app import autostart
from dengele.app import config as config_module
from dengele.app.config import Config, Pair, Theme
from dengele.app.controller import Controller
from dengele.app.watcher import Watchers
from dengele.engine import EngineError

from .pair_card import PairCard
from .pair_editor import PairEditor
from .plan_preview import PlanPreview
from .theme import resolve, stylesheet
from .widgets import FramelessWindow, banner, button, label

log = logging.getLogger(__name__)


class MainWindow(FramelessWindow):
    def __init__(self, controller: Controller, watchers: Watchers) -> None:
        super().__init__("Dengele")
        self.resize(900, 640)
        self.setMinimumSize(660, 460)

        self.controller = controller
        self.watchers = watchers
        self._cards: dict[str, PairCard] = {}

        self._error = banner("", "error")
        self._error.hide()

        self._pages = QStackedWidget()
        self._pairs_page = _PairsPage()
        self._settings_page = _SettingsPage(controller.config)
        self._pages.addWidget(self._pairs_page)
        self._pages.addWidget(self._settings_page)

        self._build()
        self._connect()
        self.refresh_pairs()
        self.apply_theme()

    # -- construction ---------------------------------------------------

    def _build(self) -> None:
        self._tab_pairs = button("Folders", "Tab")
        self._tab_settings = button("Settings", "Tab")
        for tab in (self._tab_pairs, self._tab_settings):
            tab.setCheckable(True)
        self._tab_pairs.setChecked(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self._tab_pairs, 0)
        group.addButton(self._tab_settings, 1)
        group.idClicked.connect(self._pages.setCurrentIndex)

        self._sync_all = button("Sync all")
        self._add = button("Add pair", "Primary")

        nav = QHBoxLayout()
        nav.setContentsMargins(14, 8, 14, 8)
        nav.setSpacing(6)
        nav.addWidget(self._tab_pairs)
        nav.addWidget(self._tab_settings)
        nav.addStretch(1)
        nav.addWidget(self._sync_all)
        nav.addWidget(self._add)

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(nav)

        error_row = QHBoxLayout()
        error_row.setContentsMargins(14, 0, 14, 0)
        error_row.addWidget(self._error)
        layout.addLayout(error_row)
        layout.addWidget(self._pages, 1)

    def _connect(self) -> None:
        self._add.clicked.connect(self._on_add)
        self._sync_all.clicked.connect(self.controller.start_all)
        self.title_bar.close_requested.connect(self._on_close_button)

        self.controller.pair_changed.connect(self._on_pair_changed)
        self.controller.sync_failed.connect(self._on_sync_failed)
        self._settings_page.changed.connect(self._on_settings_changed)

    # -- pairs ----------------------------------------------------------

    def refresh_pairs(self) -> None:
        self._pairs_page.clear()
        self._cards.clear()

        for pair in self.controller.config.pairs:
            card = PairCard(pair)
            card.sync_requested.connect(self._on_sync)
            card.cancel_requested.connect(self.controller.cancel)
            card.preview_requested.connect(self._on_preview)
            card.edit_requested.connect(self._on_edit)
            card.update_status(self.controller.status(pair.id))
            self._cards[pair.id] = card
            self._pairs_page.add_card(card)

        self._pairs_page.set_empty(not self.controller.config.pairs, self._on_add)
        self._sync_all.setVisible(bool(self.controller.config.pairs))

    def refresh_statuses(self) -> None:
        """Re-read every card from the controller.

        The window renders controller state rather than tracking its own, so a
        sync started by the scheduler or the file watcher shows up here exactly
        like one the user clicked.
        """
        busy = False
        for pair_id, card in self._cards.items():
            status = self.controller.status(pair_id)
            card.update_status(status)
            busy = busy or status.running
        self.title_bar.set_busy(busy)

    def _on_pair_changed(self, pair_id: str) -> None:
        card = self._cards.get(pair_id)
        if card is not None:
            card.update_status(self.controller.status(pair_id))
        self.title_bar.set_busy(self.controller.any_running())

    def _on_sync(self, pair_id: str) -> None:
        self._hide_error()
        self.controller.start(pair_id)

    def _on_sync_failed(self, _pair_id: str, message: str) -> None:
        self._show_error(message)

    def _on_preview(self, pair_id: str) -> None:
        pair = self.controller.config.pair(pair_id)
        if pair is None:
            return

        self._hide_error()
        try:
            plan = self.controller.preview(pair_id)
        except (EngineError, OSError) as err:
            self._show_error(str(err))
            return

        dialog = PlanPreview(plan, pair.name, self)
        dialog.apply_requested.connect(lambda force: self.controller.start(pair_id, force))
        dialog.show()
        dialog.center_on_parent()

    def _on_add(self) -> None:
        # Deliberately blank rather than pre-filled with the home folder, which
        # would be a dangerous default to accept by accident.
        pair = Pair.create("", Path(""), Path(""))
        editor = PairEditor(pair, is_new=True, parent=self)
        editor.saved.connect(self._on_pair_added)
        editor.show()
        editor.center_on_parent()

    def _on_edit(self, pair_id: str) -> None:
        pair = self.controller.config.pair(pair_id)
        if pair is None:
            return
        editor = PairEditor(pair, is_new=False, parent=self)
        editor.saved.connect(self._on_pair_saved)
        editor.deleted.connect(self._on_pair_deleted)
        editor.show()
        editor.center_on_parent()

    def _on_pair_added(self, pair: Pair) -> None:
        self.controller.config.pairs.append(pair)
        self._persist()
        self.refresh_pairs()

    def _on_pair_saved(self, pair: Pair) -> None:
        pairs = self.controller.config.pairs
        for index, existing in enumerate(pairs):
            if existing.id == pair.id:
                pairs[index] = pair
                break
        self._persist()
        self.refresh_pairs()

    def _on_pair_deleted(self, pair_id: str) -> None:
        self.controller.forget_pair(pair_id)
        self.controller.config.pairs = [p for p in self.controller.config.pairs if p.id != pair_id]
        self._persist()
        self.refresh_pairs()

    # -- settings -------------------------------------------------------

    def _on_settings_changed(self) -> None:
        self._persist()
        self.apply_theme()

    def apply_theme(self) -> None:
        palette = resolve(self.controller.config.theme)
        sheet = stylesheet(palette)
        self.setStyleSheet(sheet)
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _persist(self) -> None:
        try:
            config_module.save(self.controller.config)
        except OSError as err:
            self._show_error(f"Could not save settings: {err}")
            return
        autostart.set_enabled(self.controller.config.autostart)
        self.watchers.rebuild()

    # -- window ---------------------------------------------------------

    def _on_close_button(self) -> None:
        self.close()

    def closeEvent(self, event) -> None:
        # Closing the window means "get out of my way", not "stop syncing",
        # unless the user has said otherwise.
        if self.controller.config.close_to_tray:
            event.ignore()
            self.hide()
            return
        event.accept()

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _hide_error(self) -> None:
        self._error.hide()


class _PairsPage(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(14, 6, 14, 14)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._content)
        self._empty: QWidget | None = None

    def add_card(self, card: PairCard) -> None:
        self._layout.insertWidget(self._layout.count() - 1, card)

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._empty = None

    def set_empty(self, empty: bool, on_add) -> None:
        if not empty:
            return

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(8)
        layout.addStretch(1)

        heading = label("No folders yet", "Heading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blurb = label(
            "Pick two folders and Dengele keeps them identical in both directions,\n"
            "with a recycle bin for anything it removes.",
            "Muted",
        )
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        add = button("Add your first pair", "Primary")
        add.clicked.connect(on_add)
        add_row = QHBoxLayout()
        add_row.addStretch(1)
        add_row.addWidget(add)
        add_row.addStretch(1)

        layout.addWidget(heading)
        layout.addWidget(blurb)
        layout.addLayout(add_row)
        layout.addStretch(1)

        self._empty = container
        self.add_card(container)


class _SettingsPage(QScrollArea):
    changed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._config = config

        self._theme = QComboBox()
        for theme, caption in (
            (Theme.SYSTEM, "Match system"),
            (Theme.LIGHT, "Light"),
            (Theme.DARK, "Dark"),
        ):
            self._theme.addItem(caption, theme)
        self._theme.setCurrentIndex([Theme.SYSTEM, Theme.LIGHT, Theme.DARK].index(config.theme))

        self._autostart = QCheckBox("Start with the computer")
        self._autostart.setChecked(config.autostart)
        self._start_minimised = QCheckBox("Start hidden in the menu bar")
        self._start_minimised.setChecked(config.start_minimized)
        self._close_to_tray = QCheckBox("Closing the window keeps syncing in the background")
        self._close_to_tray.setChecked(config.close_to_tray)
        self._notifications = QCheckBox("Notify me when a sync changes something")
        self._notifications.setChecked(config.notifications)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 6, 14, 14)
        layout.setSpacing(10)

        form = QFormLayout()
        form.addRow(label("Appearance", "FieldLabel"), self._theme)
        layout.addLayout(form)

        for box in (
            self._autostart,
            self._start_minimised,
            self._close_to_tray,
            self._notifications,
        ):
            layout.addWidget(box)
            box.toggled.connect(self._apply)

        layout.addWidget(
            label(
                "Deleted files are kept in a recycle bin inside each folder; "
                "how long for is set per pair.",
                "Muted",
            )
        )
        layout.addStretch(1)
        self.setWidget(content)

        self._theme.currentIndexChanged.connect(self._apply)

    def _apply(self) -> None:
        self._config.theme = self._theme.currentData()
        self._config.autostart = self._autostart.isChecked()
        self._config.start_minimized = self._start_minimised.isChecked()
        self._config.close_to_tray = self._close_to_tray.isChecked()
        self._config.notifications = self._notifications.isChecked()
        self.changed.emit()
