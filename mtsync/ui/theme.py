"""Colours and the stylesheet built from them."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from mtsync.app.config import Theme


@dataclass(frozen=True, slots=True)
class Palette:
    window: str
    surface: str
    #: Faint fill for buttons and inset panels.
    surface_alt: str
    border: str
    #: Slightly stronger than `border`, for hover states.
    border_strong: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    accent_hover: str
    accent_tint: str
    danger: str
    danger_tint: str
    warning: str
    warning_tint: str

    # Translucent colours are written as rgba(), never as eight-digit hex:
    # Qt reads `#RRGGBBAA` as `#AARRGGBB`, so `#ffffff1a` comes out bright
    # yellow rather than a faint white. That mistake tints the whole app.


LIGHT = Palette(
    window="#f5f5f6",
    surface="#ffffff",
    surface_alt="rgba(0, 0, 0, 0.04)",
    border="rgba(0, 0, 0, 0.10)",
    border_strong="rgba(0, 0, 0, 0.16)",
    text="#1b1b1f",
    text_muted="#6b6b76",
    accent="#2563eb",
    accent_text="#ffffff",
    accent_hover="#3b76f0",
    accent_tint="rgba(37, 99, 235, 0.12)",
    danger="#c62828",
    danger_tint="rgba(198, 40, 40, 0.12)",
    warning="#a15c07",
    warning_tint="rgba(161, 92, 7, 0.14)",
)

DARK = Palette(
    window="#1c1c1f",
    surface="#26262b",
    surface_alt="rgba(255, 255, 255, 0.07)",
    border="rgba(255, 255, 255, 0.12)",
    border_strong="rgba(255, 255, 255, 0.20)",
    text="#f2f2f5",
    text_muted="#9b9ba5",
    accent="#3b82f6",
    accent_text="#ffffff",
    accent_hover="#5a97f8",
    accent_tint="rgba(59, 130, 246, 0.18)",
    danger="#f87171",
    danger_tint="rgba(248, 113, 113, 0.16)",
    warning="#fbbf24",
    warning_tint="rgba(251, 191, 36, 0.16)",
)

#: Radius of the window's own rounded corners, in pixels.
CORNER_RADIUS = 12


def resolve(theme: Theme) -> Palette:
    """The palette to paint with, following the system when asked to."""
    if theme is Theme.LIGHT:
        return LIGHT
    if theme is Theme.DARK:
        return DARK

    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def stylesheet(p: Palette) -> str:
    """One stylesheet for the whole app, generated from a palette.

    Widgets are styled by object name rather than by class wherever the same
    class is used for different roles, so a rule cannot leak into an unrelated
    part of the window.
    """
    return f"""
    QWidget {{
        color: {p.text};
        font-size: 13px;
    }}

    #Shell {{
        background: {p.window};
        border: 1px solid {p.border};
        border-radius: {CORNER_RADIUS}px;
    }}

    #TitleBar {{
        background: {p.surface_alt};
        border-top-left-radius: {CORNER_RADIUS}px;
        border-top-right-radius: {CORNER_RADIUS}px;
        border-bottom: 1px solid {p.border};
    }}
    #TitleText {{
        font-weight: 600;
        color: {p.text};
    }}
    #TitleBadge {{
        color: {p.accent};
        font-size: 11px;
        font-weight: 600;
    }}

    QPushButton {{
        background: {p.surface_alt};
        border: none;
        border-radius: 7px;
        padding: 6px 12px;
        font-weight: 500;
    }}
    QPushButton:hover {{ background: {p.border_strong}; }}
    QPushButton:disabled {{ color: {p.text_muted}; }}

    QPushButton#Primary {{
        background: {p.accent};
        color: {p.accent_text};
    }}
    QPushButton#Primary:hover {{ background: {p.accent_hover}; }}
    QPushButton#Primary:disabled {{ background: {p.accent}; color: {p.accent_text}; }}

    QPushButton#Danger {{
        background: {p.danger};
        color: #ffffff;
    }}

    QPushButton#Ghost {{
        background: transparent;
        color: {p.text_muted};
    }}
    QPushButton#Ghost:hover {{ background: {p.surface_alt}; color: {p.text}; }}

    QPushButton#Tab {{
        background: transparent;
        color: {p.text_muted};
    }}
    QPushButton#Tab:checked {{
        background: {p.surface_alt};
        color: {p.text};
    }}

    /* The close button carries its own object name, so it needs the window
       button styling spelled out rather than inherited from #WindowButton. */
    QPushButton#WindowButton, QPushButton#CloseButton {{
        background: transparent;
        border-radius: 4px;
        padding: 4px;
    }}
    QPushButton#WindowButton:hover {{ background: {p.border_strong}; }}
    QPushButton#CloseButton:hover {{ background: {p.danger}; color: #ffffff; }}

    #Card {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}

    QLabel#Muted, QLabel#Path {{ color: {p.text_muted}; }}
    QLabel#Path {{ font-family: monospace; font-size: 11px; }}
    QLabel#Heading {{ font-size: 14px; font-weight: 600; }}
    QLabel#FieldLabel {{ color: {p.text_muted}; font-size: 12px; font-weight: 500; }}

    QLabel#BannerError {{
        background: {p.danger_tint};
        color: {p.danger};
        border-radius: 7px;
        padding: 8px 10px;
    }}
    QLabel#BannerWarn {{
        background: {p.warning_tint};
        color: {p.warning};
        border-radius: 7px;
        padding: 8px 10px;
    }}
    QLabel#BannerInfo {{
        background: {p.accent_tint};
        color: {p.accent};
        border-radius: 7px;
        padding: 8px 10px;
    }}

    QProgressBar {{
        background: {p.surface_alt};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {p.accent};
        border-radius: 3px;
    }}

    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 5px 8px;
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {p.accent};
    }}
    QLineEdit[readOnly="true"] {{ color: {p.text_muted}; }}

    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface};
        border: 1px solid {p.border};
        selection-background-color: {p.accent};
        selection-color: {p.accent_text};
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {p.border};
        border-radius: 4px;
        background: {p.surface};
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
    }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border_strong};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QListWidget, QTreeWidget {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
    }}
    QListWidget::item {{ padding: 3px 6px; }}

    #Dialog {{
        background: {p.window};
        border: 1px solid {p.border};
        border-radius: {CORNER_RADIUS}px;
    }}

    QToolTip {{
        background: {p.surface};
        color: {p.text};
        border: 1px solid {p.border};
        padding: 4px;
    }}
    """
