from __future__ import annotations

import time
import unittest
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot

from neo_tracker.application.preview_coordinator import PreviewCoordinator, PreviewRequest
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.ui.preview_decode_worker import PreviewDecodeRequest, PreviewDecodeResult


class _Session:
    def __init__(self, request: PreviewDecodeRequest) -> None:
        self.request = request
        self.close_count = 0

    def matches(
        self,
        media_path: str,
        *,
        expected_width: int,
        expected_height: int,
        expected_identity: object,
    ) -> bool:
        return bool(
            media_path == self.request.media_path
            and expected_width == self.request.expected_width
            and expected_height == self.request.expected_height
            and expected_identity == self.request.expected_identity
        )

    def close(self) -> None:
        self.close_count += 1


class _ControlledWorker(QObject):
    completed = Signal(object)
    failed = Signal(object, str)
    canceled = Signal(object)

    def __init__(self, request: PreviewDecodeRequest, session: _Session) -> None:
        super().__init__()
        self.request = request
        self.session = session
        self.cancel_requested = False

    @Slot()
    def run(self) -> None:
        return

    def request_cancel(self) -> None:
        self.cancel_requested = True
        self.canceled.emit(self.request)


def _pump_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if not predicate():
        raise AssertionError("coordinator did not reach the expected state")


class PreviewCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make_coordinator(self) -> tuple[PreviewCoordinator, TaskSupervisor, list[_Session]]:
        sessions: list[_Session] = []

        def session_factory(request: PreviewDecodeRequest) -> _Session:
            session = _Session(request)
            sessions.append(session)
            return session

        coordinator = PreviewCoordinator(
            TaskSupervisor(),
            session_factory=session_factory,
            worker_factory=lambda request, session: _ControlledWorker(request, session),
        )
        return coordinator, coordinator.supervisor, sessions

    def test_fast_scrub_keeps_only_the_latest_request(self) -> None:
        coordinator, supervisor, _sessions = self.make_coordinator()
        self.addCleanup(coordinator.close)
        owner = object()
        first = PreviewDecodeRequest(id(owner), "/tmp/source.mp4", 1, 4, 3)
        latest = PreviewDecodeRequest(id(owner), "/tmp/source.mp4", 9, 4, 3)
        delivered: list[PreviewDecodeResult] = []
        coordinator.result_ready.connect(lambda _owner, result: delivered.append(result))

        self.assertTrue(coordinator.start(PreviewRequest(owner, first)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        first_worker = coordinator.worker
        self.assertTrue(coordinator.start(PreviewRequest(owner, latest)))
        self.assertTrue(first_worker.cancel_requested)
        _pump_until(
            lambda: coordinator.job is not None
            and coordinator.job.request.frame_index == latest.frame_index
            and coordinator.thread is not None
            and coordinator.thread.isRunning()
        )

        coordinator.handle_completed(
            PreviewDecodeResult(first, np.zeros((3, 4, 3), dtype=np.uint8))
        )
        self.assertIsNone(coordinator.job.result)
        latest_result = PreviewDecodeResult(
            latest,
            np.full((3, 4, 3), 9, dtype=np.uint8),
        )
        coordinator.worker.completed.emit(latest_result)
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [latest_result])
        self.assertIs(coordinator.cache, latest_result)
        self.assertTrue(supervisor.idle)

    def test_close_during_decode_is_idempotent_and_releases_session(self) -> None:
        coordinator, supervisor, sessions = self.make_coordinator()
        owner = object()
        request = PreviewDecodeRequest(id(owner), "/tmp/source.mp4", 3, 4, 3)
        self.assertTrue(coordinator.start(PreviewRequest(owner, request)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())

        supervisor.begin_close()
        coordinator.close()
        coordinator.close()
        _pump_until(lambda: not coordinator.busy)

        self.assertTrue(supervisor.ready_to_close)
        self.assertEqual(sessions[0].close_count, 1)
        self.assertFalse(coordinator.start(PreviewRequest(owner, request)))

    def test_task_switch_rejects_late_result_from_previous_owner(self) -> None:
        coordinator, _supervisor, _sessions = self.make_coordinator()
        self.addCleanup(coordinator.close)
        first_owner = object()
        current_owner = object()
        first = PreviewDecodeRequest(id(first_owner), "/tmp/source.mp4", 4, 4, 3)
        current = PreviewDecodeRequest(id(current_owner), "/tmp/source.mp4", 4, 4, 3)
        delivered: list[tuple[object, PreviewDecodeResult]] = []
        coordinator.result_ready.connect(
            lambda owner, result: delivered.append((owner, result))
        )

        self.assertTrue(coordinator.start(PreviewRequest(first_owner, first)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        self.assertTrue(coordinator.start(PreviewRequest(current_owner, current)))
        _pump_until(
            lambda: coordinator.job is not None
            and coordinator.job.task is current_owner
            and coordinator.thread is not None
            and coordinator.thread.isRunning()
        )

        coordinator.handle_completed(
            PreviewDecodeResult(first, np.full((3, 4, 3), 4, dtype=np.uint8))
        )
        self.assertIsNone(coordinator.job.result)
        current_result = PreviewDecodeResult(
            current,
            np.full((3, 4, 3), 8, dtype=np.uint8),
        )
        coordinator.worker.completed.emit(current_result)
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [(current_owner, current_result)])


if __name__ == "__main__":
    unittest.main()
