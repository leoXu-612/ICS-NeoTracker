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
    exponential,
    peak_rss_mb,
    sinusoidal,
    timing_summary,
    uniform_linear,
    uniform_quadratic,
)
from neo_tracker.kinematics import fitting
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.types import FitRequest, FitStatus, SampleSeries


def _request(series: SampleSeries, model: str) -> FitRequest:
    return FitRequest(
        series.series_id,
        model,
        float(series.time_s[0]),
        float(series.time_s[-1]),
        series.source_revision,
    )


def _parameter_error(result, expected: list[float]) -> float | None:
    if result.status is not FitStatus.OK:
        return None
    return float(np.max(np.abs(result.parameters - np.asarray(expected, dtype=np.float64))))


def benchmark(sample_count: int, iterations: int, sinusoid_guess_samples: int) -> dict[str, object]:
    count = max(11, int(sample_count))
    linear_fixture = uniform_linear(count)
    quadratic_fixture = uniform_quadratic(count)
    linear_series = linear_fixture.sample_series()
    quadratic_series = quadratic_fixture.sample_series()

    def polynomial_operation():
        return (
            fit_series(linear_series, _request(linear_series, "linear")),
            fit_series(quadratic_series, _request(quadratic_series, "quadratic")),
        )

    polynomial_value, timing = timing_summary(polynomial_operation, iterations=iterations)
    linear_result, quadratic_result = polynomial_value  # type: ignore[misc]
    linear_error = _parameter_error(
        linear_result,
        [linear_fixture.parameters["slope"], linear_fixture.parameters["intercept"]],
    )
    quadratic_error = _parameter_error(
        quadratic_result,
        [
            quadratic_fixture.parameters["a"],
            quadratic_fixture.parameters["b"],
            quadratic_fixture.parameters["c"],
        ],
    )

    exponential_fixture = exponential()
    exponential_series = exponential_fixture.sample_series()
    exponential_result = fit_series(
        exponential_series,
        _request(exponential_series, "exponential"),
    )
    sinusoidal_fixture = sinusoidal()
    sinusoidal_series = sinusoidal_fixture.sample_series()
    sinusoidal_result = fit_series(
        sinusoidal_series,
        _request(sinusoidal_series, "sinusoidal"),
    )
    exponential_error = _parameter_error(
        exponential_result,
        [
            exponential_fixture.parameters["amplitude"],
            exponential_fixture.parameters["rate"],
            exponential_fixture.parameters["offset"],
        ],
    )
    sinusoidal_error = _parameter_error(
        sinusoidal_result,
        [
            sinusoidal_fixture.parameters["amplitude"],
            sinusoidal_fixture.parameters["omega"],
            sinusoidal_fixture.parameters["phase"],
            sinusoidal_fixture.parameters["offset"],
        ],
    )

    guess_count = max(100, int(sinusoid_guess_samples))
    guess_fixture = sinusoidal(guess_count)
    _, guess_timing = timing_summary(
        lambda: fitting._initial_sinusoidal(guess_fixture.time_s, guess_fixture.values, None),
        iterations=max(1, min(3, int(iterations))),
    )
    guess_cancellation_latency, guess_cancellation_terminal = cancellation_latency_ms(
        lambda: fitting._initial_sinusoidal(
            guess_fixture.time_s,
            guess_fixture.values,
            CancelAfterChecks(2),
        )
    )
    errors = [
        value
        for value in (linear_error, quadratic_error, exponential_error, sinusoidal_error)
        if value is not None
    ]
    accuracy_error = max(errors, default=None)
    nonlinear_available = (
        exponential_result.status is FitStatus.OK and sinusoidal_result.status is FitStatus.OK
    )
    gates = {
        "linear_accuracy": linear_error is not None and linear_error < 1e-8,
        "quadratic_accuracy": quadratic_error is not None and quadratic_error < 1e-7,
        "builtin_exponential_guess": (
            exponential_result.status is FitStatus.UNAVAILABLE
            or (exponential_error is not None and exponential_error < 1e-5)
        ),
        "builtin_sinusoidal_guess": (
            sinusoidal_result.status is FitStatus.UNAVAILABLE
            or (sinusoidal_error is not None and sinusoidal_error < 1e-5)
        ),
        "median_under_1000_ms": timing["median"] < 1_000.0,
        "sinusoid_guess_100k_under_5000_ms": (
            guess_count < 100_000 or guess_timing["median"] < 5_000.0
        ),
        "guess_cancellation_under_500_ms": guess_cancellation_latency < 500.0,
    }
    passed = all(gates.values())
    digest_values = [linear_result.parameters, quadratic_result.parameters]
    if exponential_result.status is FitStatus.OK:
        digest_values.append(exponential_result.parameters)
    if sinusoidal_result.status is FitStatus.OK:
        digest_values.append(sinusoidal_result.parameters)
    return {
        "schema": "neo-tracker.kinematics-benchmark/v1",
        "workload": "linear_quadratic_and_builtin_nonlinear_fits",
        "sample_count": count,
        "method": "centered_lstsq+scipy_least_squares_optional",
        "iterations": max(1, int(iterations)),
        "timing_ms": timing,
        "sinusoid_guess": {
            "sample_count": guess_count,
            "frequency_candidates": 256,
            "timing_ms": guess_timing,
            "cancellation_latency_ms": guess_cancellation_latency,
            "cancellation_terminal": guess_cancellation_terminal,
        },
        "peak_rss_mb": peak_rss_mb(),
        "result_digest": digest_arrays(*digest_values),
        "accuracy_error": accuracy_error,
        "accuracy": {
            "linear_parameter_max_abs": linear_error,
            "quadratic_parameter_max_abs": quadratic_error,
            "exponential_no_initial_max_abs": exponential_error,
            "sinusoidal_no_initial_max_abs": sinusoidal_error,
        },
        "nonlinear_available": nonlinear_available,
        "cancellation_latency_ms": guess_cancellation_latency,
        "gates": gates,
        "passed": passed,
        "exit_code": 0 if passed else 1,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark polynomial and optional nonlinear fits.")
    parser.add_argument("--samples", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--sinusoid-guess-samples", type=int, default=100_000)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    result = benchmark(
        arguments.samples,
        arguments.iterations,
        arguments.sinusoid_guess_samples,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if arguments.enforce and result["exit_code"]:
        raise SystemExit(int(result["exit_code"]))


if __name__ == "__main__":
    main()
