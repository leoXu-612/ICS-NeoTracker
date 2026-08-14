from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from neo_tracker.ui.project_status_panel import (
    ProjectStatusPanel,
    build_config_result_protection_dialog,
    build_rerun_replacement_dialog,
    build_result_replacement_dialog,
    build_unapplied_drafts_dialog,
    build_unsaved_changes_dialog,
)


class ProjectStatusPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_distinguishes_new_saved_and_dirty_states_in_text(self) -> None:
        panel = ProjectStatusPanel()
        self.addCleanup(panel.close)

        panel.set_state(None, dirty=False)
        self.assertEqual(panel.name_label.text(), "Unsaved project")
        self.assertEqual(panel.state_label.text(), "Not saved yet")
        self.assertEqual(panel.state_label.property("projectState"), "new")

        panel.set_state(Path("/experiments/session-04.ntproj"), dirty=False)
        self.assertEqual(panel.name_label.text(), "Project: session-04.ntproj")
        self.assertEqual(panel.state_label.text(), "Saved")
        self.assertEqual(panel.state_label.property("projectState"), "saved")

        panel.set_state(Path("/experiments/session-04.ntproj"), dirty=True)
        self.assertEqual(panel.state_label.text(), "Unsaved changes")
        self.assertEqual(panel.state_label.property("projectState"), "dirty")
        self.assertIn("changed since the last save", panel.state_label.accessibleDescription())

    def test_unsaved_dialog_names_risk_and_offers_save_discard_cancel(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_unsaved_changes_dialog(
            parent,
            project_name="session-04.ntproj",
            action="opening another project",
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "unsavedChangesDialog")
        self.assertIn("session-04.ntproj", dialog.text())
        self.assertIn("opening another project", dialog.text())
        self.assertIn("results, edits, calibration", dialog.informativeText())
        for button in (
            QMessageBox.StandardButton.Save,
            QMessageBox.StandardButton.Discard,
            QMessageBox.StandardButton.Cancel,
        ):
            self.assertIsNotNone(dialog.button(button))
        self.assertIs(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.Save))

    def test_unapplied_drafts_dialog_names_work_and_defaults_to_keep_editing(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_unapplied_drafts_dialog(
            parent,
            action="opening another project",
            draft_names=("Calibration", "Pipeline JSON"),
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "unappliedDraftsDialog")
        self.assertIn("opening another project", dialog.text())
        self.assertIn("Calibration", dialog.informativeText())
        self.assertIn("Pipeline JSON", dialog.informativeText())
        discard = dialog.findChild(QPushButton, "discardDraftsButton")
        keep = dialog.findChild(QPushButton, "keepEditingButton")
        self.assertIsNotNone(discard)
        self.assertIsNotNone(keep)
        self.assertEqual(discard.text(), "Discard Drafts")
        self.assertEqual(keep.text(), "Keep Editing")
        self.assertIs(dialog.defaultButton(), keep)

    def test_result_replacement_dialog_names_loss_and_defaults_to_keep_results(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_result_replacement_dialog(
            parent,
            task_name="red-dot-tracking.mp4",
            result_count=72,
            edit_count=1,
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "resultReplacementDialog")
        self.assertIn("red-dot-tracking.mp4", dialog.text())
        self.assertIn("72 current results", dialog.informativeText())
        self.assertIn("1 manual edit", dialog.informativeText())
        self.assertIn("cannot restore", dialog.informativeText())
        replace = dialog.findChild(QPushButton, "confirmResultReplacementButton")
        keep = dialog.findChild(QPushButton, "keepCurrentResultsButton")
        self.assertIsNotNone(replace)
        self.assertIsNotNone(keep)
        self.assertEqual(replace.text(), "Run + Replace Results/Edits")
        self.assertEqual(keep.text(), "Keep Current Results")
        self.assertIs(dialog.defaultButton(), keep)
        self.assertIs(dialog.escapeButton(), keep)
        self.assertIn("permanently replace", replace.accessibleDescription())
        self.assertIn("unchanged", keep.accessibleDescription())

    def test_rerun_replacement_dialog_scopes_tail_and_defaults_to_keep_it(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_rerun_replacement_dialog(
            parent,
            task_name="red-dot-tracking.mp4",
            anchor_frame=20,
            start_frame=21,
            result_count=51,
            affected_edit_count=1,
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "rerunReplacementDialog")
        self.assertIn("after frame 20", dialog.text())
        self.assertIn("red-dot-tracking.mp4", dialog.text())
        self.assertIn("Starting at frame 21", dialog.informativeText())
        self.assertIn("51 later results", dialog.informativeText())
        self.assertIn("1 active manual edit", dialog.informativeText())
        self.assertIn("marked superseded", dialog.informativeText())
        self.assertIn("earlier Results/Edits stay current", dialog.informativeText())
        replace = dialog.findChild(QPushButton, "confirmRerunReplacementButton")
        keep = dialog.findChild(QPushButton, "keepCurrentTailButton")
        self.assertIsNotNone(replace)
        self.assertIsNotNone(keep)
        self.assertEqual(replace.text(), "Rerun + Replace Later Results")
        self.assertEqual(keep.text(), "Keep Current Tail")
        self.assertIs(dialog.defaultButton(), keep)
        self.assertIs(dialog.escapeButton(), keep)
        self.assertIn("mark affected manual edits superseded", replace.accessibleDescription())
        self.assertIn("unchanged", keep.accessibleDescription())

    def test_config_result_protection_dialog_names_loss_and_defaults_to_keep_results(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_config_result_protection_dialog(
            parent,
            task_name="red-dot-tracking.mp4",
            result_count=72,
            edit_count=1,
        )
        self.addCleanup(dialog.close)

        self.assertEqual(dialog.objectName(), "configResultProtectionDialog")
        self.assertEqual(dialog.accessibleName(), "Confirm current tracking result replacement")
        self.assertIn("red-dot-tracking.mp4", dialog.text())
        self.assertIn("72 current results", dialog.informativeText())
        self.assertIn("1 manual edit", dialog.informativeText())
        self.assertIn("cannot restore", dialog.informativeText())
        replace = dialog.findChild(QPushButton, "confirmConfigResultReplacementButton")
        keep = dialog.findChild(QPushButton, "keepConfigCurrentResultsButton")
        self.assertIsNotNone(replace)
        self.assertIsNotNone(keep)
        self.assertEqual(replace.text(), "Apply + Replace Results/Edits")
        self.assertEqual(keep.text(), "Keep Current Results")
        self.assertIs(dialog.defaultButton(), keep)
        self.assertIs(dialog.escapeButton(), keep)
        self.assertIn("permanently replace", replace.accessibleDescription())
        self.assertIn("unchanged", keep.accessibleDescription())

    def test_config_result_protection_dialog_covers_outcome_only_state(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        dialog = build_config_result_protection_dialog(
            parent,
            task_name="untitled.mp4",
            result_count=0,
            edit_count=0,
        )
        self.addCleanup(dialog.close)

        self.assertIn("current tracking result summary", dialog.informativeText())
        self.assertIn("cannot restore", dialog.informativeText())


if __name__ == "__main__":
    unittest.main()
