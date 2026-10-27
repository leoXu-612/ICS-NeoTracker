"""Archive the already smoke-tested native build and record its provenance."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib


root = Path(__file__).resolve().parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
system = "macOS" if sys.platform == "darwin" else "Windows"
stem = f"Neo-Tracker-{version}-{system}-{architecture}"
out = root / "dist/release"
out.mkdir(parents=True, exist_ok=True)
smoke = json.loads((root / "build/frozen-smoke.json").read_text())
if smoke.get("status") != "passed" or not smoke.get("frozen"):
    raise RuntimeError("The native executable must pass its frozen smoke test before archiving")
if sys.platform == "darwin":
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(root / "dist/Neo-Tracker.app")], check=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(root / "dist/Neo-Tracker.app"), str(out / f"{stem}.zip")], check=True)
    image_root = root / "build/dmg"
    if image_root.exists():
        shutil.rmtree(image_root)
    image_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ditto", str(root / "dist/Neo-Tracker.app"), str(image_root / "Neo-Tracker.app")], check=True)
    (image_root / "Applications").symlink_to("/Applications", target_is_directory=True)
    subprocess.run(["hdiutil", "create", "-volname", "Neo-Tracker", "-srcfolder", str(image_root), "-ov", "-format", "UDZO", str(out / f"{stem}.dmg")], check=True)
else:
    shutil.make_archive(str(out / stem), "zip", root / "dist", "Neo-Tracker")
metadata = {
    "version": version,
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "platform": platform.platform(),
    "python": platform.python_version(),
    "dependencies": {name: importlib.metadata.version(name) for name in ["numpy", "PySide6", "scipy", "opencv-python", "pyinstaller", "pyinstaller-hooks-contrib"]},
    "signing": "ad-hoc; not notarized" if sys.platform == "darwin" else "not Authenticode signed",
    "smoke_test": smoke,
}
(out / f"{stem}-build.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
checksums = []
checksum_path = out / f"{stem}-SHA256SUMS.txt"
for path in sorted(out.glob(f"{stem}*")):
    if path == checksum_path:
        continue
    with path.open("rb") as stream:
        checksums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
checksum_path.write_text("".join(checksums), encoding="utf-8")
