from __future__ import annotations

import unittest

from neo_tracker.ui.background_tasks import BackgroundTaskCoordinator, BackgroundTaskToken


class BackgroundTaskCoordinatorTests(unittest.TestCase):
    def test_generations_reject_duplicate_start_and_stale_finish(self) -> None:
        coordinator = BackgroundTaskCoordinator()

        first = coordinator.start("tracking")
        self.assertEqual(first, BackgroundTaskToken("tracking", 1))
        self.assertIsNone(coordinator.start("tracking"))
        self.assertTrue(coordinator.is_current(first))
        self.assertFalse(coordinator.finish(BackgroundTaskToken("tracking", 0)))
        self.assertTrue(coordinator.is_current(first))

        self.assertTrue(coordinator.finish(first))
        second = coordinator.start("tracking")
        self.assertEqual(second, BackgroundTaskToken("tracking", 2))
        self.assertFalse(coordinator.is_current(first))
        self.assertTrue(coordinator.is_current(second))

    def test_close_gate_waits_for_active_tasks_and_blocks_new_starts(self) -> None:
        coordinator = BackgroundTaskCoordinator()
        tracking = coordinator.start("tracking")
        analysis = coordinator.start("analysis")
        self.assertIsNotNone(tracking)
        self.assertIsNotNone(analysis)

        self.assertEqual(coordinator.begin_close(), ("tracking", "analysis"))
        self.assertTrue(coordinator.closing)
        self.assertFalse(coordinator.ready_to_close)
        self.assertIsNone(coordinator.start("review-response"))

        self.assertTrue(coordinator.finish(tracking))
        self.assertFalse(coordinator.ready_to_close)
        self.assertTrue(coordinator.finish(analysis))
        self.assertTrue(coordinator.ready_to_close)

    def test_empty_task_kind_is_rejected(self) -> None:
        coordinator = BackgroundTaskCoordinator()
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            coordinator.start("  ")

    def test_idle_close_gate_can_reopen_after_user_cancels(self) -> None:
        coordinator = BackgroundTaskCoordinator()
        self.assertEqual(coordinator.begin_close(), ())
        self.assertTrue(coordinator.closing)

        self.assertTrue(coordinator.cancel_close())

        self.assertFalse(coordinator.closing)
        self.assertIsNotNone(coordinator.start("tracking"))


if __name__ == "__main__":
    unittest.main()
