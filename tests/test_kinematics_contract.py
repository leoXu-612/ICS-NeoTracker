from __future__ import annotations

import json
import unittest

import numpy as np

from neo_tracker.kinematics import (
    DerivativeConfig,
    FitModel,
    FitRequest,
    FitResult,
    FitStatus,
    KinematicsBundle,
    ProcessingStep,
    SampleSeries,
    StaleSourceRevisionError,
    cadence_relative_deviation,
    derivative_unit,
    fit_parameter_names,
    fit_parameter_units,
    fit_sample_mask,
    require_current_revision,
    require_uniform_cadence,
)


REVISION = "results:sha256:contract-fixture"


def sample_series(
    *,
    time_s: np.ndarray | None = None,
    values: np.ndarray | None = None,
    valid_mask: np.ndarray | None = None,
    source_revision: str = REVISION,
    series_id: str = "filtered:x_world",
) -> SampleSeries:
    times = np.array([0.0, 0.1, 0.2, 0.3], dtype=np.float64) if time_s is None else time_s
    data = np.array([1.0, 1.2, 1.4, 1.6], dtype=np.float64) if values is None else values
    mask = np.ones(len(times), dtype=bool) if valid_mask is None else valid_mask
    return SampleSeries(
        series_id=series_id,
        name="Filtered x",
        frame_indices=np.arange(len(times), dtype=np.int64),
        time_s=times,
        values=data,
        valid_mask=mask,
        unit="m",
        source_kind="filtered_state",
        source_revision=source_revision,
        processing_chain=(ProcessingStep("filtered_state", {"key": "x_world"}),),
        metadata={"axis": "x", "labels": ["filtered", "world"]},
    )


def fit_result(series: SampleSeries) -> FitResult:
    predicted = np.array([1.0, 1.2, 1.4, 1.6], dtype=np.float64)
    return FitResult(
        series_id=series.series_id,
        model=FitModel.LINEAR,
        parameter_names=("slope", "intercept"),
        parameters=np.array([2.0, 1.0]),
        parameter_units=("m/s", "m"),
        standard_errors=np.array([0.0, 0.0]),
        covariance=np.zeros((2, 2), dtype=np.float64),
        predicted=predicted,
        residuals=series.values - predicted,
        valid_mask=series.valid_mask,
        rmse=0.0,
        r_squared=1.0,
        sample_count=4,
        range_start_s=0.0,
        range_end_s=0.3,
        source_revision=series.source_revision,
        status=FitStatus.OK,
    )


class SampleSeriesContractTests(unittest.TestCase):
    def test_rejects_mismatched_lengths(self) -> None:
        with self.assertRaisesRegex(ValueError, "lengths must match"):
            SampleSeries(
                series_id="x",
                name="x",
                frame_indices=np.arange(3, dtype=np.int64),
                time_s=np.arange(4, dtype=np.float64),
                values=np.arange(4, dtype=np.float64),
                valid_mask=np.ones(4, dtype=bool),
                unit="m",
                source_kind="state",
                source_revision=REVISION,
            )

    def test_rejects_non_integer_or_non_monotonic_frames(self) -> None:
        with self.assertRaisesRegex(TypeError, "must contain integers"):
            SampleSeries(
                **{
                    **sample_series().to_dict(),
                    "frame_indices": np.array([0.0, 1.0, 2.0, 3.0]),
                }
            )
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            SampleSeries(
                series_id="x",
                name="x",
                frame_indices=np.array([0, 1, 1, 3], dtype=np.int64),
                time_s=np.arange(4, dtype=np.float64),
                values=np.arange(4, dtype=np.float64),
                valid_mask=np.ones(4, dtype=bool),
                unit="m",
                source_kind="state",
                source_revision=REVISION,
            )

    def test_rejects_non_strict_valid_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "time_s values must be strictly increasing"):
            sample_series(time_s=np.array([0.0, 0.1, 0.1, 0.3]))

    def test_invalid_samples_preserve_frame_alignment(self) -> None:
        series = sample_series(
            time_s=np.array([0.0, np.nan, 0.2, 0.3]),
            values=np.array([1.0, np.nan, 1.4, 1.6]),
            valid_mask=np.array([True, False, True, True]),
        )
        self.assertEqual(len(series), 4)
        self.assertTrue(np.isnan(series.values[1]))
        np.testing.assert_array_equal(series.frame_indices, [0, 1, 2, 3])
        np.testing.assert_array_equal(series.valid_mask, [True, False, True, True])

    def test_arrays_are_detached_and_read_only(self) -> None:
        source = np.array([1.0, 1.2, 1.4, 1.6])
        series = sample_series(values=source)
        source[0] = 99.0
        self.assertEqual(series.values[0], 1.0)
        for array in (series.frame_indices, series.time_s, series.values, series.valid_mask):
            self.assertFalse(array.flags.writeable)
        with self.assertRaises(ValueError):
            series.values[0] = 2.0

    def test_series_round_trip_is_json_safe_and_exact(self) -> None:
        series = sample_series(
            time_s=np.array([0.0, np.nan, 0.2, 0.3]),
            values=np.array([1.0, np.nan, 1.4, 1.6]),
            valid_mask=np.array([True, False, True, True]),
        )
        encoded = json.loads(json.dumps(series.to_dict(), allow_nan=False))
        restored = SampleSeries.from_dict(encoded)
        self.assertEqual(series, restored)
        self.assertEqual(str(restored.processing_chain[0]), 'filtered_state(key="x_world")')

    def test_large_series_wire_format_does_not_expand_into_python_rows(self) -> None:
        count = 100_000
        series = SampleSeries(
            series_id="large",
            name="Large aligned series",
            frame_indices=np.arange(count, dtype=np.int64),
            time_s=np.arange(count, dtype=np.float64) / 240.0,
            values=np.linspace(0.0, 1.0, count, dtype=np.float64),
            valid_mask=np.ones(count, dtype=bool),
            unit="m",
            source_kind="state",
            source_revision=REVISION,
        )
        wire = series.to_dict()
        self.assertIsInstance(wire["values"], dict)
        self.assertIsInstance(wire["values"]["data_b64"], str)  # type: ignore[index]
        self.assertNotIsInstance(wire["values"], list)
        restored = SampleSeries.from_dict(wire)
        self.assertEqual(len(restored), count)
        self.assertEqual(restored.values[-1], 1.0)


