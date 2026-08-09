from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from time import monotonic


@dataclass(frozen=True)
class PlaybackTick:
    frame_index: int
    skipped_frames: int
    skipped_total: int
    reached_end: bool


class PlaybackClock:
    """Map elapsed wall time to source frames without timer-backlog drift."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._started_at: float | None = None
        self._start_frame = 0
        self._max_frame = 0
        self._fps = 30.0
        self._skipped_total = 0

    @property
    def active(self) -> bool:
        return self._started_at is not None

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def skipped_total(self) -> int:
        return self._skipped_total

    def start(self, *, current_frame: int, frame_count: int, fps: float) -> None:
        self._start_frame = max(0, int(current_frame))
        self._max_frame = max(0, int(frame_count) - 1)
        self._fps = self.normalized_fps(fps)
        self._skipped_total = 0
        self._started_at = float(self._clock())

    def stop(self) -> None:
        self._started_at = None

    def tick(self, current_frame: int) -> PlaybackTick:
        current = max(0, int(current_frame))
        if self._started_at is None:
            return PlaybackTick(current, 0, self._skipped_total, current >= self._max_frame)
        elapsed = max(0.0, float(self._clock()) - self._started_at)
        # Avoid losing a whole interval when a precise timer fires a fraction
        # of a millisecond before the nominal boundary.
        elapsed_frames = int(math.floor(elapsed * self._fps + 0.05))
        target = min(self._max_frame, self._start_frame + elapsed_frames)
        if target <= current:
            return PlaybackTick(current, 0, self._skipped_total, current >= self._max_frame)
        skipped = max(0, target - current - 1)
        self._skipped_total += skipped
        return PlaybackTick(target, skipped, self._skipped_total, target >= self._max_frame)

    @classmethod
    def timer_interval_ms(cls, fps: float) -> int:
        interval = int(round(1000.0 / cls.normalized_fps(fps)))
        return max(10, min(2_000_000_000, interval))

    @staticmethod
    def normalized_fps(fps: float) -> float:
        try:
            value = float(fps)
        except (TypeError, ValueError):
            return 30.0
        return value if math.isfinite(value) and value > 0.0 else 30.0
