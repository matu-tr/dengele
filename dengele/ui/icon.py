"""The app icon, drawn rather than shipped as a binary.

Two arrows pointing opposite ways — the whole idea of the app in one glyph.
Drawing it in code means every size is crisp and there is nothing to regenerate
when the design changes.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap

BRAND = QColor("#2563eb")

#: Arrow geometry in a 0..1 unit square, so one description scales to any size.
_TOP_SHAFT = QRectF(0.24, 0.35, 0.38, 0.08)
_TOP_HEAD = ((0.58, 0.28), (0.78, 0.39), (0.58, 0.50))
_BOTTOM_SHAFT = QRectF(0.38, 0.57, 0.38, 0.08)
_BOTTOM_HEAD = ((0.42, 0.50), (0.22, 0.61), (0.42, 0.72))


def app_icon(size: int = 256) -> QIcon:
    """The full-colour icon, for the window and the dock."""
    icon = QIcon()
    for edge in (16, 32, 64, 128, size):
        icon.addPixmap(_render(edge, background=True, foreground=QColor("#ffffff")))
    return icon


def tray_icon(size: int = 32) -> QIcon:
    """A flat icon for the menu bar.

    macOS recolours a template image to match the menu bar, so this is drawn as
    a single-colour silhouette with no background. Qt applies the template
    treatment when the icon is marked as a mask.
    """
    icon = QIcon()
    for edge in (size, size * 2):
        icon.addPixmap(_render(edge, background=False, foreground=QColor("#000000")))
    icon.setIsMask(True)
    return icon


def _render(size: int, background: bool, foreground: QColor) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    if background:
        painter.setBrush(QBrush(BRAND))
        radius = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    painter.setBrush(QBrush(foreground))
    for shaft, head in ((_TOP_SHAFT, _TOP_HEAD), (_BOTTOM_SHAFT, _BOTTOM_HEAD)):
        painter.drawRect(_scale_rect(shaft, size))
        painter.drawPath(_scale_path(head, size))

    painter.end()
    return pixmap


def _scale_rect(rect: QRectF, size: int) -> QRectF:
    return QRectF(rect.x() * size, rect.y() * size, rect.width() * size, rect.height() * size)


def _scale_path(points: tuple[tuple[float, float], ...], size: int) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(QPointF(points[0][0] * size, points[0][1] * size))
    for x, y in points[1:]:
        path.lineTo(QPointF(x * size, y * size))
    path.closeSubpath()
    return path


def save_png(path: str, size: int = 512) -> None:
    """Write the icon to disk, for packaging."""
    _render(size, background=True, foreground=QColor("#ffffff")).save(path, "PNG")
