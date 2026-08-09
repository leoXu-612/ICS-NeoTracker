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

from neo_tracker.analysis import SignalSeries, _infer_sample_rate, _result_series_value, tracking_series
from neo_tracker.core import TrackerResult


def _fixture(result_count: int, *, sparse: bool) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x": float(index)},
            filtered_state={
                "x": float("nan") if sparse and index % 100 == 0 else float(index),
            },
            confidence=1.0,
            status="ok",
        )
        for index in range(int(result_count))
    ]


def _previous_tracking_series(results: list[TrackerResult]) -> SignalSeries:
    result_items = list(results)
    time_s = np.asarray([result.time_s for result in result_items], dtype=float)
    values = np.asarray([_result_series_value(result, "x") for result in result_items], dtype=float)
    series = SignalSeries(
        name="x",
        time_s=time_s,
        values=values,
        sample_rate_hz=_infer_sample_rate(time_s),
        unit="px",
        source_type="tracking",
        metadata={"key": "x"},
    )
    clean_time_s = np.asarray(series.time_s, dtype=float)
    clean_values = np.asarray(series.values, dtype=float)
    finite = np.isfinite(clean_time_s) & np.isfinite(clean_values)
    return SignalSeries(
        name=series.name,
        time_s=clean_time_s[finite],
        values=clean_values[finite],
        sample_rate_hz=series.sample_rate_hz,
        unit=series.unit,
        source_type=series.source_type,
        metadata=dict(series.metadata),
    )


def _current_tracking_series(
    results: list[TrackerResult],
    sample_rate_hz: float,
) -> SignalSeries:
    return tracking_series(results, "x", unit="px", sample_rate_hz=sample_rate_hz)


def _measure(operation, rounds: int) -> tuple[SignalSeries, float, int]:
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


def _case(result_count: int, rounds: int, *, sparse: bool) -> dict[str, object]:
    results = _fixture(result_count, sparse=sparse)
    discovered_sample_rate_hz = _infer_sample_rate(
        np.asarray([result.time_s for result in results], dtype=float)
    )
    previous, previous_ms, previous_peak = _measure(
        lambda: _previous_tracking_series(results),
        rounds,
    )
    current, current_ms, current_peak = _measure(
        lambda: _current_tracking_series(results, discovered_sample_rate_hz),
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
        "sample_count": int(current.values.size),
        "previous_median_ms": previous_ms,
        "current_median_ms": current_ms,
        "speedup": previous_ms / current_ms,
        "time_reduction_percent": 100.0 * (previous_ms - current_ms) / previous_ms,
        "previous_python_peak_bytes": previous_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": 100.0 * (previous_peak - current_peak) / previous_peak,
        "parity": bool(parity),
    }


def benchmark(result_count: int, rounds: int) -> dict[str, object]:
    return {
        "result_count": int(result_count),
        "rounds": max(3, int(rounds)),
        "dense": _case(result_count, rounds, sparse=False),
        "sparse_one_percent_nonfinite": _case(result_count, rounds, sparse=True),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark selected tracking-series materialization with dense and sparse data."
    )
    parser.add_argument("--results", type=int, default=100_000)
    parser.add_argument("--rounds", type=int, default=15)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
