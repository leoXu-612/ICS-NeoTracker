from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QWidget

from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaIdentity, MediaInfo
from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore
from tests.integration_kinematics_support import pump_until
from tests.test_analysis_workspace import make_series
from tests import test_ui_main_window


class SourceReviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.anchor = QWidget()

    def make_window(self, export_picker=None) -> NeoTrackerWindow:
        window = NeoTrackerWindow(
            physics_layout_store=PhysicsWorkspaceStateStore(),
            physics_export_directory_picker=export_picker,
        )

        def close() -> None:
            window._discard_unapplied_drafts(show_status=False)
            window._set_project_clean()
            window.close()
            pump_until(lambda: window._background_tasks.idle and not window.isVisible(), timeout_s=30)

        self.addCleanup(close)
        return window

    def test_sampled_match_requires_review_without_claiming_content_changed(self) -> None:
        window = self.make_window()
        identity = MediaIdentity("sampled-sha256-v1", "1" * 64, 258_182_423, 786_432)
        live = MediaInfo(
            fps=240.26213571165073, frame_count=5536, width=1080, height=1920,
            duration_s=23.0415, available=True, source_identity=identity,
        )
        snapshot = ProjectTaskSnapshot(
            media_path="/not-read/PMR00056.mov",
            pipeline_key="color_marker",
            media_info=window.project_controller.media_info_to_dict(live),
            results=[TrackerResult(0, 0.0, {"x_px": 572.0}, {"x_px": 572.0}, 0.9, "ok")],
        )
        task = window.project_controller.task_from_snapshot(snapshot, live_media_info=live)
        window._apply_project(NeoTrackerProject(name="sampled source", tasks=[snapshot]), prepared_tasks=(task,))
        pump_until(lambda: window._background_tasks.idle and bool(window._physics_series_by_id), timeout_s=30)

        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertEqual(task.pipeline.results, snapshot.results)
        self.assertIn("not fully verified", task.media_info.error)
        self.assertNotIn("differs", task.media_info.error)
        self.assertEqual(window.tracking_status_label.text(), "Review source")
        self.assertIn("not fully verified", window.tracking_status_label.toolTip())
        self.assertIn("Source verification required", window.preview_label.text())
        self.assertEqual(window.media_relink_panel.status_label.text(), "Sampled identity match")
        for button in (
            window.run_tracking_button, window.export_tracking_csv_button,
            window.export_report_button, window.correct_point_button,
            window.export_physics_analysis_button, window.fit_panel.export_button,
            window.physics_workspace.export_plot_image_button,
        ):
            self.assertFalse(button.isEnabled())

        window._cancel_media_relink()
        self.assertEqual(window.media_relink_panel.status_label.text(), "Review source")
        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertEqual(task.pipeline.results, snapshot.results)

    def test_source_review_blocks_direct_physics_exports_before_file_pickers(self) -> None:
        directory_requests = []
        window = self.make_window(lambda _parent: directory_requests.append(True) or "")
        window.current_task.media_identity_requires_review = True
        window.set_physics_series((make_series(),))
        with self.subTest(operation="analysis"):
            window._export_physics_analysis()
            self.assertEqual(directory_requests, [])
        with self.subTest(operation="image"), patch.object(
            QFileDialog, "getSaveFileName", return_value=("", "")
        ) as picker:
            window._export_physics_plot_image()
            picker.assert_not_called()

        window.current_task.media_identity_requires_review = False
        window._render_tracking_status(window.current_task)
        self.assertTrue(window.export_physics_analysis_button.isEnabled())
        self.assertTrue(window.fit_panel.export_button.isEnabled())
        self.assertTrue(window.physics_workspace.export_plot_image_button.isEnabled())

    def test_toolbar_can_shrink_after_long_source_and_draft_statuses(self) -> None:
        window = self.make_window()
        window.show()
        window.global_project_dirty_label.show()
        window.global_draft_label.setText("Draft: Media replacement")
        window.global_draft_label.show()
        window.tracking_summary_label.setText("Results: 5536 · source review required")
        for status in ("Review source", "Source changed", "Preview unavailable"):
            with self.subTest(status=status):
                window.resize(1440, 900)
                window.tracking_status_label.setText(status)
                self.app.processEvents()
                window.resize(1024, 768)
                self.app.processEvents()
                self.assertEqual(window.size().toTuple(), (1024, 768))
                self.assertLessEqual(window.minimumSizeHint().width(), 1024)
                self.assertEqual(window.global_draft_label.text(), "Draft: Media replacement")
                self.assertTrue(window.global_draft_label.displayedText())
                toolbar = window.findChild(QFrame, "topToolbar")
                controls = sorted(
                    (widget for widget in toolbar.findChildren(QWidget)
                     if widget.parentWidget() is toolbar and widget.isVisible()),
                    key=lambda widget: widget.x(),
                )
                for left, right in zip(controls, controls[1:]):
                    self.assertLess(left.geometry().right(), right.geometry().left())
                self.assertLessEqual(controls[-1].geometry().right(), toolbar.contentsRect().right())

    def test_source_review_starting_in_file_picker_cancels_physics_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def choose_directory(_parent):
                window.current_task.media_identity_requires_review = True
                return directory

            window = self.make_window(choose_directory)
            window.set_physics_series((make_series(),))
            window._export_physics_analysis()
            self.assertTrue(window._background_tasks.idle)
            self.assertEqual(list(Path(directory).iterdir()), [])

            window.current_task.media_identity_requires_review = False
            target = Path(directory) / "plot.png"

            def choose_image(*_args):
                window.current_task.media_identity_requires_review = True
                return str(target), "PNG images (*.png)"

            with patch.object(QFileDialog, "getSaveFileName", side_effect=choose_image):
                window._export_physics_plot_image()
            self.assertFalse(target.exists())

    def test_review_table_tabs_leave_table_but_arrows_still_select_rows(self) -> None:
        window = self.make_window()
        window.current_task.pipeline.results = [
            TrackerResult(i, i / 10, {"x_px": float(i)}, {"x_px": float(i)}, .9, "ok")
            for i in range(3)
        ]
        window._render_results(window.current_task)
        window.sidebar_tabs.setCurrentWidget(window.review_tab)
        window.show()
        table = window.results_table
        for modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            with self.subTest(modifiers=modifiers):
                table.setFocus(Qt.FocusReason.TabFocusReason)
                self.app.processEvents()
                self.assertIs(window.focusWidget(), table)
                QTest.keyClick(table, Qt.Key.Key_Tab, modifiers)
                self.app.processEvents()
                self.assertIsNot(window.focusWidget(), table)
        table.selectRow(0)
        table.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(table, Qt.Key.Key_Down)
        self.assertEqual(table.currentIndex().row(), 1)

    def test_global_test_teardown_does_not_revisit_closed_windows(self) -> None:
        for case_type in (test_ui_main_window.MainWindowStructureTests, test_ui_main_window.ConfigResultProtectionTests):
            with self.subTest(case_type=case_type.__name__):
                window = self.make_window()
                window.close()
                window.advanced_config_view.insertPlainText(" ")
                self.assertTrue(window._advanced_config_dirty)
                case_type().tearDown()
                self.assertTrue(window._advanced_config_dirty)


if __name__ == "__main__":
    unittest.main()
