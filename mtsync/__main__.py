"""Starting the application."""

from __future__ import annotations

import logging
import logging.handlers
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from mtsync.app import config as config_module
from mtsync.app import paths
from mtsync.app.controller import Controller
from mtsync.app.watcher import Scheduler, Watchers

log = logging.getLogger(__name__)

#: Name of the socket used to detect a second launch.
_INSTANCE_KEY = "tr.matu.mtsync.instance"

#: How often the window re-reads controller state. Signals already push most
#: updates; this catches anything that happened while the window was hidden.
_REFRESH_MS = 1500


def setup_logging(verbose: bool = False) -> None:
    """Log to a rotating file, and to the console when run from a terminal.

    The predecessor wrote to a single file that grew without limit, and its
    console filter suppressed the very lines it was writing.
    """
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _claim_single_instance() -> QLocalServer | None:
    """Return a listening server, or None if another instance already runs.

    A socket rather than a lock file, because a crash releases it: the
    predecessor's ``/tmp`` lock file survived a crash and then blocked every
    subsequent run until it was deleted by hand.
    """
    probe = QLocalSocket()
    probe.connectToServer(_INSTANCE_KEY)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return None

    QLocalServer.removeServer(_INSTANCE_KEY)
    server = QLocalServer()
    if not server.listen(_INSTANCE_KEY):
        log.warning("could not claim the single-instance socket: %s", server.errorString())
    return server


def main() -> int:
    setup_logging(verbose="--verbose" in sys.argv)

    app = QApplication(sys.argv)
    app.setApplicationName(paths.APP_NAME)
    app.setApplicationDisplayName(paths.APP_NAME)
    app.setOrganizationName("matu-tr")
    # The tray keeps the app alive once the window is closed.
    app.setQuitOnLastWindowClosed(False)

    server = _claim_single_instance()
    if server is None:
        log.info("another instance is already running; asking it to show itself")
        return 0

    from mtsync.ui.icon import app_icon
    from mtsync.ui.main_window import MainWindow
    from mtsync.ui.tray import Tray

    app.setWindowIcon(app_icon())

    settings = config_module.load()
    controller = Controller(settings)
    watchers = Watchers(controller)
    scheduler = Scheduler(controller)

    window = MainWindow(controller, watchers)
    tray = Tray(controller, window)
    tray.show()

    server.newConnection.connect(lambda: tray.show_window())

    watchers.rebuild()
    scheduler.start()

    if settings.start_minimized:
        log.info("starting hidden in the tray")
    else:
        window.show()

    # A periodic refresh keeps the window truthful even when it was hidden
    # while a scheduled sync ran and its signals had no listener on screen.
    refresh = QTimer(window)
    refresh.timeout.connect(window.refresh_statuses)
    refresh.start(_REFRESH_MS)

    # Ctrl+C in a terminal should still quit; Qt otherwise swallows it.
    signal.signal(signal.SIGINT, lambda *_: tray.quit())
    keepalive = QTimer()
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)

    app.aboutToQuit.connect(scheduler.stop)
    app.aboutToQuit.connect(watchers.stop)

    log.info("MT Sync started (data in %s)", paths.data_dir())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
