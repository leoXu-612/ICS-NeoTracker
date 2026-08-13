from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.kinematics import FitOperator, FitRequest, FitResult, FitStatus, SampleSeries


@dataclass(frozen=True)
class KinematicsFitTask:
    owner: object
    series: SampleSeries
    request: FitRequest
    operator: FitOperator

    def __post_init__(self) -> None:
        if self.request.series_id != self.series.series_id:
            raise ValueError("fit request series does not match its immutable snapshot")
        if self.request.source_revision != self.series.source_revision:
            raise ValueError("fit request source revision does not match its immutable snapshot")
        if not callable(getattr(self.operator, "fit", None)):
            raise TypeError("fit operator must implement FitOperator.fit")


@dataclass
class KinematicsFitJob:
    token: BackgroundTaskToken
    task: KinematicsFitTask
    cancelled: bool = False
    cancel_message: str = "Fit canceled."
    cancel_state: str = "canceled"
    completed: bool = False
    failure_detail: str = ""


class _CancellationProbe:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class KinematicsFitWorker(QObject):
    """Worker adapter; every numerical operation is delegated to FitOperator."""

    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self, task: KinematicsFitTask) -> None:
        super().__init__()
        self.task = task
        self._cancellation = _CancellationProbe()

    def request_cancel(self) -> None:
        self._cancellation.cancel()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancellation.is_cancelled()

    @Slot()
    def run(self) -> None:
        if self.cancellation_requested:
            self.canceled.emit()
            return
        try:
            result = self.task.operator.fit(
                self.task.series,
                self.task.request,
                cancellation=self._cancellation,
            )
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
            return
        if self.cancellation_requested or (
            isinstance(result, FitResult) and result.status is FitStatus.CANCELLED
        ):
            self.canceled.emit()
        else:
            self.completed.emit(result)


WorkerFactory = Callable[[KinematicsFitTask], Any]


class KinematicsFitCoordinator(QObject):
    """Own one cancellable fit thread and enforce immutable result provenance."""

    started = Signal(object)
    result_ready = Signal(object, object)
    failed = Signal(object, str)
    canceled = Signal(object)
    state_changed = Signal(str)
    finished = Signal(object)
    idle_reached = Signal()

    TASK_KIND = "kinematics-fit"
    STALE_MESSAGE = "Series, range, model, or source revision changed before the fit finished."

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        worker_factory: WorkerFactory = KinematicsFitWorker,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self._worker_factory = worker_factory
        self._thread: QThread | None = None
        self._worker: Any | None = None
        self._job: KinematicsFitJob | None = None
        self._last_job: KinematicsFitJob | None = None
        self._terminal_received = False
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def job(self) -> KinematicsFitJob | None:
        return self._job

    @property
    def last_job(self) -> KinematicsFitJob | None:
        return self._last_job

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def worker(self) -> Any | None:
        return self._worker

    def start(self, task: KinematicsFitTask) -> bool:
        if self.busy or self._closed or self.supervisor.closing:
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        job = KinematicsFitJob(token, task)
        try:
            thread = QThread(self)
            worker = self._worker_factory(task)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._handle_completed)
            worker.failed.connect(self._handle_failed)
            worker.canceled.connect(self._handle_canceled)
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.canceled.connect(worker.deleteLater)
            worker.completed.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.canceled.connect(thread.quit)
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

    def cancel(self, message: str = "Fit canceled.", *, state: str = "canceled") -> bool:
        job, worker = self._job, self._worker
        if job is None or worker is None or job.cancelled or self._terminal_received:
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
        self.cancel("Closing Neo-Tracker; the active fit was canceled.")
        if not self.busy:
            self.state_changed.emit("closed")
            self.idle_reached.emit()

    def _handle_completed(self, result_object: object) -> None:
        job = self._job
        if not self._accepts(job) or job.cancelled:
            return
        self._terminal_received = True
        if not isinstance(result_object, FitResult) or not self._matches(job.task, result_object):
            job.cancelled = True
            job.cancel_message = self.STALE_MESSAGE
            job.cancel_state = "stale"
            self.canceled.emit(job)
        else:
            job.completed = True
            self.result_ready.emit(job, result_object)
        self.state_changed.emit("finishing")

    def _handle_failed(self, message: str) -> None:
        job = self._job
        if not self._accepts(job) or job.cancelled:
            return
        self._terminal_received = True
        job.failure_detail = str(message)
        self.failed.emit(job, job.failure_detail)
        self.state_changed.emit("finishing")

    def _handle_canceled(self) -> None:
        job = self._job
        if not self._accepts(job):
            return
        self._terminal_received = True
        if not job.cancelled:
            job.cancelled = True
        self.canceled.emit(job)
        self.state_changed.emit("finishing")

    def _handle_thread_finished(self) -> None:
        job = self._job
        terminal = self._terminal_received
        self._thread = None
        self._worker = None
        self._job = None
        self._terminal_received = False
        if job is not None:
            if not terminal:
                if job.cancelled:
                    self.canceled.emit(job)
                else:
                    job.failure_detail = "Kinematics fit worker ended without a terminal result."
                    self.failed.emit(job, job.failure_detail)
            self.supervisor.finish(job.token)
            self.finished.emit(job)
        self.state_changed.emit("closed" if self._closed else "idle")
        self.idle_reached.emit()

    def _accepts(self, job: KinematicsFitJob | None) -> bool:
        return bool(
            job is not None
            and not self._terminal_received
            and self.supervisor.is_current(job.token)
        )

    @staticmethod
    def _matches(task: KinematicsFitTask, result: FitResult) -> bool:
        request = task.request
        return bool(
            result.series_id == request.series_id
            and result.source_revision == request.source_revision
            and result.model is request.model
            and result.range_start_s == request.range_start_s
            and result.range_end_s == request.range_end_s
        )
