from __future__ import annotations

import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QTabWidget
import shiboken6

from neo_tracker.project import TrackingRunRecord
from neo_tracker.ui.run_history_panel import (
    RunHistoryComparisonDialog,
    RunHistoryPanel,
    RunHistorySelection,
    flatten_pipeline_config,
    run_config_difference_rows,
    run_summary_rows,
)


def run_record(
    outcome: str,
    *,
    mode: str = "full",
    width: int = 100,
    note: str = "",
) -> TrackingRunRecord:
    processed = 4 if outcome != "failed" else 0
    return TrackingRunRecord(
        started_at="2026-07-13T01:00:00Z",
        duration_s=0.25,
        mode=mode,
        outcome=outcome,
        start_frame=0,
        end_frame=processed - 1 if processed else None,
        processed_frames=processed,
        result_count=processed,
        note=note,
        pipeline_config={
            "roi": {"type": "rectangle", "width": width, "height": 80},
            "observation_model": {"type": "color_blob", "tolerance": 0.12},
            "debug_history_max_bytes": 64 * 1024 * 1024,
        },
        tracking_elapsed_s=0.2 if processed else 0.0,
        input_s=0.04 if processed else 0.0,
        processing_s=0.12 if processed else 0.0,
        peak_debug_bytes=2 * 1024 * 1024 if processed else 0,
        prefetch_frames=1 if processed else 0,
        compute_backend="OpenCV components" if processed else "",
    )


class RunHistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        QCoreApplication.processEvents()

    def test_filter_reports_visible_count_and_preserves_original_sequence(self) -> None:
        panel = RunHistoryPanel()
        self.addCleanup(panel.close)
        panel.set_records(
            [
                run_record("complete"),
                run_record("partial", mode="rerun"),
                run_record("failed"),
                run_record("complete", mode="rerun"),
            ]
        )

        self.assertEqual(panel.outcome_filter.itemText(0), "All outcomes (4)")
        panel.outcome_filter.setCurrentIndex(panel.outcome_filter.findData("complete"))
        self.assertEqual([selection.sequence for selection in panel.visible_runs], [1, 4])
        self.assertEqual(panel.list_widget.count(), 2)
        self.assertTrue(panel.list_widget.item(0).text().startswith("#4 Rerun · Complete"))
        self.assertEqual(panel.status_label.text(), "Showing 2 of 4 · 0 selected · Select 2 to compare")
        self.assertTrue(panel.export_button.isEnabled())
        panel.show_all()
        self.assertEqual(panel.outcome_filter.currentData(), "")
        self.assertEqual(len(panel.visible_runs), 4)

    def test_exactly_two_selected_runs_enable_comparison(self) -> None:
        panel = RunHistoryPanel()
        self.addCleanup(panel.close)
        panel.set_records([run_record("complete"), run_record("partial"), run_record("failed")])
        captured: list[object] = []
        panel.compareRequested.connect(captured.append)

        panel.list_widget.item(2).setSelected(True)
        self.assertIn("Throughput 20.0 fps", panel.list_widget.item(2).text())
        self.assertIn("Input 10.00 ms/f", panel.performance_label.text())
        self.assertIn("Backend OpenCV components", panel.performance_label.text())
        self.assertIn("1-frame pipeline", panel.performance_label.text())
        self.assertIn("Throughput 20.0 fps", panel.performance_label.text())
        self.assertEqual(panel.performance_label.text().count("\n"), 2)
        self.assertGreaterEqual(panel.performance_label.minimumHeight(), 64)
        self.assertIn("Review peak 2.0 MiB", panel.performance_label.text())
        self.assertIn("bounded 1-frame pipeline", panel.performance_label.toolTip())
        self.assertIn("Compute backend: OpenCV components", panel.performance_label.toolTip())
        self.assertIn("Configured retained-data target: 64.0 MiB", panel.performance_label.toolTip())
        self.assertIn("newest response may exceed", panel.performance_label.accessibleDescription())
        self.assertEqual(panel.performance_label.property("performanceState"), "ready")
        panel.list_widget.item(0).setSelected(True)
        self.assertEqual([selection.sequence for selection in panel.selected_runs()], [1, 3])
        self.assertIn("2 runs selected", panel.performance_label.text())
        self.assertTrue(panel.compare_button.isEnabled())
        panel.compare_button.click()

        self.assertEqual(len(captured), 1)
        selections = captured[0]
        self.assertEqual([selection.sequence for selection in selections], [1, 3])

        panel.set_records([replace(run_record("complete"), compute_backend="")])
        panel.list_widget.setCurrentRow(0)
        self.assertIn("Backend Not recorded", panel.performance_label.text())

    def test_export_request_contains_filtered_runs_in_chronological_order(self) -> None:
        panel = RunHistoryPanel()
        self.addCleanup(panel.close)
        panel.set_records(
            [run_record("complete"), run_record("partial"), run_record("complete", mode="rerun")]
        )
        captured: list[object] = []
        panel.exportRequested.connect(captured.append)
        panel.outcome_filter.setCurrentIndex(panel.outcome_filter.findData("complete"))
        panel.export_button.click()

        self.assertEqual(len(captured), 1)
        selections = captured[0]
        self.assertEqual([selection.sequence for selection in selections], [1, 3])

    def test_select_latest_exposes_the_newest_run_performance(self) -> None:
        panel = RunHistoryPanel()
        self.addCleanup(panel.close)
        panel.set_records(
            [
                run_record("complete"),
                run_record("partial", mode="rerun"),
                run_record("complete", mode="rerun"),
            ]
        )

        panel.select_latest()

        self.assertEqual(panel.list_widget.currentRow(), 0)
        self.assertEqual(
            [selection.sequence for selection in panel.selected_runs()],
            [3],
        )
        self.assertEqual(panel.performance_label.property("performanceState"), "ready")
        self.assertIn("Run #3", panel.performance_label.text())
        self.assertIn("Throughput", panel.performance_label.text())

    def test_zero_frame_run_explains_why_per_frame_metrics_are_unavailable(self) -> None:
        panel = RunHistoryPanel()
        self.addCleanup(panel.close)
        panel.set_records([run_record("failed")])

        panel.list_widget.setCurrentRow(0)

        self.assertIn("No completed frames", panel.performance_label.text())
        self.assertEqual(panel.performance_label.property("performanceState"), "unavailable")

    def test_comparison_dialog_exposes_metadata_and_changed_config_paths(self) -> None:
        older = RunHistorySelection(2, run_record("partial", width=100, note="early end"))
        newer = RunHistorySelection(5, run_record("complete", width=140))
        differences = run_config_difference_rows(older, newer)
        self.assertEqual(differences, [("$.roi.width", "100", "140")])
        flattened = flatten_pipeline_config(newer.record.pipeline_config)
        self.assertEqual(flattened["$.observation_model.type"], "color_blob")
        self.assertIn(
            ("Compute backend", "OpenCV components", "OpenCV components"),
            run_summary_rows(older, newer),
        )

        dialog = RunHistoryComparisonDialog((newer, older))
        self.addCleanup(dialog.close)
        self.assertFalse(shiboken6.createdByPython(dialog.summary_table))
        self.assertFalse(shiboken6.createdByPython(dialog.config_table))
        self.assertIn("metadata field", dialog.summary_table.accessibleDescription())
        self.assertIn("changed pipeline setting", dialog.config_table.accessibleDescription())
        tabs = dialog.findChild(QTabWidget)
        self.assertIsNotNone(tabs)
        self.assertEqual(tabs.tabText(0), "Summary")
        self.assertEqual(tabs.tabText(1), "Config changes (1)")
        self.assertEqual(dialog.config_table.item(0, 0).text(), "$.roi.width")
        self.assertEqual(dialog.config_table.horizontalHeaderItem(1).text(), "Run #2")
        self.assertEqual(dialog.config_table.horizontalHeaderItem(2).text(), "Run #5")
        self.assertIn(("Throughput", "20.000 fps", "20.000 fps"), run_summary_rows(older, newer))
        self.assertIn(("Input prefetch", "1 frame", "1 frame"), run_summary_rows(older, newer))
        self.assertIn(("Review cache peak", "2.0 MiB", "2.0 MiB"), run_summary_rows(older, newer))


if __name__ == "__main__":
    unittest.main()
