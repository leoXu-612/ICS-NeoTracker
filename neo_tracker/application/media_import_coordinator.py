from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.application.job_state import MediaProbeJob
from neo_tracker.application.qt_worker_lifecycle import bind_worker_retirement
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.media import MediaInfo
from neo_tracker.ui.media_probe_worker import MediaProbe, MediaProbeWorker


WorkerFactory = Callable[[Sequence[str], MediaProbe], Any]


@dataclass(frozen=True)
class MediaImportRequest:
    """Immutable inputs for one ordered media-inspection batch."""

    paths: tuple[str, ...]
    pipeline_key: str
    media_probe: MediaProbe


def _default_worker_factory(
    paths: Sequence[str],
    media_probe: MediaProbe,
) -> MediaProbeWorker:
    return MediaProbeWorker(paths, media_probe=media_probe)


class MediaImportCoordinator(QObject):
    """Own the media-probe worker, validation, cancellation, and cleanup."""

    progressed = Signal(int, int, str)
    completed = Signal(object, object)
    failed = Signal(object, str)
    canceled = Signal(object)
    state_changed = Signal(str)
    idle_reached = Signal()

    TASK_KIND = "media-probe"

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
        self._job: MediaProbeJob | None = None
        self._validated_results: tuple[tuple[str, MediaInfo], ...] | None = None
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
    def current_token(self) -> BackgroundTaskToken | None:
        return self._job.token if self._job is not None else None

    @property
    def current_generation(self) -> int:
        token = self.current_token
        return token.generation if token is not None else self.supervisor.generation_for(self.TASK_KIND)

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def worker(self) -> Any | None:
        return self._worker

    @property
    def job(self) -> MediaProbeJob | None:
        return self._job

    def start(self, request: MediaImportRequest) -> bool:
        paths = tuple(str(path) for path in request.paths if str(path))
        if not paths or self.busy or self.closing:
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        try:
            thread = QThread(self)
            worker = self._worker_factory(paths, request.media_probe)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progressed.connect(self.handle_progressed)
            worker.completed.connect(self.handle_completed)
            worker.failed.connect(self.handle_failed)
            worker.canceled.connect(self.handle_canceled)
            bind_worker_retirement(worker, thread, worker.completed, worker.failed, worker.canceled)
            thread.finished.connect(self.handle_thread_finished)
            thread.finished.connect(thread.deleteLater)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._job = MediaProbeJob(
            token=token,
            paths=paths,
            pipeline_key=str(request.pipeline_key),
        )
        self._validated_results = None
        self._thread = thread
        self._worker = worker
        self.state_changed.emit("running")
        thread.start()
        return True

    def cancel(self, reason: str = "canceled") -> bool:
        del reason
        job = self._job
        worker = self._worker
        if job is None or worker is None or job.cancelled:
            return False
        job.cancelled = True
        self._validated_results = None
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def close(self) -> None:
        self._closed = True
        self.cancel("closing")
        if not self.busy:
            self.state_changed.emit("closed")

    def handle_progressed(self, completed: int, total: int, filename: str) -> None:
        job = self._job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
        ):
            return
        self.progressed.emit(int(completed), int(total), str(filename))

    def handle_completed(self, payload: object) -> None:
        job = self._job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
        ):
            return
        validated, failure_detail = self._validate_payload(job.paths, payload)
        if failure_detail:
            job.failure_detail = failure_detail
            self._validated_results = None
            return
        job.completed = True
        self._validated_results = validated

    def handle_failed(self, message: str) -> None:
        job = self._job
        if job is None or not self.supervisor.is_current(job.token):
            return
        job.failure_detail = str(message) or "Media inspection failed."
        self._validated_results = None

    def handle_canceled(self) -> None:
        job = self._job
        if job is not None and self.supervisor.is_current(job.token):
            job.cancelled = True
            self._validated_results = None

    def handle_thread_finished(self) -> None:
        """Publish exactly one validated terminal state after QThread stops."""

        job = self._job
        results = self._validated_results
        worker = self._worker
        self._thread = None
        self._worker = None
        self._job = None
        self._validated_results = None
        if worker is not None:
            worker.deleteLater()
        if job is not None:
            self.supervisor.finish(job.token)
        if self.closing:
            self.state_changed.emit("closed")
            self.idle_reached.emit()
            return
        if job is not None:
            if job.cancelled:
                self.canceled.emit(job)
            elif job.failure_detail:
                self.failed.emit(job, job.failure_detail)
            elif job.completed and results is not None:
                self.completed.emit(job, results)
            else:
                self.failed.emit(job, "Media inspection ended without a result.")
        self.state_changed.emit("idle")
        self.idle_reached.emit()

    @staticmethod
    def _validate_payload(
        paths: tuple[str, ...],
        payload: object,
    ) -> tuple[tuple[tuple[str, MediaInfo], ...] | None, str]:
        results = tuple(payload) if isinstance(payload, (list, tuple)) else ()
        if len(results) != len(paths):
            return None, "Media inspection returned an incomplete result batch."
        validated: list[tuple[str, MediaInfo]] = []
        for expected_path, result in zip(paths, results):
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                return None, "Media inspection returned an invalid result."
            path, media_info = result
            if str(path) != expected_path or not isinstance(media_info, MediaInfo):
                return None, "Media inspection returned an invalid result."
            validated.append((expected_path, media_info))
        return tuple(validated), ""
