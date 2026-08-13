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
    def test_application_workers_retire_before_their_thread_exits(self) -> None:
        application = Path(__file__).parents[1] / "neo_tracker" / "application"
        for path in application.glob("*_coordinator.py"):
            source = path.read_text(encoding="utf-8")
            if "worker.moveToThread(thread)" not in source:
                continue
            with self.subTest(coordinator=path.name):
                self.assertNotIn("thread.finished.connect(worker.deleteLater)", source)
                self.assertNotIn("worker.completed.connect(worker.deleteLater)", source)
                self.assertNotIn("worker.failed.connect(worker.deleteLater)", source)
                self.assertNotIn("worker.canceled.connect(worker.deleteLater)", source)
                tree = ast.parse(source)
                handlers = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef)
                    and node.name.endswith("thread_finished")
                ]
                self.assertTrue(handlers)
                self.assertNotIn(".wait()", source)
                self.assertIn("bind_worker_retirement", source)
                self.assertIn("worker.deleteLater()", source)
                lines = source.splitlines()
                finished_handler_lines = [
                    index
                    for index, line in enumerate(lines)
                    if "thread.finished.connect(self." in line
                ]
                delete_thread_lines = [
                    index
                    for index, line in enumerate(lines)
                    if "thread.finished.connect(thread.deleteLater)" in line
                ]
                self.assertEqual(len(finished_handler_lines), len(delete_thread_lines))
                for handler_line, delete_line in zip(finished_handler_lines, delete_thread_lines):
                    self.assertLess(handler_line, delete_line)
                for terminal in ("completed", "failed", "canceled"):
                    delete_lines = [
                        index
                        for index, line in enumerate(lines)
                        if f"worker.{terminal}.connect(worker.deleteLater)" in line
                    ]
                    quit_lines = [
                        index
                        for index, line in enumerate(lines)
                        if f"worker.{terminal}.connect(thread.quit)" in line
                    ]
                    if not quit_lines:
                        continue
                    self.fail(f"{path.name}: {terminal} must not quit before worker destruction")

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

    def test_analysis_and_review_response_lifecycles_are_application_owned(self) -> None:
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
            "_analysis_thread",
            "_analysis_worker",
            "_analysis_job",
            "_review_response_thread",
            "_review_response_worker",
            "_review_response_job",
            "_pending_review_response",
        }

        self.assertTrue(forbidden_state.isdisjoint(assigned_attributes))
        self.assertNotIn("AnalysisWorker", constructed_names)
        self.assertNotIn("ReviewResponseWorker", constructed_names)
        self.assertIn("AnalysisCoordinator", constructed_names)
        self.assertIn("ReviewResponseCoordinator", constructed_names)
        for name in ("analysis_coordinator.py", "review_response_coordinator.py"):
            coordinator_source = (
                root / "neo_tracker" / "application" / name
            ).read_text(encoding="utf-8")
            self.assertNotIn("PySide6.QtWidgets", coordinator_source)

    def test_window_is_a_shell_with_registry_view_state_and_no_worker_construction(self) -> None:
        root = Path(__file__).parents[1]
        source_path = root / "neo_tracker" / "ui" / "main_window.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        window_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NeoTrackerWindow"
        )
        constructed_names = {
            node.func.id
            for node in ast.walk(window_class)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        worker_names = {
            "AnalysisWorker",
            "MediaProbeWorker",
            "PreviewDecodeWorker",
            "ProjectOpenWorker",
            "ProjectSaveWorker",
            "ReviewResponseWorker",
            "TrackingWorker",
        }

        self.assertIn("ApplicationShell", constructed_names)
        self.assertTrue(worker_names.isdisjoint(constructed_names))
        self.assertNotIn("QThread(", source)
        for direct_binding in (
            "run_tracking_button.clicked.connect(self._run_tracking)",
            "play_button.clicked.connect(self._toggle_playback)",
            "open_project_button.clicked.connect(self._open_project)",
            "save_project_button.clicked.connect(self._save_project)",
            "run_analysis_button.clicked.connect(self._run_analysis)",
        ):
            self.assertNotIn(direct_binding, source)
        self.assertLess(len(source.splitlines()), 7000)
        for relative_path in (
            "neo_tracker/ui/action_registry.py",
            "neo_tracker/ui/view_state.py",
            "neo_tracker/ui/shell/main_shell.py",
            "neo_tracker/ui/shell/bindings.py",
            "neo_tracker/ui/shell/review_editing_mixin.py",
        ):
            self.assertTrue((root / relative_path).is_file(), relative_path)


if __name__ == "__main__":
    unittest.main()
