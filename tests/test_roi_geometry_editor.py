from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QDoubleSpinBox
import shiboken6

from neo_tracker.ui.roi_geometry_editor import ROIGeometryEditor


class ROIGeometryEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        QCoreApplication.processEvents()

    def test_rectangle_changes_require_apply_and_can_revert(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        emitted: list[dict[str, object]] = []
        editor.configApplied.connect(emitted.append)
        editor.set_config({"type": "rectangle", "x": 10.0, "y": 20.0, "width": 80.0, "height": 50.0})

        self.assertEqual(editor.type_label.text(), "Rectangle")
        self.assertFalse(editor.apply_button.isEnabled())
        editor.rectangle_x_spin.setValue(14.5)
        QCoreApplication.processEvents()

        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.apply_button.isEnabled())
        self.assertEqual(editor.message_label.property("roiGeometryState"), "dirty")
        self.assertEqual(emitted, [])
        editor.revert_button.click()
        self.assertAlmostEqual(editor.rectangle_x_spin.value(), 10.0)
        self.assertFalse(editor.is_dirty())

        editor.rectangle_width_spin.setValue(96.0)
        editor.apply_button.click()
        self.assertEqual(emitted[-1]["width"], 96.0)

    def test_annulus_validation_blocks_invalid_radius_order(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        emitted: list[dict[str, object]] = []
        editor.configApplied.connect(emitted.append)
        editor.set_config(
            {
                "type": "annulus",
                "center": [50.0, 60.0],
                "inner_radius": 20.0,
                "outer_radius": 40.0,
            }
        )

        editor.annulus_inner_radius_spin.setValue(45.0)
        QCoreApplication.processEvents()
        self.assertEqual(editor.message_label.property("roiGeometryState"), "error")
        self.assertIn("ordered", editor.message_label.text())
        editor.apply_button.click()
        self.assertEqual(emitted, [])

    def test_polygon_nodes_can_be_edited_added_and_removed(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        self.assertFalse(
            shiboken6.createdByPython(editor.node_table),
            "macOS accessibility must enter a native Qt ROI node table",
        )
        self.assertIn("Select one node row", editor.node_table.accessibleDescription())
        emitted: list[dict[str, object]] = []
        editor.configApplied.connect(emitted.append)
        editor.set_config(
            {
                "type": "polygon",
                "points": [[5.0, 5.0], [40.0, 5.0], [42.0, 30.0], [8.0, 28.0]],
            }
        )

        self.assertFalse(editor.curve_width_row.isVisible())
        self.assertEqual(editor.add_node_button.text(), "Insert After")
        self.assertFalse(editor.add_node_button.isEnabled())
        x_spin = editor.node_table.cellWidget(1, 1)
        self.assertIsInstance(x_spin, QDoubleSpinBox)
        x_spin.setValue(44.0)  # type: ignore[union-attr]
        editor.node_table.selectRow(3)
        editor.remove_node_button.click()
        self.assertEqual(editor.node_table.rowCount(), 3)
        self.assertFalse(editor.remove_node_button.isEnabled())
        editor.add_node_button.click()
        self.assertEqual(editor.node_table.rowCount(), 4)
        editor.apply_button.click()

        points = emitted[-1]["points"]
        self.assertEqual(points[1], [44.0, 5.0])  # type: ignore[index]
        self.assertEqual(len(points), 4)  # type: ignore[arg-type]

    def test_curve_node_insert_uses_selected_segment_midpoint_and_endpoint_extension(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        editor.set_config(
            {
                "type": "curve_band",
                "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
                "half_width": 17.5,
            }
        )

        editor.select_node(1)
        self.assertTrue(editor.add_node_button.isEnabled())
        editor.add_node_button.click()
        self.assertEqual(editor.selected_node_index(), 2)
        self.assertEqual(
            editor.current_config()["polyline"],  # type: ignore[index]
            [[5.0, 6.0], [40.0, 8.0], [50.0, 20.0], [60.0, 32.0]],
        )
        self.assertIn("Arrow keys", editor.message_label.text())

        editor.select_node(3)
        editor.add_node_button.click()
        self.assertEqual(
            editor.current_config()["polyline"][-1],  # type: ignore[index]
            [65.0, 38.0],
        )

    def test_polygon_last_node_insert_uses_closing_segment_midpoint(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        editor.set_config(
            {
                "type": "polygon",
                "points": [[5.0, 5.0], [40.0, 5.0], [42.0, 30.0], [8.0, 28.0]],
            }
        )

        editor.select_node(3)
        editor.add_node_button.click()
        self.assertEqual(editor.selected_node_index(), 4)
        self.assertEqual(editor.current_config()["points"][-1], [6.5, 16.5])  # type: ignore[index]

    def test_curve_band_exposes_nodes_and_width(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        editor.show()
        editor.set_config(
            {
                "type": "curve_band",
                "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
                "half_width": 17.5,
            }
        )
        QCoreApplication.processEvents()

        self.assertEqual(editor.type_label.text(), "Curve band nodes")
        self.assertTrue(editor.curve_width_row.isVisible())
        editor.curve_band_half_width_spin.setValue(22.5)
        config = editor.current_config()
        self.assertEqual(config["half_width"], 22.5)  # type: ignore[index]
        self.assertEqual(len(config["polyline"]), 3)  # type: ignore[arg-type,index]

    def test_canvas_style_node_selection_and_move_update_only_the_draft(self) -> None:
        editor = ROIGeometryEditor()
        self.addCleanup(editor.close)
        drafts: list[object] = []
        selections: list[object] = []
        editor.draftChanged.connect(drafts.append)
        editor.nodeSelectionChanged.connect(selections.append)
        baseline = {
            "type": "curve_band",
            "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
            "half_width": 17.5,
        }
        editor.set_config(baseline)

        editor.select_node(1)
        self.assertEqual(editor.selected_node_index(), 1)
        self.assertEqual(selections[-1], 1)
        self.assertEqual(editor.message_label.property("roiGeometryState"), "selected")
        self.assertIn("Node 2 selected", editor.message_label.text())

        editor.move_node(1, (44.25, 18.5))
        self.assertEqual(editor.current_config()["polyline"][1], [44.25, 18.5])  # type: ignore[index]
        self.assertEqual(drafts[-1]["polyline"][1], [44.25, 18.5])  # type: ignore[index]
        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.apply_button.isEnabled())
        editor.revert_button.click()
        self.assertEqual(editor.current_config(), baseline)
        self.assertFalse(editor.is_dirty())


if __name__ == "__main__":
    unittest.main()
