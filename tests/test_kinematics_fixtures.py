from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from benchmarks.kinematics_fixtures import (
    FIXTURE_REVISION,
    constant_series,
    duplicate_time,
    fixture_catalog,
    insufficient_samples,
    very_small_dt,
)
from neo_tracker.kinematics.derivatives import derive_series
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.series import TrackingSeriesBuilder
from neo_tracker.kinematics.types import (
    DerivativeConfig,
    FitRequest,
    FitStatus,
    SampleSeries,
)


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FIXTURES = {
    "uniform_linear",
    "uniform_quadratic",
    "vfr_linear",
    "vfr_quadratic",
    "sinusoidal",
    "exponential",
    "missing_segments",
    "outlier_samples",
    "angular_wrap",
    "path_distance",
    "constant_series",
    "insufficient_samples",
    "duplicate_time",
    "very_small_dt",
}


def request_for(series: SampleSeries, model: str) -> FitRequest:
    return FitRequest(
        series_id=series.series_id,
        model=model,
        range_start_s=float(series.time_s[0]),
        range_end_s=float(series.time_s[-1]),
        source_revision=series.source_revision,
    )


class KinematicsFixtureCatalogTests(unittest.TestCase):
    def test_catalog_and_manifest_cover_all_required_fixtures(self) -> None:
        catalog = fixture_catalog()
        manifest = json.loads(
            (ROOT / "tests/fixtures/kinematics/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(catalog), REQUIRED_FIXTURES)
        self.assertEqual(set(manifest["cases"]), REQUIRED_FIXTURES)
        self.assertEqual(manifest["random_seed"], 20260810)

    def test_every_catalog_fixture_is_structural_and_deterministic(self) -> None:
        catalog = fixture_catalog()
        for name, fixture in catalog.items():
            with self.subTest(fixture=name):
                self.assertEqual(fixture.name, name)
                self.assertEqual(len(fixture.time_s), len(fixture.values))
                self.assertEqual(len(fixture.values), len(fixture.valid_mask))
                self.assertEqual(len(fixture.values), len(fixture.frame_indices))
                self.assertTrue(fixture.valid_mask.dtype == np.bool_)
                self.assertTrue(np.issubdtype(fixture.values.dtype, np.floating))
                # The catalog itself is deterministic: rebuilding yields identical data.
                rebuilt = fixture_catalog()[name]
                np.testing.assert_array_equal(fixture.time_s, rebuilt.time_s)
                np.testing.assert_array_equal(fixture.values, rebuilt.values)

    def test_constant_series_has_near_zero_first_derivative_and_stable_r2(self) -> None:
        fixture = constant_series()
        series = fixture.sample_series(source_revision=FIXTURE_REVISION)
        derivative = derive_series(
            series,
            DerivativeConfig("nonuniform_finite_difference", 1),
        )
        self.assertTrue(np.all(np.abs(derivative.values[derivative.valid_mask]) < 1e-9))
        linear = fit_series(series, request_for(series, "linear"))
        self.assertTrue(np.all(np.isfinite(linear.rmse)))
        self.assertLessEqual(abs(float(linear.r_squared)), 1.0 + 1e-12)

    def test_insufficient_samples_fails_closed(self) -> None:
        fixture = insufficient_samples()
        series = fixture.sample_series(source_revision=FIXTURE_REVISION)
        result = fit_series(series, request_for(series, "quadratic"))
        self.assertIsNot(result.status, FitStatus.OK)

    def test_duplicate_time_fixture_is_rejected_by_series_builder(self) -> None:
        fixture = duplicate_time()
        results = fixture.tracker_results()
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            TrackingSeriesBuilder().build_series(
                results,
                source_revision=FIXTURE_REVISION,
            )

    def test_very_small_dt_never_emits_silent_infinity(self) -> None:
        fixture = very_small_dt()
        series = fixture.sample_series(source_revision=FIXTURE_REVISION)
        derived = derive_series(
            series,
            DerivativeConfig("nonuniform_finite_difference", 1),
        )
        self.assertFalse(np.any(np.isinf(derived.values)))
        self.assertFalse(np.any(np.isnan(derived.values[derived.valid_mask])))


if __name__ == "__main__":
    unittest.main()
