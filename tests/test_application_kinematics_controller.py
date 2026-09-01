from __future__ import annotations

import time
import unittest
from dataclasses import replace
from threading import Event

import numpy as np
from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.kinematics import FitRequest, FitResult, FitStatus, SampleSeries
from neo_tracker.ui.analysis_workspace_controller import (
    AnalysisWorkspaceController,
    FitDraft,
    KinematicsOperationRequest,
)


def make_series(*, revision: str = "results:fit-a") -> SampleSeries:
    times = np.linspace(0.0, 1.0, 11)
    return SampleSeries(
        series_id="filtered:x",
        name="Filtered x",
        frame_indices=np.arange(11, dtype=np.int64),
        time_s=times,
        values=2.0 * times + 1.0,
        valid_mask=np.ones(11, dtype=bool),
        unit="m",
        source_kind="filtered_state",
        source_revision=revision,
    )


def fit_result(series: SampleSeries, request: FitRequest) -> FitResult:
    mask = (
        series.valid_mask
        & (series.time_s >= request.range_start_s)
        & (series.time_s <= request.range_end_s)
    )
    predicted = 2.0 * series.time_s + 1.0
    residuals = series.values - predicted
    return FitResult(
        series_id=series.series_id,
        model=request.model,
        parameter_names=("slope", "intercept"),
        parameters=np.array([2.0, 1.0]),
        parameter_units=("m/s", "m"),
        standard_errors=np.array([0.0, 0.0]),
        covariance=np.zeros((2, 2)),
        predicted=predicted,
        residuals=residuals,
        valid_mask=mask,
        rmse=0.0,
        r_squared=1.0,
        sample_count=int(np.count_nonzero(mask)),
        range_start_s=request.range_start_s,
        range_end_s=request.range_end_s,
        source_revision=series.source_revision,
        status=FitStatus.OK,
    )


