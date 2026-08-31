from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.project import AnalysisWorkspaceSnapshot, NeoTrackerProject
from neo_tracker.ui.analysis_workspace_controller import FitDraft
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.integration_kinematics_support import close_window, pump_until, tracker_results


class KinematicsPersistenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(self) -> NeoTrackerWindow:
        window = NeoTrackerWindow()
        self.addCleanup(close_window, window)
        return window

    def build_saved_derivative(self, window: NeoTrackerWindow) -> str:
        times = np.arange(15, dtype=np.float64) * 0.05
        task = window.current_task
        task.pipeline.results = tracker_results(times)
        task.mark_results_changed()
        window.refresh_physics_series()
        pump_until(
            lambda: not window._kinematics_workspace_coordinator.busy
            and "filtered_state:x" in window._physics_series_by_id
        )
        self.assertTrue(
            window.analysis_workspace_controller.select_series("filtered_state:x")
        )
        self.assertTrue(window.analysis_workspace_controller.request_derivative(1))
        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)
        return next(
            item.analysis_id
            for item in task.analysis_workspace.definitions
            if item.derivative_config is not None
        )

    def test_save_open_rebuilds_definition_without_persisting_arrays(self) -> None:
        source_window = self.make_window()
        derived_id = self.build_saved_derivative(source_window)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "physics.ntproj"
            source_window._project_from_window(path).save(path)
            encoded = path.read_text(encoding="utf-8")
            payload = json.loads(encoded)

            self.assertEqual(payload["version"], 3)
            self.assertNotIn("data_b64", encoded)
            self.assertNotIn("fit_predicted", encoded)
            self.assertNotIn("fit_residuals", encoded)
            self.assertNotIn('"valid_mask"', encoded)

            restored_window = self.make_window()
            restored_window._load_project(path)
            pump_until(
                lambda: not restored_window._kinematics_workspace_coordinator.busy
                and derived_id in restored_window._physics_series_by_id
            )

        self.assertEqual(restored_window.current_task.task_id, source_window.current_task.task_id)
        self.assertEqual(
            restored_window._physics_definition_states[derived_id],
            "current",
        )
        np.testing.assert_allclose(
            restored_window._physics_series_by_id[derived_id].values[1:-1],
            2.0,
            atol=1e-11,
        )

    def test_changed_results_leave_saved_definition_disabled_as_stale(self) -> None:
        source_window = self.make_window()
        derived_id = self.build_saved_derivative(source_window)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stale.ntproj"
            source_window._project_from_window(path).save(path)
            project = NeoTrackerProject.load(path)
            project.tasks[0].results[0].filtered_state["x"] += 10.0

            restored_window = self.make_window()
            restored_window._apply_loaded_project(project, path)
            pump_until(
                lambda: not restored_window._kinematics_workspace_coordinator.busy
                and "filtered_state:x" in restored_window._physics_series_by_id
            )

        self.assertNotIn(derived_id, restored_window._physics_series_by_id)
        self.assertEqual(restored_window._physics_definition_states[derived_id], "stale")
        cursor = restored_window.physics_workspace.cursor_label
        self.assertIn("source frame 0", cursor.toolTip())
        self.assertIn("stale definition", cursor.toolTip())
        self.assertEqual(cursor.toolTip(), cursor.accessibleDescription())

    def test_v2_project_opens_with_empty_analysis_workspace_and_saves_as_v3(self) -> None:
        payload = {
            "format": "neo-tracker-project",
            "version": 2,
            "name": "legacy",
            "media_paths": [],
            "pipeline_library": [],
            "tasks": [{"media_path": None, "pipeline_key": "color_marker", "results": []}],
            "notes": "legacy note",
        }
        migrated = NeoTrackerProject.from_dict(payload)
        self.assertEqual(migrated.tasks[0].analysis_workspace.definitions, ())
        self.assertEqual(migrated.to_dict()["version"], 3)
        self.assertTrue(migrated.tasks[0].task_id)

    def test_fit_range_and_view_state_are_recomputed_on_open(self) -> None:
        source_window = self.make_window()
        times = np.arange(21, dtype=np.float64) * 0.05
        task = source_window.current_task
        task.pipeline.results = tracker_results(times)
        task.mark_results_changed()
        source_window.refresh_physics_series()
        pump_until(
            lambda: not source_window._kinematics_workspace_coordinator.busy
            and "filtered_state:x" in source_window._physics_series_by_id
        )
        source_window.analysis_workspace_controller.select_series("filtered_state:x")
        source_window._run_physics_fit(
            FitDraft(
                "filtered_state:x",
                "linear",
                0.15,
                0.75,
                use_valid_only=False,
            )
        )
        pump_until(
            lambda: not source_window.analysis_workspace_controller.busy
            and source_window.analysis_workspace_controller.state.fit_result is not None
        )
        fit_definition = next(
            item for item in task.analysis_workspace.definitions if item.fit_config is not None
        )
        source_window._set_project_clean()
        source_window.physics_workspace.show_page("Plot")
        source_window._toggle_physics_residual()
        fit_definition = next(
            item for item in task.analysis_workspace.definitions if item.fit_config is not None
        )
        self.assertEqual(
            fit_definition.view_state,
            {"page": "Plot", "residual_visible": True},
        )
        self.assertTrue(source_window._project_dirty)
        source_window._set_project_clean()
        source_window.physics_workspace.plot.set_selected_range(0.25, 0.65)
        source_window.physics_workspace.plot.rangeSelected.emit(0.25, 0.65)
        fit_definition = next(
            item for item in task.analysis_workspace.definitions if item.fit_config is not None
        )
        self.assertEqual(fit_definition.selected_range_s, (0.25, 0.65))
        self.assertTrue(source_window._project_dirty)
        source_window._set_project_clean()
        source_window.physics_workspace.plot.rangeSelected.emit(0.25, 0.65)
        self.assertFalse(source_window._project_dirty)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fit-replay.ntproj"
            source_window._project_from_window(path).save(path)
            restored_window = self.make_window()
            restored_window._load_project(path)
            pump_until(
                lambda: not restored_window._kinematics_workspace_coordinator.busy
                and not restored_window.analysis_workspace_controller.busy
                and restored_window.analysis_workspace_controller.state.fit_result is not None
            )

        fit_result = restored_window.analysis_workspace_controller.state.fit_result
        assert fit_result is not None
        self.assertAlmostEqual(float(fit_result.parameters[0]), 2.0, places=10)
        self.assertEqual(restored_window.physics_workspace.current_page, "Plot")
        self.assertEqual(restored_window.physics_workspace.plot.selected_range, (0.25, 0.65))
        self.assertEqual(restored_window.fit_panel.series_combo.currentData(), "filtered_state:x")
        self.assertEqual(restored_window.fit_panel.model_combo.currentData(), "linear")
        self.assertAlmostEqual(restored_window.fit_panel.range_start_spin.value(), 0.15)
        self.assertAlmostEqual(restored_window.fit_panel.range_end_spin.value(), 0.75)
        self.assertFalse(restored_window.fit_panel.valid_only_checkbox.isChecked())
        self.assertNotIn("Physics fit", restored_window._unapplied_draft_names())
        self.assertTrue(restored_window.analysis_workspace_controller.state.residual_visible)
        self.assertFalse(restored_window._project_dirty)
        self.assertEqual(
            restored_window._physics_definition_states[fit_definition.analysis_id],
            "current",
        )

    def test_hidden_fit_definition_remains_disabled_on_open(self) -> None:
        source_window = self.make_window()
        times = np.arange(11, dtype=np.float64) * 0.1
        task = source_window.current_task
        task.pipeline.results = tracker_results(times)
        task.mark_results_changed()
        source_window.refresh_physics_series()
        pump_until(
            lambda: not source_window._kinematics_workspace_coordinator.busy
            and "filtered_state:x" in source_window._physics_series_by_id
        )
        source_window.analysis_workspace_controller.select_series("filtered_state:x")
        source_window._run_physics_fit(FitDraft("filtered_state:x", "linear", 0.0, 1.0))
        pump_until(lambda: not source_window.analysis_workspace_controller.busy)
        fit_definition = next(
            item for item in task.analysis_workspace.definitions if item.fit_config is not None
        )
        hidden = replace(fit_definition, visible=False)
        task.analysis_workspace = AnalysisWorkspaceSnapshot((hidden,))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hidden-fit.ntproj"
            source_window._project_from_window(path).save(path)
            restored_window = self.make_window()
            restored_window._load_project(path)
            pump_until(
                lambda: not restored_window._kinematics_workspace_coordinator.busy
                and "filtered_state:x" in restored_window._physics_series_by_id
            )

        self.assertIsNone(restored_window.analysis_workspace_controller.state.fit_result)
        self.assertEqual(
            restored_window._physics_definition_states[hidden.analysis_id],
            "hidden",
        )


if __name__ == "__main__":
    unittest.main()
