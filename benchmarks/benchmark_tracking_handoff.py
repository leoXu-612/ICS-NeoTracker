from __future__ import annotations

import argparse
import json
from pathlib import Path
from queue import Empty, Queue
from statistics import median
import sys
from threading import Event, Semaphore, Thread
from time import perf_counter

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.ui.tracking_worker import PrefetchedFrameStream, _PrefetchedFrame


class _ReusableFrameReader:
    def __init__(self) -> None:
        self.frame = np.zeros((1, 1, 3), dtype=np.uint8)

    def read_frame(self, _frame_index: int) -> np.ndarray:
        return self.frame

    def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
        return self.frame

    def close(self) -> None:
        return None


class _QueueSemaphoreBaseline:
    """Previous handoff implementation retained for same-process comparison."""

    def __init__(
        self,
        *,
        reader_factory,
        start_frame: int,
        frame_count: int,
        cancel_requested: Event,
        prefetch_frames: int,
    ) -> None:
        self.reader_factory = reader_factory
        self.start_frame = int(start_frame)
        self.frame_count = int(frame_count)
        self.cancel_requested = cancel_requested
        self.prefetch_frames = max(1, int(prefetch_frames))
        self._queue: Queue[_PrefetchedFrame] = Queue(maxsize=self.prefetch_frames)
        self._slots = Semaphore(self.prefetch_frames)
        self._stop_requested = Event()
        self._finished = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(target=self._produce, daemon=True)
        self._thread.start()

    def read(self, frame_index: int) -> tuple[np.ndarray, float] | None:
        while not self._should_stop():
            try:
                prefetched = self._queue.get(timeout=0.02)
            except Empty:
                if self._finished.is_set():
                    raise RuntimeError("Baseline input prefetch ended early")
                continue
            self._slots.release()
            if prefetched.frame_index != frame_index or prefetched.frame is None:
                raise RuntimeError("Baseline input prefetch order mismatch")
            return prefetched.frame, prefetched.input_s
        return None

    def close(self) -> None:
        self._stop_requested.set()
        if self._thread is not None:
            self._thread.join()

    def _should_stop(self) -> bool:
        return self.cancel_requested.is_set() or self._stop_requested.is_set()

    def _reserve_slot(self) -> bool:
        while not self._should_stop():
            if self._slots.acquire(timeout=0.02):
                return True
        return False

    def _produce(self) -> None:
        reader = self.reader_factory()
        try:
            read = getattr(reader, "read_frame_for_processing", reader.read_frame)
            for frame_index in range(self.start_frame, self.frame_count):
                if not self._reserve_slot():
                    return
                started = perf_counter()
                frame = read(frame_index)
                self._queue.put(
                    _PrefetchedFrame(frame_index, frame, perf_counter() - started)
                )
        finally:
            reader.close()
            self._finished.set()


def _measure(stream_type, frame_count: int, prefetch_frames: int) -> float:
    cancel_requested = Event()
    stream = stream_type(
        reader_factory=_ReusableFrameReader,
        start_frame=0,
        frame_count=frame_count,
        cancel_requested=cancel_requested,
        prefetch_frames=prefetch_frames,
    )
    started = perf_counter()
    stream.start()
    try:
        for frame_index in range(frame_count):
            prefetched = stream.read(frame_index)
            if prefetched is None:
                raise RuntimeError(f"Handoff stopped at frame {frame_index}")
    finally:
        stream.close()
    return perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare bounded tracking handoff synchronization overhead."
    )
    parser.add_argument("--frames", type=int, default=100_000)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--prefetch-frames", type=int, default=1)
    args = parser.parse_args()
    frame_count = max(1, int(args.frames))
    rounds = max(1, int(args.rounds))
    prefetch_frames = max(1, int(args.prefetch_frames))

    _measure(_QueueSemaphoreBaseline, 1_000, prefetch_frames)
    _measure(PrefetchedFrameStream, 1_000, prefetch_frames)
    baseline: list[float] = []
    current: list[float] = []
    for round_index in range(rounds):
        ordered = (
            ((_QueueSemaphoreBaseline, baseline), (PrefetchedFrameStream, current))
            if round_index % 2 == 0
            else ((PrefetchedFrameStream, current), (_QueueSemaphoreBaseline, baseline))
        )
        for stream_type, samples in ordered:
            samples.append(_measure(stream_type, frame_count, prefetch_frames))
    baseline_s = median(baseline)
    current_s = median(current)
    print(
        json.dumps(
            {
                "frames": frame_count,
                "rounds": rounds,
                "prefetch_frames": prefetch_frames,
                "baseline_s": baseline_s,
                "current_s": current_s,
                "baseline_us_per_frame": 1_000_000.0 * baseline_s / frame_count,
                "current_us_per_frame": 1_000_000.0 * current_s / frame_count,
                "speedup": baseline_s / current_s if current_s > 0.0 else 0.0,
                "reduction_percent": (
                    100.0 * (baseline_s - current_s) / baseline_s if baseline_s > 0.0 else 0.0
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
