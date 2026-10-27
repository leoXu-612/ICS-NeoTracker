from pathlib import Path
import sys
import tomllib
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
is_mac = sys.platform == "darwin"
icon = root / "assets/app-icon/ICSTrackerWaveROIPoint.icns"
if not is_mac:
    from PIL import Image
    icon = root / "build/Neo-Tracker.ico"
    icon.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(root / "assets/app-icon/icstracker-app-icon-wave-roi-point-1024.png") as source:
        source.save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

a = Analysis(
    [str(root / "packaging/launcher.py")],
    pathex=[str(root)],
    datas=sum((copy_metadata(name) for name in ["numpy", "scipy", "PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "opencv-python"]), []),
    hiddenimports=[],
    excludes=["av", "PyQt5", "PyQt6", "PySide2", "tkinter", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Neo-Tracker",
    debug=False,
    strip=False,
    upx=False,
    console=not is_mac,
    hide_console="hide-early" if not is_mac else None,
    argv_emulation=False,
    icon=str(icon),
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Neo-Tracker")
if is_mac:
    app = BUNDLE(
        collection,
        name="Neo-Tracker.app",
        icon=str(icon),
        bundle_identifier="org.ics.neo-tracker",
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
        },
    )
