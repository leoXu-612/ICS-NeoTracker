from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6

from neo_tracker.analysis import AnalysisConfig
from neo_tracker.application.analysis_coordinator import (
    AnalysisCoordinator,
    AnalysisRequest,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.ui.analysis_controller import AnalysisRun, AnalysisSource


class _ControlledWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()
    stage_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.release = Event()
        self.cancel_requested = Event()
        self.run_object: object | None = None
        self.failure = ""
        self.terminal_delay_s = 0.0

    @Slot()
    def run(self) -> None:
        self.stage_changed.emit("loading")
        self.release.wait(timeout=1.0)
        if self.cancel_requested.is_set():
            self.canceled.emit()
        elif self.failure:
            self.failed.emit(self.failure)
        else:
            self.completed.emit(self.run_object)
        if self.terminal_delay_s > 0.0:
            time.sleep(self.terminal_delay_s)

    def request_cancel(self) -> None:
        self.cancel_requested.set()
        self.release.set()


def _pump_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if not predicate():
        raise AssertionError("coordinator did not reach the expected state")


class AnalysisCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> tuple[AnalysisCoordinator, TaskSupervisor]:
        supervisor = TaskSupervisor()
        coordinator = AnalysisCoordinator(
            supervisor,
            worker_factory=lambda _request: _ControlledWorker(),
        )
        return coordinator, supervisor

    @staticmethod
    def request(owner: object) -> AnalysisRequest:
        return AnalysisRequest(
            owner=owner,
            source=AnalysisSource(
                kind="tracking",
                label="Tracking: x",
                key="x",
                sample_rate_hz=10.0,
                sample_count=16,
            ),
            config=AnalysisConfig(method="fft", window="boxcar"),
            series_loader=lambda _cancel: None,  # type: ignore[arg-type,return-value]
        )

    @staticmethod
    def run_for(
        request: AnalysisRequest,
        *,
        config: AnalysisConfig | None = None,
        source: AnalysisSource | None = None,
        owner_token: int | None = None,
    ) -> AnalysisRun:
        return AnalysisRun(
            kind="fft",
            source=source or request.source,
            series=None,  # type: ignore[arg-type]
            config=config or request.config,
            result=None,  # type: ignore[arg-type]
            summary="ready",
            owner_token=id(request.owner) if owner_token is None else owner_token,
        )

    def test_valid_result_is_owner_source_and_settings_bound_before_thread_exit(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        request = self.request(object())
        delivered: list[AnalysisRun] = []
        coordinator.result_ready.connect(lambda _job, run: delivered.append(run))

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.worker.terminal_delay_s = 0.05
        coordinator.worker.run_object = self.run_for(request)
        coordinator.worker.release.set()
        _pump_until(lambda: bool(delivered))

        self.assertIsNotNone(coordinator.thread)
        self.assertEqual(delivered[0].owner_token, id(request.owner))
        _pump_until(lambda: not coordinator.busy)
        self.assertTrue(supervisor.idle)

    def test_terminal_worker_moves_to_main_loop_and_is_destroyed(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        request = self.request(object())

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        worker = coordinator.worker
        assert worker is not None
        coordinator.worker.run_object = self.run_for(request)
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)
        _pump_until(lambda: not shiboken6.isValid(worker))

        self.assertFalse(shiboken6.isValid(worker))

    def test_settings_mismatch_is_rejected_as_stale(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        request = self.request(object())
        delivered: list[AnalysisRun] = []
        coordinator.result_ready.connect(lambda _job, run: delivered.append(run))

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.worker.run_object = self.run_for(
            request,
            config=AnalysisConfig(method="fft", window="hamming"),
        )
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertTrue(coordinator.last_job.cancelled)
        self.assertEqual(coordinator.last_job.cancel_state, "dirty")

    def test_source_mismatch_is_rejected_as_stale(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        request = self.request(object())
        delivered: list[AnalysisRun] = []
        coordinator.result_ready.connect(lambda _job, run: delivered.append(run))

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.worker.run_object = self.run_for(
            request,
            source=AnalysisSource(kind="tracking", label="other", key="other"),
        )
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertTrue(coordinator.last_job.cancelled)

    def test_owner_mismatch_is_rejected_as_stale(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        request = self.request(object())
        delivered: list[AnalysisRun] = []
        coordinator.result_ready.connect(lambda _job, run: delivered.append(run))

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.worker.run_object = self.run_for(request, owner_token=id(object()))
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertTrue(coordinator.last_job.cancelled)

    def test_explicit_cancel_rejects_late_result(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        request = self.request(object())
        delivered: list[AnalysisRun] = []
        coordinator.result_ready.connect(lambda _job, run: delivered.append(run))

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.worker.run_object = self.run_for(request)
        self.assertTrue(coordinator.cancel("Source changed.", state="dirty"))
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertTrue(coordinator.last_job.cancelled)
        self.assertEqual(coordinator.last_job.cancel_message, "Source changed.")

    def test_close_cancels_and_releases_supervisor(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        request = self.request(object())

        self.assertTrue(coordinator.start(request))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        supervisor.begin_close()
        coordinator.close()
        self.assertTrue(coordinator.worker.cancel_requested.is_set())
        _pump_until(lambda: not coordinator.busy)

        self.assertTrue(supervisor.ready_to_close)
        self.assertTrue(coordinator.last_job.cancelled)


if __name__ == "__main__":
    unittest.main()
