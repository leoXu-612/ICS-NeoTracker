from __future__ import annotations

import unittest

from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.ui.background_tasks import BackgroundTaskCoordinator


class TaskSupervisorTests(unittest.TestCase):
    def test_start_finish_and_stale_token_contract(self) -> None:
        supervisor = TaskSupervisor()

        first = supervisor.start("tracking")
        self.assertEqual(first, BackgroundTaskToken("tracking", 1))
        self.assertEqual(supervisor.current_token("tracking"), first)
        self.assertEqual(supervisor.generation_for("tracking"), 1)
        self.assertIsNone(supervisor.start("tracking"))

        stale = BackgroundTaskToken("tracking", 0)
        self.assertFalse(supervisor.finish(stale))
        self.assertTrue(supervisor.is_current(first))
        self.assertTrue(supervisor.finish(first))
        self.assertIsNone(supervisor.current_token("tracking"))

        second = supervisor.start("tracking")
        self.assertEqual(second, BackgroundTaskToken("tracking", 2))
        self.assertFalse(supervisor.is_current(first))
        self.assertTrue(supervisor.is_current(second))

    def test_close_gate_is_idempotent_and_only_reopens_when_idle(self) -> None:
        supervisor = TaskSupervisor()
        tracking = supervisor.start("tracking")
        analysis = supervisor.start("analysis")

        self.assertEqual(supervisor.begin_close(), ("tracking", "analysis"))
        self.assertEqual(supervisor.begin_close(), ("tracking", "analysis"))
        self.assertTrue(supervisor.closing)
        self.assertFalse(supervisor.ready_to_close)
        self.assertIsNone(supervisor.start("preview-decode"))
        self.assertFalse(supervisor.cancel_close())

        self.assertTrue(supervisor.finish(tracking))
        self.assertTrue(supervisor.finish(analysis))
        self.assertTrue(supervisor.ready_to_close)
        self.assertTrue(supervisor.cancel_close())
        self.assertFalse(supervisor.closing)

    def test_legacy_background_coordinator_is_a_compatible_supervisor(self) -> None:
        coordinator = BackgroundTaskCoordinator()

        self.assertIsInstance(coordinator, TaskSupervisor)
        token = coordinator.start("project-open")
        self.assertEqual(token, BackgroundTaskToken("project-open", 1))
        self.assertTrue(coordinator.finish(token))


if __name__ == "__main__":
    unittest.main()