def pump_until(predicate, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if not predicate():
        raise AssertionError("kinematics controller did not reach the expected state")


class RecordingFitOperator:
    def __init__(self) -> None:
        self.requests: list[FitRequest] = []
        self.thread: QThread | None = None

    def fit(self, series, request, *, cancellation=None):
        self.requests.append(request)
        self.thread = QThread.currentThread()
        return fit_result(series, request)


class BlockingFitOperator(RecordingFitOperator):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.canceled = Event()

    def fit(self, series, request, *, cancellation=None):
        self.requests.append(request)
        self.thread = QThread.currentThread()
        self.entered.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if cancellation is not None and cancellation.is_cancelled():
                self.canceled.set()
                break
            time.sleep(0.001)
        return fit_result(series, request)


class UnavailableFitOperator(RecordingFitOperator):
    def fit(self, series, request, *, cancellation=None):
        self.requests.append(request)
        self.thread = QThread.currentThread()
        return replace(
            fit_result(series, request),
            status=FitStatus.UNAVAILABLE,
            message="The selected model is unavailable in this engine.",
        )


class MisalignedFitOperator(RecordingFitOperator):
    def fit(self, series, request, *, cancellation=None):
        result = fit_result(series, request)
        mask = result.valid_mask[:-1]
        return replace(
            result,
            predicted=result.predicted[:-1],
            residuals=result.residuals[:-1],
            valid_mask=mask,
            sample_count=int(np.count_nonzero(mask)),
        )


class AnalysisWorkspaceControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_controller_builds_immutable_request_and_runs_only_through_protocol(self) -> None:
        supervisor = TaskSupervisor()
        operator = RecordingFitOperator()
        controller = AnalysisWorkspaceController(supervisor, fit_operator=operator)
        self.addCleanup(controller.close)
        owner = object()
        source = make_series()
        controller.set_series(owner, (source,))
        completed: list[FitResult] = []
        controller.fitResultReady.connect(completed.append)

        started = controller.run_fit(
            owner,
            FitDraft(
                series_id=source.series_id,
                model="linear",
                range_start_s=0.2,
                range_end_s=0.8,
            ),
        )
        pump_until(lambda: not controller.busy)

        self.assertTrue(started)
        self.assertEqual(len(completed), 1)
        self.assertEqual(operator.requests[0].source_revision, source.source_revision)
        self.assertEqual(operator.requests[0].range_start_s, 0.2)
        self.assertIsNot(operator.thread, QApplication.instance().thread())
        self.assertEqual(controller.state.status, "complete")
        self.assertIs(controller.state.fit_result, completed[0])

    def test_duplicate_series_ids_are_rejected_before_controller_state_changes(self) -> None:
        controller = AnalysisWorkspaceController(TaskSupervisor())
        self.addCleanup(controller.close)
        source = make_series()

        with self.assertRaisesRegex(ValueError, "unique"):
            controller.set_series(object(), (source, source))

        self.assertEqual(controller.state.series_ids, ())

    def test_unavailable_terminal_result_remains_text_only(self) -> None:
        operator = UnavailableFitOperator()
        controller = AnalysisWorkspaceController(TaskSupervisor(), fit_operator=operator)
        self.addCleanup(controller.close)
        owner = object()
        source = make_series()
        controller.set_series(owner, (source,))
        delivered: list[FitResult] = []
        exports: list[KinematicsOperationRequest] = []
        controller.fitResultReady.connect(delivered.append)
        controller.operationRequested.connect(exports.append)

        self.assertTrue(controller.run_fit(owner, FitDraft(source.series_id, "linear", 0.0, 1.0)))
        pump_until(lambda: not controller.busy)

        self.assertEqual(delivered, [])
        self.assertEqual(controller.state.status, "unavailable")
        self.assertIn("unavailable", controller.state.message.lower())
        self.assertIsNone(controller.state.fit_result)
        controller.set_residual_visible(True)
        self.assertFalse(controller.state.residual_visible)
        self.assertTrue(controller.request_export())
        self.assertIsNone(exports[-1].configuration["fit_result"])

    def test_misaligned_fit_result_is_rejected_before_controller_state_changes(self) -> None:
        operator = MisalignedFitOperator()
        controller = AnalysisWorkspaceController(TaskSupervisor(), fit_operator=operator)
        self.addCleanup(controller.close)
        owner = object()
        source = make_series()
        controller.set_series(owner, (source,))
        delivered: list[FitResult] = []
        controller.fitResultReady.connect(delivered.append)

        self.assertTrue(controller.run_fit(owner, FitDraft(source.series_id, "linear", 0.0, 1.0)))
        pump_until(lambda: not controller.busy)

        self.assertEqual(delivered, [])
        self.assertEqual(controller.state.status, "stale")
        self.assertIsNone(controller.state.fit_result)

    def test_source_change_cancels_active_fit_and_rejects_late_result(self) -> None:
        supervisor = TaskSupervisor()
        operator = BlockingFitOperator()
        controller = AnalysisWorkspaceController(supervisor, fit_operator=operator)
        self.addCleanup(controller.close)
        owner = object()
        first = make_series()
        controller.set_series(owner, (first,))
        delivered: list[FitResult] = []
        controller.fitResultReady.connect(delivered.append)
        self.assertTrue(
            controller.run_fit(
                owner,
                FitDraft(first.series_id, "linear", 0.0, 1.0),
            )
        )
        pump_until(operator.entered.is_set)

        replacement = make_series(revision="results:fit-b")
        controller.set_series(owner, (replacement,))
        pump_until(lambda: not controller.busy)

        self.assertTrue(operator.canceled.is_set())
        self.assertEqual(delivered, [])
        self.assertEqual(controller.state.source_revision, replacement.source_revision)
        self.assertEqual(controller.state.status, "ready")

    def test_model_or_range_change_invalidates_result_and_cancel_is_terminal(self) -> None:
        supervisor = TaskSupervisor()
        operator = BlockingFitOperator()
        controller = AnalysisWorkspaceController(supervisor, fit_operator=operator)
        owner = object()
        source = make_series()
        controller.set_series(owner, (source,))
        controller.run_fit(owner, FitDraft(source.series_id, "linear", 0.0, 1.0))
        pump_until(operator.entered.is_set)

        self.assertTrue(controller.invalidate("Fit range changed."))
        pump_until(lambda: not controller.busy)

        self.assertEqual(controller.state.status, "dirty")
        self.assertIn("range", controller.state.message.lower())
        self.assertIsNone(controller.state.fit_result)
        controller.close()
        self.assertTrue(supervisor.idle)


if __name__ == "__main__":
    unittest.main()
