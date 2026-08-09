from __future__ import annotations

import unittest

import numpy as np

from benchmarks.kinematics_fixtures import uniform_quadratic
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.residuals import residual_metrics, residual_series
from neo_tracker.kinematics.types import FitRequest, FitStatus
from neo_tracker.kinematics.units import state_unit, velocity_unit
from neo_tracker.kinematics.validation import derivative_unit, fit_parameter_units


class ResidualMetricTests(unittest.TestCase):
    def test_large_dc_offset_with_resolvable_variation_is_not_constant(self) -> None:
        observed = 1e9 + np.arange(20, dtype=np.float64)
        predicted = np.full(20, float(np.mean(observed)))
        mask = np.ones(20, dtype=bool)
        metrics = residual_metrics(observed, predicted, mask)

        self.assertAlmostEqual(metrics.r_squared, 0.0, places=14)
        perfect = residual_metrics(observed, observed, mask)
        self.assertEqual(perfect.r_squared, 1.0)

    def test_true_constant_series_has_deterministic_r_squared(self) -> None:
        observed = np.full(12, 1e9, dtype=np.float64)
        mask = np.ones(12, dtype=bool)
        self.assertEqual(residual_metrics(observed, observed, mask).r_squared, 1.0)
        wrong = observed + 1.0
        self.assertEqual(residual_metrics(observed, wrong, mask).r_squared, 0.0)

    def test_residual_series_preserves_alignment_provenance_and_units(self) -> None:
        fixture = uniform_quadratic()
        source = fixture.sample_series()
        request = FitRequest(
            source.series_id,
            "quadratic",
            float(source.time_s[20]),
            float(source.time_s[-20]),
            source.source_revision,
        )
        fit = fit_series(source, request)
        self.assertIs(fit.status, FitStatus.OK)
        residual = residual_series(source, fit)

        np.testing.assert_array_equal(residual.frame_indices, source.frame_indices)
        np.testing.assert_array_equal(residual.time_s, source.time_s)
        np.testing.assert_array_equal(residual.valid_mask, fit.valid_mask)
        self.assertTrue(np.isnan(residual.values[~fit.valid_mask]).all())
        self.assertEqual(residual.unit, "m")
        self.assertEqual(residual.source_revision, source.source_revision)
        self.assertEqual(residual.processing_chain[-1].operation, "fit_residual")
        self.assertFalse(residual.values.flags.writeable)


class UnitRuleTests(unittest.TestCase):
    def test_state_and_filter_velocity_units_are_conservative(self) -> None:
        self.assertEqual(state_unit("theta"), "rad")
        self.assertEqual(state_unit("theta_unwrapped"), "rad")
        self.assertEqual(state_unit("x_px"), "px")
        self.assertEqual(state_unit("x_world"), "")
        self.assertEqual(state_unit("x_world", {"x_world": "cm"}), "cm")
        self.assertEqual(velocity_unit("omega"), "rad/s")
        self.assertEqual(velocity_unit("v_x_px"), "px/s")
        self.assertEqual(velocity_unit("v_x_world", {"x_world": "m"}), "m/s")

    def test_derived_and_fit_parameter_units_do_not_conflate_quadratic_a_with_2a(self) -> None:
        self.assertEqual(derivative_unit("m", 1), "m/s")
        self.assertEqual(derivative_unit("m", 2), "m/s²")
        self.assertEqual(fit_parameter_units("linear", "m"), ("m/s", "m"))
        self.assertEqual(fit_parameter_units("quadratic", "m"), ("m/s²", "m/s", "m"))
        self.assertEqual(
            fit_parameter_units("sinusoidal", "rad"),
            ("rad", "rad/s", "rad", "rad"),
        )
        self.assertEqual(fit_parameter_units("exponential", ""), ("", "1/s", ""))


if __name__ == "__main__":
    unittest.main()
