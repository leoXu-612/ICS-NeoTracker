from __future__ import annotations

import unittest

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.ui.analysis_workspace_controller import KinematicsOperationRequest
from neo_tracker.ui.main_window import NeoTrackerWindow
from tests.test_application_kinematics_controller import (
    RecordingFitOperator,
    make_series,
    pump_until,
)


class PhysicsActionRegistryTests(unittest.TestCase):
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

    def test_inspector_and_menu_actions_emit_one_immutable_engine_request_each(self) -> None:
        window = self.make_window()
        source = make_series()
        window.set_physics_series((source,))
        requests: list[KinematicsOperationRequest] = []
        window.physicsOperationRequested.connect(requests.append)

        window.create_velocity_button.click()
        window.action_registry.action("physics.velocity").trigger()
        window.smooth_series_button.click()

        self.assertEqual([request.operation for request in requests], ["derivative", "derivative", "smooth"])
        self.assertEqual([request.configuration.order for request in requests[:2]], [1, 1])
        self.assertEqual({request.series_id for request in requests}, {source.series_id})
        self.assertFalse(hasattr(window.physics_inspector, "fit_operator"))

    def test_fit_export_and_residual_surfaces_share_canonical_actions(self) -> None:
        window = self.make_window()
        window.set_kinematics_fit_operator(RecordingFitOperator())
        source = make_series()
        window.set_physics_series((source,))

        window.action_registry.action("physics.fit").trigger()
        self.assertEqual(window.physics_workspace.current_page, "Fit")
        window.fit_panel.run_button.click()
        pump_until(lambda: not window.analysis_workspace_controller.busy)

        requests: list[KinematicsOperationRequest] = []
        window.physicsOperationRequested.connect(requests.append)
        window.export_physics_analysis_button.click()
        window.fit_panel.export_button.click()

        self.assertEqual([request.operation for request in requests], ["export", "export"])
        self.assertIs(
            requests[0].configuration["fit_result"],
            window.analysis_workspace_controller.state.fit_result,
        )

        self.assertFalse(window.analysis_workspace_controller.state.residual_visible)
        window.show_residual_button.click()
        self.assertTrue(window.analysis_workspace_controller.state.residual_visible)
        window.fit_panel.residual_checkbox.click()
        self.assertFalse(window.analysis_workspace_controller.state.residual_visible)
        window.action_registry.action("physics.residual").trigger()
        self.assertTrue(window.analysis_workspace_controller.state.residual_visible)
        self.assertEqual(window.action_registry.action("physics.residual").text(), "Hide Residual")


if __name__ == "__main__":
    unittest.main()
