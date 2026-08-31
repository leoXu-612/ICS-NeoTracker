from __future__ import annotations

import unittest

import numpy as np

from benchmarks.kinematics_fixtures import uniform_linear, uniform_quadratic, vfr_linear
from neo_tracker.kinematics.fitting import KinematicsFitOperator, fit_series
from neo_tracker.kinematics.runtime import CancellationToken
from neo_tracker.kinematics.types import FitRequest, FitStatus, SampleSeries


def request_for(series: SampleSeries, model: str, *, start: float | None = None, end: float | None = None) -> FitRequest:
    return FitRequest(
        series_id=series.series_id,
        model=model,
        range_start_s=float(series.time_s[0] if start is None else start),
        range_end_s=float(series.time_s[-1] if end is None else end),
        source_revision=series.source_revision,
    )


class LinearQuadraticFitTests(unittest.TestCase):
    def test_linear_fit_recovers_vfr_parameters_and_aligned_outputs(self) -> None:
        fixture = vfr_linear()
        source = fixture.sample_series()
        result = fit_series(source, request_for(source, "linear"))

        self.assertIs(result.status, FitStatus.OK)
        self.assertEqual(result.parameter_names, ("slope", "intercept"))
        np.testing.assert_allclose(
            result.parameters,
            [fixture.parameters["slope"], fixture.parameters["intercept"]],
            rtol=2e-13,
            atol=2e-13,
        )
        np.testing.assert_allclose(result.predicted, source.values, rtol=2e-13, atol=2e-13)
        self.assertEqual(result.parameter_units, ("m/s", "m"))
        self.assertLess(result.rmse, 1e-13)
        self.assertAlmostEqual(result.r_squared, 1.0)
        self.assertEqual(len(result.predicted), len(source))

    def test_quadratic_fit_recovers_original_time_coordinate_coefficients(self) -> None:
        fixture = uniform_quadratic()
        shifted_time = fixture.time_s + 1_000.0
        a, b, c = 0.75, -1.25, 3.5
        values = (a * shifted_time + b) * shifted_time + c
        source = SampleSeries(
            series_id="large-time-quadratic",
            name="Large absolute time quadratic",
            frame_indices=fixture.frame_indices,
            time_s=shifted_time,
            values=values,
            valid_mask=fixture.valid_mask,
            unit="m",
            source_kind="synthetic",
            source_revision="revision",
        )
        result = KinematicsFitOperator().fit(source, request_for(source, "quadratic"))

        self.assertIs(result.status, FitStatus.OK)
        np.testing.assert_allclose(result.parameters[:2], [a, b], rtol=3e-7, atol=3e-7)
        self.assertAlmostEqual(result.parameters[2], c, delta=0.001)
        np.testing.assert_allclose(result.predicted, source.values, rtol=2e-14, atol=2e-6)
        self.assertEqual(result.parameter_units, ("m/s²", "m/s", "m"))

    def test_fit_range_and_valid_mask_remain_frame_aligned(self) -> None:
        fixture = uniform_linear(101)
        mask = np.ones(101, dtype=bool)
        mask[50] = False
        values = np.array(fixture.values, copy=True)
        values[50] = np.nan
        source = SampleSeries(
            series_id="range-mask",
            name="Range mask",
            frame_indices=fixture.frame_indices,
            time_s=fixture.time_s,
            values=values,
            valid_mask=mask,
            unit="m",
            source_kind="synthetic",
            source_revision="revision",
        )
        result = fit_series(source, request_for(source, "linear", start=0.4, end=1.6))

        expected_mask = mask & (source.time_s >= 0.4) & (source.time_s <= 1.6)
        np.testing.assert_array_equal(result.valid_mask, expected_mask)
        self.assertTrue(np.isnan(result.predicted[~expected_mask]).all())
        self.assertTrue(np.isnan(result.residuals[~expected_mask]).all())
        self.assertEqual(result.sample_count, int(np.count_nonzero(expected_mask)))

    def test_constant_series_has_stable_r_squared(self) -> None:
        time_s = np.arange(20, dtype=np.float64) * 0.1
        source = SampleSeries(
            series_id="constant",
            name="Constant",
            frame_indices=np.arange(20, dtype=np.int64),
            time_s=time_s,
            values=np.full(20, 7.0),
            valid_mask=np.ones(20, dtype=bool),
            unit="m",
            source_kind="synthetic",
            source_revision="revision",
        )
        result = fit_series(source, request_for(source, "linear"))
        self.assertIs(result.status, FitStatus.OK)
        self.assertEqual(result.r_squared, 1.0)

    def test_insufficient_samples_and_bounds_have_explicit_failed_terminal_state(self) -> None:
        source = uniform_linear(2).sample_series()
        quadratic = fit_series(source, request_for(source, "quadratic"))
        self.assertIs(quadratic.status, FitStatus.FAILED)
        self.assertIn("at least 3", quadratic.message)
        bounded_request = FitRequest(
            series_id=source.series_id,
            model="linear",
            range_start_s=float(source.time_s[0]),
            range_end_s=float(source.time_s[-1]),
            source_revision=source.source_revision,
            bounds={"slope": (-10.0, 10.0)},
        )
        bounded = fit_series(source, bounded_request)
        self.assertIs(bounded.status, FitStatus.FAILED)
        self.assertIn("only for nonlinear", bounded.message)
        initialized_request = FitRequest(
            series_id=source.series_id,
            model="linear",
            range_start_s=float(source.time_s[0]),
            range_end_s=float(source.time_s[-1]),
            source_revision=source.source_revision,
            initial_parameters={"slope": 2.0},
        )
        initialized = fit_series(source, initialized_request)
        self.assertIs(initialized.status, FitStatus.FAILED)
        self.assertIn("only for nonlinear", initialized.message)

    def test_stale_and_cancelled_requests_are_single_terminal_results(self) -> None:
        source = uniform_linear().sample_series()
        stale_request = FitRequest(
            source.series_id,
            "linear",
            float(source.time_s[0]),
            float(source.time_s[-1]),
            "old-revision",
        )
        stale = fit_series(source, stale_request)
        self.assertIs(stale.status, FitStatus.STALE)
        self.assertEqual(stale.sample_count, 0)

        token = CancellationToken()
        token.cancel()
        cancelled = fit_series(source, request_for(source, "linear"), cancellation=token)
        self.assertIs(cancelled.status, FitStatus.CANCELLED)
        self.assertEqual(cancelled.sample_count, 0)
        self.assertIn("cancelled", cancelled.message)


if __name__ == "__main__":
    unittest.main()
