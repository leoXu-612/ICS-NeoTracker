from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace

from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.kinematics import DerivativeConfig, ProcessingStep
from neo_tracker.ui.analysis_workspace_controller import (
    AnalysisWorkspaceController,
    FitDraft,
    KinematicsOperationRequest,
)
from neo_tracker.ui.inspectors import PhysicsInspector
from neo_tracker.ui.series_table_model import SeriesTableModel
from tests.test_application_kinematics_controller import fit_result, make_series


class AnalysisOperationRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_derivative_and_smoothing_are_immutable_engine_requests(self) -> None:
        controller = AnalysisWorkspaceController(TaskSupervisor())
        self.addCleanup(controller.close)
        source = make_series()
        controller.set_series(object(), (source,))
        requests: list[KinematicsOperationRequest] = []
        controller.operationRequested.connect(requests.append)

        self.assertTrue(controller.request_derivative(1))
        self.assertTrue(controller.request_derivative(2))
        self.assertTrue(controller.request_smoothing(window_length=9, polyorder=2))

        self.assertEqual([request.operation for request in requests], ["derivative", "derivative", "smooth"])
        self.assertEqual([request.series_id for request in requests], [source.series_id] * 3)
        self.assertEqual([request.source_revision for request in requests], [source.source_revision] * 3)
        self.assertEqual([request.configuration.order for request in requests[:2]], [1, 2])
        self.assertIsInstance(requests[0].configuration, DerivativeConfig)
        self.assertEqual(requests[2].configuration["method"], "savgol_uniform")
        with self.assertRaises(TypeError):
            requests[2].configuration["window_length"] = 7
        with self.assertRaises(FrozenInstanceError):
            requests[0].series_id = "changed"

    def test_smoothing_rejects_implicit_or_invalid_resampling_configuration(self) -> None:
        controller = AnalysisWorkspaceController(TaskSupervisor())
        self.addCleanup(controller.close)
        controller.set_series(object(), (make_series(),))
        requests: list[KinematicsOperationRequest] = []
        controller.operationRequested.connect(requests.append)

        with self.assertRaisesRegex(ValueError, "odd"):
            controller.request_smoothing(window_length=10)
        with self.assertRaisesRegex(ValueError, "polyorder"):
            controller.request_smoothing(window_length=5, polyorder=5)

        self.assertEqual(requests, [])


class PhysicsInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_inspector_exposes_series_sample_and_fit_provenance_in_text(self) -> None:
        source = make_series()
        derived = type(source)(
            series_id="velocity:x",
            name="Velocity x",
            frame_indices=source.frame_indices,
            time_s=source.time_s,
            values=source.values,
            valid_mask=source.valid_mask,
            unit="m/s",
            source_kind="derived",
            source_revision=source.source_revision,
            processing_chain=(ProcessingStep("differentiate", {"order": 1}),),
        )
        inspector = PhysicsInspector()

        inspector.show_series(derived)
        self.assertIn("Derived Series", inspector.object_label.text())
        self.assertEqual(inspector.unit_label.text(), "m/s")
        self.assertIn("differentiate", inspector.processing_label.text())

        inspector.show_sample(derived, 2, match="nearest")
        self.assertIn("frame 2", inspector.object_label.text())
        self.assertIn("nearest match", inspector.validity_label.text())
        self.assertIn("true time", inspector.range_label.text())

        request = FitDraft(source.series_id, "linear", 0.0, 1.0).to_request(source)
        inspector.show_fit(fit_result(source, request))
        self.assertIn("Fit", inspector.object_label.text())
        self.assertIn("R²", inspector.quality_label.text())
        self.assertIn("slope=", inspector.processing_label.text())
        self.assertIn("status ok", inspector.validity_label.text())

        inspector.clear()
        self.assertIn("No analysis selection", inspector.accessibleDescription())
        self.assertNotIn("slope=", inspector.accessibleDescription())
        self.assertIn("No analysis selection", inspector.object_label.toolTip())

    def test_base_series_provenance_is_not_misclassified_as_derived(self) -> None:
        source = replace(
            make_series(),
            source_kind="state",
            processing_chain=(ProcessingStep("state", {"key": "x"}),),
        )
        inspector = PhysicsInspector()
        model = SeriesTableModel()

        inspector.show_series(source)
        model.set_series(source)

        self.assertIn("Base Series", inspector.object_label.text())
        self.assertTrue(str(model.data(model.index(0, 4))).startswith("RAW"))


if __name__ == "__main__":
    unittest.main()