class ConfigurationContractTests(unittest.TestCase):
    def test_unknown_derivative_method_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown derivative method"):
            DerivativeConfig(method="frame_over_fps", order=1)

    def test_unknown_fit_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fit model"):
            FitRequest(
                series_id="x",
                model="cubic",
                range_start_s=0.0,
                range_end_s=1.0,
                source_revision=REVISION,
            )

    def test_derivative_config_rejects_invalid_savgol_shape(self) -> None:
        with self.assertRaisesRegex(ValueError, "odd"):
            DerivativeConfig(method="savgol_uniform", order=1, window_length=10)
        with self.assertRaisesRegex(ValueError, "polyorder"):
            DerivativeConfig(method="savgol_uniform", order=2, window_length=5, polyorder=1)

    def test_fit_request_rejects_invalid_range_and_parameters(self) -> None:
        with self.assertRaisesRegex(ValueError, "fit range"):
            FitRequest("x", "linear", 1.0, 1.0, REVISION)
        with self.assertRaisesRegex(ValueError, "must be finite"):
            FitRequest(
                "x",
                "linear",
                0.0,
                1.0,
                REVISION,
                initial_parameters={"slope": float("nan")},
            )

    def test_configs_round_trip(self) -> None:
        derivative = DerivativeConfig(
            method="savgol_uniform",
            order=2,
            edge_policy="one_sided",
            window_length=9,
            polyorder=3,
            uniformity_tolerance=0.002,
        )
        request = FitRequest(
            "filtered:x_world",
            "sinusoidal",
            0.2,
            3.4,
            REVISION,
            initial_parameters={"omega": 4.0},
            bounds={"omega": (0.1, 20.0)},
        )
        self.assertEqual(DerivativeConfig.from_dict(derivative.to_dict()), derivative)
        self.assertEqual(FitRequest.from_dict(request.to_dict()), request)


