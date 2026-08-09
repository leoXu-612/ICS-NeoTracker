from __future__ import annotations

import unittest

import numpy as np

from benchmarks.kinematics_fixtures import FIXTURE_REVISION, missing_segments, vfr_linear
from neo_tracker.core import TrackerResult
from neo_tracker.kinematics.runtime import CancellationToken, KinematicsCancelled
from neo_tracker.kinematics.series import TrackingSeriesBuilder, snapshot_tracker_results


class TrackingSeriesBuilderTests(unittest.TestCase):
    def test_builds_aligned_raw_filtered_and_filter_velocity_series(self) -> None:
        fixture = vfr_linear(40)
        slope = fixture.parameters["slope"]
        results = fixture.tracker_results(
            velocity_key="v_x_world",
            velocity_values=np.full(len(fixture.values), slope),
        )

        series = TrackingSeriesBuilder(units={"x_world": "m"}).build_series(
            results,
            source_revision=FIXTURE_REVISION,
        )
        by_id = {item.series_id: item for item in series}

        self.assertEqual(
            set(by_id),
            {"state:x_world", "filtered_state:x_world", "filter_velocity:v_x_world"},
        )
        np.testing.assert_array_equal(by_id["state:x_world"].frame_indices, fixture.frame_indices)
        np.testing.assert_allclose(by_id["filtered_state:x_world"].time_s, fixture.time_s)
        np.testing.assert_allclose(by_id["filter_velocity:v_x_world"].values, slope)
        self.assertEqual(by_id["filtered_state:x_world"].unit, "m")
        self.assertEqual(by_id["filter_velocity:v_x_world"].unit, "m/s")

    def test_lost_samples_remain_frame_aligned_and_invalid(self) -> None:
        fixture = missing_segments(90)
        series = TrackingSeriesBuilder(units={"x_world": "m"}).build_series(
            fixture.tracker_results(),
            source_revision=FIXTURE_REVISION,
        )
        filtered = next(item for item in series if item.series_id == "filtered_state:x_world")

        self.assertEqual(len(filtered), 90)
        np.testing.assert_array_equal(filtered.frame_indices, fixture.frame_indices)
        np.testing.assert_array_equal(filtered.valid_mask, fixture.valid_mask)
        self.assertTrue(np.isnan(filtered.values[~fixture.valid_mask]).all())

    def test_snapshot_detaches_mutable_tracker_result_state(self) -> None:
        result = TrackerResult(3, 0.125, {"theta": 0.4}, {"theta": 0.5}, 0.9, "ok")
        snapshot = snapshot_tracker_results([result])
        result.state["theta"] = 9.0
        result.filtered_state["theta"] = 10.0

        series = TrackingSeriesBuilder().build_series(
            snapshot,
            source_revision=FIXTURE_REVISION,
        )
        by_id = {item.series_id: item for item in series}
        self.assertEqual(by_id["state:theta"].values[0], 0.4)
        self.assertEqual(by_id["filtered_state:theta"].values[0], 0.5)
        self.assertEqual(by_id["filtered_state:theta"].unit, "rad")

    def test_duplicate_or_non_finite_timestamps_are_rejected(self) -> None:
        duplicate = [
            TrackerResult(0, 0.0, {"x_px": 0.0}, {"x_px": 0.0}, 1.0, "ok"),
            TrackerResult(1, 0.0, {"x_px": 1.0}, {"x_px": 1.0}, 1.0, "ok"),
        ]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            TrackingSeriesBuilder().build_series(duplicate, source_revision=FIXTURE_REVISION)

        non_finite = [TrackerResult(0, float("nan"), {}, {}, 0.0, "lost")]
        with self.assertRaisesRegex(ValueError, "finite"):
            TrackingSeriesBuilder().build_series(non_finite, source_revision=FIXTURE_REVISION)

    def test_cancelled_build_has_no_partial_public_result(self) -> None:
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(KinematicsCancelled):
            TrackingSeriesBuilder().build_series(
                vfr_linear(1_000).tracker_results(),
                source_revision=FIXTURE_REVISION,
                cancellation=token,
            )


if __name__ == "__main__":
    unittest.main()
