from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.project_io_coordinator import (
    ProjectIOCoordinator,
    ProjectOpenRequest,
    ProjectSaveRequest,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.project import NeoTrackerProject
from neo_tracker.ui.project_open_worker import PreparedProjectOpen
from neo_tracker.ui.project_save_worker import CompletedProjectSave


class _ControlledOpenWorker(QObject):
    progressed = Signal(str, int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.cancel_requested = Event()
        self.release = Event()
        self.payload: object = None
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


class _ControlledSaveWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.release = Event()
        self.payload: object = None
        self.failure = ""

    @Slot()
    def run(self) -> None:
        self.release.wait(timeout=1.0)
        if self.failure:
            self.failed.emit(self.failure)
        else:
            self.completed.emit(self.payload)


def _pump_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if not predicate():
        raise AssertionError("coordinator did not reach the expected state")


class ProjectIOCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        # On macOS, initialize the GUI backend before exercising any QThread.
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> tuple[ProjectIOCoordinator, TaskSupervisor]:
        supervisor = TaskSupervisor()
        coordinator = ProjectIOCoordinator(
            supervisor,
            open_worker_factory=lambda _path, _controller, _loader: _ControlledOpenWorker(),
            save_worker_factory=lambda _project, _path, _saver: _ControlledSaveWorker(),
        )
        return coordinator, supervisor

    @staticmethod
    def open_request(path: Path) -> ProjectOpenRequest:
        return ProjectOpenRequest(
            path=path,
            controller=object(),
            project_loader=lambda _path: NeoTrackerProject(name="loaded"),
        )

    @staticmethod
    def save_request(path: Path, *, revision: int = 7) -> ProjectSaveRequest:
        return ProjectSaveRequest(
            project=NeoTrackerProject(name="snapshot"),
            path=path,
            content_revision=revision,
            project_saver=lambda _project, _path: None,
        )

    def test_open_rejects_invalid_prepared_payload_after_thread_stops(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        prepared: list[object] = []
        failures: list[str] = []
        coordinator.open_prepared.connect(lambda _job, payload: prepared.append(payload))
        coordinator.open_failed.connect(lambda _job, message: failures.append(message))
        path = Path("/projects/target.ntproj")

        self.assertTrue(coordinator.start_open(self.open_request(path)))
        _pump_until(lambda: coordinator.open_thread is not None and coordinator.open_thread.isRunning())
        worker = coordinator.open_worker
        worker.payload = PreparedProjectOpen(
            path=Path("/projects/wrong.ntproj"),
            project=NeoTrackerProject(name="wrong"),
            tasks=(),
            fingerprint="fingerprint",
            media_count=0,
        )
        worker.release.set()
        self.assertEqual(prepared, [])
        _pump_until(lambda: not coordinator.open_busy)

        self.assertEqual(prepared, [])
        self.assertEqual(
            failures,
            ["Project loading returned an invalid prepared project."],
        )
        self.assertTrue(supervisor.idle)

    def test_cancel_open_discards_late_prepared_payload(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        prepared: list[object] = []
        canceled: list[object] = []
        coordinator.open_prepared.connect(lambda _job, payload: prepared.append(payload))
        coordinator.open_canceled.connect(canceled.append)
        path = Path("/projects/target.ntproj")

        self.assertTrue(coordinator.start_open(self.open_request(path)))
        _pump_until(lambda: coordinator.open_thread is not None and coordinator.open_thread.isRunning())
        worker = coordinator.open_worker
        worker.ignore_cancel = True
        worker.payload = PreparedProjectOpen(
            path=path,
            project=NeoTrackerProject(name="late"),
            tasks=(),
            fingerprint="fingerprint",
            media_count=0,
        )
        self.assertTrue(coordinator.cancel_open("user"))
        _pump_until(lambda: not coordinator.open_busy)

        self.assertEqual(prepared, [])
        self.assertEqual(len(canceled), 1)
        self.assertTrue(supervisor.idle)

    def test_save_completion_preserves_snapshot_revision_and_path(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        completed: list[tuple[object, CompletedProjectSave]] = []
        coordinator.save_completed.connect(
            lambda job, payload: completed.append((job, payload))
        )
        path = Path("/projects/snapshot.ntproj")

        self.assertTrue(coordinator.start_save(self.save_request(path, revision=11)))
        _pump_until(lambda: coordinator.save_thread is not None and coordinator.save_thread.isRunning())
        worker = coordinator.save_worker
        worker.payload = CompletedProjectSave(path=path, fingerprint="saved-fingerprint")
        worker.release.set()
        self.assertEqual(completed, [])
        _pump_until(lambda: not coordinator.save_busy)

        self.assertEqual(completed[0][0].content_revision, 11)
        self.assertEqual(completed[0][0].path, path)
        self.assertEqual(completed[0][1].fingerprint, "saved-fingerprint")
        self.assertTrue(supervisor.idle)

    def test_close_during_save_waits_for_worker_without_publishing_success(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        completed: list[object] = []
        coordinator.save_completed.connect(lambda _job, payload: completed.append(payload))
        path = Path("/projects/snapshot.ntproj")

        self.assertTrue(coordinator.start_save(self.save_request(path)))
        _pump_until(lambda: coordinator.save_thread is not None and coordinator.save_thread.isRunning())
        supervisor.begin_close()
        coordinator.close()
        self.assertFalse(supervisor.ready_to_close)
        worker = coordinator.save_worker
        worker.payload = CompletedProjectSave(path=path, fingerprint="saved-fingerprint")
        worker.release.set()
        _pump_until(lambda: not coordinator.save_busy)

        self.assertEqual(completed, [])
        self.assertTrue(supervisor.ready_to_close)


if __name__ == "__main__":
    unittest.main()
