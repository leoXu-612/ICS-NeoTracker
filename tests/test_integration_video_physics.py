from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.media import has_media_backend
from neo_tracker.kinematics import FitStatus
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore
from tests.integration_kinematics_support import pump_until
from tests.test_ui_main_window import close_window_safely


class VideoPhysicsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    @unittest.skipUnless(has_media_backend(), "OpenCV is required for encoded video integration")
    def test_encoded_video_tracking_fit_export_and_reopen_preserve_sample_identity(self) -> None:
        from benchmarks.benchmark_preview_media import _write_video

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "synthetic-marker.mp4"
            exports = root / "exports"
            exports.mkdir()
            # The existing fixture moves x = 100 + 7*frame without wrapping here.
            _write_video(video, 640, 360, 60, 30.0)
            window = NeoTrackerWindow(
                physics_layout_store=PhysicsWorkspaceStateStore(),
                physics_export_directory_picker=lambda _parent: str(exports),
            )
            try:
                self.assertTrue(window._start_media_probe([str(video)]))
                pump_until(lambda: window._background_tasks.idle, timeout_s=20.0)
                self.assertEqual(len(window.tasks), 1)
                self.assertTrue(window.current_task.media_info.available)
                self.assertEqual(window.current_task.media_info.frame_count, 60)

                window._run_tracking()
                pump_until(lambda: window._background_tasks.idle, timeout_s=30.0)
                results = window.current_task.pipeline.results
                self.assertEqual(len(results), 60)
                self.assertEqual(window.results_model.rowCount(), 60)
                source = window._physics_series_by_id["state:x_px"]
                np.testing.assert_array_equal(source.frame_indices, np.arange(60))
                np.testing.assert_array_equal(source.time_s, [item.time_s for item in results])
                np.testing.assert_allclose(source.time_s, np.arange(60) / 30, atol=1e-10)
                self.assertTrue(source.valid_mask.all())
                # Lossy video centroids need not equal ideal integer pixel centers.
                np.testing.assert_allclose(source.values, 100 + 7 * np.arange(60), atol=2.0)
                self.assertEqual(source.unit, "px")

                self.assertTrue(window.analysis_workspace_controller.select_series(source.series_id))
                window._run_physics_fit(window.fit_panel.draft())
                pump_until(lambda: window._background_tasks.idle, timeout_s=10.0)
                result = window.analysis_workspace_controller.state.fit_result
                self.assertIsNotNone(result)
                self.assertIs(result.status, FitStatus.OK)
                self.assertEqual(result.sample_count, 60)
                slope_index = result.parameter_names.index("slope")
                self.assertAlmostEqual(result.parameters[slope_index], 210.0, delta=1.0)
                self.assertEqual(result.parameter_units[slope_index], "px/s")

                window._export_physics_analysis()
                pump_until(lambda: window._background_tasks.idle, timeout_s=10.0)
                paths = {path.suffix: path for path in exports.iterdir()}
                self.assertEqual(set(paths), {".csv", ".npz", ".md"})
                with paths[".csv"].open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 60)
                np.testing.assert_array_equal([int(row["frame"]) for row in rows], source.frame_indices)
                np.testing.assert_array_equal([float(row["time_s"]) for row in rows], source.time_s)
                np.testing.assert_array_equal([float(row["value"]) for row in rows], source.values)
                np.testing.assert_array_equal([float(row["fit_prediction"]) for row in rows], result.predicted)
                with np.load(paths[".npz"], allow_pickle=False) as archive:
                    np.testing.assert_array_equal(archive["frame_indices"], source.frame_indices)
                    np.testing.assert_array_equal(archive["time_s"], source.time_s)
                    np.testing.assert_array_equal(archive["values"], source.values)
                    np.testing.assert_array_equal(archive["fit_predicted"], result.predicted)
                    np.testing.assert_array_equal(archive["fit_residuals"], result.residuals)
                    self.assertTrue(all(not archive[name].dtype.hasobject for name in archive.files))
                markdown = paths[".md"].read_text(encoding="utf-8")
                self.assertIn(source.source_revision, markdown)
                self.assertIn("px/s", markdown)
                self.assertIn("## Fit", markdown)
                self.assertIn("Exported physics analysis", window.statusBar().currentMessage())

                project_path = root / "synthetic-flow.ntproj"
                window.project_path = project_path
                self.assertTrue(window._save_project())
                pump_until(lambda: window._background_tasks.idle, timeout_s=10.0)
                self.assertTrue(project_path.is_file())
                self.assertFalse(window._project_dirty)
                restored = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
                try:
                    self.assertTrue(restored._start_project_open(project_path))
                    pump_until(
                        lambda: restored._background_tasks.idle
                        and restored.analysis_workspace_controller.state.fit_result is not None,
                        timeout_s=20.0,
                    )
                    self.assertEqual(restored.current_task.task_id, window.current_task.task_id)
                    self.assertEqual(restored._physics_series_by_id[source.series_id], source)
                    self.assertEqual(restored.analysis_workspace_controller.state.fit_result, result)
                    self.assertFalse(restored.fit_panel.is_dirty())
                    self.assertFalse(restored._project_dirty)
                finally:
                    close_window_safely(restored, timeout_s=15.0)
                self.assertTrue(restored._background_tasks.idle)
            finally:
                close_window_safely(window, timeout_s=15.0)
            self.assertTrue(window._background_tasks.idle)
            self.assertFalse(window.isVisible())


if __name__ == "__main__":
    unittest.main()
