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

    def test_preview_and_playback_lifecycle_are_application_owned(self) -> None:
        root = Path(__file__).parents[1]
        source_path = root / "neo_tracker" / "ui" / "main_window.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        window_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NeoTrackerWindow"
        )
        constructor = next(
            node
            for node in window_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned_attributes = {
            target.attr
            for node in ast.walk(constructor)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        forbidden_window_state = {
            "play_timer",
            "playback_clock",
            "_preview_decode_thread",
            "_preview_decode_worker",
            "_preview_decode_job",
            "_pending_preview_decode",
            "_preview_decode_cache",
            "_preview_decoder_session",
        }
        constructed_names = {
            node.func.id
            for node in ast.walk(window_class)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertTrue(forbidden_window_state.isdisjoint(assigned_attributes))
        self.assertNotIn("PreviewDecodeWorker", constructed_names)
        self.assertNotIn("PreviewDecoderSession", constructed_names)
        self.assertIn("PreviewCoordinator", constructed_names)
        self.assertIn("PlaybackCoordinator", constructed_names)

        for coordinator_name in ("preview_coordinator.py", "playback_coordinator.py"):
            source = (root / "neo_tracker" / "application" / coordinator_name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("PySide6.QtWidgets", source)

    def test_media_import_lifecycle_is_application_owned(self) -> None:
        root = Path(__file__).parents[1]
        source_path = root / "neo_tracker" / "ui" / "main_window.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        window_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NeoTrackerWindow"
        )
        constructor = next(
            node
            for node in window_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned_attributes = {
            target.attr
            for node in ast.walk(constructor)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        constructed_names = {
            node.func.id
            for node in ast.walk(window_class)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertTrue(
            {"_media_probe_thread", "_media_probe_worker", "_media_probe_job"}.isdisjoint(
                assigned_attributes
            )
        )
        self.assertNotIn("MediaProbeWorker", constructed_names)
        self.assertIn("MediaImportCoordinator", constructed_names)
        coordinator_source = (
            root / "neo_tracker" / "application" / "media_import_coordinator.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("PySide6.QtWidgets", coordinator_source)

    def test_project_io_lifecycle_is_application_owned(self) -> None:
        root = Path(__file__).parents[1]
        source_path = root / "neo_tracker" / "ui" / "main_window.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        window_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NeoTrackerWindow"
        )
        constructor = next(
            node
            for node in window_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned_attributes = {
            target.attr
            for node in ast.walk(constructor)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        constructed_names = {
            node.func.id
            for node in ast.walk(window_class)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        forbidden_state = {
            "_project_open_thread",
            "_project_open_worker",
            "_project_open_job",
            "_project_save_thread",
            "_project_save_worker",
            "_project_save_job",
        }

        self.assertTrue(forbidden_state.isdisjoint(assigned_attributes))
        self.assertNotIn("ProjectOpenWorker", constructed_names)
        self.assertNotIn("ProjectSaveWorker", constructed_names)
        self.assertIn("ProjectIOCoordinator", constructed_names)
        coordinator_source = (
            root / "neo_tracker" / "application" / "project_io_coordinator.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("PySide6.QtWidgets", coordinator_source)

    def test_tracking_lifecycle_is_application_owned(self) -> None:
        root = Path(__file__).parents[1]
        source_path = root / "neo_tracker" / "ui" / "main_window.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        window_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NeoTrackerWindow"
        )
        constructor = next(
            node
            for node in window_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned_attributes = {
            target.attr
            for node in ast.walk(constructor)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        constructed_names = {
            node.func.id
            for node in ast.walk(window_class)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertTrue(
            {"_tracking_thread", "_tracking_worker", "_tracking_job"}.isdisjoint(
                assigned_attributes
            )
        )
        self.assertNotIn("TrackingWorker", constructed_names)
        self.assertIn("TrackingCoordinator", constructed_names)
        coordinator_source = (
            root / "neo_tracker" / "application" / "tracking_coordinator.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("PySide6.QtWidgets", coordinator_source)


if __name__ == "__main__":
    unittest.main()
