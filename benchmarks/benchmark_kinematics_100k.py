from __future__ import annotations

import argparse
import gc
import json
import sys
import tempfile
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
    uniform_quadratic,
)
from neo_tracker.kinematics.derivatives import derive_series
from neo_tracker.kinematics.export import export_csv, export_markdown, export_npz
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.runtime import CancellationToken
from neo_tracker.kinematics.series import TrackingSeriesBuilder, snapshot_tracker_results
from neo_tracker.kinematics.types import DerivativeConfig, FitRequest, FitStatus


def benchmark(sample_count: int, iterations: int) -> dict[str, object]:
    count = max(100, int(sample_count))
    rounds = max(1, int(iterations))
    fixture = uniform_linear(count)
    live_results = fixture.tracker_results()
    rss_live = current_rss_mb()
    snapshot_started = perf_counter()
    snapshot = snapshot_tracker_results(live_results)
    snapshot_ms = (perf_counter() - snapshot_started) * 1_000.0
    rss_snapshot = current_rss_mb()
    builder = TrackingSeriesBuilder(units={"x_world": "m"})

    built_value, build_timing = timing_summary(
        lambda: builder.build_series(snapshot, source_revision=FIXTURE_REVISION),
        iterations=rounds,
    )
    built = tuple(built_value)  # type: ignore[arg-type]
    source = next(item for item in built if item.series_id == "filtered_state:x_world")
    rss_series = current_rss_mb()
    first_config = DerivativeConfig(
        "nonuniform_finite_difference",
        1,
        edge_policy="one_sided",
    )
    second_config = DerivativeConfig(
        "nonuniform_finite_difference",
        2,
        edge_policy="one_sided",
    )

    def derivative_operation():
        return (
            derive_series(source, first_config),
            derive_series(source, second_config),
        )

    derivative_value, derivative_timing = timing_summary(
        derivative_operation,
        iterations=rounds,
    )
    first, second = derivative_value  # type: ignore[misc]
    linear_request = FitRequest(
        source.series_id,
        "linear",
        float(source.time_s[0]),
        float(source.time_s[-1]),
        source.source_revision,
    )
    quadratic_fixture = uniform_quadratic(count)
    quadratic_series = quadratic_fixture.sample_series()
    quadratic_request = FitRequest(
        quadratic_series.series_id,
        "quadratic",
        float(quadratic_series.time_s[0]),
        float(quadratic_series.time_s[-1]),
        quadratic_series.source_revision,
    )

    def fit_operation():
        return (
            fit_series(source, linear_request),
            fit_series(quadratic_series, quadratic_request),
        )

    fit_value, fit_timing = timing_summary(fit_operation, iterations=rounds)
    linear_fit, quadratic_fit = fit_value  # type: ignore[misc]

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _, csv_timing = timing_summary(
            lambda: export_csv(root / "analysis.csv", source, linear_fit),
            iterations=1,
        )
        _, npz_timing = timing_summary(
            lambda: export_npz(root / "analysis.bundle", source, linear_fit),
            iterations=1,
        )
        _, markdown_timing = timing_summary(
            lambda: export_markdown(root / "analysis.md", source, linear_fit),
            iterations=1,
        )
        export_sizes = {
            "csv_bytes": (root / "analysis.csv").stat().st_size,
            "npz_bytes": (root / "analysis.bundle").stat().st_size,
            "markdown_bytes": (root / "analysis.md").stat().st_size,
        }

    def pipeline_operation():
        local_source = next(
            item
            for item in builder.build_series(snapshot, source_revision=FIXTURE_REVISION)
            if item.series_id == "filtered_state:x_world"
        )
        local_first = derive_series(local_source, first_config)
        local_fit = fit_series(
            local_source,
            FitRequest(
                local_source.series_id,
                "linear",
                float(local_source.time_s[0]),
                float(local_source.time_s[-1]),
                local_source.source_revision,
            ),
        )
        return local_source, local_first, local_fit

    _, pipeline_timing = timing_summary(
        pipeline_operation,
        iterations=max(1, min(3, rounds)),
    )
    rss_repeats: list[float] = []
    for _ in range(max(3, min(5, rounds))):
        repeated = pipeline_operation()
        del repeated
        gc.collect()
        rss_repeats.append(current_rss_mb())
    rss_growth = rss_repeats[-1] - rss_repeats[0]

    series_cancel, series_terminal = cancellation_latency_ms(
        lambda: builder.build_series(
            live_results,
            source_revision=FIXTURE_REVISION,
            cancellation=CancelAfterChecks(2),
        )
    )
    derivative_cancel, derivative_terminal = cancellation_latency_ms(
        lambda: derive_series(source, first_config, cancellation=CancelAfterChecks(2))
    )
    fit_token = CancellationToken()
    fit_token.cancel()
    fit_cancel, fit_terminal = cancellation_latency_ms(
        lambda: fit_series(source, linear_request, cancellation=fit_token)
    )
    export_token = CancellationToken()
    export_token.cancel()
    with tempfile.TemporaryDirectory() as directory:
        export_cancel, export_terminal = cancellation_latency_ms(
            lambda: export_npz(
                Path(directory) / "cancelled.npz",
                source,
                linear_fit,
                cancellation=export_token,
            )
        )
    cancellation = {
        "series": {"latency_ms": series_cancel, "terminal": series_terminal},
        "derivative": {"latency_ms": derivative_cancel, "terminal": derivative_terminal},
        "fit": {"latency_ms": fit_cancel, "terminal": fit_terminal},
        "export": {"latency_ms": export_cancel, "terminal": export_terminal},
    }
    maximum_cancel = max(item["latency_ms"] for item in cancellation.values())
    slope_error = float(abs(linear_fit.parameters[0] - fixture.parameters["slope"]))
    acceleration_error = float(np.max(np.abs(second.values[second.valid_mask])))
    quadratic_error = float(
        np.max(
            np.abs(
                quadratic_fit.parameters
                - np.array(
                    [
                        quadratic_fixture.parameters["a"],
                        quadratic_fixture.parameters["b"],
                        quadratic_fixture.parameters["c"],
                    ]
                )
            )
        )
    )
    accuracy_error = max(slope_error, acceleration_error, quadratic_error)
    gates = {
        "series_aligned": len(source) == count and bool(np.all(source.valid_mask)),
        "fit_terminal_ok": (
            linear_fit.status is FitStatus.OK and quadratic_fit.status is FitStatus.OK
        ),
        "accuracy": accuracy_error < 1e-5,
        "build_median_under_2500_ms": build_timing["median"] < 2_500.0,
        "derivative_median_under_1000_ms": derivative_timing["median"] < 1_000.0,
        "fit_median_under_1000_ms": fit_timing["median"] < 1_000.0,
        "pipeline_median_under_3500_ms": pipeline_timing["median"] < 3_500.0,
        "csv_under_5000_ms": csv_timing["max"] < 5_000.0,
        "npz_under_5000_ms": npz_timing["max"] < 5_000.0,
        "cancellation_under_500_ms": maximum_cancel < 500.0,
        "rss_repeat_growth_under_64_mb": rss_growth < 64.0,
        "snapshot_delta_under_128_mb": rss_snapshot - rss_live < 128.0,
        "pipeline_peak_under_512_mb": peak_rss_mb() < 512.0,
    }
    passed = all(gates.values())
    return {
        "schema": "neo-tracker.kinematics-benchmark/v1",
        "workload": "end_to_end_100k_engine_pipeline",
        "sample_count": count,
        "method": "snapshot+series+derivatives+fits+exports",
        "iterations": rounds,
        "timing_scope": (
            "hot series-build+first-derivative+linear-fit pipeline; excludes the one-time "
            "live-result snapshot and exports reported under stage_timing_ms"
        ),
        "timing_ms": pipeline_timing,
        "stage_timing_ms": {
            "snapshot": {"median": snapshot_ms, "p95": snapshot_ms, "max": snapshot_ms},
            "series_build": build_timing,
            "derivatives": derivative_timing,
            "fits": fit_timing,
            "csv_export": csv_timing,
            "npz_export": npz_timing,
            "markdown_export": markdown_timing,
        },
        "peak_rss_mb": peak_rss_mb(),
        "rss_stage_mb": {
            "live_tracker_results": rss_live,
            "immutable_snapshot": rss_snapshot,
            "final_series": rss_series,
        },
        "rss_repeat_mb": rss_repeats,
        "rss_repeat_growth_mb": rss_growth,
        "result_digest": digest_arrays(
            source.frame_indices,
            source.time_s,
            source.values,
            first.values,
            second.values,
            linear_fit.parameters,
            quadratic_fit.parameters,
        ),
        "accuracy_error": accuracy_error,
        "accuracy": {
            "linear_slope_abs": slope_error,
            "linear_acceleration_max_abs": acceleration_error,
            "quadratic_parameter_max_abs": quadratic_error,
        },
        "cancellation_latency_ms": maximum_cancel,
        "cancellation": cancellation,
        "export_sizes": export_sizes,
        "gates": gates,
        "passed": passed,
        "exit_code": 0 if passed else 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the complete 100k kinematics engine path.")
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
