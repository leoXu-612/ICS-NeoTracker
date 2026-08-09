from __future__ import annotations

import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableView, QWidget

from neo_tracker.kinematics import SampleSeries
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


if __name__ == "__main__":
    unittest.main()
