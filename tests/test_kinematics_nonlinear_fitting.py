from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np

from benchmarks.kinematics_fixtures import exponential, sinusoidal, uniform_linear
from neo_tracker.kinematics import fitting
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.models import exponential_model
from neo_tracker.kinematics.types import FitRequest, FitStatus, SampleSeries


def nonlinear_request(series: SampleSeries, model: str, parameters: dict[str, float]) -> FitRequest:
    return FitRequest(
        series_id=series.series_id,
        model=model,
        range_start_s=float(series.time_s[0]),
        range_end_s=float(series.time_s[-1]),
        source_revision=series.source_revision,
        initial_parameters=parameters,
    )


class NonlinearFitTests(unittest.TestCase):
    @unittest.skipUnless(fitting._load_least_squares() is not None, "SciPy is unavailable")
    def test_exponential_fit_recovers_negative_and_positive_rates(self) -> None:
        for rate in (-0.6, 0.35):
            with self.subTest(rate=rate):
                fixture = exponential(rate=rate)
                source = fixture.sample_series()
                result = fit_series(
                    source,
                    nonlinear_request(source, "exponential", dict(fixture.parameters)),
                )
                self.assertIs(result.status, FitStatus.OK)
                np.testing.assert_allclose(
                    result.parameters,
                    [
                        fixture.parameters["amplitude"],
                        fixture.parameters["rate"],
                        fixture.parameters["offset"],
                    ],
                    rtol=2e-8,
                    atol=2e-8,
                )
                self.assertEqual(result.parameter_units, ("m", "1/s", "m"))
                self.assertLess(result.rmse, 1e-10)

    @unittest.skipUnless(fitting._load_least_squares() is not None, "SciPy is unavailable")
    def test_sinusoidal_fit_recovers_canonical_parameters(self) -> None:
        fixture = sinusoidal()
        source = fixture.sample_series()
        initial = dict(fixture.parameters)
        initial["amplitude"] *= 0.9
        initial["omega"] *= 1.04
        initial["phase"] -= 0.1
        result = fit_series(source, nonlinear_request(source, "sinusoidal", initial))

        self.assertIs(result.status, FitStatus.OK)
        self.assertGreaterEqual(result.parameters[0], 0.0)
        self.assertGreaterEqual(result.parameters[1], 0.0)
        self.assertGreaterEqual(result.parameters[2], -math.pi)
        self.assertLess(result.parameters[2], math.pi)
        np.testing.assert_allclose(
            result.parameters,
            [
                fixture.parameters["amplitude"],
                fixture.parameters["omega"],
                fixture.parameters["phase"],
                fixture.parameters["offset"],
            ],
            rtol=2e-8,
            atol=2e-8,
        )
        self.assertEqual(result.parameter_units, ("m", "rad/s", "rad", "m"))

    def test_optional_scipy_absence_is_unavailable_but_polynomial_fit_still_works(self) -> None:
        nonlinear_source = sinusoidal(80).sample_series()
        with patch.object(fitting, "_load_least_squares", return_value=None):
            unavailable = fit_series(
                nonlinear_source,
                nonlinear_request(
                    nonlinear_source,
                    "sinusoidal",
                    dict(sinusoidal(80).parameters),
                ),
            )
            linear_source = uniform_linear().sample_series()
            linear = fit_series(
                linear_source,
                FitRequest(
                    linear_source.series_id,
                    "linear",
                    float(linear_source.time_s[0]),
                    float(linear_source.time_s[-1]),
                    linear_source.source_revision,
                ),
            )
        self.assertIs(unavailable.status, FitStatus.UNAVAILABLE)
        self.assertIn("optional science", unavailable.message)
        self.assertIs(linear.status, FitStatus.OK)

    def test_nonconvergence_has_a_stable_failed_terminal_state(self) -> None:
        class FailedOptimization:
            success = False
            message = "evaluation budget reached"
            x = np.ones(4)
            jac = np.empty((0, 4))

        def fake_least_squares(*args: object, **kwargs: object) -> FailedOptimization:
            return FailedOptimization()

        source = sinusoidal(80).sample_series()
        with patch.object(fitting, "_load_least_squares", return_value=fake_least_squares):
            result = fit_series(
                source,
                nonlinear_request(source, "sinusoidal", dict(sinusoidal(80).parameters)),
            )
        self.assertIs(result.status, FitStatus.FAILED)
        self.assertIn("did not converge", result.message)
        self.assertEqual(result.sample_count, 0)

    @unittest.skipUnless(fitting._load_least_squares() is not None, "SciPy is unavailable")
    def test_cancellation_inside_optimizer_returns_cancelled_terminal_state(self) -> None:
        class CancelDuringObjective:
            def __init__(self) -> None:
                self.calls = 0

            def is_cancelled(self) -> bool:
                self.calls += 1
                return self.calls >= 2

        fixture = sinusoidal(160)
        source = fixture.sample_series()
        result = fit_series(
            source,
            nonlinear_request(source, "sinusoidal", dict(fixture.parameters)),
            cancellation=CancelDuringObjective(),
        )
        self.assertIs(result.status, FitStatus.CANCELLED)
        self.assertIn("cancelled", result.message)
        self.assertEqual(result.sample_count, 0)

    def test_exponential_overflow_is_rejected_before_nonfinite_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite evaluation range"):
            exponential_model(np.array([0.0, 10.0]), 1.0, 100.0, 0.0)

        source = exponential().sample_series()
        request = nonlinear_request(
            source,
            "exponential",
            {"amplitude": 1.0, "rate": 1_000.0, "offset": 0.0},
        )
        result = fit_series(source, request)
        self.assertIs(result.status, FitStatus.FAILED)
        self.assertIn("outside safe", result.message)


if __name__ == "__main__":
    unittest.main()
