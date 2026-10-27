from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from neo_tracker.project import NeoTrackerProject


class ReleaseEntryTests(unittest.TestCase):
    def test_application_entry_can_run_project_helper_without_opening_gui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project.ntproj"
            stage = Path(directory) / "stage.jsonl"
            stage.touch()
            NeoTrackerProject(name="Release entry").save(project)
            result = subprocess.run(
                [sys.executable, "-m", "neo_tracker", "--isolated-load", str(project), str(stage)],
                capture_output=True,
                timeout=8,
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)["status"], "ok")
            self.assertIn("Release entry", stage.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