class ProvenanceAndUnitContractTests(unittest.TestCase):
    def test_stale_source_revision_is_rejected(self) -> None:
        with self.assertRaises(StaleSourceRevisionError):
            require_current_revision("old", "new")
        series = sample_series()
        stale_request = FitRequest(series.series_id, "linear", 0.0, 0.3, "old")
        with self.assertRaises(StaleSourceRevisionError):
            fit_sample_mask(series, stale_request)

    def test_fit_sample_mask_preserves_alignment_and_range(self) -> None:
        series = sample_series(valid_mask=np.array([True, False, True, True]))
        request = FitRequest(series.series_id, "linear", 0.05, 0.25, REVISION)
        mask = fit_sample_mask(series, request)
        np.testing.assert_array_equal(mask, [False, False, True, False])
        self.assertFalse(mask.flags.writeable)

    def test_units_are_stable_and_unknown_units_remain_unknown(self) -> None:
        self.assertEqual(derivative_unit("m", 1), "m/s")
        self.assertEqual(derivative_unit("m", 2), "m/s²")
        self.assertEqual(derivative_unit("rad", 2), "rad/s²")
        self.assertEqual(derivative_unit("", 1), "")
        self.assertEqual(fit_parameter_names("quadratic"), ("a", "b", "c"))
        self.assertEqual(fit_parameter_units("quadratic", "m"), ("m/s²", "m/s", "m"))
        self.assertEqual(
            fit_parameter_units("sinusoidal", ""),
            ("", "rad/s", "rad", ""),
        )

    def test_uniform_cadence_is_segment_aware_and_vfr_is_rejected(self) -> None:
        mask = np.array([True, True, False, True, True], dtype=bool)
        uniform = sample_series(
            time_s=np.array([0.0, 0.1, np.nan, 0.3, 0.4]),
            values=np.array([1.0, 1.1, np.nan, 1.3, 1.4]),
            valid_mask=mask,
        )
        self.assertAlmostEqual(cadence_relative_deviation(uniform.time_s, mask), 0.0)
        require_uniform_cadence(uniform, tolerance=1e-3)
        different_segment_cadence = sample_series(
            time_s=np.array([0.0, 0.1, np.nan, 1.0, 1.2]),
            values=np.array([1.0, 1.1, np.nan, 1.3, 1.4]),
            valid_mask=mask,
        )
        self.assertAlmostEqual(
            cadence_relative_deviation(
                different_segment_cadence.time_s,
                different_segment_cadence.valid_mask,
            ),
            0.0,
        )
        vfr = sample_series(time_s=np.array([0.0, 0.1, 0.23, 0.31]))
        with self.assertRaisesRegex(ValueError, "explicit resampling is not available"):
            require_uniform_cadence(vfr, tolerance=0.01)


class FitResultAndBundleContractTests(unittest.TestCase):
    def test_fit_result_rejects_nonfinite_parameters(self) -> None:
        series = sample_series()
        result = fit_result(series)
        with self.assertRaisesRegex(ValueError, "parameters must be finite"):
            FitResult(
                **{
                    **result.__dict__,
                    "parameters": np.array([float("nan"), 1.0]),
                }
            )

    def test_fit_result_round_trip_and_read_only_arrays(self) -> None:
        result = fit_result(sample_series())
        restored = FitResult.from_dict(json.loads(json.dumps(result.to_dict(), allow_nan=False)))
        self.assertEqual(result, restored)
        for array in (
            restored.parameters,
            restored.standard_errors,
            restored.covariance,
            restored.predicted,
            restored.residuals,
            restored.valid_mask,
        ):
            self.assertFalse(array.flags.writeable)

    def test_failed_fit_with_unavailable_metrics_is_strict_json(self) -> None:
        failed = FitResult(
            series_id="filtered:x_world",
            model="linear",
            parameter_names=(),
            parameters=np.array([], dtype=np.float64),
            parameter_units=(),
            standard_errors=np.array([], dtype=np.float64),
            covariance=np.empty((0, 0), dtype=np.float64),
            predicted=np.full(4, np.nan),
            residuals=np.full(4, np.nan),
            valid_mask=np.zeros(4, dtype=bool),
            rmse=np.nan,
            r_squared=np.nan,
            sample_count=0,
            range_start_s=0.0,
            range_end_s=0.3,
            source_revision=REVISION,
            status="failed",
            message="rank deficient",
        )
        payload = json.loads(json.dumps(failed.to_dict(), allow_nan=False))
        self.assertIsNone(payload["rmse"])
        self.assertEqual(FitResult.from_dict(payload), failed)

    def test_bundle_rejects_stale_members(self) -> None:
        stale = sample_series(source_revision="old")
        with self.assertRaisesRegex(ValueError, "stale series"):
            KinematicsBundle((stale,), (), (), REVISION)

    def test_bundle_round_trip(self) -> None:
        series = sample_series()
        bundle = KinematicsBundle((series,), (), (fit_result(series),), REVISION)
        restored = KinematicsBundle.from_dict(
            json.loads(json.dumps(bundle.to_dict(), allow_nan=False))
        )
        self.assertEqual(bundle, restored)


if __name__ == "__main__":
    unittest.main()
