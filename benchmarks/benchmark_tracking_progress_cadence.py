from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import numpy as np
from PySide6.QtCore import Qt

from neo_tracker.media import MediaReader, probe_media
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.tracking_worker import (
    DEFAULT_TRACKING_PROGRESS_INTERVAL_S,
    TrackingProgress,
    TrackingWorker,
)


class _Reader:
    def __init__(self) -> None:
        self.frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def read_frame(self, _frame_index: int) -> np.ndarray:
        return self.frame

    def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
        return self.frame

    def close(self) -> None:
        return None


class _Filter:
    def prime(self, _result: object) -> None:
        return None


class _Pipeline:
    def __init__(self) -> None:
        self.results: list[int] = []
        self.tracker_filter = _Filter()
        self.debug_history_max_bytes = 0

    def reset(self) -> None:
        self.results.clear()

    def process_frame(self, frame: np.ndarray, frame_index: int, _time_s: float) -> None:
        # Keep a tiny deterministic per-frame acquisition consumer without
        # masking the cost of obsolete progress callbacks on short fast jobs.
        if int(frame[0, 0, 0]) != 0:
            raise RuntimeError("unexpected benchmark frame")
        self.results.append(frame_index)

    def debug_history_usage(self) -> tuple[int, int]:
        return 0, 0


def _run_once(frames: int, progress_interval_s: float) -> tuple[float, int, list[int]]:
    pipeline = _Pipeline()
    worker = TrackingWorker(
        pipeline=pipeline,  # type: ignore[arg-type]
        reader_factory=_Reader,
        frame_count=frames,
        fps=30.0,
        prefetch_frames=0,
        progress_interval_s=progress_interval_s,
    )
    progress_count = [0]
    rendered_text = [""]

    def render_progress(progress: TrackingProgress) -> None:
        progress_count[0] += 1
        rendered_text[0] = (
            f"Frame {progress.completed}/{progress.total} · "
            f"Throughput {progress.throughput_fps:.1f} fps · "
            f"Input {progress.input_ms_per_frame:.2f} ms/f · "
            f"Compute {progress.processing_ms_per_frame:.2f} ms/f"
        )

    worker.progress.connect(render_progress, Qt.ConnectionType.DirectConnection)
    started = perf_counter()
    worker.run()
    elapsed = perf_counter() - started
    if not rendered_text[0] or pipeline.results != list(range(frames)):
        raise RuntimeError("tracking cadence benchmark lost terminal state")
    return elapsed, progress_count[0], pipeline.results


def benchmark(frames: int, rounds: int) -> dict[str, object]:
    frames = max(2, int(frames))
    rounds = max(1, int(rounds))
    samples: dict[str, list[float]] = {"legacy": [], "current": []}
    counts: dict[str, list[int]] = {"legacy": [], "current": []}
    results: dict[str, list[int]] = {}
    for round_index in range(rounds):
        order = (
            (("legacy", 0.0), ("current", DEFAULT_TRACKING_PROGRESS_INTERVAL_S))
            if round_index % 2 == 0
            else (("current", DEFAULT_TRACKING_PROGRESS_INTERVAL_S), ("legacy", 0.0))
        )
        for label, interval in order:
            elapsed, progress_count, tracked = _run_once(frames, interval)
            samples[label].append(elapsed)
            counts[label].append(progress_count)
            results[label] = tracked

    legacy_s = median(samples["legacy"])
    current_s = median(samples["current"])
    legacy_count = int(median(counts["legacy"]))
    current_count = int(median(counts["current"]))
    return {
        "frames": frames,
        "rounds": rounds,
        "progress_interval_ms": DEFAULT_TRACKING_PROGRESS_INTERVAL_S * 1000.0,
        "legacy_progress_events": legacy_count,
        "current_progress_events": current_count,
        "progress_event_reduction_percent": (
            100.0 * (legacy_count - current_count) / legacy_count if legacy_count else 0.0
        ),
        "legacy_elapsed_ms": legacy_s * 1000.0,
        "current_elapsed_ms": current_s * 1000.0,
        "elapsed_speedup": legacy_s / current_s if current_s > 0.0 else 0.0,
        "results_exact": results.get("legacy") == results.get("current") == list(range(frames)),
    }


