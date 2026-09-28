from __future__ import annotations

import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tests.integration_kinematics_support import pump_until


class IntegrationWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_queued_followup_job_does_not_turn_transient_idle_into_failure(self) -> None:
        events = []
        busy = False

        def finish():
            nonlocal busy
            events.append("finished")
            busy = False

        def start():
            nonlocal busy
            busy = True
            events.append("started")
            QTimer.singleShot(10, finish)

        QTimer.singleShot(0, start)
        pump_until(lambda: not busy, timeout_s=1.0)
        self.assertEqual(events, ["started", "finished"])

    def test_unmet_predicate_still_fails_at_deadline(self) -> None:
        with self.assertRaisesRegex(AssertionError, "expected state"):
            pump_until(lambda: False, timeout_s=0.01)


if __name__ == "__main__":
    unittest.main()
