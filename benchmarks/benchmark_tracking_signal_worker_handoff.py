from __future__ import annotations

import argparse
import gc
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

from neo_tracker.analysis import SignalSeries, tracking_series
from neo_tracker.core import TrackerResult


def _fixture(result_count: int) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x": float(index)},
            filtered_state={"x": float(index)},
            confidence=1.0,
            status="ok",
        )
        for index in range(int(result_count))
    ]


def _previous_ui_prepare(results: list[TrackerResult]) -> SignalSeries:
    return tracking_series(results, "x", unit="px", sample_rate_hz=120.0)


def _current_ui_handoff(results: list[TrackerResult]) -> tuple[TrackerResult, ...]:
    return tuple(results)


def _current_total_prepare(results: list[TrackerResult]) -> SignalSeries:
    snapshot = _current_ui_handoff(results)
    return tracking_series(snapshot, "x", unit="px", sample_rate_hz=120.0)


def _measure(operation, rounds: int):
    operation()
    elapsed: list[float] = []
    for _ in range(max(3, int(rounds))):
        gc.collect()
        started = perf_counter()
        result = operation()
        elapsed.append((perf_counter() - started) * 1000.0)
        del result
    gc.collect()
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, median(elapsed), int(peak)


def benchmark(result_count: int, rounds: int) -> dict[str, object]:
    results = _fixture(result_count)
    previous, previous_ui_ms, previous_ui_peak = _measure(
        lambda: _previous_ui_prepare(results),
        rounds,
    )
    snapshot, current_ui_ms, current_ui_peak = _measure(
        lambda: _current_ui_handoff(results),
        rounds,
    )
    current, current_total_ms, current_total_peak = _measure(
        lambda: _current_total_prepare(results),
        rounds,
    )
    parity = (
        previous.name == current.name
        and previous.unit == current.unit
        and previous.source_type == current.source_type
        and previous.metadata == current.metadata
        and previous.sample_rate_hz == current.sample_rate_hz
        and np.array_equal(previous.time_s, current.time_s)
        and np.array_equal(previous.values, current.values)
    )
    return {
        "result_count": int(result_count),
        "rounds": max(3, int(rounds)),
        "previous_ui_stall_median_ms": previous_ui_ms,
        "current_ui_handoff_median_ms": current_ui_ms,
        "ui_stall_speedup": previous_ui_ms / current_ui_ms,
        "ui_stall_reduction_percent": 100.0 * (previous_ui_ms - current_ui_ms) / previous_ui_ms,
        "previous_ui_python_peak_bytes": previous_ui_peak,
        "current_ui_python_peak_bytes": current_ui_peak,
        "ui_python_peak_reduction_percent": 100.0 * (previous_ui_peak - current_ui_peak) / previous_ui_peak,
        "current_total_prepare_median_ms": current_total_ms,
        "current_total_python_peak_bytes": current_total_peak,
        "current_snapshot_result_count": len(snapshot),
        "parity": bool(parity),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the GUI-thread handoff for tracking Signal preparation."
    )
    parser.add_argument("--results", type=int, default=500_000)
    parser.add_argument("--rounds", type=int, default=15)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
