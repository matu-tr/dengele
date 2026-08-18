# PyInstaller build description.
#
# Run from the repository root:  pyinstaller packaging/dengele.spec
#
# PySide6 ships every Qt module; bundling the lot would produce a ~400 MB app
# for a program that uses widgets and a socket. The excludes below keep it to
# what is actually imported.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

# Qt modules this app never touches. WebEngine alone is well over half the
# size of an unfiltered bundle.
EXCLUDED_QT = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
]

EXCLUDED_OTHER = [
    "tkinter",
    "matplotlib",
    "numpy",
    "PIL",
    "pytest",
    "setuptools",
]

analysis = Analysis(
    [str(ROOT / "dengele" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # watchdog picks its observer at runtime, so PyInstaller cannot see it.
        "watchdog.observers.fsevents",
        "watchdog.observers.winapi",
        "watchdog.observers.polling",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Dengele",
    debug=False,
    strip=False,
    upx=False,
    # A windowed build has no console; on Windows that also stops a terminal
    # flashing up behind the window at launch.
    console=False,
    icon=str(ROOT / "packaging" / "icon.icns")
    if sys.platform == "darwin"
    else str(ROOT / "packaging" / "icon.ico"),
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Dengele",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="Dengele.app",
        icon=str(ROOT / "packaging" / "icon.icns"),
        bundle_identifier="tr.matu.dengele",
        info_plist={
            "CFBundleName": "Dengele",
            "CFBundleDisplayName": "Dengele",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            # Without these, macOS cannot present a meaningful consent prompt
            # for the protected folders people most want to sync — and a denied
            # read can hang rather than fail, which looks like a frozen app.
            "NSDesktopFolderUsageDescription": (
                "Dengele needs access to your Desktop to keep the folders you "
                "chose in sync."
            ),
            "NSDocumentsFolderUsageDescription": (
                "Dengele needs access to your Documents to keep the folders you "
                "chose in sync."
            ),
            "NSDownloadsFolderUsageDescription": (
                "Dengele needs access to your Downloads to keep the folders you "
                "chose in sync."
            ),
            "NSRemovableVolumesUsageDescription": (
                "Dengele needs access to external drives to sync onto them."
            ),
            "NSNetworkVolumesUsageDescription": (
                "Dengele needs access to network volumes to sync onto them."
            ),
            # The app lives in the menu bar; it should not bounce in the Dock
            # when it starts hidden.
            "LSUIElement": False,
        },
    )
