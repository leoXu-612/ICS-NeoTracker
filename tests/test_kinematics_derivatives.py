from __future__ import annotations

import unittest

import numpy as np

from benchmarks.kinematics_fixtures import (
    missing_segments,
    vfr_linear,
    vfr_quadratic,
)
from neo_tracker.kinematics.derivatives import KinematicsDerivativeOperator, derive_series
from neo_tracker.kinematics.runtime import CancellationToken, KinematicsCancelled
from neo_tracker.kinematics.types import DerivativeConfig, SampleSeries


class NonuniformDerivativeTests(unittest.TestCase):
    def test_vfr_linear_first_derivative_uses_true_time(self) -> None:
        fixture = vfr_linear()
        source = fixture.sample_series()
        derived = derive_series(
            source,
            DerivativeConfig(
                method="nonuniform_finite_difference",
                order=1,
                edge_policy="one_sided",
            ),
        )

        np.testing.assert_allclose(
            derived.values[derived.valid_mask],
            fixture.parameters["slope"],
            rtol=2e-12,
            atol=2e-12,
        )
        np.testing.assert_array_equal(derived.frame_indices, source.frame_indices)
        np.testing.assert_array_equal(derived.time_s, source.time_s)
        self.assertEqual(derived.unit, "m/s")
        self.assertFalse(derived.values.flags.writeable)
        self.assertEqual(derived.processing_chain[-1].operation, "nonuniform_finite_difference")

    def test_vfr_quadratic_first_and_second_derivatives_are_analytic(self) -> None:
        fixture = vfr_quadratic()
        source = fixture.sample_series()
        first = KinematicsDerivativeOperator().derive(
            source,
            DerivativeConfig("nonuniform_finite_difference", 1, edge_policy="one_sided"),
        )
        second = KinematicsDerivativeOperator().derive(
            source,
            DerivativeConfig("nonuniform_finite_difference", 2, edge_policy="one_sided"),
        )
        expected_first = 2.0 * fixture.parameters["a"] * fixture.time_s + fixture.parameters["b"]
        expected_second = np.full(len(source), 2.0 * fixture.parameters["a"])

        np.testing.assert_allclose(first.values, expected_first, rtol=3e-11, atol=3e-11)
        np.testing.assert_allclose(second.values, expected_second, rtol=2e-9, atol=2e-9)
        self.assertEqual(second.unit, "m/s²")

    def test_default_invalid_edges_and_missing_segments_never_cross_gap(self) -> None:
        fixture = missing_segments()
        source = fixture.sample_series()
        derived = derive_series(
            source,
            DerivativeConfig("nonuniform_finite_difference", 1, edge_policy="invalid"),
        )

        expected_mask = np.array(fixture.valid_mask, copy=True)
        for start, stop in ((0, 20), (27, 61), (66, len(source))):
            expected_mask[start] = False
            expected_mask[stop - 1] = False
        np.testing.assert_array_equal(derived.valid_mask, expected_mask)
        self.assertTrue(np.isnan(derived.values[~expected_mask]).all())
        expected = 2.0 * fixture.parameters["a"] * fixture.time_s + fixture.parameters["b"]
        np.testing.assert_allclose(derived.values[expected_mask], expected[expected_mask], atol=1e-10)

    def test_short_segments_follow_explicit_edge_policy(self) -> None:
        source = SampleSeries(
            series_id="short-segments",
            name="Short segments",
            frame_indices=np.arange(6, dtype=np.int64),
            time_s=np.arange(6, dtype=np.float64),
            values=np.array([0.0, 2.0, np.nan, 9.0, 16.0, np.nan]),
            valid_mask=np.array([True, True, False, True, True, False]),
            unit="m",
            source_kind="synthetic",
            source_revision="revision",
        )
        invalid = derive_series(
            source,
            DerivativeConfig("nonuniform_finite_difference", 1, edge_policy="invalid"),
        )
        one_sided = derive_series(
            source,
            DerivativeConfig("nonuniform_finite_difference", 1, edge_policy="one_sided"),
        )
        second = derive_series(
            source,
            DerivativeConfig("nonuniform_finite_difference", 2, edge_policy="one_sided"),
        )

        self.assertFalse(invalid.valid_mask.any())
        np.testing.assert_array_equal(one_sided.valid_mask, source.valid_mask)
        np.testing.assert_allclose(one_sided.values[source.valid_mask], [2.0, 2.0, 7.0, 7.0])
        self.assertFalse(second.valid_mask.any())

    def test_numerically_unsafe_tiny_dt_fails_without_silent_infinity(self) -> None:
        tiny = np.array([0.0, 1e-200, 2e-200], dtype=np.float64)
        source = SampleSeries(
            series_id="tiny-dt",
            name="Tiny dt",
            frame_indices=np.arange(3, dtype=np.int64),
            time_s=tiny,
            values=tiny**2,
            valid_mask=np.ones(3, dtype=bool),
            unit="m",
            source_kind="synthetic",
            source_revision="revision",
        )
        with self.assertRaisesRegex(ValueError, "numerically unstable"):
            derive_series(
                source,
                DerivativeConfig("nonuniform_finite_difference", 2, edge_policy="one_sided"),
            )

    def test_cancelled_derivative_does_not_publish_a_partial_series(self) -> None:
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(KinematicsCancelled):
            derive_series(
                vfr_linear(10_000).sample_series(),
                DerivativeConfig("nonuniform_finite_difference", 1),
                cancellation=token,
            )


if __name__ == "__main__":
    unittest.main()
