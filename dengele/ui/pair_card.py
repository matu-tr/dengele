"""The card that represents one folder pair on the main screen."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from dengele.app.config import Pair
from dengele.app.controller import PairStatus

from .widgets import banner, button, card, format_when, label


class PairCard(QWidget):
    """Shows a pair's paths, its live progress, and what the last sync did."""

    sync_requested = Signal(str)
    cancel_requested = Signal(str)
    preview_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(self, pair: Pair, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pair = pair

        self._name = label(pair.name, "Heading")
        self._paused = label("paused", "Muted")
        self._path_a = label(str(pair.engine.path_a), "Path")
        self._path_b = label(str(pair.engine.path_b), "Path")
        self._summary = label("", "Muted")
        self._activity = label("", "Path")

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.hide()
        self._activity.hide()

        self._blocked = banner("", "warn")
        self._blocked.hide()
        self._error = banner("", "error")
        self._error.hide()

        self._preview = button("Preview", "Ghost", "See what a sync would change")
        self._sync = button("Sync", "Primary")
        self._stop = button("Stop")
        self._edit = button("⚙", "Ghost", "Edit this pair")
        self._edit.setFixedWidth(34)
        self._stop.hide()

        self._preview.clicked.connect(lambda: self.preview_requested.emit(pair.id))
        self._sync.clicked.connect(lambda: self.sync_requested.emit(pair.id))
        self._stop.clicked.connect(lambda: self.cancel_requested.emit(pair.id))
        self._edit.clicked.connect(lambda: self.edit_requested.emit(pair.id))

        self._build()
        self.update_pair(pair)

    def _build(self) -> None:
        container = card()

        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addWidget(self._name)
        name_row.addWidget(self._paused)
        name_row.addStretch(1)
        title.addLayout(name_row)
        title.addWidget(self._path_a)
        title.addWidget(self._path_b)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self._preview)
        actions.addWidget(self._sync)
        actions.addWidget(self._stop)
        actions.addWidget(self._edit)

        header.addLayout(title, 1)
        header.addLayout(actions)
        header.setAlignment(actions, Qt.AlignmentFlag.AlignTop)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(14, 12, 14, 12)
        inner.setSpacing(8)
        inner.addLayout(header)
        inner.addWidget(self._progress)
        inner.addWidget(self._activity)
        inner.addWidget(self._summary)
        inner.addWidget(self._blocked)
        inner.addWidget(self._error)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

    def update_pair(self, pair: Pair) -> None:
        self.pair = pair
        self._name.setText(pair.name)
        self._path_a.setText(str(pair.engine.path_a))
        self._path_b.setText(str(pair.engine.path_b))
        self._paused.setVisible(not pair.enabled)

    def update_status(self, status: PairStatus) -> None:
        running = status.running
        self._sync.setVisible(not running)
        self._preview.setVisible(not running)
        self._stop.setVisible(running)
        self._sync.setEnabled(self.pair.enabled)
        self._progress.setVisible(running)
        self._activity.setVisible(running)
        self._summary.setVisible(not running)

        if running:
            done, total, description = status.progress
            if total > 0:
                self._progress.setRange(0, total)
                self._progress.setValue(done)
            else:
                # An indeterminate bar while scanning, which has no total yet.
                self._progress.setRange(0, 0)
            self._activity.setText(description or "scanning…")
        else:
            self._summary.setText(self._summarise(status))

        self._blocked.setVisible(bool(status.blocked))
        if status.blocked:
            self._blocked.setText(
                f"Held back: this sync {status.blocked}. "
                "Open Preview to see what it wanted to remove."
            )

        self._error.setVisible(bool(status.last_error))
        if status.last_error:
            self._error.setText(status.last_error)

    @staticmethod
    def _summarise(status: PairStatus) -> str:
        parts = [f"Last sync {format_when(status.last_sync_ms)}"]
        if status.copied or status.deleted:
            parts.append(f"{status.copied} copied, {status.deleted} removed")
        if status.conflicts:
            plural = "" if status.conflicts == 1 else "s"
            parts.append(f"{status.conflicts} conflict{plural}")
        return " · ".join(parts)
