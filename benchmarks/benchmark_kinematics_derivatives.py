from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from benchmarks.kinematics_fixtures import (
    CancelAfterChecks,
    cancellation_latency_ms,
    digest_arrays,
    peak_rss_mb,
    timing_summary,
    uniform_quadratic,
    vfr_quadratic,
)
from neo_tracker.kinematics.derivatives import derive_series
from neo_tracker.kinematics.types import DerivativeConfig


def benchmark(sample_count: int, iterations: int) -> dict[str, object]:
    count = max(11, int(sample_count))
    vfr = vfr_quadratic(count)
    vfr_source = vfr.sample_series()
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

    def nonuniform_operation():
        return (
            derive_series(vfr_source, first_config),
            derive_series(vfr_source, second_config),
        )

    nonuniform_value, timing = timing_summary(nonuniform_operation, iterations=iterations)
    first, second = nonuniform_value  # type: ignore[misc]
    expected_first = 2.0 * vfr.parameters["a"] * vfr.time_s + vfr.parameters["b"]
    expected_second = np.full(count, 2.0 * vfr.parameters["a"], dtype=np.float64)
    nonuniform_accuracy = max(
        float(np.max(np.abs(first.values - expected_first))),
        float(np.max(np.abs(second.values - expected_second))),
    )

    uniform = uniform_quadratic(count)
    uniform_source = uniform.sample_series()
    savgol_first = DerivativeConfig(
        "savgol_uniform",
        1,
        edge_policy="one_sided",
        window_length=11,
        polyorder=3,
    )
    savgol_second = DerivativeConfig(
        "savgol_uniform",
        2,
        edge_policy="one_sided",
        window_length=11,
        polyorder=3,
    )

    def savgol_operation():
        return (
            derive_series(uniform_source, savgol_first),
            derive_series(uniform_source, savgol_second),
        )

    savgol_value, savgol_timing = timing_summary(savgol_operation, iterations=iterations)
    smooth_first, smooth_second = savgol_value  # type: ignore[misc]
    expected_uniform_first = (
        2.0 * uniform.parameters["a"] * uniform.time_s + uniform.parameters["b"]
    )
    expected_uniform_second = np.full(count, 2.0 * uniform.parameters["a"])
    savgol_accuracy = max(
        float(np.max(np.abs(smooth_first.values - expected_uniform_first))),
        float(np.max(np.abs(smooth_second.values - expected_uniform_second))),
    )
    cancellation_latency, cancellation_terminal = cancellation_latency_ms(
        lambda: derive_series(
            vfr_source,
            first_config,
            cancellation=CancelAfterChecks(2),
        )
    )
    accuracy_error = max(nonuniform_accuracy, savgol_accuracy)
    gates = {
        "nonuniform_accuracy": nonuniform_accuracy < 1e-5,
        "savgol_accuracy": savgol_accuracy < 1e-4,
        "median_under_1000_ms": timing["median"] < 1_000.0,
        "savgol_median_under_1000_ms": savgol_timing["median"] < 1_000.0,
        "cancellation_under_250_ms": cancellation_latency < 250.0,
    }
    passed = all(gates.values())
    return {
        "schema": "neo-tracker.kinematics-benchmark/v1",
        "workload": "first_and_second_derivative",
        "sample_count": count,
        "method": "nonuniform_finite_difference+savgol_uniform",
        "iterations": max(1, int(iterations)),
        "timing_ms": timing,
        "savgol_timing_ms": savgol_timing,
        "peak_rss_mb": peak_rss_mb(),
        "result_digest": digest_arrays(
            first.values,
            second.values,
            smooth_first.values,
            smooth_second.values,
        ),
        "accuracy_error": accuracy_error,
        "accuracy": {
            "nonuniform_max_abs": nonuniform_accuracy,
            "savgol_max_abs": savgol_accuracy,
        },
        "cancellation_latency_ms": cancellation_latency,
        "cancellation_terminal": cancellation_terminal,
        "gates": gates,
        "passed": passed,
        "exit_code": 0 if passed else 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark VFR and Savitzky-Golay derivatives.")
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
