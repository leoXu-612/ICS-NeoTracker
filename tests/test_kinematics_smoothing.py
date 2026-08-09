from __future__ import annotations

import unittest

import numpy as np

from benchmarks.kinematics_fixtures import missing_segments, uniform_quadratic, vfr_linear
from neo_tracker.kinematics.derivatives import derive_series
from neo_tracker.kinematics.runtime import CancellationToken, KinematicsCancelled
from neo_tracker.kinematics.smoothing import smooth_series
from neo_tracker.kinematics.types import DerivativeConfig


class SavitzkyGolayTests(unittest.TestCase):
    def test_smoothing_preserves_uniform_quadratic_including_one_sided_edges(self) -> None:
        fixture = uniform_quadratic()
        source = fixture.sample_series()
        smoothed = smooth_series(
            source,
            window_length=11,
            polyorder=3,
            edge_policy="one_sided",
        )

        np.testing.assert_array_equal(smoothed.valid_mask, source.valid_mask)
        np.testing.assert_allclose(smoothed.values, source.values, rtol=2e-12, atol=2e-12)
        self.assertEqual(smoothed.unit, source.unit)
        self.assertEqual(smoothed.processing_chain[-1].operation, "savgol_uniform")
        self.assertEqual(smoothed.metadata["derivative_order"], 0)

    def test_savgol_derivative_recovers_quadratic_first_and_second_derivative(self) -> None:
        fixture = uniform_quadratic()
        source = fixture.sample_series()
        first = derive_series(
            source,
            DerivativeConfig(
                "savgol_uniform",
                1,
                edge_policy="one_sided",
                window_length=9,
                polyorder=3,
            ),
        )
        second = derive_series(
            source,
            DerivativeConfig(
                "savgol_uniform",
                2,
                edge_policy="one_sided",
                window_length=9,
                polyorder=3,
            ),
        )
        expected_first = 2.0 * fixture.parameters["a"] * fixture.time_s + fixture.parameters["b"]
        expected_second = np.full(len(source), 2.0 * fixture.parameters["a"])

        np.testing.assert_allclose(first.values, expected_first, rtol=2e-10, atol=2e-10)
        np.testing.assert_allclose(second.values, expected_second, rtol=2e-8, atol=2e-8)

    def test_vfr_input_is_rejected_without_resampling(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit resampling is not available"):
            smooth_series(vfr_linear().sample_series(), window_length=9, polyorder=3)
        with self.assertRaisesRegex(ValueError, "explicit resampling is not available"):
            derive_series(
                vfr_linear().sample_series(),
                DerivativeConfig("savgol_uniform", 1, window_length=9, polyorder=3),
            )

    def test_missing_segments_are_smoothed_independently(self) -> None:
        fixture = missing_segments()
        result = smooth_series(
            fixture.sample_series(),
            window_length=7,
            polyorder=2,
            edge_policy="invalid",
        )

        self.assertTrue(np.isnan(result.values[~result.valid_mask]).all())
        self.assertFalse(result.valid_mask[19])
        self.assertFalse(result.valid_mask[27])
        self.assertFalse(result.valid_mask[60])
        self.assertFalse(result.valid_mask[66])
        self.assertFalse(result.valid_mask[20:27].any())
        expected = fixture.values
        np.testing.assert_allclose(result.values[result.valid_mask], expected[result.valid_mask], atol=1e-11)

    def test_invalid_configuration_is_explicit(self) -> None:
        source = uniform_quadratic().sample_series()
        with self.assertRaisesRegex(ValueError, "odd"):
            smooth_series(source, window_length=8, polyorder=3)
        with self.assertRaisesRegex(ValueError, "polyorder"):
            smooth_series(source, window_length=7, polyorder=7)

    def test_cancelled_smoothing_has_no_partial_public_result(self) -> None:
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(KinematicsCancelled):
            smooth_series(uniform_quadratic(100_000).sample_series(), cancellation=token)


if __name__ == "__main__":
    unittest.main()
