"""Showing exactly what a sync would do, before it does it.

This is the screen that turns the delete guard from an obstacle into an answer:
when a plan is held back, the user can see *which* files it wanted to remove
and decide, rather than simply being told no.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mtsync.engine import ConflictKind, Op, OpKind, Plan, Side

from .widgets import Dialog, banner, button, card, format_bytes, label

#: Listing every operation of a huge first sync would freeze the window while
#: the list widget builds; the count is always shown in full.
MAX_ROWS = 500


class PlanPreview(Dialog):
    apply_requested = Signal(bool)  # force

    def __init__(self, plan: Plan, pair_name: str, parent: QWidget | None = None) -> None:
        super().__init__(f"What syncing “{pair_name}” would do", parent)
        self.resize(680, 560)

        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        if plan.blocked:
            layout.addWidget(
                banner(
                    f"Held back: this sync {plan.blocked}. Check the removals below "
                    "before continuing — an unmounted drive looks exactly like this.",
                    "warn",
                )
            )

        layout.addWidget(self._stats(plan))

        if plan.stats.bytes_to_copy:
            layout.addWidget(
                label(f"About {format_bytes(plan.stats.bytes_to_copy)} to transfer.", "Muted")
            )

        operations = plan.effective_ops
        if not operations:
            empty = label("Both folders already agree — nothing to do.", "Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
        else:
            layout.addWidget(self._operations(operations), 1)

        if plan.conflicts:
            layout.addWidget(label("Conflicts", "Heading"))
            layout.addWidget(self._conflicts(plan))

        skipped = len(plan.placeholders_a) + len(plan.placeholders_b)
        if skipped:
            plural = "" if skipped == 1 else "s"
            layout.addWidget(
                banner(
                    f"{skipped} file{plural} skipped: stored in the cloud and not "
                    "downloaded locally, so only a placeholder exists here.",
                    "info",
                )
            )

        errors = plan.errors_a + plan.errors_b
        if errors:
            plural = "" if len(errors) == 1 else "s"
            layout.addWidget(
                banner(
                    f"{len(errors)} path{plural} could not be read, "
                    f"e.g. {errors[0][0]} ({errors[0][1]}).",
                    "error",
                )
            )

        layout.addLayout(self._buttons(plan))

    def _stats(self, plan: Plan) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        for caption, value in (
            ("A → B", plan.stats.copy_a_to_b),
            ("B → A", plan.stats.copy_b_to_a),
            ("Removed", plan.stats.deletes),
            ("Conflicts", len(plan.conflicts)),
        ):
            tile = card()
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(12, 8, 12, 8)
            inner.setSpacing(0)
            inner.addWidget(label(str(value), "Heading"))
            inner.addWidget(label(caption, "Muted"))
            layout.addWidget(tile)

        return container

    def _operations(self, operations: list[Op]) -> QWidget:
        listing = QListWidget()
        listing.setAlternatingRowColors(False)
        for op in operations[:MAX_ROWS]:
            listing.addItem(QListWidgetItem(f"{_tag(op):<12} {op.rel}"))
        if len(operations) > MAX_ROWS:
            listing.addItem(QListWidgetItem(f"… and {len(operations) - MAX_ROWS} more"))
        return listing

    def _conflicts(self, plan: Plan) -> QWidget:
        listing = QListWidget()
        listing.setMaximumHeight(120)
        for conflict in plan.conflicts:
            if conflict.resolved_to is None:
                resolution = "left for you to resolve"
            else:
                resolution = f"keeping {conflict.resolved_to.label}"
            listing.addItem(f"{conflict.rel} — {_describe(conflict.kind)}, {resolution}")
        return listing

    def _buttons(self, plan: Plan) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addStretch(1)

        close = button("Close")
        close.clicked.connect(self.close)
        layout.addWidget(close)

        if plan.effective_ops:
            forced = bool(plan.blocked)
            confirm = button(
                "Sync anyway" if forced else "Sync now", "Danger" if forced else "Primary"
            )
            confirm.clicked.connect(lambda: self._apply(forced))
            layout.addWidget(confirm)

        return layout

    def _apply(self, force: bool) -> None:
        self.apply_requested.emit(force)
        self.close()


def _tag(op: Op) -> str:
    match op.kind:
        case OpKind.COPY:
            return "A → B" if op.side is Side.A else "B → A"
        case OpKind.DELETE:
            return f"remove {op.side.label}"
        case OpKind.MKDIR:
            return f"folder {op.side.label}"
        case OpKind.PRESERVE_LOSER:
            return "keep copy"
    return "record"


def _describe(kind: ConflictKind) -> str:
    return {
        ConflictKind.BOTH_MODIFIED: "changed on both sides",
        ConflictKind.MODIFIED_AND_DELETED: "edited on one side, deleted on the other",
        ConflictKind.TYPE_MISMATCH: "a file on one side, a folder on the other",
    }[kind]
