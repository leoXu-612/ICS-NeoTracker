from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableView, QWidget

from neo_tracker.kinematics import SampleSeries
from neo_tracker.media import MediaInfo
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceState
from neo_tracker.ui.workspaces.physics_workspace import PhysicsWorkspace


def make_series() -> SampleSeries:
    return SampleSeries(
        series_id="raw:x",
        name="Raw x",
        frame_indices=np.arange(4, dtype=np.int64),
        time_s=np.array([0.0, 0.04, 0.09, 0.13]),
        values=np.array([0.0, 1.0, 2.0, 3.0]),
        valid_mask=np.array([True, True, True, True]),
        unit="m",
        source_kind="state",
        source_revision="results:workspace",
    )


class PhysicsWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_workspace_has_all_required_pages_and_virtual_table(self) -> None:
        workspace = PhysicsWorkspace()

        self.assertEqual(
            tuple(workspace.tabs.tabText(index) for index in range(workspace.tabs.count())),
            ("Data", "Plot", "Fit", "Diagnostics", "Runs", "Edits", "Signal"),
        )
        self.assertIsInstance(workspace.series_table, QTableView)
        self.assertIs(workspace.series_table.model(), workspace.series_model)
        self.assertEqual(workspace.accessibleName(), "Physics analysis workspace")

    def test_workspace_rejects_duplicate_series_ids_without_collapsing_rows(self) -> None:
        workspace = PhysicsWorkspace()
        source = make_series()

        with self.assertRaisesRegex(ValueError, "unique"):
            workspace.set_series((source, source))

        self.assertEqual(workspace.series_combo.count(), 0)

    def test_collapse_and_focus_mode_restore_page_and_height(self) -> None:
        workspace = PhysicsWorkspace()
        workspace.tabs.setCurrentIndex(2)
        workspace.remember_height(310)

        workspace.set_collapsed(True)
        collapsed = workspace.layout_state()
        workspace.set_canvas_focus(True)
        workspace.set_canvas_focus(False)
        workspace.set_collapsed(False)

        self.assertTrue(collapsed.collapsed)
        self.assertEqual(workspace.tabs.currentIndex(), 2)
        self.assertEqual(workspace.preferred_height, 310)
        self.assertTrue(workspace.tabs.isVisibleTo(workspace) or not workspace.isVisible())

    def test_workspace_state_round_trip_is_bounded_and_strict(self) -> None:
        state = PhysicsWorkspaceState(collapsed=False, page="Plot", height=280)
        restored = PhysicsWorkspaceState.from_mapping(state.to_mapping())
        self.assertEqual(restored, state)
        with self.assertRaisesRegex(ValueError, "page"):
            PhysicsWorkspaceState(collapsed=False, page="Unknown", height=280)
        with self.assertRaisesRegex(ValueError, "height"):
            PhysicsWorkspaceState(collapsed=False, page="Data", height=50_000)

    def test_table_and_plot_emit_selection_without_cell_widgets(self) -> None:
        workspace = PhysicsWorkspace()
        source = make_series()
        workspace.set_series((source,))
        selected: list[tuple[str, int]] = []
        workspace.sampleActivated.connect(lambda series_id, row: selected.append((series_id, row)))

        workspace.series_table.selectRow(2)
        workspace._table_selection_changed()

        self.assertEqual(selected[-1], ("raw:x", 2))
        self.assertIsNone(workspace.series_table.indexWidget(workspace.series_model.index(2, 2)))
        self.assertEqual(workspace.cursor_label.text(), "true time 0.090000 s · frame 2 · exact")

    def test_copy_row_button_uses_the_models_full_precision_text(self) -> None:
        workspace = PhysicsWorkspace()
        workspace.set_series((make_series(),))
        self.assertFalse(workspace.copy_row_button.isEnabled())
        workspace.apply_selection("raw:x", 2, 2, 0.09, "exact")
        self.assertTrue(workspace.copy_row_button.isEnabled())

        workspace.copy_row_button.click()

        self.assertEqual(
            QApplication.clipboard().text(),
            workspace.series_model.copy_rows([2]),
        )
        self.assertEqual(workspace.copy_status_label.text(), "Copied 1 physical data row.")

        workspace.apply_selection(None, None, None, None, "unavailable")
        self.assertFalse(workspace.copy_row_button.isEnabled())

    def test_selection_keeps_an_overflow_series_visible_in_the_bounded_plot(self) -> None:
        workspace = PhysicsWorkspace()
        source = make_series()
        series = tuple(
            replace(source, series_id=f"raw:{index}", name=f"Series {index}")
            for index in range(9)
        )
        workspace.set_series(series)
        workspace.plot.set_selected_range(0.04, 0.13)

        workspace.apply_selection(series[-1].series_id, 2, 2, 0.09, "exact")

        self.assertEqual(len(workspace.plot.series_ids), 8)
        self.assertIn(series[-1].series_id, workspace.plot.series_ids)
        self.assertEqual(workspace.plot.selected_sample_index, 2)
        self.assertEqual(workspace.plot.selected_range, (0.04, 0.13))

    def test_quantity_change_keeps_an_overflow_series_selectable_in_the_plot(self) -> None:
        workspace = PhysicsWorkspace()
        source = make_series()
        series = tuple(
            replace(source, series_id=f"raw:{index}", name=f"Series {index}")
            for index in range(9)
        )
        workspace.set_series(series)
        workspace.plot.set_selected_range(0.04, 0.13)

        workspace.series_combo.setCurrentIndex(8)
        workspace.series_table.selectRow(2)

        self.assertEqual(len(workspace.plot.series_ids), 8)
        self.assertIn(series[-1].series_id, workspace.plot.series_ids)
        self.assertEqual(workspace.plot.selected_sample_index, 2)
        self.assertEqual(workspace.plot.selected_range, (0.04, 0.13))


class MainWindowPhysicsWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(self) -> NeoTrackerWindow:
        window = NeoTrackerWindow()
        window._set_project_clean()

        def close_cleanly() -> None:
            window._discard_unapplied_drafts(show_status=False)
            window._set_project_clean()
            window.close()

        self.addCleanup(close_cleanly)
        return window

    @staticmethod
    def sparse_series() -> SampleSeries:
        return SampleSeries(
            series_id="filtered:x",
            name="Filtered x",
            frame_indices=np.array([2, 6, 10], dtype=np.int64),
            time_s=np.array([0.071, 0.203, 0.341]),
            values=np.array([1.0, 2.0, 3.0]),
            valid_mask=np.array([True, True, True]),
            unit="m",
            source_kind="filtered_state",
            source_revision="results:window-workspace",
        )

    def test_bottom_workspace_is_mounted_in_vertical_shell_at_1024(self) -> None:
        window = self.make_window()
        window.resize(1024, 768)
        window.show()
        QApplication.processEvents()

        self.assertEqual(window.workspace_splitter.orientation(), Qt.Orientation.Vertical)
        self.assertIs(window.workspace_splitter.widget(1), window.physics_workspace)
        self.assertLessEqual(window.minimumSizeHint().height(), 768)
        self.assertGreaterEqual(window.physics_workspace.height(), 38)

    def test_window_rejects_duplicate_series_ids_before_session_attachment(self) -> None:
        window = self.make_window()
        source = self.sparse_series()
        before = window.selection_session.state

        with self.assertRaisesRegex(ValueError, "unique"):
            window.set_physics_series((source, source))

        self.assertEqual(window.selection_session.state, before)

    def test_table_video_and_video_table_share_one_selection_session(self) -> None:
        window = self.make_window()
        window.current_task.media_info = MediaInfo(
            fps=30.0,
            frame_count=30,
            width=640,
            height=360,
            duration_s=1.0,
            available=True,
        )
        source = self.sparse_series()
        self.assertTrue(window.set_physics_series((source,)))

        window.physics_workspace.series_table.selectRow(1)
        QApplication.processEvents()
        self.assertEqual(window.current_task.preview_frame_index, 6)
        self.assertEqual(window.selection_session.state.selected_sample_index, 1)

        before_revision = window.selection_session.state.selection_revision
        window._preview_frame_changed(8)
        QApplication.processEvents()
        state = window.selection_session.state
        self.assertEqual(state.selection_revision, before_revision + 1)
        self.assertEqual(state.selected_frame_index, 8)
        self.assertEqual(state.selected_sample_index, 1)  # equal-distance tie -> earlier frame 6
        self.assertEqual(state.match.value, "nearest")
        self.assertIn("frame 8 · nearest", window.physics_workspace.cursor_label.text())

        window.preview_label.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        window._start_polygon_roi_selection()
        window.preview_label._polygon_points[:] = [(10.0, 10.0), (30.0, 10.0)]
        window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]
        window.physics_workspace.series_table.selectRow(2)
        QApplication.processEvents()

        state = window.selection_session.state
        self.assertEqual(window.current_task.preview_frame_index, 8)
        self.assertEqual(state.selected_frame_index, 8)
        self.assertEqual(state.selected_sample_index, 1)
        self.assertEqual(window.preview_label.selection_mode(), "roi_polygon")
        self.assertEqual(window.preview_label._polygon_points, [(10.0, 10.0), (30.0, 10.0)])

    def test_data_quantity_change_updates_the_shared_action_source(self) -> None:
        window = self.make_window()
        source = self.sparse_series()
        other = replace(source, series_id="filtered:y", name="Filtered y")
        self.assertTrue(window.set_physics_series((source, other)))

        window.physics_workspace.series_combo.setCurrentIndex(
            window.physics_workspace.series_combo.findData(other.series_id)
        )
        QApplication.processEvents()

        self.assertIs(window.physics_workspace.series_model.series, other)
        self.assertEqual(window.selection_session.state.selected_series_id, other.series_id)
        self.assertEqual(
            window.analysis_workspace_controller.state.selected_series_id,
            other.series_id,
        )

    def test_one_plot_keyboard_action_commits_one_canonical_revision(self) -> None:
        window = self.make_window()
        window.current_task.media_info = MediaInfo(
            fps=30.0,
            frame_count=30,
            width=640,
            height=360,
            duration_s=1.0,
            available=True,
        )
        source = self.sparse_series()
        window.set_physics_series((source,))
        plot = window.physics_workspace.plot
        plot.setFocus()
        before = window.selection_session.state.selection_revision

        QTest.keyClick(plot, Qt.Key.Key_Right)
        QApplication.processEvents()

        self.assertEqual(window.selection_session.state.selection_revision, before + 1)
        self.assertEqual(window.selection_session.state.selected_sample_index, 1)

    def test_plot_image_export_is_reachable_from_workspace(self) -> None:
        window = self.make_window()
        window.set_physics_series((self.sparse_series(),))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "physics-plot.png"
            with patch(
                "neo_tracker.ui.shell.physics_workspace_mixin.QFileDialog.getSaveFileName",
                return_value=(str(path), "PNG images (*.png)"),
            ):
                window.physics_workspace.export_plot_image_button.click()

            self.assertGreater(path.stat().st_size, 0)
        self.assertIn("Exported physics plot", window.statusBar().currentMessage())

    def test_final_window_close_unsubscribes_selection_session(self) -> None:
        window = self.make_window()
        self.assertEqual(window.selection_session.listener_count, 1)
        window._set_project_clean()

        window.close()
        QApplication.processEvents()

        self.assertEqual(window.selection_session.listener_count, 0)


if __name__ == "__main__":
    unittest.main()
