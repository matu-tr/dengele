#!/usr/bin/env python3
"""Write the app icon to the formats each installer needs.

The icon itself is drawn in ``dengele/ui/icon.py``; this only renders it out to
``.icns`` and ``.ico``, which PyInstaller wants as files on disk. Run from the
repository root:

    python packaging/make_icons.py

Neither container needs a third-party library: both are thin wrappers around
PNGs on the platforms this app targets.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QBuffer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dengele.ui.icon import _render  # noqa: E402

#: OSType codes macOS uses for each square PNG size.
ICNS_TYPES = {
    32: b"ic11",
    64: b"ic12",
    128: b"ic07",
    256: b"ic13",
    512: b"ic14",
}

ICO_SIZES = (16, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    pixmap = _render(size, background=True, foreground=QColor("#ffffff"))
    # QBuffer must own its byte array: handing it a temporary QByteArray leaves
    # it pointing at freed memory and the process dies on the next write.
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def write_icns(path: Path) -> None:
    chunks = b""
    for size, ostype in sorted(ICNS_TYPES.items()):
        payload = png_bytes(size)
        chunks += ostype + struct.pack(">I", len(payload) + 8) + payload
    path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def write_ico(path: Path) -> None:
    images = [(size, png_bytes(size)) for size in ICO_SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = b""
    for size, payload in images:
        # A size of 0 in the directory means 256; the format has one byte here.
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(payload),
            offset,
        )
        offset += len(payload)

    path.write_bytes(header + directory + b"".join(payload for _, payload in images))


def main() -> int:
    QApplication([])
    write_icns(HERE / "icon.icns")
    write_ico(HERE / "icon.ico")
    (HERE / "icon.png").write_bytes(png_bytes(512))
    print(f"wrote icon.icns, icon.ico and icon.png to {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
