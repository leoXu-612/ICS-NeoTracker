from __future__ import annotations

import unittest

from neo_tracker.ui.playback_controller import PlaybackClock


class PlaybackClockTests(unittest.TestCase):
    def test_timer_interval_preserves_slow_source_rates(self) -> None:
        self.assertEqual(PlaybackClock.timer_interval_ms(1.0), 1000)
        self.assertEqual(PlaybackClock.timer_interval_ms(0.5), 2000)
        self.assertEqual(PlaybackClock.timer_interval_ms(20.0), 50)

    def test_elapsed_time_catches_up_without_source_timeline_drift(self) -> None:
        now = [100.0]
        playback = PlaybackClock(clock=lambda: now[0])
        playback.start(current_frame=0, frame_count=300, fps=30.0)

        now[0] += 0.67
        tick = playback.tick(current_frame=0)

        self.assertEqual(tick.frame_index, 20)
        self.assertEqual(tick.skipped_frames, 19)
        self.assertEqual(tick.skipped_total, 19)
        self.assertFalse(tick.reached_end)

    def test_tick_does_not_advance_early_and_clamps_at_source_end(self) -> None:
        now = [10.0]
        playback = PlaybackClock(clock=lambda: now[0])
        playback.start(current_frame=7, frame_count=10, fps=20.0)

        now[0] += 0.02
        early = playback.tick(current_frame=7)
        self.assertEqual(early.frame_index, 7)
        self.assertEqual(early.skipped_total, 0)

        now[0] += 1.0
        ended = playback.tick(current_frame=7)
        self.assertEqual(ended.frame_index, 9)
        self.assertEqual(ended.skipped_frames, 1)
        self.assertTrue(ended.reached_end)

    def test_invalid_source_rate_uses_documented_fallback(self) -> None:
        self.assertEqual(PlaybackClock.normalized_fps(0.0), 30.0)
        self.assertEqual(PlaybackClock.normalized_fps(float("nan")), 30.0)
        self.assertEqual(PlaybackClock.normalized_fps("invalid"), 30.0)


if __name__ == "__main__":
    unittest.main()
