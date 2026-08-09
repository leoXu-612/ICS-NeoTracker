from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.core import DEFAULT_DEBUG_HISTORY_MAX_BYTES
from neo_tracker.presets import color_marker_preset
from neo_tracker.roi import RectangularROI


def _run_case(
    frame: np.ndarray,
    *,
    frames: int,
    rounds: int,
    history_limit: int,
    byte_limit: int,
) -> dict[str, float | int]:
    height, width = frame.shape[:2]
    elapsed_samples: list[float] = []
    peak_samples: list[int] = []
    retained_samples: list[int] = []
    retained_frame_samples: list[int] = []
    for _ in range(max(1, int(rounds))):
        pipeline = color_marker_preset(
            roi=RectangularROI(0.0, 0.0, float(width), float(height)),
            tolerance=0.08,
        )
        pipeline.debug_history_limit = max(1, int(history_limit))
        pipeline.debug_history_max_bytes = max(0, int(byte_limit))
        tracemalloc.start()
        started = perf_counter()
        for frame_index in range(max(1, int(frames))):
            pipeline.process_frame(frame, frame_index, frame_index / 30.0)
        elapsed_samples.append(perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        retained_bytes, retained_frames = pipeline.debug_history_usage()
        peak_samples.append(int(peak))
        retained_samples.append(int(retained_bytes))
        retained_frame_samples.append(int(retained_frames))
    frame_count = max(1, int(frames))
    return {
        "retained_frames": int(median(retained_frame_samples)),
        "retained_heavy_bytes": int(median(retained_samples)),
        "python_peak_bytes": int(median(peak_samples)),
        "elapsed_ms_per_frame": 1000.0 * median(elapsed_samples) / frame_count,
    }


def benchmark(width: int, height: int, frames: int, rounds: int) -> dict[str, object]:
    width = max(1, int(width))
    height = max(1, int(height))
    frames = max(1, int(frames))
    history_limit = 4
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    unconstrained_limit = history_limit * width * height * 16
    frame_only = _run_case(
        frame,
        frames=frames,
        rounds=rounds,
        history_limit=history_limit,
        byte_limit=unconstrained_limit,
    )
    bounded = _run_case(
        frame,
        frames=frames,
        rounds=rounds,
        history_limit=history_limit,
        byte_limit=DEFAULT_DEBUG_HISTORY_MAX_BYTES,
    )
    frame_only_peak = int(frame_only["python_peak_bytes"])
    bounded_peak = int(bounded["python_peak_bytes"])
    frame_only_retained = int(frame_only["retained_heavy_bytes"])
    bounded_retained = int(bounded["retained_heavy_bytes"])
    return {
        "resolution": f"{width}x{height}",
        "frames": frames,
        "rounds": max(1, int(rounds)),
        "history_limit": history_limit,
        "byte_budget": DEFAULT_DEBUG_HISTORY_MAX_BYTES,
        "frame_count_only_reference": frame_only,
        "byte_bounded": bounded,
        "retained_reduction_percent": (
            100.0 * (frame_only_retained - bounded_retained) / frame_only_retained
            if frame_only_retained > 0
            else 0.0
        ),
        "python_peak_reduction_percent": (
            100.0 * (frame_only_peak - bounded_peak) / frame_only_peak
            if frame_only_peak > 0
            else 0.0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark resolution-aware heavy Review-data retention.")
    parser.add_argument("--width", type=int, default=3840)
    parser.add_argument("--height", type=int, default=2160)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.frames, args.rounds), indent=2))


if __name__ == "__main__":
    main()
