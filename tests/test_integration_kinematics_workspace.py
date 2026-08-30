from __future__ import annotations

import gc
import unittest
from dataclasses import replace
from threading import Event

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.kinematics_workspace_coordinator import (
    KinematicsWorkspaceCoordinator,
    KinematicsWorkspaceTask,
)
from neo_tracker.application.kinematics_coordinator import (
    KinematicsFitCoordinator,
    KinematicsFitTask,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.kinematics import (
    FitRequest,
    KinematicsEngineRuntime,
    SampleSeries,
)
from neo_tracker.media import MediaInfo
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.integration_kinematics_support import close_window, pump_until, tracker_results


class KinematicsWorkspaceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(self) -> NeoTrackerWindow:
        window = NeoTrackerWindow()
        self.addCleanup(close_window, window)
        return window

    def attach_results(self, window: NeoTrackerWindow, count: int = 17) -> None:
        times = np.cumsum(np.linspace(0.025, 0.055, count))
        times -= times[0]
        task = window.current_task
        task.pipeline.results = tracker_results(times)
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=count,
            width=640,
            height=360,
            duration_s=float(times[-1]),
            available=True,
        )
        task.mark_results_changed()
        window.refresh_physics_series()
        pump_until(
            lambda: not window._kinematics_workspace_coordinator.busy
            and "filtered_state:x" in window._physics_series_by_id
        )

    def test_results_build_derivative_and_true_time_selection_flow(self) -> None:
        window = self.make_window()
        self.attach_results(window)

        source = window._physics_series_by_id["filtered_state:x"]
        self.assertTrue(source.source_revision.startswith("sha256:"))
        np.testing.assert_allclose(
            source.time_s,
            [result.time_s for result in window.current_task.pipeline.results],
        )
        self.assertTrue(window.analysis_workspace_controller.select_series(source.series_id))
        self.assertTrue(window.analysis_workspace_controller.request_derivative(1))
        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

        derived = next(
            item
            for item in window._physics_series_by_id.values()
            if item.series_id.startswith("filtered_state:x:derivative:1:")
        )
        np.testing.assert_allclose(derived.values[derived.valid_mask], 2.0, atol=1e-11)
        self.assertEqual(derived.unit, "")  # unknown source units are not guessed
        self.assertIn(
            derived.series_id,
            {item.analysis_id for item in window.current_task.analysis_workspace.definitions},
        )

        self.assertTrue(window.analysis_workspace_controller.select_series(derived.series_id))
        window.physics_workspace.series_table.selectRow(8)
        QApplication.processEvents()
        self.assertEqual(window.current_task.preview_frame_index, 8)
        self.assertEqual(window.selection_session.state.selected_sample_index, 8)
        self.assertAlmostEqual(
            window.selection_session.state.selected_time_s,
            derived.time_s[8],
        )

    def test_generation_change_cancels_and_rejects_late_derivative(self) -> None:
        window = self.make_window()
        self.attach_results(window)
        source = window._physics_series_by_id["filtered_state:x"]
        self.assertTrue(window.analysis_workspace_controller.select_series(source.series_id))
        self.assertTrue(window.analysis_workspace_controller.request_derivative(1))

        window.current_task.mark_results_changed()
        window._reset_physics_context(schedule_build=False)
        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

        self.assertEqual(window._physics_series_by_id, {})
        self.assertEqual(window.current_task.analysis_workspace.definitions, ())
        self.assertIn(
            f"results:{window.current_task.results_generation}",
            window.selection_session.state.selected_result_identity,
        )

    def test_uniform_smoothing_persists_explicit_no_resampling_config(self) -> None:
        window = self.make_window()
        task = window.current_task
        times = np.arange(15, dtype=np.float64) * 0.05
        task.pipeline.results = tracker_results(times)
        task.mark_results_changed()
        window.refresh_physics_series()
        pump_until(
            lambda: not window._kinematics_workspace_coordinator.busy
            and "filtered_state:x" in window._physics_series_by_id
        )
        window.analysis_workspace_controller.select_series("filtered_state:x")

        self.assertTrue(
            window.analysis_workspace_controller.request_smoothing(
                window_length=5,
                polyorder=2,
            )
        )
        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)

        smoothed = next(
            item
            for item in window._physics_series_by_id.values()
            if ":savgol:" in item.series_id
        )
        np.testing.assert_allclose(smoothed.values[smoothed.valid_mask], source_values := (
            2.0 * smoothed.time_s[smoothed.valid_mask] + 1.0
        ), atol=1e-11)
        self.assertEqual(len(source_values), 11)
        definition = next(
            item
            for item in task.analysis_workspace.definitions
            if item.analysis_id == smoothed.series_id
        )
        assert definition.smoothing_config is not None
        self.assertIs(definition.smoothing_config["resample"], False)

    def test_series_limit_is_rejected_before_any_session_state_changes(self) -> None:
        window = self.make_window()
        base = SampleSeries(
            series_id="state:0",
            name="State 0",
            frame_indices=np.arange(3, dtype=np.int64),
            time_s=np.array([0.0, 0.1, 0.2]),
            values=np.array([0.0, 1.0, 2.0]),
            valid_mask=np.ones(3, dtype=bool),
            unit="m",
            source_kind="state",
            source_revision="sha256:series-limit",
        )
        series = tuple(
            replace(base, series_id=f"state:{index}", name=f"State {index}")
            for index in range(65)
        )
        before = window.selection_session.state

        with self.assertRaisesRegex(ValueError, "at most 64"):
            window.set_physics_series(series)

        self.assertEqual(window.selection_session.state, before)
        self.assertEqual(window._physics_series_by_id, {})

    def test_close_active_series_build_reaches_supervisor_idle(self) -> None:
        original_gc_thresholds = gc.get_threshold()
        window = self.make_window()
        task = window.current_task
        times = np.arange(50_000, dtype=np.float64) * 0.01
        task.pipeline.results = tracker_results(times)
        task.mark_results_changed()
        window._set_project_clean()
        window.refresh_physics_series()
        pump_until(lambda: window._kinematics_workspace_coordinator.busy)
        self.assertIn("Building immutable physical series", window.physics_workspace.cursor_label.toolTip())
        self.assertEqual(
            window.physics_workspace.cursor_label.toolTip(),
            window.physics_workspace.cursor_label.accessibleDescription(),
        )

        window.close()
        pump_until(lambda: window._background_tasks.idle, timeout_s=10.0)

        self.assertFalse(window._kinematics_workspace_coordinator.busy)
        self.assertTrue(window._background_tasks.ready_to_close)
        self.assertEqual(gc.get_threshold(), original_gc_thresholds)

    def test_failed_series_build_clears_checking_cursor_state(self) -> None:
        window = self.make_window()
        task = window.current_task
        task.pipeline.results = [object()]
        task.mark_results_changed()

        window.refresh_physics_series()
        pump_until(lambda: "Physics operation failed:" in window.statusBar().currentMessage())
        task.pipeline.results = []

        self.assertFalse(window._kinematics_workspace_coordinator.busy)
        self.assertNotIn("checking", window.physics_workspace.cursor_label.text())
        self.assertEqual(
            window.physics_workspace.cursor_label.toolTip(),
            window.physics_workspace.cursor_label.accessibleDescription(),
        )

    def test_cancel_after_worker_completed_still_emits_exactly_one_terminal(self) -> None:
        supervisor = TaskSupervisor()
        coordinator = KinematicsWorkspaceCoordinator(supervisor)
        self.addCleanup(coordinator.close)
        terminals: list[str] = []
        worker_completed = Event()
        coordinator.output_ready.connect(lambda _job, _output: terminals.append("ready"))
        coordinator.failed.connect(lambda _job, _message: terminals.append("failed"))
        coordinator.canceled.connect(lambda _job: terminals.append("canceled"))
        results = tracker_results(np.arange(100_000, dtype=np.float64) * 0.01)

        self.assertTrue(
            coordinator.start(
                KinematicsWorkspaceTask(
                    owner=results,
                    task_id="00000000-0000-4000-8000-000000000654",
                    results_generation=0,
                    operation="build",
                    results=results,
                )
            )
        )
        assert coordinator._worker is not None
        coordinator._worker.completed.connect(
            lambda _output: worker_completed.set(),
            Qt.ConnectionType.DirectConnection,
        )
        self.assertTrue(worker_completed.wait(10.0))
        self.assertTrue(coordinator.cancel())
        pump_until(lambda: not coordinator.busy, timeout_s=10.0)

        self.assertEqual(terminals, ["canceled"])
        self.assertTrue(supervisor.idle)

    def test_fit_cancel_after_completed_signal_has_one_terminal(self) -> None:
        supervisor = TaskSupervisor()
        coordinator = KinematicsFitCoordinator(supervisor)
        self.addCleanup(coordinator.close)
        terminals: list[str] = []
        worker_completed = Event()
        coordinator.result_ready.connect(lambda _job, _result: terminals.append("ready"))
        coordinator.failed.connect(lambda _job, _message: terminals.append("failed"))
        coordinator.canceled.connect(lambda _job: terminals.append("canceled"))
        times = np.linspace(0.0, 10.0, 100_000)
        source = SampleSeries(
            series_id="synthetic:sine",
            name="Synthetic sine",
            frame_indices=np.arange(len(times), dtype=np.int64),
            time_s=times,
            values=2.0 * np.sin(1.7 * times + 0.4) + 3.0,
            valid_mask=np.ones(len(times), dtype=bool),
            unit="m",
            source_kind="synthetic",
            source_revision="sha256:fit-cancel-race",
        )
        request = FitRequest(
            source.series_id,
            "sinusoidal",
            float(times[0]),
            float(times[-1]),
            source.source_revision,
        )

        self.assertTrue(
            coordinator.start(
                KinematicsFitTask(
                    owner=source,
                    series=source,
                    request=request,
                    operator=KinematicsEngineRuntime(),
                )
            )
        )
        assert coordinator.worker is not None
        coordinator.worker.completed.connect(
            lambda _result: worker_completed.set(),
            Qt.ConnectionType.DirectConnection,
        )
        self.assertTrue(worker_completed.wait(10.0))
        self.assertTrue(coordinator.cancel())
        pump_until(lambda: not coordinator.busy, timeout_s=10.0)

        self.assertEqual(terminals, ["canceled"])
        self.assertTrue(supervisor.idle)


if __name__ == "__main__":
    unittest.main()
