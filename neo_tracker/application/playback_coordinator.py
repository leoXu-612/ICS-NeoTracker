from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from neo_tracker.ui.playback_controller import PlaybackClock, PlaybackTick


class PlaybackCoordinator(QObject):
    """Own source-time playback state and its UI-agnostic timer lifecycle."""

    advance_requested = Signal()
    state_changed = Signal(str)

    def __init__(self, *, clock: PlaybackClock | None = None) -> None:
        super().__init__()
        self._clock = clock or PlaybackClock()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.advance_requested)
        self._generation = 0
        self._closing = False

    @property
    def timer(self) -> QTimer:
        return self._timer

    @property
    def clock(self) -> PlaybackClock:
        return self._clock

    @clock.setter
    def clock(self, value: PlaybackClock) -> None:
        if self.active:
            self.stop()
        self._clock = value

    @property
    def busy(self) -> bool:
        return self.active

    @property
    def active(self) -> bool:
        return self._timer.isActive() or self._clock.active

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def current_generation(self) -> int:
        return self._generation

    @property
    def fps(self) -> float:
        return self._clock.fps

    @property
    def skipped_total(self) -> int:
        return self._clock.skipped_total

    def start(self, *, current_frame: int, frame_count: int, fps: float) -> bool:
        if self._closing:
            return False
        self._generation += 1
        self._clock.start(
            current_frame=current_frame,
            frame_count=frame_count,
            fps=fps,
        )
        self._timer.start(self.timer_interval_ms(fps))
        self.state_changed.emit("playing")
        return True

    def tick(self, current_frame: int) -> PlaybackTick:
        return self._clock.tick(current_frame)

    def cancel(self, reason: str = "canceled") -> bool:
        del reason
        return self.stop()

    def stop(self) -> bool:
        was_active = self.active
        if self._timer.isActive():
            self._timer.stop()
        self._clock.stop()
        if was_active:
            self.state_changed.emit("idle")
        return was_active

    def close(self) -> None:
        self._closing = True
        self.stop()
        self.state_changed.emit("closed")

    @staticmethod
    def timer_interval_ms(fps: float) -> int:
        return PlaybackClock.timer_interval_ms(fps)
