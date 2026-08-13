from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.analysis import AnalysisConfig
from neo_tracker.application.job_state import AnalysisJob
from neo_tracker.application.qt_worker_lifecycle import bind_worker_retirement
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.ui.analysis_controller import AnalysisRun, AnalysisSource
from neo_tracker.ui.analysis_worker import AnalysisWorker, SeriesLoader


WorkerFactory = Callable[["AnalysisRequest"], Any]


@dataclass(frozen=True)
class AnalysisRequest:
    """Immutable owner, source, settings, and data-loader context for one run."""

    owner: object
    source: AnalysisSource
    config: AnalysisConfig
    series_loader: SeriesLoader


def _default_worker_factory(request: AnalysisRequest) -> AnalysisWorker:
    return AnalysisWorker(
        source=request.source,
        config=request.config,
        owner_token=id(request.owner),
        series_loader=request.series_loader,
    )


class AnalysisCoordinator(QObject):
    """Own one FFT/STFT worker and reject results outside its frozen context."""

    started = Signal(object)
    stage_changed = Signal(object, str)
    completed = Signal(object, object)
    result_ready = Signal(object, object)
    failed = Signal(object, str)
    canceled = Signal(object)
    state_changed = Signal(str)
    finished = Signal(object)
    idle_reached = Signal()

    TASK_KIND = "analysis"
    STALE_MESSAGE = (
        "Task, source, or settings changed before processing finished. "
        "Run processing again."
    )

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        worker_factory: WorkerFactory = _default_worker_factory,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self._worker_factory = worker_factory
        self._thread: QThread | None = None
        self._worker: Any | None = None
        self._job: AnalysisJob | None = None
        self._last_job: AnalysisJob | None = None
        self._terminal_received = False
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def active(self) -> bool:
        return self.busy

    @property
    def closing(self) -> bool:
        return self._closed or self.supervisor.closing

    @property
    def can_start(self) -> bool:
        return not self.busy and not self.closing

    @property
    def current_token(self) -> BackgroundTaskToken | None:
        return self._job.token if self._job is not None else None

    @property
    def current_generation(self) -> int:
        token = self.current_token
        return token.generation if token is not None else self.supervisor.generation_for(
            self.TASK_KIND
        )

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def worker(self) -> Any | None:
        return self._worker

    @property
    def job(self) -> AnalysisJob | None:
        return self._job

    @property
    def last_job(self) -> AnalysisJob | None:
        return self._last_job

    def start(self, request: AnalysisRequest) -> bool:
        if not self.can_start:
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        job = AnalysisJob(
            token=token,
            task=request.owner,  # type: ignore[arg-type]
            source=request.source,
            config=request.config,
        )
        try:
            thread = QThread(self)
            worker = self._worker_factory(request)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._handle_completed)
            worker.failed.connect(self._handle_failed)
            worker.canceled.connect(self._handle_canceled)
            worker.stage_changed.connect(self._handle_stage_changed)
            bind_worker_retirement(
                worker,
                thread,
                worker.completed,
                worker.failed,
                worker.canceled,
            )
            thread.finished.connect(self._handle_thread_finished)
            thread.finished.connect(thread.deleteLater)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._job = job
        self._last_job = job
        self._thread = thread
        self._worker = worker
        self._terminal_received = False
        self.state_changed.emit("running")
        self.started.emit(job)
        thread.start()
        return True

    def cancel(
        self,
        message: str = "Processing canceled.",
        *,
        state: str = "canceled",
    ) -> bool:
        job = self._job
        worker = self._worker
        if job is None or worker is None or job.cancelled:
            return False
        job.cancelled = True
        job.cancel_message = str(message)
        job.cancel_state = str(state)
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel("Closing Neo-Tracker; signal processing was canceled.")
        if not self.busy:
            self.state_changed.emit("closed")
            self.idle_reached.emit()

    def _handle_stage_changed(self, stage: str) -> None:
        job = self._job
        if not self._accepts_callback(job) or job.cancelled:
            return
        self.stage_changed.emit(job, str(stage))

    def _handle_completed(self, run_object: object) -> None:
        job = self._job
        if not self._accepts_callback(job) or job.cancelled:
            return
        self._terminal_received = True
        if not self._run_matches(job, run_object):
            job.cancelled = True
            job.cancel_message = self.STALE_MESSAGE
            job.cancel_state = "dirty"
            self.canceled.emit(job)
        else:
            job.completed = True
            self.completed.emit(job, run_object)
            self.result_ready.emit(job, run_object)
        self.state_changed.emit("finishing")

    def _handle_failed(self, message: str) -> None:
        job = self._job
        if not self._accepts_callback(job) or job.cancelled:
            return
        self._terminal_received = True
        job.failure_detail = str(message)
        self.failed.emit(job, job.failure_detail)
        self.state_changed.emit("finishing")

    def _handle_canceled(self) -> None:
        job = self._job
        if not self._accepts_callback(job):
            return
        self._terminal_received = True
        if not job.cancelled:
            job.cancelled = True
            job.cancel_message = "Processing canceled."
            job.cancel_state = "canceled"
        self.canceled.emit(job)
        self.state_changed.emit("finishing")

    def _handle_thread_finished(self) -> None:
        job = self._job
        terminal_received = self._terminal_received
        worker = self._worker
        self._worker = None
        self._thread = None
        self._job = None
        self._terminal_received = False
        if worker is not None:
            worker.deleteLater()
        if job is not None:
            if not terminal_received and not job.cancelled:
                job.failure_detail = "Analysis worker ended without a terminal result."
                self.failed.emit(job, job.failure_detail)
            self.supervisor.finish(job.token)
            self.finished.emit(job)
        self.state_changed.emit("closed" if self.closing else "idle")
        self.idle_reached.emit()

    def _accepts_callback(self, job: AnalysisJob | None) -> bool:
        return bool(
            job is not None
            and not self._terminal_received
            and self.supervisor.is_current(job.token)
        )

    @staticmethod
    def _run_matches(job: AnalysisJob, run_object: object) -> bool:
        return bool(
            isinstance(run_object, AnalysisRun)
            and run_object.owner_token == id(job.task)
            and run_object.source.identity == job.source.identity
            and run_object.config == job.config
        )
