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

from neo_tracker.analysis import SignalSeries, _infer_sample_rate, _result_series_value
from neo_tracker.core import TrackerResult
from neo_tracker.ui.analysis_controller import AnalysisController, AnalysisSource


def _fixture(result_count: int) -> tuple[list[TrackerResult], dict[str, str]]:
    results: list[TrackerResult] = []
    for index in range(int(result_count)):
        filtered_state = {f"s{key_index}": float(index + key_index) for key_index in range(6)}
        if index % 997 == 0:
            filtered_state["s2"] = float("nan")
        velocity = {f"v{key_index}": float(key_index - index) for key_index in range(3)}
        results.append(
            TrackerResult(
                frame_index=index,
                time_s=index / 120.0,
                state=dict(filtered_state),
                filtered_state=filtered_state,
                confidence=1.0,
                status="ok",
                debug={"filter": {"velocity": velocity}},
            )
        )
    units = {f"s{key_index}": "px" for key_index in range(6)}
    units.update({f"v{key_index}": "px/s" for key_index in range(3)})
    return results, units


def _previous_tracking_series(results: list[TrackerResult], key: str, unit: str) -> SignalSeries:
    results = list(results)
    time_s = np.asarray([result.time_s for result in results], dtype=float)
    values = np.asarray([_result_series_value(result, key) for result in results], dtype=float)
    series = SignalSeries(
        name=key,
        time_s=time_s,
        values=values,
        sample_rate_hz=_infer_sample_rate(time_s),
        unit=unit,
        source_type="tracking",
        metadata={"key": key},
    )
    clean_time = np.asarray(series.time_s, dtype=float)
    clean_values = np.asarray(series.values, dtype=float)
    finite = np.isfinite(clean_time) & np.isfinite(clean_values)
    return SignalSeries(
        name=series.name,
        time_s=clean_time[finite],
        values=clean_values[finite],
        sample_rate_hz=series.sample_rate_hz,
        unit=series.unit,
        source_type=series.source_type,
        metadata=dict(series.metadata),
    )


def _previous_available_sources(
    results: list[TrackerResult],
    units: dict[str, str],
) -> list[AnalysisSource]:
    keys: set[str] = set()
    for result in results:
        keys.update(result.filtered_state.keys())
        keys.update(result.debug.get("filter", {}).get("velocity", {}).keys())
    sources: list[AnalysisSource] = []
    for key in sorted(keys):
        unit = AnalysisController.tracking_unit_for_key(key, units)
        series = _previous_tracking_series(results, key, unit)
        suffix = f" ({unit})" if unit else ""
        sources.append(
            AnalysisSource(
                kind="tracking",
                label=f"Tracking: {key}{suffix}",
                key=key,
                unit=unit,
                sample_rate_hz=series.sample_rate_hz,
                sample_count=int(series.values.size),
            )
        )
    return sources


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


def _source_summary(sources: list[AnalysisSource]) -> list[tuple[str, float, int]]:
    return [(source.key, source.sample_rate_hz, source.sample_count) for source in sources]


def benchmark(result_count: int, rounds: int) -> dict[str, object]:
    results, units = _fixture(result_count)
    previous_sources, previous_source_ms, previous_source_peak = _measure(
        lambda: _previous_available_sources(results, units),
        rounds,
    )
    current_sources, current_source_ms, current_source_peak = _measure(
        lambda: AnalysisController.available_sources(results, units, None, None),
        rounds,
    )
    cached_controller = AnalysisController()
    first_cached_sources, first_cached_ms, first_cached_peak = _measure(
        lambda: AnalysisController().available_sources_cached(results, units, None, None),
        rounds,
    )
    cached_controller.available_sources_cached(results, units, None, None)
    repeated_cached_sources, repeated_cached_ms, repeated_cached_peak = _measure(
        lambda: cached_controller.available_sources_cached(results, units, None, None),
        rounds,
    )
    stale_before_invalidation = True
    present_after_invalidation = True
    if results:
        results[len(results) // 2].filtered_state["cache_refresh_probe"] = 1.0
        stale_sources = cached_controller.available_sources_cached(results, units, None, None)
        cached_controller.invalidate_source_cache()
        invalidated_sources = cached_controller.available_sources_cached(results, units, None, None)
        stale_before_invalidation = "cache_refresh_probe" not in {
            source.key for source in stale_sources
        }
        present_after_invalidation = "cache_refresh_probe" in {
            source.key for source in invalidated_sources
        }
    return {
        "result_count": int(result_count),
        "signal_count": len(current_sources),
        "rounds": max(3, int(rounds)),
        "source_discovery": {
            "previous_median_ms": previous_source_ms,
            "current_median_ms": current_source_ms,
            "speedup": previous_source_ms / current_source_ms,
            "time_reduction_percent": 100.0
            * (previous_source_ms - current_source_ms)
            / previous_source_ms,
            "previous_python_peak_bytes": previous_source_peak,
            "current_python_peak_bytes": current_source_peak,
            "python_peak_reduction_percent": 100.0
            * (previous_source_peak - current_source_peak)
            / previous_source_peak,
            "parity": _source_summary(previous_sources) == _source_summary(current_sources),
        },
        "first_cached_scan": {
            "uncached_median_ms": current_source_ms,
            "cached_api_median_ms": first_cached_ms,
            "uncached_python_peak_bytes": current_source_peak,
            "cached_api_python_peak_bytes": first_cached_peak,
            "parity": _source_summary(current_sources) == _source_summary(first_cached_sources),
        },
        "unchanged_refresh": {
            "previous_uncached_median_ms": current_source_ms,
            "current_cached_median_ms": repeated_cached_ms,
            "speedup": current_source_ms / repeated_cached_ms,
            "time_reduction_percent": 100.0
            * (current_source_ms - repeated_cached_ms)
            / current_source_ms,
            "previous_python_peak_bytes": current_source_peak,
            "current_python_peak_bytes": repeated_cached_peak,
            "python_peak_reduction_percent": 100.0
            * (current_source_peak - repeated_cached_peak)
            / current_source_peak,
            "parity": _source_summary(current_sources) == _source_summary(repeated_cached_sources),
        },
        "in_place_edit_recovery": {
            "stale_before_explicit_invalidation": stale_before_invalidation,
            "present_after_explicit_invalidation": present_after_invalidation,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark tracking-signal source metadata discovery."
    )
    parser.add_argument("--results", type=int, default=100_000)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
