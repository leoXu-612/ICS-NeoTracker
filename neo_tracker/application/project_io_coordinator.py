from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.application.job_state import ProjectOpenJob, ProjectSaveJob
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.project import NeoTrackerProject
from neo_tracker.ui.project_controller import ProjectTaskController
from neo_tracker.ui.project_open_worker import (
    PreparedProjectOpen,
    ProjectLoader,
    ProjectOpenWorker,
)
from neo_tracker.ui.project_save_worker import (
    CompletedProjectSave,
    ProjectSaveWorker,
    ProjectSaver,
)


OpenWorkerFactory = Callable[[Path, ProjectTaskController, ProjectLoader], Any]
SaveWorkerFactory = Callable[[NeoTrackerProject, Path, ProjectSaver], Any]


@dataclass(frozen=True)
class ProjectOpenRequest:
    """Immutable inputs for one staged project-open operation."""

    path: Path
    controller: ProjectTaskController
    project_loader: ProjectLoader


@dataclass(frozen=True)
class ProjectSaveRequest:
    """One stable project snapshot and its UI revision guard."""

    project: NeoTrackerProject
    path: Path
    content_revision: int
    project_saver: ProjectSaver


def _default_open_worker_factory(
    path: Path,
    controller: ProjectTaskController,
    project_loader: ProjectLoader,
) -> ProjectOpenWorker:
    return ProjectOpenWorker(path, controller, project_loader=project_loader)


def _default_save_worker_factory(
    project: NeoTrackerProject,
    path: Path,
    project_saver: ProjectSaver,
) -> ProjectSaveWorker:
    return ProjectSaveWorker(project, path, project_saver=project_saver)


