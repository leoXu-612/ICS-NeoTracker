from __future__ import annotations

import unittest

from benchmarks.kinematics_fixtures import FIXTURE_REVISION, uniform_linear
from neo_tracker.kinematics.protocols import DerivativeOperator, FitOperator, SeriesBuilder
from neo_tracker.kinematics.runtime import KinematicsEngineRuntime
from neo_tracker.kinematics.types import DerivativeConfig, FitRequest, FitStatus


class KinematicsEngineRuntimeTests(unittest.TestCase):
    def test_facade_implements_all_runtime_neutral_protocols(self) -> None:
        engine = KinematicsEngineRuntime(units={"x_world": "m"})
        self.assertIsInstance(engine, SeriesBuilder)
        self.assertIsInstance(engine, DerivativeOperator)
        self.assertIsInstance(engine, FitOperator)

    def test_facade_builds_derives_and_fits_one_revision(self) -> None:
        fixture = uniform_linear()
        engine = KinematicsEngineRuntime(units={"x_world": "m"})
        series = engine.build_series(
            fixture.tracker_results(),
            source_revision=FIXTURE_REVISION,
        )
        source = next(item for item in series if item.series_id == "filtered_state:x_world")
        velocity = engine.derive(
            source,
            DerivativeConfig(
                "nonuniform_finite_difference",
                1,
                edge_policy="one_sided",
            ),
        )
        fit = engine.fit(
            source,
            FitRequest(
                source.series_id,
                "linear",
                float(source.time_s[0]),
                float(source.time_s[-1]),
                source.source_revision,
            ),
        )

        self.assertEqual(velocity.source_revision, FIXTURE_REVISION)
        self.assertEqual(velocity.unit, "m/s")
        self.assertIs(fit.status, FitStatus.OK)
        self.assertEqual(fit.source_revision, FIXTURE_REVISION)


if __name__ == "__main__":
    unittest.main()
