from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.core import TrackerResult, TrackingPipeline
from neo_tracker.presets import color_marker_preset


def _result(frame_index: int) -> TrackerResult:
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 30.0,
        state={"x": float(frame_index), "y": 0.0},
        filtered_state={"x": float(frame_index), "y": 0.0},
        confidence=1.0,
        status="ok",
        debug={
            "response_map": np.ones((4, 4), dtype=np.float32),
            "response_origin": (0, 0),
            "response_frame_shape": (4, 4),
            "debug_layers": {},
        },
    )


def _pipeline(frame_count: int) -> TrackingPipeline:
    pipeline = color_marker_preset()
    pipeline.debug_history_limit = frame_count
    pipeline.debug_history_max_bytes = frame_count * 4 * 4 * np.dtype(np.float32).itemsize
    pipeline.reset()
    return pipeline


def _run_incremental(frame_count: int) -> tuple[float, tuple[int, int]]:
    pipeline = _pipeline(frame_count)
    started = perf_counter()
    for frame_index in range(frame_count):
        pipeline._append_result(_result(frame_index))
        usage = pipeline.debug_history_usage()
    return perf_counter() - started, usage


def _run_scanning_reference(frame_count: int) -> tuple[float, tuple[int, int]]:
    pipeline = _pipeline(frame_count)
    results = pipeline.results
    frame_limit = pipeline.debug_history_limit
    byte_limit = pipeline.debug_history_max_bytes
    started = perf_counter()
    for frame_index in range(frame_count):
        result = _result(frame_index)
        results.append(result)
        stale_index = len(results) - frame_limit - 1
        if stale_index >= 0:
            pipeline._drop_heavy_debug_arrays(results[stale_index])
        retained_frames = 0
        retained_bytes = 0
        for retained in reversed(results[-frame_limit:]):
            result_bytes = pipeline._heavy_debug_array_bytes(retained)
            if result_bytes <= 0:
                continue
            within_budget = retained_frames == 0 or retained_bytes + result_bytes <= byte_limit
            if byte_limit > 0 and within_budget:
                retained_frames += 1
                retained_bytes += result_bytes
            else:
                pipeline._drop_heavy_debug_arrays(retained)
        usage_bytes = 0
        usage_frames = 0
        for retained in results[-frame_limit:]:
            result_bytes = pipeline._heavy_debug_array_bytes(retained)
            if result_bytes > 0:
                usage_bytes += result_bytes
                usage_frames += 1
        usage = (usage_bytes, usage_frames)
    return perf_counter() - started, usage


def benchmark(frame_counts: list[int], rounds: int) -> dict[str, object]:
    cases: list[dict[str, float | int | bool]] = []
    for frame_count in frame_counts:
        incremental_samples: list[float] = []
        scanning_samples: list[float] = []
        exact_usage = True
        for round_index in range(max(1, rounds)):
            runners = (
                (_run_incremental, incremental_samples),
                (_run_scanning_reference, scanning_samples),
            )
            if round_index % 2:
                runners = tuple(reversed(runners))
            usages: dict[str, tuple[int, int]] = {}
            for runner, samples in runners:
                elapsed, usage = runner(frame_count)
                samples.append(elapsed)
                usages[runner.__name__] = usage
            exact_usage = exact_usage and usages["_run_incremental"] == usages["_run_scanning_reference"]
        incremental = median(incremental_samples)
        scanning = median(scanning_samples)
        cases.append(
            {
                "frames": frame_count,
                "incremental_elapsed_s": incremental,
                "scanning_reference_elapsed_s": scanning,
                "speedup": scanning / incremental if incremental > 0 else 0.0,
                "usage_exact": exact_usage,
            }
        )
    return {
        "rounds": max(1, rounds),
        "cases": cases,
        "limits": (
            "Synthetic append/usage hot path with a history limit equal to frame count. "
            "The scanning reference reproduces the previous repeated full-window retention and usage scans."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark incremental heavy-debug history accounting.")
    parser.add_argument("--frames", type=int, nargs="+", default=[1000, 2000, 4000])
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(benchmark([max(1, value) for value in args.frames], args.rounds), indent=2))


if __name__ == "__main__":
    main()
