from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from neo_tracker.core import TrackerResult
from neo_tracker.ui.results_table_model import ResultsTableModel
from neo_tracker.ui.review_controller import ReviewController


def tracking_results(count: int) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x_px": float(index), "y_px": float(index + 1)},
            filtered_state={"x_px": float(index), "y_px": float(index + 1)},
            confidence=0.25 if index == 1 else 0.95,
            status="lost" if index == 2 else "ok",
        )
        for index in range(count)
    ]


class ResultsTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_large_model_formats_only_requested_rows(self) -> None:
        model = ResultsTableModel(row_cache_size=32)
        results = tracking_results(100_000)
        original_formatter = ReviewController.format_state_value
        with patch.object(
            ReviewController,
            "format_state_value",
            wraps=original_formatter,
        ) as formatter:
            model.set_results(results, {"x_px": "px", "y_px": "px"})
            self.assertEqual(formatter.call_count, 0)
            self.assertEqual(model.rowCount(), 100_000)
            self.assertEqual(model.columnCount(), 6)
            self.assertEqual(model.data(model.index(99_999, 0)), "99999")
            self.assertEqual(model.data(model.index(99_999, 2)), "99999")
            self.assertEqual(model.data(model.index(99_999, 3)), "100000")
            self.assertEqual(formatter.call_count, 2)
            self.assertEqual(model.data(model.index(99_999, 2)), "99999")
            self.assertEqual(formatter.call_count, 2)

        self.assertEqual(model.cached_row_count, 1)
        self.assertIn("Results: 100000", model.summary)

    def test_model_preserves_headers_tones_and_result_indices(self) -> None:
        model = ResultsTableModel()
        model.set_results(tracking_results(4), {"x_px": "px", "y_px": "px"}, "trusted run")

        headers = [
            model.headerData(index, Qt.Orientation.Horizontal)
            for index in range(model.columnCount())
        ]
        self.assertEqual(headers, ["Frame", "Time", "x_px (px)", "y_px (px)", "Conf", "Status"])
        self.assertEqual(model.data(model.index(3, 0), Qt.ItemDataRole.UserRole), 3)
        self.assertEqual(
            model.data(model.index(1, 0), Qt.ItemDataRole.BackgroundRole).name(),
            "#fff8e6",
        )
        self.assertEqual(
            model.data(model.index(2, 0), Qt.ItemDataRole.BackgroundRole).name(),
            "#fff0f0",
        )
        self.assertEqual(model.summary_tooltip, "trusted run")
        self.assertIn("Run note: trusted run", model.summary)

    def test_membership_snapshot_stays_stable_until_next_reset(self) -> None:
        model = ResultsTableModel()
        results = tracking_results(3)
        model.set_results(results, {})
        results.append(tracking_results(1)[0])

        self.assertEqual(model.rowCount(), 3)

        model.set_results(results, {})
        self.assertEqual(model.rowCount(), 4)

    def test_incremental_result_refresh_updates_row_tone_and_summary(self) -> None:
        model = ResultsTableModel()
        results = tracking_results(4)
        model.set_results(results, {"x_px": "px", "y_px": "px"})
        replacement = replace(results[2], confidence=0.0, status="manual_lost")

        self.assertTrue(model.refresh_result(2, replacement))

        self.assertEqual(model.data(model.index(2, 4)), "0.000")
        self.assertEqual(model.data(model.index(2, 5)), "manual_lost")
        self.assertEqual(
            model.data(model.index(2, 0), Qt.ItemDataRole.BackgroundRole).name(),
            "#fff0f0",
        )
        self.assertIn("manual_lost 1", model.summary)
        self.assertIn("avg confidence 0.54", model.tracking_summary)

    def test_incremental_result_refresh_rejects_new_state_columns(self) -> None:
        model = ResultsTableModel()
        results = tracking_results(2)
        model.set_results(results, {"x_px": "px", "y_px": "px"})
        replacement = replace(
            results[0],
            filtered_state={**results[0].filtered_state, "theta": 0.5},
        )

        self.assertFalse(model.refresh_result(0, replacement))

    def test_incremental_result_refresh_rejects_removed_state_columns(self) -> None:
        model = ResultsTableModel()
        results = tracking_results(2)
        model.set_results(results, {"x_px": "px", "y_px": "px"})
        replacement = replace(
            results[0],
            filtered_state={"x_px": results[0].filtered_state["x_px"]},
        )

        self.assertFalse(model.refresh_result(0, replacement))


if __name__ == "__main__":
    unittest.main()
