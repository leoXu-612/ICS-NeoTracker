from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from neo_tracker.core import TrackerResult
from neo_tracker.ui.confidence_plot import ConfidencePlot


def tracking_results(count: int) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x_px": float(index)},
            filtered_state={"x_px": float(index)},
            confidence=(index % 101) / 100.0,
            status="manual" if index % 997 == 0 else "ok",
        )
        for index in range(count)
    ]


class ConfidencePlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dense_timeline_is_bounded_by_display_pixels_and_keeps_selection(self) -> None:
        plot = ConfidencePlot()
        self.addCleanup(plot.close)
        plot.resize(800, 104)
        plot.set_results(tracking_results(100_000), selected_frame=54_321)

        image = plot.grab()

        self.assertFalse(image.isNull())
        self.assertEqual(plot.point_count, 100_000)
        self.assertGreater(plot.display_point_count, 0)
        self.assertLessEqual(plot.display_point_count, plot.width() * 5 + 2)
        self.assertIn(54_321, plot._display_index_cache)
        self.assertIn("100,000 data points", plot.accessibleDescription())
        self.assertIn("per-pixel minimum/maximum", plot.toolTip())
        self.assertIsNotNone(plot._last_plot_rect)
        self.assertEqual(plot._frame_at_x(plot._last_plot_rect.left()), 0)
        self.assertEqual(plot._frame_at_x(plot._last_plot_rect.right()), 99_999)

    def test_small_timeline_keeps_every_point(self) -> None:
        plot = ConfidencePlot()
        self.addCleanup(plot.close)
        plot.resize(500, 104)
        plot.set_results(tracking_results(5), selected_frame=2)

        plot.grab()

        self.assertEqual(plot.point_count, 5)
        self.assertEqual(plot.display_point_count, 5)

    def test_deferred_confidence_load_updates_compatibility_values_before_render(self) -> None:
        plot = ConfidencePlot()
        self.addCleanup(plot.close)
        plot.set_results(tracking_results(5), selected_frame=2, render=False)

        self.assertEqual(len(plot._values), 5)
        self.assertEqual(plot.point_count, 0)

        plot.show_confidence(2)

        self.assertEqual(plot.point_count, 5)
        self.assertEqual(plot._frame_targets, [0, 1, 2, 3, 4])
        self.assertEqual(plot._statuses[0], "manual")

    def test_series_filter_keeps_frame_and_status_alignment_without_tuple_staging(self) -> None:
        plot = ConfidencePlot()
        self.addCleanup(plot.close)
        plot.set_series(
            "Filtered",
            [0.0, 1.0, 2.0],
            [0.2, float("nan"), 0.8],
            frame_targets=[10, 11, 12],
            statuses=["ok", "lost", "manual"],
            selected_frame=12,
        )

        self.assertEqual(plot._x_values, [0.0, 2.0])
        self.assertEqual(plot._y_values, [0.2, 0.8])
        self.assertEqual(plot._frame_targets, [10, 12])
        self.assertEqual(plot._statuses, ["ok", "manual"])


if __name__ == "__main__":
    unittest.main()