class ProjectIOCoordinator(QObject):
    """Own project open/save workers and publish only validated terminal results."""

    open_progressed = Signal(str, int, int, str)
    open_prepared = Signal(object, object)
    open_failed = Signal(object, str)
    open_canceled = Signal(object)
    save_completed = Signal(object, object)
    save_failed = Signal(object, str)
    state_changed = Signal(str, str)
    idle_reached = Signal()

    OPEN_TASK_KIND = "project-open"
    SAVE_TASK_KIND = "project-save"

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        open_worker_factory: OpenWorkerFactory = _default_open_worker_factory,
        save_worker_factory: SaveWorkerFactory = _default_save_worker_factory,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self._open_worker_factory = open_worker_factory
        self._save_worker_factory = save_worker_factory
        self._open_thread: QThread | None = None
        self._open_worker: Any | None = None
        self._open_job: ProjectOpenJob | None = None
        self._prepared_open: PreparedProjectOpen | None = None
        self._save_thread: QThread | None = None
        self._save_worker: Any | None = None
        self._save_job: ProjectSaveJob | None = None
        self._completed_save: CompletedProjectSave | None = None
        self._closed = False

    @property
    def busy(self) -> bool:
        return self.open_busy or self.save_busy

    @property
    def active(self) -> bool:
        return self.busy

    @property
    def closing(self) -> bool:
        return self._closed or self.supervisor.closing

    @property
    def open_busy(self) -> bool:
        return self._open_thread is not None

    @property
    def open_thread(self) -> QThread | None:
        return self._open_thread

    @property
    def open_worker(self) -> Any | None:
        return self._open_worker

    @property
    def open_job(self) -> ProjectOpenJob | None:
        return self._open_job

    @property
    def open_token(self) -> BackgroundTaskToken | None:
        return self._open_job.token if self._open_job is not None else None

    @property
    def save_busy(self) -> bool:
        return self._save_thread is not None

    @property
    def save_thread(self) -> QThread | None:
        return self._save_thread

    @property
    def save_worker(self) -> Any | None:
        return self._save_worker

    @property
    def save_job(self) -> ProjectSaveJob | None:
        return self._save_job

    @property
    def save_token(self) -> BackgroundTaskToken | None:
        return self._save_job.token if self._save_job is not None else None

    def start_open(self, request: ProjectOpenRequest) -> bool:
        if self.open_busy or self.closing:
            return False
        token = self.supervisor.start(self.OPEN_TASK_KIND)
        if token is None:
            return False
        path = Path(request.path)
        try:
            thread = QThread(self)
            worker = self._open_worker_factory(
                path,
                request.controller,
                request.project_loader,
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progressed.connect(self.handle_open_progressed)
            worker.completed.connect(self.handle_open_completed)
            worker.failed.connect(self.handle_open_failed)
            worker.canceled.connect(self.handle_open_canceled)
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.canceled.connect(worker.deleteLater)
            worker.completed.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.canceled.connect(thread.quit)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self.handle_open_thread_finished)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._open_job = ProjectOpenJob(token=token, path=path)
        self._prepared_open = None
        self._open_thread = thread
        self._open_worker = worker
        self.state_changed.emit("open", "running")
        thread.start()
        return True

    def cancel_open(self, reason: str = "canceled") -> bool:
        del reason
        job = self._open_job
        worker = self._open_worker
        if job is None or worker is None or job.cancelled:
            return False
        job.cancelled = True
        self._prepared_open = None
        worker.request_cancel()
        self.state_changed.emit("open", "canceling")
        return True

    def start_save(self, request: ProjectSaveRequest) -> bool:
        if self.save_busy or self.closing:
            return False
        token = self.supervisor.start(self.SAVE_TASK_KIND)
        if token is None:
            return False
        path = Path(request.path)
        try:
            thread = QThread(self)
            worker = self._save_worker_factory(
                request.project,
                path,
                request.project_saver,
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self.handle_save_completed)
            worker.failed.connect(self.handle_save_failed)
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.completed.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self.handle_save_thread_finished)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._save_job = ProjectSaveJob(
            token=token,
            path=path,
            content_revision=int(request.content_revision),
        )
        self._completed_save = None
        self._save_thread = thread
        self._save_worker = worker
        self.state_changed.emit("save", "running")
        thread.start()
        return True

    def close(self) -> None:
        """Stop accepting work, cancel open, and let an atomic save finish."""

        self._closed = True
        self.cancel_open("closing")
        if not self.busy:
            self.state_changed.emit("all", "closed")
            self.idle_reached.emit()

    def handle_open_progressed(
        self,
        phase: str,
        completed: int,
        total: int,
        filename: str,
    ) -> None:
        job = self._open_job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
        ):
            return
        self.open_progressed.emit(
            str(phase),
            int(completed),
            int(total),
            str(filename),
        )

    def handle_open_completed(self, payload: object) -> None:
        job = self._open_job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
        ):
            return
        if not isinstance(payload, PreparedProjectOpen) or payload.path != job.path:
            job.failure_detail = "Project loading returned an invalid prepared project."
            self._prepared_open = None
            return
        job.completed = True
        self._prepared_open = payload

    def handle_open_failed(self, message: str) -> None:
        job = self._open_job
        if job is None or not self.supervisor.is_current(job.token):
            return
        job.failure_detail = str(message) or "Project loading failed."
        self._prepared_open = None

    def handle_open_canceled(self) -> None:
        job = self._open_job
        if job is not None and self.supervisor.is_current(job.token):
            job.cancelled = True
            self._prepared_open = None

    def handle_open_thread_finished(self) -> None:
        job = self._open_job
        prepared = self._prepared_open
        self._open_thread = None
        self._open_worker = None
        self._open_job = None
        self._prepared_open = None
        if job is not None:
            self.supervisor.finish(job.token)
        if not self.closing and job is not None:
            if job.cancelled:
                self.open_canceled.emit(job)
            elif job.failure_detail:
                self.open_failed.emit(job, job.failure_detail)
            elif job.completed and prepared is not None:
                self.open_prepared.emit(job, prepared)
            else:
                self.open_failed.emit(job, "Project loading ended without a result.")
        self.state_changed.emit("open", "closed" if self.closing else "idle")
        self._emit_idle_if_reached()

    def handle_save_completed(self, payload: object) -> None:
        job = self._save_job
        if job is None or not self.supervisor.is_current(job.token):
            return
        if not isinstance(payload, CompletedProjectSave) or payload.path != job.path:
            job.failure_detail = "Project saving returned an invalid result."
            self._completed_save = None
            return
        job.completed = True
        self._completed_save = payload

    def handle_save_failed(self, message: str) -> None:
        job = self._save_job
        if job is None or not self.supervisor.is_current(job.token):
            return
        job.failure_detail = str(message) or "Project saving failed."
        self._completed_save = None

    def handle_save_thread_finished(self) -> None:
        job = self._save_job
        completed = self._completed_save
        self._save_thread = None
        self._save_worker = None
        self._save_job = None
        self._completed_save = None
        if job is not None:
            self.supervisor.finish(job.token)
        if not self.closing and job is not None:
            if job.failure_detail:
                self.save_failed.emit(job, job.failure_detail)
            elif job.completed and completed is not None:
                self.save_completed.emit(job, completed)
            else:
                self.save_failed.emit(job, "Project saving ended without a result.")
        self.state_changed.emit("save", "closed" if self.closing else "idle")
        self._emit_idle_if_reached()

    def _emit_idle_if_reached(self) -> None:
        if not self.busy:
            self.idle_reached.emit()
