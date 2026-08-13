from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from neo_tracker.ui.calibration_editor import CalibrationEditor


class CalibrationEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        QCoreApplication.processEvents()

    def test_marked_line_enables_non_modal_length_and_unit_apply(self) -> None:
        editor = CalibrationEditor()
        self.addCleanup(editor.close)
        applied: list[dict[str, object]] = []
        editor.calibrationApplied.connect(applied.append)

        self.assertFalse(editor.apply_button.isEnabled())
        self.assertTrue(editor.set_line((10.0, 20.0), (110.0, 20.0)))
        self.assertTrue(editor.apply_button.isEnabled())
        self.assertIn("100.000 px", editor.summary_label.text())
        self.assertIn("1 px = 0.1 cm", editor.message_label.text())

        editor.length_spin.setValue(25.0)
        editor.unit_combo.setCurrentText("mm")
        editor.y_direction_combo.setCurrentIndex(1)
        editor.apply_button.click()
        self.assertEqual(applied[-1]["real_length"], 25.0)
        self.assertEqual(applied[-1]["unit"], "mm")
        self.assertEqual(applied[-1]["y_positive"], "down")
        self.assertEqual(applied[-1]["start_px"], [10.0, 20.0])

    def test_existing_calibration_can_be_corrected_and_reverted_without_remarking(self) -> None:
        editor = CalibrationEditor()
        self.addCleanup(editor.close)
        baseline = {
            "start_px": [10.0, 20.0],
            "end_px": [110.0, 20.0],
            "real_length": 50.0,
            "unit": "cm",
            "y_positive": "down",
        }
        drafts: list[object] = []
        editor.draftChanged.connect(drafts.append)
        editor.set_calibration(baseline)

        editor.length_spin.setValue(500.0)
        editor.unit_combo.setCurrentText("mm")
        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.apply_button.isEnabled())
        editor.revert_button.click()

        self.assertEqual(editor.current_config(), baseline)
        self.assertEqual(editor.y_direction_combo.currentData(), "down")
        self.assertFalse(editor.is_dirty())
        self.assertEqual(drafts[-1], baseline)

    def test_positive_x_can_be_reversed_as_an_unapplied_draft(self) -> None:
        editor = CalibrationEditor()
        self.addCleanup(editor.close)
        baseline = {
            "start_px": [10.0, 20.0],
            "end_px": [110.0, 20.0],
            "real_length": 50.0,
            "unit": "cm",
            "y_positive": "up",
        }
        drafts: list[object] = []
        editor.draftChanged.connect(drafts.append)
        editor.set_calibration(baseline)

        editor.reverse_x_button.click()

        self.assertEqual(editor.current_config()["start_px"], [110.0, 20.0])  # type: ignore[index]
        self.assertEqual(editor.current_config()["end_px"], [10.0, 20.0])  # type: ignore[index]
        self.assertEqual(drafts[-1], editor.current_config())
        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.apply_button.isEnabled())
        self.assertIn("(110.0, 20.0) → (10.0, 20.0)", editor.summary_label.text())

        editor.revert_button.click()
        self.assertEqual(editor.current_config(), baseline)
        self.assertFalse(editor.is_dirty())

    def test_drawing_and_invalid_unit_have_explicit_action_states(self) -> None:
        editor = CalibrationEditor()
        self.addCleanup(editor.close)
        editor.set_drawing_active(True)
        self.assertFalse(editor.length_spin.isEnabled())
        self.assertFalse(editor.reverse_x_button.isEnabled())
        self.assertEqual(editor.message_label.property("calibrationState"), "drawing")

        editor.set_line((0.0, 0.0), (100.0, 0.0))
        editor.set_drawing_active(False)
        editor.unit_combo.setCurrentText("")
        self.assertFalse(editor.apply_button.isEnabled())
        self.assertEqual(editor.message_label.property("calibrationState"), "error")

        editor.unit_combo.setCurrentText("custom")
        self.assertTrue(editor.apply_button.isEnabled())
        self.assertEqual(editor.message_label.property("calibrationState"), "dirty")

        editor.set_axis_direction_available(False)
        self.assertTrue(editor.y_direction_combo.isHidden())
        self.assertTrue(editor.y_direction_label.isHidden())
        self.assertTrue(editor.reverse_x_button.isHidden())


if __name__ == "__main__":
    unittest.main()
