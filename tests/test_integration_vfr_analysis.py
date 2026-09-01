from __future__ import annotations

import unittest
from dataclasses import replace

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget

import neo_tracker
from benchmarks.kinematics_fixtures import uniform_linear
from neo_tracker.application.kinematics_workspace_coordinator import (
    KinematicsWorkspaceCoordinator,
    KinematicsWorkspaceOutput,
    KinematicsWorkspaceTask,
    tracking_source_revision,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.kinematics import DerivativeConfig, snapshot_tracker_results
from tests.integration_kinematics_support import pump_until, tracker_results


TASK_ID = "00000000-0000-4000-8000-000000000321"


class VFRAnalysisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> KinematicsWorkspaceCoordinator:
        coordinator = KinematicsWorkspaceCoordinator(TaskSupervisor())
        self.addCleanup(coordinator.close)
        return coordinator

    def test_vfr_results_build_and_gap_aware_derivative_use_true_time(self) -> None:
        coordinator = self.make_coordinator()
        times = np.array(
            [0.0, 0.031, 0.071, 0.104, 0.162, 0.211, 0.257, 0.319, 0.354, 0.411, 0.463, 0.508]
        )
        results = tracker_results(times, lost_indices={4, 5}, quadratic=True)
        outputs: list[KinematicsWorkspaceOutput] = []
        coordinator.output_ready.connect(lambda _job, output: outputs.append(output))

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=results,
                    task_id=TASK_ID,
                    results_generation=0,
                    operation="build",
                    results=results,
                    units={"x": "m"},
                )
            )
        )
        pump_until(lambda: not coordinator.busy)
        source = next(
            item for item in outputs[-1].series if item.series_id == "filtered_state:x"
        )

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=results,
                    task_id=TASK_ID,
                    results_generation=0,
                    operation="derivative",
                    source=source,
                    configuration=DerivativeConfig(
                        "nonuniform_finite_difference",
                        1,
                        edge_policy="invalid",
                    ),
                )
            )
        )
        pump_until(lambda: not coordinator.busy)
        derived = outputs[-1].series[0]

        expected = 2.5 * times + 2.0
        np.testing.assert_allclose(
            derived.values[derived.valid_mask],
            expected[derived.valid_mask],
            atol=2e-12,
        )
        self.assertFalse(derived.valid_mask[3])
        self.assertFalse(derived.valid_mask[4])
        self.assertFalse(derived.valid_mask[5])
        self.assertFalse(derived.valid_mask[6])
        self.assertEqual(derived.unit, "m/s")

    def test_duplicate_true_timestamps_fail_without_nominal_fps_fallback(self) -> None:
        coordinator = self.make_coordinator()
        failed: list[str] = []
        outputs: list[KinematicsWorkspaceOutput] = []
        coordinator.failed.connect(lambda _job, message: failed.append(message))
        coordinator.output_ready.connect(lambda _job, output: outputs.append(output))
        results = tracker_results([0.0, 0.05, 0.05, 0.10])

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=results,
                    task_id=TASK_ID,
                    results_generation=0,
                    operation="build",
                    results=results,
                )
            )
        )
        pump_until(lambda: not coordinator.busy)

        self.assertEqual(outputs, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("strictly increasing", failed[0])

    def test_smoothing_worker_does_not_parse_numeric_strings(self) -> None:
        coordinator = self.make_coordinator()
        source = uniform_linear(21).sample_series()
        failed: list[str] = []
        outputs: list[KinematicsWorkspaceOutput] = []
        coordinator.failed.connect(lambda _job, message: failed.append(message))
        coordinator.output_ready.connect(lambda _job, output: outputs.append(output))

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=source,
                    task_id=TASK_ID,
                    results_generation=0,
                    operation="smooth",
                    source=source,
                    configuration={
                        "window_length": "9",
                        "polyorder": 2,
                        "uniformity_tolerance": 1e-3,
                    },
                )
            )
        )
        pump_until(lambda: not coordinator.busy)

        self.assertEqual(outputs, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("window_length must be an integer", failed[0])

    def test_smoothing_worker_preserves_one_sided_edge_policy(self) -> None:
        coordinator = self.make_coordinator()
        source = uniform_linear(21).sample_series()
        outputs: list[KinematicsWorkspaceOutput] = []
        coordinator.output_ready.connect(lambda _job, output: outputs.append(output))

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=source,
                    task_id=TASK_ID,
                    results_generation=0,
                    operation="smooth",
                    source=source,
                    configuration={
                        "method": "savgol_uniform",
                        "window_length": 5,
                        "polyorder": 2,
                        "uniformity_tolerance": 1e-3,
                        "edge_policy": "one_sided",
                        "gap_policy": "split",
                        "resample": False,
                    },
                )
            )
        )
        pump_until(lambda: not coordinator.busy)

        smoothed = outputs[-1].series[0]
        self.assertTrue(smoothed.valid_mask.all())
        self.assertEqual(smoothed.processing_chain[-1].parameters["edge_policy"], "one_sided")

    def test_source_revision_hashes_only_engine_consumed_fields(self) -> None:
        first = tracker_results([0.0, 0.1, 0.2])
        second = tracker_results([0.0, 0.1, 0.2])
        second[0].confidence = 0.25
        first_revision = tracking_source_revision(snapshot_tracker_results(first))
        second_revision = tracking_source_revision(snapshot_tracker_results(second))
        self.assertEqual(first_revision, second_revision)

        second[0].filtered_state["x"] += 1.0
        changed_revision = tracking_source_revision(snapshot_tracker_results(second))
        self.assertNotEqual(first_revision, changed_revision)

    def test_output_rejects_series_bound_to_a_different_revision(self) -> None:
        results = tracker_results([0.0, 0.1, 0.2])
        coordinator = self.make_coordinator()
        outputs: list[KinematicsWorkspaceOutput] = []
        coordinator.output_ready.connect(lambda _job, output: outputs.append(output))
        coordinator.start(
            KinematicsWorkspaceTask(
                owner=results,
                task_id=TASK_ID,
                results_generation=0,
                operation="build",
                results=results,
            )
        )
        pump_until(lambda: not coordinator.busy)
        source = outputs[0].series[0]

        with self.assertRaisesRegex(ValueError, "revision"):
            KinematicsWorkspaceOutput(
                TASK_ID,
                0,
                "build",
                source.source_revision,
                (replace(source, source_revision="sha256:different"),),
            )

    def test_root_package_exposes_stable_kinematics_contracts(self) -> None:
        self.assertIs(neo_tracker.DerivativeConfig, DerivativeConfig)
        self.assertIs(neo_tracker.TrackingSeriesBuilder, type(neo_tracker.TrackingSeriesBuilder()))
        self.assertTrue(callable(neo_tracker.KinematicsEngineRuntime().fit))


if __name__ == "__main__":
    unittest.main()
