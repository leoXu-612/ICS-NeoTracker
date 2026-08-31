from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from neo_tracker.ui.action_registry import ActionRegistry
from neo_tracker.ui.view_state import ViewState


class ActionRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_button_and_action_share_one_handler_and_view_state(self) -> None:
        parent = QWidget()
        button = QPushButton("Run", parent)
        calls: list[str] = []
        registry = ActionRegistry(parent)
        action = registry.register("tracking.run", "Run", lambda: calls.append("run"))
        registry.bind_button("tracking.run", button)

        disabled = registry.snapshot_view_state().with_action(
            "tracking.run",
            enabled=False,
            text="Cancel",
            tool_tip="Cancel tracking",
        )
        registry.apply_view_state(disabled)

        self.assertFalse(action.isEnabled())
        self.assertFalse(button.isEnabled())
        self.assertEqual(action.text(), "Cancel")
        self.assertEqual(button.text(), "Cancel")
        self.assertEqual(button.toolTip(), "Cancel tracking")

        enabled = disabled.with_action("tracking.run", enabled=True)
        registry.apply_view_state(enabled)
        button.click()
        action.trigger()
        self.assertEqual(calls, ["run", "run"])

    def test_unknown_and_duplicate_command_keys_are_rejected(self) -> None:
        parent = QWidget()
        registry = ActionRegistry(parent)
        registry.register("project.open", "Open", lambda: None)

        with self.assertRaises(KeyError):
            registry.register("project.open", "Again", lambda: None)
        with self.assertRaises(KeyError):
            registry.action("missing")

    def test_blocked_action_keeps_latest_view_state_for_restore(self) -> None:
        parent = QWidget()
        button = QPushButton("Run", parent)
        registry = ActionRegistry(parent)
        action = registry.register("tracking.run", "Run", lambda: None)
        registry.bind_button("tracking.run", button)
        disabled = registry.snapshot_view_state().with_action(
            "tracking.run",
            enabled=False,
        )
        registry.apply_view_state(disabled)

        registry.set_actions_blocked(("tracking.run",), True)
        updated = disabled.with_action(
            "tracking.run",
            enabled=True,
            text="Run updated",
        )
        registry.apply_view_state(updated)

        self.assertFalse(action.isEnabled())
        self.assertFalse(button.isEnabled())
        self.assertEqual(action.text(), "Run updated")
        registry.set_actions_blocked(("tracking.run",), False)
        self.assertTrue(action.isEnabled())
        self.assertTrue(button.isEnabled())


if __name__ == "__main__":
    unittest.main()
