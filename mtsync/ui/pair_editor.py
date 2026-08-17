"""Creating and editing a folder pair."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mtsync.app import paths
from mtsync.app.config import Pair, WatchMode
from mtsync.engine import ConflictPolicy, ExcludeError, ExcludeSet, RootError, validate_roots

from .widgets import Dialog, banner, button, label

_POLICIES = [
    (ConflictPolicy.NEWEST_WINS, "Newest wins"),
    (ConflictPolicy.A_WINS, "Folder A wins"),
    (ConflictPolicy.B_WINS, "Folder B wins"),
    (ConflictPolicy.ASK, "Leave it alone and tell me"),
]

_MODES = [
    (WatchMode.ON_CHANGE, "When files change"),
    (WatchMode.INTERVAL, "On a schedule"),
    (WatchMode.MANUAL, "Only when I ask"),
]


class PairEditor(Dialog):
    """Edits an existing pair, or collects the details for a new one."""

    saved = Signal(object)  # Pair
    deleted = Signal(str)  # pair id

    def __init__(self, pair: Pair, is_new: bool, parent: QWidget | None = None) -> None:
        title = "Add a folder pair" if is_new else f"Edit “{pair.name}”"
        super().__init__(title, parent)
        self.resize(600, 640)

        self._pair = pair.copy()
        self._is_new = is_new
        self._confirming_delete = False

        self._error = banner("", "error")
        self._error.hide()

        self._name = QLineEdit(self._pair.name)
        self._name.setPlaceholderText("e.g. Documents ↔ External drive")
        self._path_a = QLineEdit(str(self._pair.engine.path_a))
        self._path_b = QLineEdit(str(self._pair.engine.path_b))
        for field in (self._path_a, self._path_b):
            field.setReadOnly(True)

        self._enabled = QCheckBox("Sync this pair")
        self._enabled.setChecked(self._pair.enabled)

        self._watch = QComboBox()
        for mode, caption in _MODES:
            self._watch.addItem(caption, mode)
        self._watch.setCurrentIndex(
            next(i for i, (m, _) in enumerate(_MODES) if m is self._pair.watch)
        )

        self._interval = QSpinBox()
        self._interval.setRange(1, 1440)
        self._interval.setSuffix(" min")
        self._interval.setValue(self._pair.interval_minutes)

        self._policy = QComboBox()
        for policy, caption in _POLICIES:
            self._policy.addItem(caption, policy)
        self._policy.setCurrentIndex(
            next(i for i, (p, _) in enumerate(_POLICIES) if p is self._pair.engine.conflict_policy)
        )

        self._excludes = QPlainTextEdit("\n".join(self._pair.engine.excludes))
        self._excludes.setPlaceholderText("node_modules/\n*.tmp")
        self._excludes.setFixedHeight(110)

        self._require_marker = QCheckBox("Require a .mt-sync-root file in both folders")
        self._require_marker.setChecked(self._pair.engine.require_marker)
        self._skip_cloud = QCheckBox("Skip files that are not downloaded")
        self._skip_cloud.setChecked(self._pair.engine.skip_cloud_placeholders)

        self._threshold_pct = QSpinBox()
        self._threshold_pct.setRange(1, 100)
        self._threshold_pct.setSuffix(" %")
        self._threshold_pct.setValue(round(self._pair.engine.delete_threshold_pct * 100))

        self._threshold_min = QSpinBox()
        self._threshold_min.setRange(0, 100_000)
        self._threshold_min.setValue(self._pair.engine.delete_threshold_min)

        self._retention = QSpinBox()
        self._retention.setRange(0, 3650)
        self._retention.setSuffix(" days")
        self._retention.setValue(self._pair.engine.recycle_retention_days)

        self._watch.currentIndexChanged.connect(self._sync_interval_visibility)
        self._build()
        self._sync_interval_visibility()

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(16, 14, 16, 8)
        layout.setSpacing(10)
        layout.addWidget(self._error)

        fields = QFormLayout()
        fields.setSpacing(8)
        fields.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        fields.addRow(label("Name", "FieldLabel"), self._name)
        fields.addRow(label("Folder A", "FieldLabel"), self._picker(self._path_a))
        fields.addRow(label("Folder B", "FieldLabel"), self._picker(self._path_b))
        fields.addRow("", self._enabled)
        fields.addRow(label("When to sync", "FieldLabel"), self._watch)
        self._interval_label = label("Every", "FieldLabel")
        fields.addRow(self._interval_label, self._interval)
        fields.addRow(label("When both sides changed", "FieldLabel"), self._policy)
        layout.addLayout(fields)

        layout.addWidget(
            label(
                "The version that loses is always kept alongside the winner, never discarded.",
                "Muted",
            )
        )

        layout.addWidget(label("Never sync these", "FieldLabel"))
        layout.addWidget(self._excludes)
        layout.addWidget(
            label("One gitignore-style pattern per line, matched inside each folder.", "Muted")
        )

        layout.addWidget(label("Safety", "Heading"))
        layout.addWidget(self._require_marker)
        layout.addWidget(
            label(
                "Fails the sync when a drive is not mounted, so an empty mount point "
                "is never mistaken for “everything was deleted”.",
                "Muted",
            )
        )
        layout.addWidget(self._skip_cloud)
        layout.addWidget(
            label(
                "Copying a cloud placeholder produces a stub, not the file it stands for.",
                "Muted",
            )
        )

        safety = QFormLayout()
        safety.setSpacing(8)
        safety.addRow(label("Hold a sync deleting over", "FieldLabel"), self._threshold_pct)
        safety.addRow(label("…but never under", "FieldLabel"), self._threshold_min)
        safety.addRow(label("Keep deleted files for", "FieldLabel"), self._retention)
        layout.addLayout(safety)
        layout.addWidget(label("A retention of 0 keeps deleted files forever.", "Muted"))
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(form)

        outer = QVBoxLayout(self.body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._footer())

    def _picker(self, field: QLineEdit) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        choose = button("Choose…")
        choose.clicked.connect(lambda: self._choose_folder(field))
        layout.addWidget(field, 1)
        layout.addWidget(choose)
        return container

    def _footer(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(16, 8, 16, 14)
        layout.setSpacing(8)

        if not self._is_new:
            self._delete = button("Remove pair", "Ghost")
            self._delete.clicked.connect(self._on_delete)
            layout.addWidget(self._delete)

        layout.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.close)
        save = button("Add pair" if self._is_new else "Save", "Primary")
        save.clicked.connect(self._on_save)
        layout.addWidget(cancel)
        layout.addWidget(save)
        return container

    def _sync_interval_visibility(self) -> None:
        interval = self._watch.currentData() is WatchMode.INTERVAL
        self._interval.setVisible(interval)
        self._interval_label.setVisible(interval)

    # -- actions --------------------------------------------------------

    def _choose_folder(self, field: QLineEdit) -> None:
        start = field.text() or str(paths.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if not chosen:
            return
        field.setText(chosen)
        if not self._name.text().strip():
            self._name.setText(Path(chosen).name)
        self._warn_if_privacy_protected(Path(chosen))

    def _warn_if_privacy_protected(self, path: Path) -> None:
        """Say so up front when macOS will gate this folder behind permission.

        Finding out later means a sync that appears to do nothing, which is
        exactly the failure this app is trying not to repeat.
        """
        if paths.is_privacy_protected(path):
            self._show_error(
                "macOS protects this location. The first sync will ask for permission — "
                "if no prompt appears, allow MT Sync under System Settings › Privacy & "
                "Security › Files and Folders.",
                tone="warn",
            )

    def _on_save(self) -> None:
        pair = self._collect()
        if pair is None:
            return
        self.saved.emit(pair)
        self.close()

    def _on_delete(self) -> None:
        if not self._confirming_delete:
            self._confirming_delete = True
            self._delete.setObjectName("Danger")
            self._delete.setText("Really remove?")
            self._delete.setStyleSheet("")  # force a re-polish with the new role
            self._show_error("Removing a pair leaves both folders untouched.", tone="warn")
            return
        self.deleted.emit(self._pair.id)
        self.close()

    def _collect(self) -> Pair | None:
        path_a = Path(self._path_a.text())
        path_b = Path(self._path_b.text())
        if not self._path_a.text() or not self._path_b.text():
            self._show_error("Choose both folders first.")
            return None

        patterns = [
            line.strip() for line in self._excludes.toPlainText().splitlines() if line.strip()
        ]
        try:
            ExcludeSet(patterns)
        except ExcludeError as err:
            self._show_error(f"That exclude list has a problem: {err}")
            return None

        pair = self._pair
        pair.name = self._name.text().strip() or path_a.name
        pair.enabled = self._enabled.isChecked()
        pair.watch = self._watch.currentData()
        pair.interval_minutes = self._interval.value()

        engine = pair.engine
        engine.path_a = path_a
        engine.path_b = path_b
        engine.excludes = patterns
        engine.conflict_policy = self._policy.currentData()
        engine.require_marker = self._require_marker.isChecked()
        engine.skip_cloud_placeholders = self._skip_cloud.isChecked()
        engine.delete_threshold_pct = self._threshold_pct.value() / 100
        engine.delete_threshold_min = self._threshold_min.value()
        engine.recycle_retention_days = self._retention.value()

        try:
            validate_roots(engine)
        except RootError as err:
            self._show_error(str(err))
            return None

        return pair

    def _show_error(self, message: str, tone: str = "error") -> None:
        self._error.setText(message)
        self._error.setObjectName(f"Banner{tone.capitalize()}")
        self._error.setStyleSheet("")
        self._error.show()
        style = self.style()
        style.unpolish(self._error)
        style.polish(self._error)
