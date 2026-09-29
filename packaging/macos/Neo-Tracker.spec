from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata
from PySide6.QtCore import QLibraryInfo

root = Path(SPECPATH).resolve().parents[1]
metadata = []
metadata.append((str(Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"), "PySide6/Qt/translations"))
for distribution in ("numpy", "scipy", "opencv-python", "PySide6", "shiboken6"):
    metadata += copy_metadata(distribution)

a = Analysis(
    [str(root / "packaging/macos/entry.py")],
    pathex=[str(root), str(root / "packaging/macos")],
    datas=metadata,
    # Optional backends in the build machine's environment are not app dependencies.
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "IPython", "matplotlib",
              "pandas", "torch", "tensorflow", "cupy", "jax", "av"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Neo-Tracker",
    console=False, debug=False, strip=False, upx=False,
    target_arch="arm64", codesign_identity=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Neo-Tracker")
app = BUNDLE(
    collection, name="Neo-Tracker.app",
    icon=str(root / "assets/app-icon/ICSTrackerWaveROIPoint.icns"),
    bundle_identifier="org.ics.neotracker", version="0.1.0",
    info_plist={
        "CFBundleDisplayName": "Neo-Tracker",
        "CFBundleVersion": "20260929.1",
        "LSMinimumSystemVersion": "27.0",
        "NSHighResolutionCapable": True,
    },
)
