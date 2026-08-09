from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from threading import Event

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.review_response_coordinator import ReviewResponseCoordinator
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.core import TrackerResult
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.project_controller import DesktopTask
from neo_tracker.ui.review_response import ReviewResponse, ReviewResponseService


class _ControlledWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.release = Event()
        self.cancel_requested = Event()
        self.response: object | None = None

    @Slot()
    def run(self) -> None:
        self.release.wait(timeout=1.0)
        if self.cancel_requested.is_set():
            self.canceled.emit()
        else:
            self.completed.emit(self.response)

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


def _result(frame_index: int) -> TrackerResult:
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 10.0,
        state={"x_px": float(frame_index), "y_px": 0.0},
        filtered_state={"x_px": float(frame_index), "y_px": 0.0},
        confidence=1.0,
        status="tracked",
    )


class ReviewResponseCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> tuple[ReviewResponseCoordinator, TaskSupervisor]:
        supervisor = TaskSupervisor()
        coordinator = ReviewResponseCoordinator(
            supervisor,
            service=ReviewResponseService(max_entries=2),
            worker_factory=lambda _request: _ControlledWorker(),
        )
        return coordinator, supervisor

    @staticmethod
    def make_requests() -> tuple[DesktopTask, object, object]:
        pipeline = color_marker_preset()
        pipeline.results = [_result(0), _result(1)]
        task = DesktopTask("/media/source.mp4", "color_marker", pipeline)
        service = ReviewResponseService()
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        return (
            task,
            service.prepare_request(task, pipeline, pipeline.results[0], frame),
            service.prepare_request(task, pipeline, pipeline.results[1], frame),
        )

    def test_latest_request_supersedes_active_and_is_the_only_lru_commit(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task, first, latest = self.make_requests()
        delivered: list[object] = []
        coordinator.response_ready.connect(lambda request, _response: delivered.append(request))

        self.assertTrue(coordinator.queue(first))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        self.assertTrue(coordinator.queue(latest))
        _pump_until(
            lambda: coordinator.job is not None
            and coordinator.job.request is latest
            and coordinator.thread is not None
            and coordinator.thread.isRunning()
        )
        coordinator.worker.response = ReviewResponse(
            np.ones((8, 8), dtype=float),
            "recomputed",
            "latest",
        )
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [latest])
        self.assertEqual(coordinator.service.cached_frames(task), [1])
        self.assertTrue(supervisor.idle)

    def test_result_identity_change_rejects_late_response(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task, first, _latest = self.make_requests()
        delivered: list[object] = []
        coordinator.response_ready.connect(lambda request, _response: delivered.append(request))

        self.assertTrue(coordinator.queue(first))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        task.pipeline.results[0] = _result(0)
        coordinator.worker.response = ReviewResponse(
            np.ones((8, 8), dtype=float),
            "recomputed",
            "stale",
        )
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertEqual(coordinator.service.cached_frames(task), [])

    def test_close_clears_pending_and_cancels_active(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        _task, first, latest = self.make_requests()

        self.assertTrue(coordinator.queue(first))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        self.assertTrue(coordinator.queue(latest))
        supervisor.begin_close()
        coordinator.close()
        _pump_until(lambda: not coordinator.busy)

        self.assertIsNone(coordinator.pending_request)
        self.assertTrue(supervisor.ready_to_close)


if __name__ == "__main__":
    unittest.main()
