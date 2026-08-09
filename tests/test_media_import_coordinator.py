from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.media_import_coordinator import (
    MediaImportCoordinator,
    MediaImportRequest,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.media import MediaInfo


class _ControlledWorker(QObject):
    progressed = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self, paths: tuple[str, ...], _media_probe: object) -> None:
        super().__init__()
        self.paths = paths
        self.cancel_requested = Event()
        self.release = Event()
        self.payload: object = ()
        self.failure = ""
        self.ignore_cancel = False

    @Slot()
    def run(self) -> None:
        self.release.wait(timeout=1.0)
        if self.cancel_requested.is_set() and not self.ignore_cancel:
            self.canceled.emit()
        elif self.failure:
            self.failed.emit(self.failure)
        else:
            self.completed.emit(self.payload)

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


class MediaImportCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        # On macOS, initialize the GUI backend before exercising any QThread.
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> tuple[MediaImportCoordinator, TaskSupervisor]:
        supervisor = TaskSupervisor()
        coordinator = MediaImportCoordinator(
            supervisor,
            worker_factory=lambda paths, probe: _ControlledWorker(paths, probe),
        )
        return coordinator, supervisor

    @staticmethod
    def request(*paths: str) -> MediaImportRequest:
        return MediaImportRequest(
            paths=tuple(paths),
            pipeline_key="color_marker",
            media_probe=lambda _path: MediaInfo(available=True),
        )

    def test_validated_batch_is_emitted_only_after_thread_stops(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        delivered: list[tuple[object, object]] = []
        coordinator.completed.connect(lambda job, results: delivered.append((job, results)))

        self.assertTrue(coordinator.start(self.request("/media/a.mp4", "/media/b.mp4")))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        worker = coordinator.worker
        payload = (
            ("/media/a.mp4", MediaInfo(frame_count=10, available=True)),
            ("/media/b.mp4", MediaInfo(frame_count=20, available=True)),
        )
        worker.payload = payload
        worker.release.set()

        self.assertEqual(delivered, [])
        _pump_until(lambda: not coordinator.busy)
        self.assertEqual(delivered[0][1], payload)
        self.assertTrue(delivered[0][0].completed)
        self.assertTrue(supervisor.idle)

    def test_invalid_or_reordered_payload_fails_closed(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        completed: list[object] = []
        failures: list[str] = []
        coordinator.completed.connect(lambda _job, payload: completed.append(payload))
        coordinator.failed.connect(lambda _job, message: failures.append(message))

        self.assertTrue(coordinator.start(self.request("/media/a.mp4", "/media/b.mp4")))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        worker = coordinator.worker
        worker.payload = (
            ("/media/b.mp4", MediaInfo(available=True)),
            ("/media/a.mp4", MediaInfo(available=True)),
        )
        worker.release.set()
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(completed, [])
        self.assertEqual(failures, ["Media inspection returned an invalid result."])

    def test_cancel_discards_a_late_completed_batch(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        delivered: list[object] = []
        canceled: list[object] = []
        coordinator.completed.connect(lambda _job, payload: delivered.append(payload))
        coordinator.canceled.connect(canceled.append)

        self.assertTrue(coordinator.start(self.request("/media/a.mp4")))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        worker = coordinator.worker
        worker.ignore_cancel = True
        worker.payload = (("/media/a.mp4", MediaInfo(available=True)),)
        self.assertTrue(coordinator.cancel("user"))
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(delivered, [])
        self.assertEqual(len(canceled), 1)
        self.assertTrue(supervisor.idle)


if __name__ == "__main__":
    unittest.main()
