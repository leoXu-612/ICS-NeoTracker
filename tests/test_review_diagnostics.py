from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox

from neo_tracker.core import TrackerResult
from neo_tracker.ui.review_diagnostics import ReviewDiagnosticsPanel, prepare_review_diagnostics


def diagnostic_result(frame_index: int, theta_peak: int) -> TrackerResult:
    signal = np.zeros(24, dtype=float)
    signal[theta_peak] = 1.0
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 10.0,
        state={"x_px": float(frame_index * 4), "theta": theta_peak / 24.0 * 2.0 * np.pi},
        filtered_state={"x_px": float(frame_index * 4), "theta": theta_peak / 24.0 * 2.0 * np.pi},
        confidence=0.9 - frame_index * 0.1,
        status="ok",
        debug={
            "filter": {"velocity": {"v_x_px": float(3 + frame_index)}},
            "candidates": [{"selected": True, "motion_score": 0.9 - frame_index * 0.1}],
            "debug_layers": {"theta_signal": signal},
        },
    )


class ReviewDiagnosticsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        QCoreApplication.processEvents()

    def test_available_modes_and_selected_values_follow_result_context(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        results = [diagnostic_result(index, 4 + index) for index in range(3)]
        panel.set_results(results, {"x_px": "px", "theta": "rad"}, selected_frame=1)

        modes = [panel.mode_combo.itemData(index) for index in range(panel.mode_combo.count())]
        self.assertEqual(modes, ["confidence", "velocity", "motion_mismatch", "angular_response"])
        self.assertIn("Frame 1 · confidence 0.800", panel.status_label.text())

        self.assertTrue(panel.select_mode("velocity"))
        self.assertEqual(panel.series_combo.currentData(), "v_x_px")
        self.assertEqual(panel.series_combo.currentText(), "v_x_px (px/s)")
        self.assertEqual(
            panel.series_combo.sizeAdjustPolicy(),
            QComboBox.SizeAdjustPolicy.AdjustToContents,
        )
        self.assertIn("v_x_px 4 px/s", panel.status_label.text())
        self.assertEqual(panel.plot.kind, "velocity")

        self.assertTrue(panel.select_mode("motion_mismatch"))
        self.assertIn("mismatch 0.200", panel.status_label.text())
        self.assertNotIn("1 − motion score", panel.status_label.text())
        self.assertIn("1 − motion score", panel.status_label.toolTip())
        self.assertEqual(panel.plot.point_count, 3)

        self.assertTrue(panel.select_mode("angular_response"))
        self.assertEqual(panel.plot.kind, "angular_response")
        self.assertEqual(panel.plot.point_count, 24)
        self.assertIn("peak 75.0°", panel.status_label.text())
        self.assertIn("tracked 75.0°", panel.status_label.text())

    def test_worker_prepared_results_preserve_modes_selection_and_full_timeline(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        results = [diagnostic_result(index, 4 + index) for index in range(3)]

        panel.set_prepared_results(
            prepare_review_diagnostics(results),
            {"x_px": "px", "theta": "rad"},
            selected_frame=1,
        )

        self.assertEqual(panel.plot.point_count, 3)
        self.assertEqual(
            [panel.mode_combo.itemData(index) for index in range(panel.mode_combo.count())],
            ["confidence", "velocity", "motion_mismatch", "angular_response"],
        )
        self.assertIn("Frame 1 · confidence 0.800", panel.status_label.text())

    def test_timeline_click_and_keyboard_emit_frame_navigation(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        panel.resize(520, 130)
        panel.set_results([diagnostic_result(index, 4 + index) for index in range(3)], {}, selected_frame=1)
        panel.show()
        QCoreApplication.processEvents()

        activated: list[int] = []
        panel.frameActivated.connect(activated.append)
        QTest.mouseClick(
            panel.plot,
            Qt.MouseButton.LeftButton,
            pos=QPoint(panel.plot.width() - 15, panel.plot.height() // 2),
        )
        self.assertEqual(activated[-1], 2)

        panel.plot.setFocus()
        QTest.keyClick(panel.plot, Qt.Key.Key_Left)
        self.assertEqual(activated[-1], 0)

    def test_static_timeline_selection_reuses_series_but_angular_response_rebuilds(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        panel.set_results(
            [diagnostic_result(index, 4 + index) for index in range(3)],
            {"x_px": "px", "theta": "rad"},
            selected_frame=0,
        )
        self.assertTrue(panel.select_mode("velocity"))

        with patch.object(panel.plot, "set_series", wraps=panel.plot.set_series) as set_series:
            panel.set_selected_frame(2)
            set_series.assert_not_called()
        self.assertIn("Frame 2 · v_x_px 5 px/s", panel.status_label.text())

        self.assertTrue(panel.select_mode("angular_response"))
        with patch.object(panel.plot, "set_series", wraps=panel.plot.set_series) as set_series:
            panel.set_selected_frame(1)
            set_series.assert_called_once()
        self.assertIn("Frame 1", panel.status_label.text())

    def test_large_result_snapshot_and_static_series_cache_are_bounded(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        results = [diagnostic_result(index, index % 24) for index in range(1_000)]
        panel.set_results(results, {"x_px": "px", "theta": "rad"}, selected_frame=999)

        self.assertIn("1,000 tracking results", panel.accessibleDescription())
        results.append(diagnostic_result(1_000, 1))
        self.assertEqual(len(panel._results), 1_000)

        with patch.object(
            panel,
            "_build_velocity_series",
            wraps=panel._build_velocity_series,
        ) as build_velocity:
            self.assertTrue(panel.select_mode("velocity"))
            self.assertTrue(panel.select_mode("velocity"))
            build_velocity.assert_called_once_with("v_x_px")

        with patch.object(
            panel,
            "_build_motion_mismatch_series",
            wraps=panel._build_motion_mismatch_series,
        ) as build_mismatch:
            self.assertTrue(panel.select_mode("motion_mismatch"))
            self.assertTrue(panel.select_mode("motion_mismatch"))
            build_mismatch.assert_called_once_with()

        self.assertEqual(panel.cached_series_count, 2)

        panel.set_results(results, {"x_px": "px", "theta": "rad"}, selected_frame=1_000)

        self.assertEqual(len(panel._results), 1_001)
        self.assertEqual(panel.cached_series_count, 1)
        self.assertIn("Frame 1000", panel.status_label.text())

    def test_incremental_result_refresh_updates_active_series_and_selection(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        results = [diagnostic_result(index, 4 + index) for index in range(3)]
        panel.set_results(results, {"x_px": "px", "theta": "rad"}, selected_frame=1)
        self.assertTrue(panel.select_mode("velocity"))
        replacement = replace(
            results[1],
            confidence=0.0,
            status="manual_lost",
            debug={
                **results[1].debug,
                "filter": {"velocity": {"v_x_px": 42.0}},
            },
        )

        with patch.object(
            panel,
            "_index_results",
            side_effect=AssertionError("incremental refresh must not rescan the timeline"),
        ):
            self.assertTrue(panel.refresh_result(1, replacement))

        self.assertIs(panel._results_by_frame[1], replacement)
        self.assertEqual(panel.plot._y_values[1], 42.0)
        self.assertEqual(panel.plot._statuses[1], "manual_lost")
        self.assertIn("v_x_px 42 px/s", panel.status_label.text())
        self.assertTrue(panel.select_mode("confidence"))
        self.assertEqual(panel.plot._values[1], (1, 0.0, "manual_lost"))

    def test_incremental_result_refresh_repaints_when_active_mode_disappears(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        result = diagnostic_result(0, 4)
        panel.set_results([result], {"x_px": "px", "theta": "rad"}, selected_frame=0)
        self.assertTrue(panel.select_mode("velocity"))
        replacement = replace(result, debug={})

        self.assertTrue(panel.refresh_result(0, replacement))

        self.assertEqual(panel.mode, "confidence")
        self.assertEqual(panel.plot.kind, "confidence")
        self.assertEqual(panel.plot._values[0], (0, replacement.confidence, replacement.status))

    def test_incremental_result_refresh_reselects_an_available_velocity_field(self) -> None:
        panel = ReviewDiagnosticsPanel()
        self.addCleanup(panel.close)
        result = diagnostic_result(0, 4)
        panel.set_results([result], {"x_px": "px", "theta": "rad"}, selected_frame=0)
        self.assertTrue(panel.select_mode("velocity"))
        replacement = replace(
            result,
            debug={"filter": {"velocity": {"v_y_px": 9.0}}},
        )

        self.assertTrue(panel.refresh_result(0, replacement))

        self.assertEqual(panel.mode, "velocity")
        self.assertEqual(panel.series_combo.currentData(), "v_y_px")
        self.assertEqual(panel.plot.kind, "velocity")
        self.assertEqual(panel.plot._y_values, [9.0])


if __name__ == "__main__":
    unittest.main()
