from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.shell.bindings import PRIMARY_BUTTON_ATTRIBUTES


class ApplicationShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(self) -> NeoTrackerWindow:
        window = NeoTrackerWindow()
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        self.addCleanup(window.close)
        return window

    def test_primary_buttons_and_menus_share_registry_actions(self) -> None:
        window = self.make_window()

        self.assertEqual(set(window.action_registry.keys), set(PRIMARY_BUTTON_ATTRIBUTES))
        menus = [
            window.menuBar().findChild(QMenu, menu_name)
            for menu_name in ("fileMenu", "runMenu", "reviewMenu")
        ]
        self.assertTrue(all(menu is not None for menu in menus))
        menu_actions = {action for menu in menus if menu is not None for action in menu.actions()}
        for key, button_attribute in PRIMARY_BUTTON_ATTRIBUTES.items():
            action = window.action_registry.action(key)
            button = getattr(window, button_attribute)
            self.assertIn(action, menu_actions)
            self.assertEqual(action.isEnabled(), button.isEnabled())
            self.assertEqual(action.text(), button.text())

    def test_view_state_drives_button_and_action_and_both_trigger_one_command(self) -> None:
        window = self.make_window()
        window._update_action(
            "tracking.run",
            enabled=True,
            text="Cancel",
            tool_tip="Cancel tracking",
        )

        action = window.action_registry.action("tracking.run")
        self.assertTrue(window.view_state.action("tracking.run").enabled)
        self.assertEqual(action.text(), "Cancel")
        self.assertEqual(window.run_tracking_button.text(), "Cancel")

        with patch.object(window, "_run_tracking") as run_tracking:
            window.run_tracking_button.click()
            action.trigger()
        self.assertEqual(run_tracking.call_count, 2)
        QCoreApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
