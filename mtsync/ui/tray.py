"""The menu-bar / system-tray presence."""

from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from mtsync.app.controller import Controller

from .icon import app_icon, tray_icon


class Tray(QSystemTrayIcon):
    """Keeps the app reachable once its window is closed."""

    def __init__(self, controller: Controller, window) -> None:
        super().__init__(tray_icon())
        self._controller = controller
        self._window = window

        self.setToolTip("MT Sync")
        self.setContextMenu(self._menu())
        self.activated.connect(self._on_activated)
        controller.pair_changed.connect(self._refresh_tooltip)

    def _menu(self) -> QMenu:
        menu = QMenu()

        show = QAction("Open MT Sync", menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        menu.addSeparator()

        sync = QAction("Sync all now", menu)
        sync.triggered.connect(self._controller.start_all)
        menu.addAction(sync)

        stop = QAction("Stop syncing", menu)
        stop.triggered.connect(self._controller.cancel_all)
        menu.addAction(stop)
        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        # Held so Python does not garbage-collect the menu while Qt shows it.
        self._menu_ref = menu
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left click toggles the window; the menu stays on right click, which is
        # what both platforms lead people to expect.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self._window.isVisible() and self._window.isActiveWindow():
                self._window.hide()
            else:
                self.show_window()

    def show_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        self._controller.cancel_all()
        # Give a running sync a moment to stop cleanly rather than leaving a
        # half-written temp file behind.
        self._controller.wait(3_000)
        self.hide()
        QApplication.quit()

    def _refresh_tooltip(self, _pair_id: str = "") -> None:
        busy = self._controller.any_running()
        self.setToolTip("MT Sync — syncing…" if busy else "MT Sync")


def window_icon() -> QIcon:
    return app_icon()
