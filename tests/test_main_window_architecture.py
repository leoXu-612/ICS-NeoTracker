from __future__ import annotations

import ast
from pathlib import Path
import unittest


_JOB_NAMES = {
    "TrackingJob",
    "AnalysisJob",
    "MediaProbeJob",
    "ProjectOpenJob",
    "ProjectSaveJob",
    "ReviewResponseJob",
    "PreviewDecodeJob",
}


class MainWindowArchitectureTests(unittest.TestCase):
    def test_background_job_dataclasses_live_in_application_layer(self) -> None:
        source_path = Path(__file__).parents[1] / "neo_tracker" / "ui" / "main_window.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        locally_defined = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }

        self.assertTrue(_JOB_NAMES.isdisjoint(locally_defined))

        from neo_tracker.application import job_state
        from neo_tracker.ui import main_window

        for name in _JOB_NAMES:
            with self.subTest(job=name):
                self.assertIs(getattr(main_window, name), getattr(job_state, name))


if __name__ == "__main__":
    unittest.main()
