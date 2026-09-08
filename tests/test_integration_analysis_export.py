from __future__ import annotations

import csv
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from neo_tracker.kinematics import SampleSeries
from neo_tracker.kinematics.runtime import KinematicsCancelled
from neo_tracker.ui.analysis_workspace_controller import FitDraft
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.integration_kinematics_support import close_window, pump_until
from tests.test_application_kinematics_controller import make_series


class AnalysisExportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_real_fit_exports_csv_safe_npz_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            export_directory = Path(directory)
            window = NeoTrackerWindow(
                physics_export_directory_picker=lambda _parent: str(export_directory)
            )
            self.addCleanup(close_window, window)
            times = np.linspace(0.0, 2.0, 41)
            source = SampleSeries(
                series_id="filtered:x",
                name=" =SUM(1,2)",
                frame_indices=np.arange(len(times), dtype=np.int64),
                time_s=times,
                values=3.5 * times - 2.0,
                valid_mask=np.ones(len(times), dtype=bool),
                unit="m",
                source_kind="filtered_state",
                source_revision="sha256:export-integration",
            )
            self.assertTrue(window.set_physics_series((source,)))
            window._run_physics_fit(
                FitDraft(source.series_id, "linear", float(times[0]), float(times[-1]))
            )
            pump_until(
                lambda: not window.analysis_workspace_controller.busy
                and window.analysis_workspace_controller.state.status == "complete"
            )

            window._export_physics_analysis()
            pump_until(lambda: not window._kinematics_workspace_coordinator.busy)
            csv_path, npz_path, markdown_path = sorted(
                export_directory.iterdir(), key=lambda path: path.suffix
            )
            paths_by_suffix = {
                path.suffix: path for path in (csv_path, npz_path, markdown_path)
            }

            self.assertEqual(set(paths_by_suffix), {".csv", ".npz", ".md"})
            with paths_by_suffix[".csv"].open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), len(source))
            self.assertTrue(rows[0]["series_name"].startswith("'"))
            self.assertAlmostEqual(float(rows[-1]["fit_prediction"]), 5.0, places=10)

            with np.load(paths_by_suffix[".npz"], allow_pickle=False) as archive:
                self.assertIn("fit_predicted", archive.files)
                self.assertIn("fit_residuals", archive.files)
                self.assertTrue(all(not archive[name].dtype.hasobject for name in archive.files))
                np.testing.assert_allclose(archive["fit_residuals"], 0.0, atol=1e-11)

            markdown = paths_by_suffix[".md"].read_text(encoding="utf-8")
            self.assertIn("## Fit", markdown)
            self.assertIn("slope", markdown)
            self.assertIn("R²", markdown)
            self.assertEqual(
                {path.name for path in export_directory.glob(".*.tmp")},
                set(),
            )

    def test_stale_fit_falls_back_to_current_series_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = NeoTrackerWindow(
                physics_export_directory_picker=lambda _parent: directory
            )
            self.addCleanup(close_window, window)
            times = np.linspace(0.0, 1.0, 11)
            source = SampleSeries(
                series_id="state:x",
                name="Raw x",
                frame_indices=np.arange(11, dtype=np.int64),
                time_s=times,
                values=times,
                valid_mask=np.ones(11, dtype=bool),
                unit="m",
                source_kind="state",
                source_revision="sha256:first",
            )
            window.set_physics_series((source,))
            window._run_physics_fit(FitDraft(source.series_id, "linear", 0.0, 1.0))
            pump_until(lambda: not window.analysis_workspace_controller.busy)

            replacement = SampleSeries(
                series_id=source.series_id,
                name=source.name,
                frame_indices=source.frame_indices,
                time_s=source.time_s,
                values=source.values + 10.0,
                valid_mask=source.valid_mask,
                unit=source.unit,
                source_kind=source.source_kind,
                source_revision="sha256:second",
            )
            window.set_physics_series((replacement,))

            self.assertTrue(window.export_physics_analysis_button.isEnabled())
            window.export_physics_analysis_button.click()
            pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

            paths = {path.suffix: path for path in Path(directory).iterdir()}
            self.assertEqual(set(paths), {".csv", ".npz", ".md"})
            with paths[".csv"].open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertAlmostEqual(float(rows[0]["value"]), 10.0)
            self.assertEqual(rows[0]["fit_prediction"], "")
            self.assertEqual(rows[0]["residual"], "")
            with np.load(paths[".npz"], allow_pickle=False) as archive:
                self.assertNotIn("fit_predicted", archive.files)
                np.testing.assert_allclose(archive["values"], replacement.values)
            markdown = paths[".md"].read_text(encoding="utf-8")
            self.assertNotIn("## Fit", markdown)
            self.assertIn(replacement.source_revision, markdown)

    def test_existing_export_files_require_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = make_series()
            targets = tuple(
                Path(directory) / f"Filtered-x.{suffix}"
                for suffix in ("csv", "npz", "md")
            )
            for target in targets:
                target.write_bytes(b"existing-evidence")
            window = NeoTrackerWindow(
                physics_export_directory_picker=lambda _parent: directory
            )
            self.addCleanup(close_window, window)
            window.set_physics_series((source,))

            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as confirm:
                window._export_physics_analysis()
                pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

            confirm.assert_called_once()
            self.assertEqual(
                [target.read_bytes() for target in targets],
                [b"existing-evidence"] * 3,
            )
            self.assertIn("kept", window.statusBar().currentMessage().lower())

            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ) as confirm_replace:
                window._export_physics_analysis()
                pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

            confirm_replace.assert_called_once()
            self.assertTrue(
                all(target.read_bytes() != b"existing-evidence" for target in targets)
            )

    def test_dangling_export_symlink_requires_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "Filtered-x.csv"
            target.symlink_to("missing-original.csv")
            window = NeoTrackerWindow(physics_export_directory_picker=lambda _parent: directory)
            self.addCleanup(close_window, window)
            window.set_physics_series((make_series(),))

            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as confirm:
                window._export_physics_analysis()
                pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

            confirm.assert_called_once()
            self.assertTrue(target.is_symlink())
            self.assertEqual(os.readlink(target), "missing-original.csv")
            self.assertEqual(set(root.iterdir()), {target})
            self.assertIn("kept", window.statusBar().currentMessage().lower())

    def test_dialog_context_changes_cancel_export_before_any_file_write(self) -> None:
        for boundary in ("picker", "overwrite"):
            for change in ("source", "task", "generation"):
                with (
                    self.subTest(boundary=boundary, change=change),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    source = make_series()
                    targets = tuple(
                        Path(directory) / f"Filtered-x.{suffix}"
                        for suffix in ("csv", "npz", "md")
                    )
                    if boundary == "overwrite":
                        for target in targets:
                            target.write_bytes(b"existing-evidence")

                    def change_context(parent: NeoTrackerWindow) -> str:
                        if change == "source":
                            parent.set_physics_series((replace(
                                source,
                                source_revision="sha256:changed-during-dialog",
                                values=source.values + 10.0,
                            ),))
                        elif change == "task":
                            parent.scratch_task = parent._new_task(None, parent.default_pipeline_key)
                            parent.current_task = parent.scratch_task
                            parent.set_physics_series((source,))
                        else:
                            parent.current_task.mark_results_changed()
                        return directory

                    window = NeoTrackerWindow(physics_export_directory_picker=(
                        change_context if boundary == "picker" else lambda _parent: directory
                    ))
                    self.addCleanup(close_window, window)
                    window.set_physics_series((source,))

                    def confirm_replace(*_args: object) -> QMessageBox.StandardButton:
                        change_context(window)
                        return QMessageBox.StandardButton.Yes

                    with (
                        patch("PySide6.QtWidgets.QMessageBox.question", side_effect=confirm_replace),
                        patch.object(
                            window._kinematics_workspace_coordinator,
                            "start",
                            wraps=window._kinematics_workspace_coordinator.start,
                        ) as start,
                    ):
                        window._export_physics_analysis()
                        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

                    start.assert_not_called()
                    self.assertIn("changed", window.statusBar().currentMessage().lower())
                    if boundary == "overwrite":
                        self.assertEqual(
                            [path.read_bytes() for path in targets], [b"existing-evidence"] * 3
                        )
                        self.assertEqual(set(Path(directory).iterdir()), set(targets))
                    else:
                        self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_or_canceled_export_keeps_all_previous_files(self) -> None:
        for terminal in ("failed", "canceled"):
            for existing in (False, True):
                with (
                    self.subTest(terminal=terminal, existing=existing),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    window = NeoTrackerWindow(physics_export_directory_picker=lambda _parent: directory)
                    self.addCleanup(close_window, window)
                    window.set_physics_series((make_series(),))
                    targets = tuple(Path(directory) / f"Filtered-x.{suffix}" for suffix in ("csv", "npz", "md"))
                    if existing:
                        for path in targets:
                            path.write_bytes(b"previous-evidence")
                    outcomes: list[str] = []
                    coordinator = window._kinematics_workspace_coordinator
                    coordinator.failed.connect(lambda *_args: outcomes.append("failed"))
                    coordinator.canceled.connect(lambda *_args: outcomes.append("canceled"))
                    coordinator.output_ready.connect(lambda *_args: outcomes.append("complete"))

                    def interrupt_npz(*_args: object, **_kwargs: object) -> None:
                        if terminal == "canceled":
                            coordinator._worker.request_cancel()
                            raise KinematicsCancelled("canceled while preparing NPZ")
                        raise OSError("disk full while preparing NPZ")

                    with (
                        patch("neo_tracker.kinematics.export.np.savez_compressed", side_effect=interrupt_npz),
                        patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
                    ):
                        window._export_physics_analysis()
                        pump_until(lambda: not coordinator.busy)

                    self.assertEqual(outcomes, [terminal])
                    if existing:
                        self.assertEqual([path.read_bytes() for path in targets], [b"previous-evidence"] * 3)
                        self.assertEqual(set(Path(directory).iterdir()), set(targets))
                    else:
                        self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancel_during_publication_reports_the_committed_bundle(self) -> None:
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            window = NeoTrackerWindow(physics_export_directory_picker=lambda _parent: str(root))
            self.addCleanup(close_window, window)
            source = make_series()
            window.set_physics_series((source,))
            coordinator = window._kinematics_workspace_coordinator
            published, release = Event(), Event()
            outcomes: list[str] = []
            coordinator.output_ready.connect(lambda *_args: outcomes.append("complete"))
            coordinator.canceled.connect(lambda *_args: outcomes.append("canceled"))
            coordinator.failed.connect(lambda *_args: outcomes.append("failed"))

            def pause_after_first_publication(staged: object, target: object) -> None:
                real_replace(staged, target)
                if Path(target) == root / "Filtered-x.csv":
                    published.set()
                    if not release.wait(timeout=10.0):
                        raise TimeoutError("test did not release publication")

            with patch("neo_tracker.kinematics.export.os.replace", side_effect=pause_after_first_publication):
                window._export_physics_analysis()
                try:
                    pump_until(published.is_set)
                    job = coordinator.job
                    self.assertTrue(coordinator.cancel())
                finally:
                    release.set()
                pump_until(lambda: not coordinator.busy)

            self.assertEqual(outcomes, ["complete"])
            self.assertIsNotNone(job)
            self.assertTrue(job.completed)
            self.assertFalse(job.cancelled)
            self.assertIn("Exported physics analysis", window.statusBar().currentMessage())
            self.assertEqual({path.suffix for path in root.iterdir()}, {".csv", ".npz", ".md"})
            with np.load(root / "Filtered-x.npz", allow_pickle=False) as archive:
                np.testing.assert_array_equal(archive["values"], source.values)

    def test_cancel_cannot_hide_failed_rollback_or_delete_recovery_backups(self) -> None:
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            window = NeoTrackerWindow(physics_export_directory_picker=lambda _parent: str(root))
            self.addCleanup(close_window, window)
            window.set_physics_series((make_series(),))
            targets = {suffix: root / f"Filtered-x.{suffix}" for suffix in ("csv", "npz", "md")}
            for path in targets.values():
                path.write_bytes(b"previous-evidence")
            coordinator = window._kinematics_workspace_coordinator
            failing, release = Event(), Event()
            failures: list[str] = []
            outcomes: list[str] = []
            coordinator.failed.connect(lambda _job, message: (outcomes.append("failed"), failures.append(message)))
            coordinator.output_ready.connect(lambda *_args: outcomes.append("complete"))
            coordinator.canceled.connect(lambda *_args: outcomes.append("canceled"))

            def deny_publication_and_rollback(staged: object, target: object) -> None:
                if Path(target) == targets["npz"] and Path(staged).name == "new":
                    failing.set()
                    if not release.wait(timeout=10.0):
                        raise TimeoutError("test did not release publication")
                    raise PermissionError("publication denied")
                if Path(target) == targets["csv"] and Path(staged).name == "previous":
                    raise PermissionError("rollback denied")
                real_replace(staged, target)

            with (
                patch("neo_tracker.kinematics.export.os.replace", side_effect=deny_publication_and_rollback),
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            ):
                window._export_physics_analysis()
                try:
                    pump_until(failing.is_set)
                    self.assertTrue(coordinator.cancel())
                finally:
                    release.set()
                pump_until(lambda: not coordinator.busy)

            self.assertEqual(outcomes, ["failed"])
            self.assertIn("rollback incomplete", failures[0])
            self.assertIn(str(targets["csv"]), failures[0])
            backups = list(root.glob(".neo-tracker-export-*/previous"))
            self.assertEqual(len(backups), 3)
            self.assertTrue(all(path.read_bytes() == b"previous-evidence" for path in backups))
            self.assertTrue(all(str(path) in failures[0] for path in backups))
            self.assertIn("rollback incomplete", window.statusBar().currentMessage())
            self.assertEqual(targets["npz"].read_bytes(), b"previous-evidence")
            self.assertEqual(targets["md"].read_bytes(), b"previous-evidence")


if __name__ == "__main__":
    unittest.main()
