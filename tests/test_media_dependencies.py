from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_SOURCE = ROOT / "neo_tracker"
PYPROJECT = ROOT / "pyproject.toml"

_IMPORT_AV = re.compile(r"^\s*(?:import av\b|from av\b)", re.MULTILINE)
_PACKAGE_SPEC = re.compile(r"""["']av(?=["']|[>=<~!])""")


class MediaDependencyGuardTests(unittest.TestCase):
    """Guard the invariant that the GUI process never loads PyAV alongside OpenCV.

    OpenCV bundles FFmpeg 61 while PyAV bundles FFmpeg 62; importing both in one
    interpreter makes macOS report duplicate AVFFrameReceiver/AVFAudioReceiver
    classes in two copies of libavdevice, which may cause casting failures and
    mysterious crashes. The product only uses OpenCV, so PyAV must stay out of
    both the declared dependency set and the import graph.
    """

    def test_product_source_never_imports_pyav(self) -> None:
        offenders = [
            str(path)
            for path in sorted(PRODUCT_SOURCE.rglob("*.py"))
            if _IMPORT_AV.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "neo_tracker must not import PyAV: "
            "importing both cv2 and av in one interpreter duplicates macOS "
            "libavdevice classes (FFmpeg 61 vs 62) and risks native crashes. "
            "Offending files: {0}".format(", ".join(offenders)),
        )

    def test_media_extra_does_not_declare_pyav(self) -> None:
        text = PYPROJECT.read_text(encoding="utf-8")
        media_spec = re.search(r"^media\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(media_spec, "pyproject.toml must declare a media extra")
        self.assertIsNotNone(media_spec)
        self.assertIsNone(
            _PACKAGE_SPEC.search(media_spec.group(1)),
            "the media extra must not declare the PyAV package; "
            "opencv-python is the only media backend in use",
        )

    def test_importing_cv2_does_not_transitively_load_pyav(self) -> None:
        probe = (
            "import sys\n"
            "import cv2\n"
            "assert 'av' not in sys.modules, (\n"
            "    'importing cv2 loaded PyAV into the interpreter; '\n"
            "    'the GUI process must not hold two FFmpeg stacks'\n"
            ")\n"
            "print('cv2', cv2.__version__, 'ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            "cv2 import probe failed:\nstdout:\n{0}\nstderr:\n{1}".format(
                result.stdout, result.stderr
            ),
        )
        self.assertIn("ok", result.stdout)
