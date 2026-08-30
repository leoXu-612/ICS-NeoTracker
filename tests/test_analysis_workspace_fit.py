from __future__ import annotations

from dataclasses import replace
import unittest
import time
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaInfo
from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot
from neo_tracker.ui.analysis_workspace_controller import FitDraft
from neo_tracker.ui.fit_panel import FitPanel
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.test_application_kinematics_controller import (
    BlockingFitOperator,
    RecordingFitOperator,
    UnavailableFitOperator,
    fit_result,
    make_series,
    pump_until,
)


class FitPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_panel_emits_user_draft_not_a_numerical_result(self) -> None:
        panel = FitPanel()
        source = make_series()
        panel.set_series((source,))
        panel.model_combo.setCurrentText("Linear")
        panel.range_start_spin.setValue(0.2)
        panel.range_end_spin.setValue(0.8)
        drafts: list[FitDraft] = []
        panel.runRequested.connect(drafts.append)

        panel.run_button.click()

        self.assertEqual(drafts, [FitDraft(source.series_id, "linear", 0.2, 0.8)])
        self.assertFalse(hasattr(panel, "fit_operator"))

    def test_nonlinear_parameters_are_explicit_json_and_validation_is_textual(self) -> None:
        panel = FitPanel()
        panel.set_series((make_series(),))
        panel.model_combo.setCurrentText("Sinusoidal")
        self.assertTrue(panel.initial_parameters_edit.isVisibleTo(panel) or not panel.isVisible())
        panel.initial_parameters_edit.setText('{"omega": 3.0}')
        panel.bounds_edit.setText('{"omega": [0.1, 10.0]}')

        draft = panel.draft()

        self.assertEqual(draft.initial_parameters, {"omega": 3.0})
        self.assertEqual(draft.bounds, {"omega": (0.1, 10.0)})
        panel.initial_parameters_edit.setText("not-json")
        panel.run_button.click()
        self.assertIn("valid JSON", panel.status_label.text())

    def test_result_summary_shows_parameters_units_metrics_and_samples(self) -> None:
        panel = FitPanel()
        source = make_series()
        panel.set_series((source,))
        request = FitDraft(source.series_id, "linear", 0.0, 1.0).to_request(source)
        result = fit_result(source, request)

        panel.show_result(result)

        self.assertIn("R² 1", panel.summary_label.text())
        self.assertIn("RMSE 0", panel.summary_label.text())
        self.assertIn("11 samples", panel.summary_label.text())
        self.assertEqual(panel.parameter_table.rowCount(), 2)
        self.assertEqual(panel.parameter_table.item(0, 0).text(), "slope")
        self.assertEqual(panel.parameter_table.item(0, 2).text(), "m/s")
        self.assertTrue(panel.residual_checkbox.isEnabled())
        self.assertTrue(panel.export_button.isEnabled())

    def test_busy_state_exposes_cancel_and_disables_mutating_inputs(self) -> None:
        panel = FitPanel()
        panel.set_series((make_series(),))
        panel.set_busy(True)

        self.assertFalse(panel.run_button.isEnabled())
        self.assertTrue(panel.cancel_button.isEnabled())
        self.assertFalse(panel.series_combo.isEnabled())
        self.assertFalse(panel.valid_only_checkbox.isEnabled())
        self.assertIn("running", panel.status_label.text().lower())

    def test_restored_draft_roundtrips_controls_without_becoming_dirty(self) -> None:
        panel = FitPanel()
        source = make_series()
        panel.set_series((source,))
        restored = FitDraft(
            source.series_id,
            "sinusoidal",
            0.2,
            0.8,
            initial_parameters={"omega": 3.0},
            bounds={"omega": (0.1, 10.0)},
            use_valid_only=False,
        )

        self.assertTrue(panel.restore_draft(restored))

        self.assertEqual(panel.draft(), restored)
        self.assertFalse(panel.is_dirty())

        panel.range_end_spin.setValue(0.7)
        self.assertFalse(panel.restore_draft(restored))
        self.assertAlmostEqual(panel.range_end_spin.value(), 0.7)


class MainWindowFitIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(self) -> NeoTrackerWindow:
        window = NeoTrackerWindow()

        def close_cleanly() -> None:
            if window.analysis_workspace_controller.busy:
                window.analysis_workspace_controller.cancel("Test cleanup.")
                pump_until(lambda: not window.analysis_workspace_controller.busy)
            window._discard_unapplied_drafts(show_status=False)
            window._set_project_clean()
            window.close()
            QCoreApplication.processEvents()

        self.addCleanup(close_cleanly)
        return window

    def test_engine_protocol_fit_updates_panel_plot_and_selection(self) -> None:
        window = self.make_window()
        operator = RecordingFitOperator()
        window.set_kinematics_fit_operator(operator)
        source = make_series()
        window.current_task.media_info = MediaInfo(
            fps=30.0,
            frame_count=len(source),
            width=640,
            height=360,
            duration_s=1.0,
            available=True,
        )
        window.set_physics_series((source,))
        self.assertTrue(window.fit_panel.run_button.isEnabled())

        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        self.assertEqual(window.fit_panel.parameter_table.rowCount(), 2)
        self.assertIsNotNone(window.physics_workspace.plot._fit_series)
        self.assertIsNotNone(window.selection_session.state.selected_fit_id)
        self.assertEqual(window.analysis_workspace_controller.state.status, "complete")
        self.assertNotIn("Physics fit", window._unapplied_draft_names())

        window.fit_panel.range_end_spin.setValue(0.8)
        QCoreApplication.processEvents()
        self.assertEqual(window.analysis_workspace_controller.state.status, "dirty")
        self.assertIsNone(window.physics_workspace.plot._fit_series)
        self.assertIn("Physics fit", window._unapplied_draft_names())

    def test_equivalent_parameter_order_reuses_persisted_fit_definition(self) -> None:
        window = self.make_window()
        window.set_kinematics_fit_operator(RecordingFitOperator())
        window.set_physics_series((make_series(),))
        window.fit_panel.model_combo.setCurrentText("Sinusoidal")
        window.fit_panel.initial_parameters_edit.setText(
            '{"omega": 3.0, "amplitude": 1.0}'
        )
        window.fit_panel.bounds_edit.setText(
            '{"omega": [0.1, 10.0], "amplitude": [0.1, 2.0]}'
        )
        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        window.fit_panel.initial_parameters_edit.setText(
            '{"amplitude": 1.0, "omega": 3.0}'
        )
        window.fit_panel.bounds_edit.setText(
            '{"amplitude": [0.1, 2.0], "omega": [0.1, 10.0]}'
        )
        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        definitions = [
            item
            for item in window.current_task.analysis_workspace.definitions
            if item.fit_config is not None
        ]
        self.assertEqual(len(definitions), 1)

    def test_validity_policy_creates_a_distinct_persisted_fit_definition(self) -> None:
        window = self.make_window()
        window.set_kinematics_fit_operator(RecordingFitOperator())
        source = make_series()
        window.set_physics_series((source,))

        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)
        window.fit_panel.valid_only_checkbox.setChecked(False)
        self.assertIn("Physics fit", window._unapplied_draft_names())
        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        definitions = [
            item
            for item in window.current_task.analysis_workspace.definitions
            if item.fit_config is not None
        ]
        self.assertEqual(len(definitions), 2)

    def test_unrun_fit_settings_are_protected_across_task_switch(self) -> None:
        window = self.make_window()
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/take-a.mp4",
                        pipeline_key=window.default_pipeline_key,
                    ),
                    ProjectTaskSnapshot(
                        media_path="/offline/take-b.mp4",
                        pipeline_key=window.default_pipeline_key,
                    ),
                ],
            )
        )
        original_task = window.current_task
        source = make_series()
        window.set_physics_series((source,))
        window.fit_panel.range_end_spin.setValue(0.8)
        QCoreApplication.processEvents()
        window.set_physics_series(
            (source, replace(source, series_id="derived:v", name="Velocity"))
        )

        self.assertIn("Physics fit", window._unapplied_draft_names())
        prompts: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: prompts.append((action, names)) or False
        )
        window.task_list.setCurrentRow(1)

        self.assertIs(window.current_task, original_task)
        self.assertEqual(window.task_list.currentRow(), 0)
        self.assertAlmostEqual(window.fit_panel.range_end_spin.value(), 0.8)
        self.assertEqual(prompts, [("switching to take-b.mp4", ("Physics fit",))])

        prompts.clear()
        self.assertFalse(
            window._confirm_configuration_change(original_task, "changing calibration")
        )
        self.assertEqual(prompts, [("changing calibration", ("Physics fit",))])

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window.task_list.setCurrentRow(1)
        self.assertIs(window.current_task, window.tasks[1])
        self.assertNotIn("Physics fit", window._unapplied_draft_names())

    def test_review_edit_does_not_silently_discard_unrun_fit_settings(self) -> None:
        window = self.make_window()
        result = TrackerResult(
            0,
            0.0,
            {"x_px": 12.0, "y_px": 20.0},
            {"x_px": 12.0, "y_px": 20.0},
            0.9,
            "ok",
        )
        window.current_task.pipeline.results = [result]
        window.set_physics_series((make_series(),))
        window.fit_panel.range_end_spin.setValue(0.8)
        QCoreApplication.processEvents()
        prompts: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: prompts.append((action, names)) or False
        )

        window._mark_current_result_lost()

        self.assertIs(window.current_task.pipeline.results[0], result)
        self.assertAlmostEqual(window.fit_panel.range_end_spin.value(), 0.8)
        self.assertEqual(prompts, [("marking a result lost", ("Physics fit",))])

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window._mark_current_result_lost()
        self.assertEqual(window.current_task.pipeline.results[0].status, "manual_lost")
        self.assertNotIn("Physics fit", window._unapplied_draft_names())

    def test_non_destructive_media_relink_preserves_fit_draft_without_prompt(self) -> None:
        window = self.make_window()
        task = window.current_task
        task.media_path = "/offline/original.mp4"
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=640,
            height=360,
            duration_s=4.0,
            available=True,
        )
        window.set_physics_series((make_series(),))
        window.fit_panel.range_end_spin.setValue(0.8)
        replacement = "/offline/replacement.mp4"
        assessment = window._stage_media_relink(replacement, task.media_info)
        prompts: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: prompts.append((action, names)) or False
        )

        with patch.object(window, "_queue_preview_decode"):
            applied = window._apply_media_relink()

        self.assertFalse(assessment.clear_results)
        self.assertTrue(applied)
        self.assertEqual(prompts, [])
        self.assertEqual(task.media_path, replacement)
        self.assertAlmostEqual(window.fit_panel.range_end_spin.value(), 0.8)
        self.assertIn("Physics fit", window._unapplied_draft_names())

    def test_destructive_media_relink_protects_fit_draft_until_confirmed(self) -> None:
        window = self.make_window()
        task = window.current_task
        task.media_path = "/offline/original.mp4"
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=640,
            height=360,
            duration_s=4.0,
            available=False,
        )
        result = TrackerResult(
            0,
            0.0,
            {"x_px": 12.0},
            {"x_px": 12.0},
            0.9,
            "ok",
        )
        task.pipeline.results = [result]
        window.set_physics_series((make_series(),))
        window.fit_panel.range_end_spin.setValue(0.8)
        replacement = "/offline/replacement.mp4"
        assessment = window._stage_media_relink(
            replacement,
            MediaInfo(
                fps=60.0,
                frame_count=240,
                width=1280,
                height=720,
                duration_s=4.0,
                available=True,
            ),
        )
        prompts: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: prompts.append((action, names)) or False
        )

        self.assertFalse(window._apply_media_relink())

        self.assertTrue(assessment.clear_results)
        self.assertEqual(prompts, [("applying media replacement", ("Physics fit",))])
        self.assertEqual(task.media_path, "/offline/original.mp4")
        self.assertIs(task.pipeline.results[0], result)
        self.assertAlmostEqual(window.fit_panel.range_end_spin.value(), 0.8)

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        with patch.object(window, "_queue_preview_decode"):
            self.assertTrue(window._apply_media_relink())
        self.assertEqual(task.pipeline.results, [])
        self.assertNotIn("Physics fit", window._unapplied_draft_names())

    def test_close_cancels_active_fit_and_reaches_idle(self) -> None:
        window = self.make_window()
        operator = BlockingFitOperator()
        window.set_kinematics_fit_operator(operator)
        source = make_series()
        window.set_physics_series((source,))
        window.fit_panel.run_button.click()
        pump_until(operator.entered.is_set)
        window._set_project_clean()

        window.close()
        deadline = time.monotonic() + 2.0
        while window.analysis_workspace_controller.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.001)
        QCoreApplication.processEvents()

        self.assertTrue(operator.canceled.is_set())
        self.assertFalse(window.analysis_workspace_controller.busy)
        self.assertTrue(window._background_tasks.idle)

    def test_unavailable_fit_keeps_series_export_but_not_residual(self) -> None:
        window = self.make_window()
        window.set_kinematics_fit_operator(UnavailableFitOperator())
        source = make_series()
        window.set_physics_series((source,))

        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        state = window.analysis_workspace_controller.state
        self.assertEqual(state.status, "unavailable")
        self.assertIn("unavailable", window.fit_panel.status_label.text().lower())
        self.assertIsNone(state.fit_result)
        self.assertIsNone(window.physics_workspace.plot._fit_series)
        self.assertIsNone(window.selection_session.state.selected_fit_id)
        self.assertFalse(window.fit_panel.residual_checkbox.isEnabled())
        self.assertTrue(window.fit_panel.export_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
