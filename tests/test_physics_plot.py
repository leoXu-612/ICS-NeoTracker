from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.kinematics import SampleSeries
from neo_tracker.ui.physics_plot import PhysicsPlot, decimate_series


def make_series(count: int = 100_000) -> SampleSeries:
    time_s = np.cumsum(np.where(np.arange(count) % 3 == 0, 0.006, 0.004)).astype(np.float64)
    values = np.sin(time_s)
    values[count // 2] = 99.0
    valid = np.ones(count, dtype=bool)
    valid[count // 3 : count // 3 + 11] = False
    values[~valid] = np.nan
    return SampleSeries(
        series_id="raw:x",
        name="Raw x",
        frame_indices=np.arange(count, dtype=np.int64),
        time_s=time_s,
        values=values,
        valid_mask=valid,
        unit="m",
        source_kind="state",
        source_revision="results:plot",
    )


class PlotDecimationTests(unittest.TestCase):
    def test_decimation_is_width_bounded_and_preserves_semantic_points(self) -> None:
        source = make_series()
        selected = 40_123
        range_indices = (20_001, 70_007)

        prepared = decimate_series(
            source,
            width_px=640,
            selected_sample_index=selected,
            range_sample_indices=range_indices,
        )

        semantic = set(int(value) for value in prepared.sample_indices if value >= 0)
        self.assertLessEqual(len(prepared.sample_indices), 640 * 5 + 8)
        self.assertIn(0, semantic)
        self.assertIn(len(source) - 1, semantic)
        self.assertIn(len(source) // 2, semantic)  # narrow spike / local maximum
        self.assertIn(selected, semantic)
        self.assertTrue(set(range_indices).issubset(semantic))
        self.assertIn(-1, prepared.sample_indices)  # explicit invalid gap
        first_position = int(np.flatnonzero(prepared.sample_indices == 0)[0])
        self.assertEqual(prepared.time_s[first_position], source.time_s[0])

    def test_decimation_uses_stored_vfr_time_not_frame_over_fps(self) -> None:
        source = make_series(12)
        prepared = decimate_series(source, width_px=200)
        valid_indices = prepared.sample_indices >= 0
        np.testing.assert_array_equal(
            prepared.time_s[valid_indices],
            source.time_s[prepared.sample_indices[valid_indices]],
        )


class PhysicsPlotWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_keyboard_moves_selection_and_exports_image(self) -> None:
        plot = PhysicsPlot()
        plot.resize(640, 280)
        plot.set_series((make_series(200),))
        plot.set_selected_sample(10)
        activated: list[tuple[str, int, float]] = []
        plot.sampleActivated.connect(lambda series_id, index, time_s: activated.append((series_id, index, time_s)))
        plot.show()
        plot.setFocus()

        QTest.keyClick(plot, Qt.Key.Key_Right)

        self.assertEqual(activated[-1][:2], ("raw:x", 11))
        self.assertIn("true time", plot.accessibleDescription().lower())
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plot.png"
            self.assertTrue(plot.export_image(path))
            self.assertGreater(path.stat().st_size, 0)
        plot.close()


if __name__ == "__main__":
    unittest.main()
