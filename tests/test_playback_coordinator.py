from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from neo_tracker.application.playback_coordinator import PlaybackCoordinator
from neo_tracker.ui.playback_controller import PlaybackClock


class PlaybackCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Use the most capable Qt application type so this module can run before
        # widget-based coordinator tests in the same unittest process.  A
        # QCoreApplication cannot later be upgraded to QApplication, and Qt
        # aborts if those later tests construct a QWidget.
        cls.app = QApplication.instance() or QApplication([])

    def test_source_time_tick_and_idempotent_stop(self) -> None:
        now = [100.0]
        coordinator = PlaybackCoordinator(clock=PlaybackClock(clock=lambda: now[0]))

        self.assertTrue(
            coordinator.start(current_frame=0, frame_count=40, fps=30.0)
        )
        self.assertTrue(coordinator.active)
        now[0] += 0.67

        tick = coordinator.tick(0)

        self.assertEqual(tick.frame_index, 20)
        self.assertEqual(tick.skipped_total, 19)
        self.assertTrue(coordinator.stop())
        self.assertFalse(coordinator.stop())
        self.assertFalse(coordinator.active)

    def test_close_blocks_restart_and_preserves_slow_source_interval(self) -> None:
        coordinator = PlaybackCoordinator()

        self.assertEqual(coordinator.timer_interval_ms(1.0), 1000)
        coordinator.close()

        self.assertTrue(coordinator.closing)
        self.assertFalse(
            coordinator.start(current_frame=0, frame_count=8, fps=1.0)
        )


if __name__ == "__main__":
    unittest.main()
