from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.kinematics import SampleSeries
from neo_tracker.ui.analysis_workspace_controller import FitDraft
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.integration_kinematics_support import close_window, pump_until


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

    def test_stale_fit_is_cleared_before_export_request(self) -> None:
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
                values=source.values,
                valid_mask=source.valid_mask,
                unit=source.unit,
                source_kind=source.source_kind,
                source_revision="sha256:second",
            )
            window.set_physics_series((replacement,))

            self.assertFalse(window.analysis_workspace_controller.request_export())
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
