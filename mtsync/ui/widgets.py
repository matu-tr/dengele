"""Small building blocks shared by the screens."""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def button(text: str, role: str = "", tooltip: str = "") -> QPushButton:
    """A push button wired to one of the roles the stylesheet knows about."""
    widget = QPushButton(text)
    if role:
        widget.setObjectName(role)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def label(text: str, role: str = "") -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def banner(text: str, tone: str = "info") -> QLabel:
    """A coloured strip for a warning, error or note."""
    widget = QLabel(text)
    widget.setObjectName(f"Banner{tone.capitalize()}")
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def card() -> QFrame:
    widget = QFrame()
    widget.setObjectName("Card")
    return widget


def row(*widgets: QWidget, spacing: int = 8) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return container


def stretch() -> QWidget:
    filler = QWidget()
    filler.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return filler


def column(margin: int = 0, spacing: int = 8) -> tuple[QWidget, QVBoxLayout]:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(spacing)
    return container, layout


class TitleBar(QWidget):
    """Stands in for the system title bar the window does not have.

    Dragging and resizing go through Qt's native helpers rather than manual
    coordinate arithmetic, which is what keeps window snapping working on
    Windows — the usual casualty of a frameless window.
    """

    minimise_requested = Signal()
    maximise_requested = Signal()
    close_requested = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(40)

        self._title = label(title, "TitleText")
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._badge = label("", "TitleBadge")
        self._badge.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        controls = self._controls()
        # macOS puts window controls on the left and centres the title;
        # Windows puts them on the right.
        if sys.platform == "darwin":
            layout.addWidget(controls)
            layout.addStretch(1)
            layout.addWidget(self._title)
            layout.addWidget(self._badge)
            layout.addStretch(1)
            spacer = QWidget()
            spacer.setFixedWidth(controls.sizeHint().width())
            layout.addWidget(spacer)
        else:
            layout.addWidget(self._title)
            layout.addWidget(self._badge)
            layout.addStretch(1)
            layout.addWidget(controls)

    def set_busy(self, busy: bool) -> None:
        self._badge.setText("syncing…" if busy else "")
        self._badge.setVisible(busy)

    def _controls(self) -> QWidget:
        if sys.platform == "darwin":
            close = _TrafficLight("#ff5f57", "Close")
            minimise = _TrafficLight("#febc2e", "Minimise")
            maximise = _TrafficLight("#28c840", "Zoom")
            order = (close, minimise, maximise)
        else:
            minimise = button("–", "WindowButton", "Minimise")
            maximise = button("☐", "WindowButton", "Maximise")
            close = button("✕", "WindowButton", "Close")
            close.setObjectName("CloseButton")
            for control in (minimise, maximise, close):
                control.setFixedSize(28, 24)
            order = (minimise, maximise, close)

        close.clicked.connect(self.close_requested)
        minimise.clicked.connect(self.minimise_requested)
        maximise.clicked.connect(self.maximise_requested)
        return row(*order, spacing=8 if sys.platform == "darwin" else 2)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window().windowHandle()
            if window is not None:
                window.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximise_requested.emit()
            return
        super().mouseDoubleClickEvent(event)


class _TrafficLight(QPushButton):
    """A macOS-style window control dot."""

    def __init__(self, colour: str, tooltip: str) -> None:
        super().__init__()
        self.setFixedSize(12, 12)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QPushButton {{ background: {colour}; border-radius: 6px; border: none; }}"
            f"QPushButton:hover {{ background: {colour}; }}"
        )


class FramelessWindow(QWidget):
    """A window that paints its own frame, including a resize grip border."""

    #: How close to an edge counts as "grab to resize", in pixels.
    RESIZE_MARGIN = 6

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(title)
        self.setMouseTracking(True)

        self._shell = QWidget(self)
        self._shell.setObjectName("Shell")
        self._shell.setMouseTracking(True)

        outer = QVBoxLayout(self)
        # A pixel of padding keeps the rounded corners from being clipped and
        # gives the resize border somewhere to live.
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(self._shell)

        self.title_bar = TitleBar(title)
        self.body = QWidget()
        self.body.setMouseTracking(True)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.title_bar)
        shell_layout.addWidget(self.body, 1)

        self.title_bar.minimise_requested.connect(self.showMinimized)
        self.title_bar.maximise_requested.connect(self._toggle_maximised)

    def _toggle_maximised(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        edges = self._edges_at(event.position().toPoint())
        if edges and event.button() == Qt.MouseButton.LeftButton:
            window = self.windowHandle()
            if window is not None:
                window.startSystemResize(edges)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.setCursor(_cursor_for(self._edges_at(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def _edges_at(self, point: QPoint) -> Qt.Edge:
        if self.isMaximized():
            return Qt.Edge(0)

        margin = self.RESIZE_MARGIN
        edges = Qt.Edge(0)
        if point.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif point.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if point.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif point.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges


def _cursor_for(edges: Qt.Edge) -> Qt.CursorShape:
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)

    if (left and top) or (right and bottom):
        return Qt.CursorShape.SizeFDiagCursor
    if (right and top) or (left and bottom):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    if top or bottom:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


class Dialog(QWidget):
    """A frameless modal panel, styled like the main window."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowTitle(title)

        self._panel = QWidget(self)
        self._panel.setObjectName("Dialog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(self._panel)

        self.title_bar = TitleBar(title)
        self.title_bar.minimise_requested.connect(lambda: None)
        self.title_bar.maximise_requested.connect(lambda: None)
        self.title_bar.close_requested.connect(self.close)

        self.body = QWidget()
        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.body, 1)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def center_on_parent(self) -> None:
        reference = self.parentWidget() or QApplication.activeWindow()
        if reference is None:
            return
        geometry = reference.frameGeometry()
        self.move(geometry.center() - self.rect().center())


def format_bytes(count: int) -> str:
    """``1.2 GB``, ``840 KB``, ``0 B``."""
    if count < 1024:
        return f"{count} B"
    value = float(count)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}" if value < 10 else f"{round(value)} {unit}"
    return f"{value:.1f} PB"


def format_when(ms: int | None) -> str:
    """``just now``, ``5 min ago``, or a date once it is older than a day."""
    if not ms:
        return "never"
    delta = time.time() - ms / 1000
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    return time.strftime("%d %b %Y", time.localtime(ms / 1000))


def elide(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]
