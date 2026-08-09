from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from neo_tracker.ui.edit_history_panel import EditHistoryPanel, EditHistorySelection


def edit_entry(event_type: str, frame_index: int | None, minute: int) -> dict[str, object]:
    entry: dict[str, object] = {
        "time": f"2026-07-13T12:{minute:02d}:00Z",
        "type": event_type,
        "details": {},
    }
    if frame_index is not None:
        entry["frame_index"] = frame_index
    return entry


class EditHistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        QCoreApplication.processEvents()

    def test_filter_counts_selection_and_jump_use_original_sequence(self) -> None:
        panel = EditHistoryPanel()
        self.addCleanup(panel.close)
        records = [
            edit_entry("manual_correction", 12, 1),
            edit_entry("mark_lost", 18, 2),
            edit_entry("rerun_after", 18, 3),
            edit_entry("plugin_note", None, 4),
        ]
        panel.set_records(records)

        self.assertEqual(panel.event_filter.itemText(0), "All edits (4)")
        self.assertEqual(panel.event_filter.itemText(1), "Corrected (1)")
        self.assertEqual(panel.event_filter.itemText(2), "Marked lost (1)")
        self.assertEqual(panel.event_filter.itemText(3), "Rerun (1)")
        self.assertEqual(panel.event_filter.itemText(4), "Other (1)")
        self.assertIn("#4", panel.list_widget.item(0).text())

        jumped: list[int] = []
        panel.jumpRequested.connect(jumped.append)
        self.assertTrue(panel.select_sequence(2))
        self.assertEqual(panel.selected_edit(), EditHistorySelection(2, records[1]))
        self.assertTrue(panel.jump_button.isEnabled())
        panel.jump_button.click()
        self.assertEqual(jumped, [18])
        self.assertIn("Edit #2 · frame 18", panel.status_label.text())

        self.assertTrue(panel.select_sequence(4))
        self.assertFalse(panel.jump_button.isEnabled())
        self.assertIn("Edit #4 has no frame", panel.status_label.text())

    def test_filter_and_export_emit_only_visible_entries(self) -> None:
        panel = EditHistoryPanel()
        self.addCleanup(panel.close)
        records = [
            edit_entry("manual_correction", 12, 1),
            edit_entry("mark_lost", 18, 2),
            edit_entry("manual_correction", 24, 3),
        ]
        panel.set_records(records)
        panel.event_filter.setCurrentIndex(panel.event_filter.findData("manual_correction"))
        QCoreApplication.processEvents()

        self.assertEqual([selection.sequence for selection in panel.visible_edits], [1, 3])
        self.assertEqual(panel.list_widget.count(), 2)
        self.assertEqual(panel.status_label.text(), "Showing 2 of 3 · Select an edit to jump")

        exported: list[tuple[EditHistorySelection, ...]] = []
        panel.exportRequested.connect(exported.append)
        panel.export_button.click()
        self.assertEqual([[selection.sequence for selection in group] for group in exported], [[1, 3]])

    def test_superseded_edit_is_muted_and_named_as_historical(self) -> None:
        panel = EditHistoryPanel()
        self.addCleanup(panel.close)
        current = edit_entry("manual_correction", 20, 1)
        superseded = edit_entry("mark_lost", 50, 2)
        superseded["superseded_at"] = "2026-07-14T10:00:00Z"
        superseded["superseded_by_rerun_start_frame"] = 21
        panel.set_records([current, superseded])

        superseded_item = panel.list_widget.item(0)
        self.assertIn("superseded by rerun from frame 21", superseded_item.text())
        self.assertEqual(superseded_item.background().color().name(), "#f0f1f3")
        self.assertEqual(superseded_item.foreground().color().name(), "#646970")
        self.assertIn("1 superseded", panel.status_label.text())

        self.assertTrue(panel.select_sequence(2))
        self.assertIn("historical", panel.status_label.text())


if __name__ == "__main__":
    unittest.main()
