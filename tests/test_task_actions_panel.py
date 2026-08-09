from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from neo_tracker.ui.task_actions_panel import TaskActionsPanel


class TaskActionsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_current_task_summary_names_impact_and_enables_removal(self) -> None:
        panel = TaskActionsPanel()
        self.addCleanup(panel.close)

        panel.set_current(
            "calibration-check.mp4",
            result_count=8,
            edit_count=2,
            run_count=2,
            can_remove=True,
        )

        self.assertEqual(
            panel.summary_label.text(),
            "Selected: calibration-check.mp4 · 8 results · 2 edits · 2 runs",
        )
        self.assertTrue(panel.remove_button.isEnabled())
        self.assertEqual(panel.remove_button.text(), "Remove Task…")

    def test_remove_requires_inline_confirmation_and_cancel_keeps_task(self) -> None:
        panel = TaskActionsPanel()
        self.addCleanup(panel.close)
        emitted: list[bool] = []
        panel.removeConfirmed.connect(lambda: emitted.append(True))
        panel.set_current("calibration-check.mp4", result_count=8, edit_count=2, run_count=2, can_remove=True)

        panel.remove_button.click()

        self.assertEqual(emitted, [])
        self.assertEqual(panel.remove_button.text(), "Confirm Remove")
        self.assertEqual(panel.message_label.property("taskActionState"), "warning")
        self.assertIn("media file stays on disk", panel.message_label.text())
        self.assertFalse(panel.cancel_button.isHidden())

        panel.cancel_button.click()

        self.assertEqual(emitted, [])
        self.assertEqual(panel.remove_button.text(), "Remove Task…")
        self.assertTrue(panel.message_label.isHidden())

    def test_confirm_emits_removal_and_removed_state_exposes_undo(self) -> None:
        panel = TaskActionsPanel()
        self.addCleanup(panel.close)
        removed: list[bool] = []
        undone: list[bool] = []
        panel.removeConfirmed.connect(lambda: removed.append(True))
        panel.undoRequested.connect(lambda: undone.append(True))
        panel.set_current("calibration-check.mp4", result_count=8, edit_count=2, run_count=2, can_remove=True)

        panel.remove_button.click()
        panel.remove_button.click()
        panel.show_removed("calibration-check.mp4")

        self.assertEqual(removed, [True])
        self.assertEqual(panel.message_label.property("taskActionState"), "removed")
        self.assertIn("media file stays on disk", panel.message_label.text())
        self.assertFalse(panel.undo_button.isHidden())

        panel.undo_button.click()
        self.assertEqual(undone, [True])


if __name__ == "__main__":
    unittest.main()
