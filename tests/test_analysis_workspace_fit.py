from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.ui.analysis_workspace_controller import FitDraft
from neo_tracker.ui.fit_panel import FitPanel
from tests.test_application_kinematics_controller import fit_result, make_series


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
        self.assertIn("running", panel.status_label.text().lower())


if __name__ == "__main__":
    unittest.main()
