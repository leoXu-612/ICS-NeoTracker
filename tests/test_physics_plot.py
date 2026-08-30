from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.kinematics import FitStatus, SampleSeries
from neo_tracker.ui.physics_plot import PhysicsPlot, decimate_series
from tests.test_application_kinematics_controller import fit_result
from neo_tracker.ui.analysis_workspace_controller import FitDraft


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
        source = make_series(200)
        plot.set_series((source,))
        plot.set_selected_sample(10)
        activated: list[tuple[str, int, float]] = []
        plot.sampleActivated.connect(lambda series_id, index, time_s: activated.append((series_id, index, time_s)))
        plot.show()
        plot.setFocus()

        QTest.keyClick(plot, Qt.Key.Key_Right)

        self.assertEqual(activated[-1][:2], ("raw:x", 11))
        self.assertIn("true time", plot.accessibleDescription().lower())
        self.assertIn(
            f"from {source.time_s[0]:.6g} to {source.time_s[-1]:.6g} seconds",
            plot.accessibleDescription(),
        )
        bounds = plot._data_bounds()
        assert bounds is not None
        self.assertIn(
            f"vertical values from {bounds[2]:.4g} to {bounds[3]:.4g} {source.unit}",
            plot.accessibleDescription(),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "plot.png"
            self.assertTrue(plot.export_image(path))
            self.assertGreater(path.stat().st_size, 0)
            image = QImage(str(path))
        plot_rect = plot._plot_rect()
        background = image.pixelColor(0, 0).rgba()
        self.assertTrue(
            any(
                image.pixelColor(x, y).rgba() != background
                for x in range(round(plot_rect.left()), round(plot_rect.left()) + 40)
                for y in range(round(plot_rect.bottom()) + 2, round(plot_rect.bottom()) + 14)
            )
        )
        self.assertTrue(
            any(
                image.pixelColor(x, y).rgba() != background
                for x in range(4, round(plot_rect.left()) - 4)
                for y in range(round(plot_rect.bottom()) - 14, round(plot_rect.bottom()))
            )
        )
        plot.close()

    def test_mouse_selection_is_limited_to_the_data_rectangle(self) -> None:
        plot = PhysicsPlot()
        plot.resize(640, 280)
        plot.set_series((make_series(20),))
        activated: list[int] = []
        plot.sampleActivated.connect(lambda _series_id, index, _time_s: activated.append(index))
        plot.show()

        QTest.mouseClick(plot, Qt.MouseButton.LeftButton, pos=QPoint(20, 8))

        self.assertIsNone(plot.selected_sample_index)
        self.assertEqual(activated, [])

        QTest.mouseClick(
            plot,
            Qt.MouseButton.LeftButton,
            pos=plot._plot_rect().center().toPoint(),
        )
        self.assertIsNotNone(plot.selected_sample_index)
        self.assertEqual(len(activated), 1)
        plot.close()

    def test_invalid_selected_sample_keeps_cursor_without_drawing_a_data_point(self) -> None:
        source = SampleSeries(
            series_id="raw:cursor-validity",
            name="Cursor validity",
            frame_indices=np.arange(3, dtype=np.int64),
            time_s=np.array([0.0, 1.0, 2.0]),
            values=np.array([0.0, 5.0, 10.0]),
            valid_mask=np.array([True, False, True]),
            unit="m",
            source_kind="state",
            source_revision="results:cursor-validity",
        )
        plot = PhysicsPlot()
        plot.resize(400, 220)
        plot.set_series((source,))
        bounds = plot._data_bounds()
        assert bounds is not None
        painter = Mock()

        plot.set_selected_sample(0)
        plot._paint_cursor(painter, plot._plot_rect(), bounds)
        painter.drawLine.assert_called_once()
        painter.drawEllipse.assert_called_once()

        painter.reset_mock()
        plot.set_selected_sample(1)
        plot._paint_cursor(painter, plot._plot_rect(), bounds)
        painter.drawLine.assert_called_once()
        painter.drawEllipse.assert_not_called()

    def test_duplicate_ids_and_non_ok_fit_never_create_ambiguous_layers(self) -> None:
        plot = PhysicsPlot()
        source = make_series(20)
        with self.assertRaisesRegex(ValueError, "unique"):
            plot.set_series((source, source))

        plot.set_series((source,))
        request = FitDraft(source.series_id, "linear", float(source.time_s[0]), float(source.time_s[-1])).to_request(source)
        unavailable = replace(
            fit_result(source, request),
            status=FitStatus.UNAVAILABLE,
            message="Fit unavailable",
        )
        plot.set_fit_result(source, unavailable)

        self.assertIsNone(plot._fit_series)

    def test_single_axis_rejects_mixed_or_unknown_unit_overlays(self) -> None:
        plot = PhysicsPlot()
        source = make_series(20)
        velocity = replace(source, series_id="derived:v", unit="m/s")
        with self.assertRaisesRegex(ValueError, "one known unit"):
            plot.set_series((source, velocity))

        unknown = replace(source, series_id="raw:unknown", unit="")
        other_unknown = replace(unknown, series_id="raw:other")
        with self.assertRaisesRegex(ValueError, "one known unit"):
            plot.set_series((unknown, other_unknown))

    def test_overlay_legend_names_layers_without_relying_on_color(self) -> None:
        plot = PhysicsPlot()
        plot.resize(640, 280)
        source = make_series(20)
        filtered = replace(source, series_id="filtered:x", name="Filtered x")
        plot.set_series((source, filtered))

        self.assertIn("1 Raw x", plot.accessibleDescription())
        self.assertIn("2 Filtered x", plot.accessibleDescription())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legend.png"
            self.assertTrue(plot.export_image(path))
            image = QImage(str(path))
        background = image.pixelColor(0, 0).rgba()
        self.assertTrue(
            any(
                image.pixelColor(x, y).rgba() != background
                for x in range(8, image.width() - 8)
                for y in range(2, 16)
            )
        )

    def test_isolated_valid_samples_between_gaps_render_as_points(self) -> None:
        source = SampleSeries(
            series_id="raw:isolated",
            name="Isolated samples",
            frame_indices=np.arange(5, dtype=np.int64),
            time_s=np.arange(5, dtype=np.float64),
            values=np.array([0.0, np.nan, 5.0, np.nan, 10.0]),
            valid_mask=np.array([True, False, True, False, True]),
            unit="m",
            source_kind="state",
            source_revision="results:isolated",
        )
        plot = PhysicsPlot()
        plot.resize(400, 200)
        plot.set_series((source,))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "isolated.png"
            self.assertTrue(plot.export_image(path))
            image = QImage(str(path))

        plot_rect = plot._plot_rect()
        bounds = plot._data_bounds()
        assert bounds is not None
        expected = (
            plot._x_for_time(2.0, plot_rect, bounds[0], bounds[1]),
            plot._y_for_value(5.0, plot_rect, bounds[2], bounds[3]),
        )

        def is_trace(x: int, y: int) -> bool:
            color = image.pixelColor(x, y)
            return color.red() < 90 and 80 < color.green() < 145 and 130 < color.blue() < 190

        self.assertTrue(
            any(
                is_trace(x, y)
                for x in range(round(expected[0]) - 3, round(expected[0]) + 4)
                for y in range(round(expected[1]) - 3, round(expected[1]) + 4)
            )
        )

    def test_retina_batched_polyline_keeps_invalid_gap_visually_open(self) -> None:
        class RetinaGapPlot(PhysicsPlot):
            def devicePixelRatioF(self) -> float:  # noqa: N802
                return 2.0

        source = SampleSeries(
            series_id="raw:gap",
            name="Gap",
            frame_indices=np.arange(5, dtype=np.int64),
            time_s=np.arange(5, dtype=np.float64),
            values=np.array([0.0, 0.0, np.nan, 10.0, 10.0]),
            valid_mask=np.array([True, True, False, True, True]),
            unit="m",
            source_kind="state",
            source_revision="results:retina-gap",
        )
        plot = RetinaGapPlot()
        plot.resize(400, 200)
        plot.set_series((source,))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retina-gap.png"
            self.assertTrue(plot.export_image(path))
            image = QImage(str(path))

        self.assertEqual((image.width(), image.height()), (800, 400))

        def is_trace(x: int, y: int) -> bool:
            color = image.pixelColor(x, y)
            return color.red() < 110 and 75 < color.green() < 155 and color.blue() > 125

        self.assertTrue(
            any(is_trace(x, y) for x in range(image.width()) for y in range(image.height()))
        )
        # Logical gap midpoint is (219, 92); scale to the exported 2x pixels.
        self.assertFalse(
            any(
                is_trace(x, y)
                for x in range(432, 445)
                for y in range(178, 191)
            )
        )


if __name__ == "__main__":
    unittest.main()
