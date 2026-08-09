from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from benchmarks.kinematics_fixtures import (
    FIXTURE_REVISION,
    CancelAfterChecks,
    cancellation_latency_ms,
    current_rss_mb,
    digest_arrays,
    peak_rss_mb,
    timing_summary,
    uniform_linear,
)
from neo_tracker.kinematics.series import TrackingSeriesBuilder, snapshot_tracker_results


def benchmark(sample_count: int, iterations: int) -> dict[str, object]:
    count = max(2, int(sample_count))
    fixture = uniform_linear(count)
    velocity = np.full(count, fixture.parameters["slope"], dtype=np.float64)
    live_results = fixture.tracker_results(
        velocity_key="v_x_world",
        velocity_values=velocity,
    )
    rss_live_input = current_rss_mb()
    snapshot_started = perf_counter()
    snapshot = snapshot_tracker_results(live_results)
    snapshot_ms = (perf_counter() - snapshot_started) * 1_000.0
    rss_immutable_snapshot = current_rss_mb()
    builder = TrackingSeriesBuilder(units={"x_world": "m"})
    built_value, timing = timing_summary(
        lambda: builder.build_series(snapshot, source_revision=FIXTURE_REVISION),
        iterations=iterations,
    )
    built = tuple(built_value)  # type: ignore[arg-type]
    rss_final_series = current_rss_mb()
    by_id = {series.series_id: series for series in built}
    filtered = by_id["filtered_state:x_world"]
    velocity_series = by_id["filter_velocity:v_x_world"]
    accuracy_error = max(
        float(np.max(np.abs(filtered.values - fixture.values))),
        float(np.max(np.abs(velocity_series.values - velocity))),
    )
    cancellation_latency, cancellation_terminal = cancellation_latency_ms(
        lambda: builder.build_series(
            live_results,
            source_revision=FIXTURE_REVISION,
            cancellation=CancelAfterChecks(2),
        )
    )
    gates = {
        "aligned": len(filtered) == count and bool(np.all(filtered.valid_mask)),
        "accuracy": accuracy_error <= 1e-12,
        "median_under_2500_ms": timing["median"] < 2_500.0,
        "cancellation_under_250_ms": cancellation_latency < 250.0,
        "snapshot_delta_under_128_mb": rss_immutable_snapshot - rss_live_input < 128.0,
        "pipeline_peak_under_512_mb": peak_rss_mb() < 512.0,
    }
    passed = all(gates.values())
    return {
        "schema": "neo-tracker.kinematics-benchmark/v1",
        "workload": "tracker_result_snapshot_to_base_series",
        "sample_count": count,
        "method": "immutable_snapshot+aligned_series",
        "iterations": max(1, int(iterations)),
        "timing_ms": timing,
        "snapshot_ms": snapshot_ms,
        "peak_rss_mb": peak_rss_mb(),
        "rss_stage_mb": {
            "live_tracker_results": rss_live_input,
            "immutable_snapshot": rss_immutable_snapshot,
            "final_series": rss_final_series,
        },
        "result_digest": digest_arrays(
            filtered.frame_indices,
            filtered.time_s,
            filtered.values,
            filtered.valid_mask,
            velocity_series.values,
        ),
        "accuracy_error": accuracy_error,
        "cancellation_latency_ms": cancellation_latency,
        "cancellation_terminal": cancellation_terminal,
        "series_count": len(built),
        "gates": gates,
        "passed": passed,
        "exit_code": 0 if passed else 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark immutable TrackerResult series construction.")
    parser.add_argument("--samples", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    result = benchmark(arguments.samples, arguments.iterations)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if arguments.enforce and result["exit_code"]:
        raise SystemExit(int(result["exit_code"]))


if __name__ == "__main__":
    main()
