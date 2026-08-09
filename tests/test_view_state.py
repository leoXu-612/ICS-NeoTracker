from __future__ import annotations

import unittest

from neo_tracker.ui.view_state import ActionViewState, ViewState, ViewStateStore


class ViewStateTests(unittest.TestCase):
    def test_action_updates_are_immutable_and_preserve_other_commands(self) -> None:
        initial = ViewState.from_mapping(
            {
                "project.open": ActionViewState(True, "Open", "Open a project"),
                "tracking.run": ActionViewState(True, "Run", "Run tracking"),
            }
        )

        updated = initial.with_action("tracking.run", enabled=False, text="Cancel")

        self.assertTrue(initial.action("tracking.run").enabled)
        self.assertEqual(updated.action("tracking.run").text, "Cancel")
        self.assertFalse(updated.action("tracking.run").enabled)
        self.assertEqual(updated.action("project.open"), initial.action("project.open"))
        with self.assertRaises(TypeError):
            updated.actions["new"] = ActionViewState(True, "New", "")  # type: ignore[index]

    def test_store_notifies_once_for_a_semantic_change_and_skips_noop(self) -> None:
        state = ViewState.from_mapping(
            {"project.save": ActionViewState(True, "Save", "Save project")}
        )
        store = ViewStateStore(state)
        observed: list[ViewState] = []
        store.subscribe(observed.append)

        store.update_action("project.save", enabled=False)
        store.update_action("project.save", enabled=False)

        self.assertEqual(len(observed), 1)
        self.assertFalse(store.state.action("project.save").enabled)


if __name__ == "__main__":
    unittest.main()