def _run_media_once(
    media_path: Path,
    progress_interval_s: float,
) -> tuple[float, int, list[object]]:
    info = probe_media(str(media_path))
    if not info.available or info.kind != "video":
        raise RuntimeError(info.error or f"Could not benchmark video: {media_path}")
    pipeline = color_marker_preset()
    worker = TrackingWorker(
        pipeline=pipeline,
        reader_factory=lambda: MediaReader(str(media_path)),
        frame_count=info.frame_count,
        fps=info.fps,
        prefetch_frames=1,
        progress_interval_s=progress_interval_s,
    )
    progress_count = [0]
    rendered_text = [""]

    def render_progress(progress: TrackingProgress) -> None:
        progress_count[0] += 1
        rendered_text[0] = (
            f"Frame {progress.completed}/{progress.total} · "
            f"Throughput {progress.throughput_fps:.1f} fps · "
            f"Input {progress.input_ms_per_frame:.2f} ms/f · "
            f"Compute {progress.processing_ms_per_frame:.2f} ms/f"
        )

    worker.progress.connect(render_progress, Qt.ConnectionType.DirectConnection)
    started = perf_counter()
    worker.run()
    elapsed = perf_counter() - started
    if not rendered_text[0] or len(pipeline.results) != info.frame_count:
        raise RuntimeError("media cadence benchmark lost terminal state")
    return elapsed, progress_count[0], list(pipeline.results)


def _result_error(legacy: list[object], current: list[object]) -> tuple[float, bool]:
    if len(legacy) != len(current):
        return float("inf"), False
    maximum_error = 0.0
    statuses_match = True
    for old, new in zip(legacy, current):
        old_status = getattr(old, "status", None)
        new_status = getattr(new, "status", None)
        statuses_match = statuses_match and old_status == new_status
        old_state = getattr(old, "filtered_state", {})
        new_state = getattr(new, "filtered_state", {})
        for key in set(old_state) | set(new_state):
            maximum_error = max(
                maximum_error,
                abs(float(old_state.get(key, 0.0)) - float(new_state.get(key, 0.0))),
            )
    return maximum_error, statuses_match


def benchmark_media(media_path: Path, rounds: int) -> dict[str, object]:
    media_path = media_path.expanduser().resolve()
    if not media_path.exists():
        raise FileNotFoundError(media_path)
    rounds = max(1, int(rounds))
    samples: dict[str, list[float]] = {"legacy": [], "current": []}
    counts: dict[str, list[int]] = {"legacy": [], "current": []}
    results: dict[str, list[object]] = {}
    for round_index in range(rounds):
        order = (
            (("legacy", 0.0), ("current", DEFAULT_TRACKING_PROGRESS_INTERVAL_S))
            if round_index % 2 == 0
            else (("current", DEFAULT_TRACKING_PROGRESS_INTERVAL_S), ("legacy", 0.0))
        )
        for label, interval in order:
            elapsed, progress_count, tracked = _run_media_once(media_path, interval)
            samples[label].append(elapsed)
            counts[label].append(progress_count)
            results[label] = tracked

    legacy_s = median(samples["legacy"])
    current_s = median(samples["current"])
    legacy_count = int(median(counts["legacy"]))
    current_count = int(median(counts["current"]))
    maximum_error, statuses_match = _result_error(results["legacy"], results["current"])
    return {
        "media": str(media_path),
        "frames": len(results["current"]),
        "rounds": rounds,
        "legacy_progress_events": legacy_count,
        "current_progress_events": current_count,
        "progress_event_reduction_percent": (
            100.0 * (legacy_count - current_count) / legacy_count if legacy_count else 0.0
        ),
        "legacy_elapsed_ms": legacy_s * 1000.0,
        "current_elapsed_ms": current_s * 1000.0,
        "elapsed_speedup": legacy_s / current_s if current_s > 0.0 else 0.0,
        "max_filtered_state_error": maximum_error,
        "statuses_match": statuses_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare stride-only and time-bounded tracking progress event cadence."
    )
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--rounds", type=int, default=31)
    parser.add_argument("--synthetic-rounds", type=int, default=301)
    args = parser.parse_args()
    print(
        json.dumps(
            {
                "progress_interval_ms": DEFAULT_TRACKING_PROGRESS_INTERVAL_S * 1000.0,
                "repository_media": benchmark_media(args.media, args.rounds),
                "synthetic_fast_path": benchmark(args.frames, args.synthetic_rounds),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
