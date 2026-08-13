from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.application.job_state import PreviewDecodeJob
from neo_tracker.application.qt_worker_lifecycle import bind_worker_retirement
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.ui.isolated_media import PreviewDecoderSession
from neo_tracker.ui.preview_decode_worker import (
    PreviewDecodeRequest,
    PreviewDecodeResult,
    PreviewDecodeWorker,
    same_preview_request,
)


SessionFactory = Callable[[PreviewDecodeRequest], Any]
WorkerFactory = Callable[[PreviewDecodeRequest, Any], Any]


@dataclass(frozen=True)
class PreviewRequest:
    """One immutable owner-bound request accepted by PreviewCoordinator."""

    owner: object
    decode_request: PreviewDecodeRequest


def _default_session_factory(request: PreviewDecodeRequest) -> PreviewDecoderSession:
    return PreviewDecoderSession(
        request.media_path,
        expected_width=request.expected_width,
        expected_height=request.expected_height,
        expected_identity=request.expected_identity,
    )


def _default_worker_factory(
    request: PreviewDecodeRequest,
    session: PreviewDecoderSession,
) -> PreviewDecodeWorker:
    return PreviewDecodeWorker(request, session=session)


class PreviewCoordinator(QObject):
    """Own preview request coalescing, decoder sessions, and QThread cleanup."""

    result_ready = Signal(object, object)
    failed = Signal(object, object, str)
    state_changed = Signal(str)
    idle_reached = Signal()

    TASK_KIND = "preview-decode"

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        session_factory: SessionFactory = _default_session_factory,
        worker_factory: WorkerFactory = _default_worker_factory,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self._session_factory = session_factory
        self._worker_factory = worker_factory
        self._thread: QThread | None = None
        self._worker: Any | None = None
        self._job: PreviewDecodeJob | None = None
        self._pending: PreviewRequest | None = None
        self._desired: PreviewRequest | None = None
        self._cache: PreviewDecodeResult | None = None
        self._session: Any | None = None
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
    def job(self) -> PreviewDecodeJob | None:
        return self._job

    @property
    def pending_request(self) -> PreviewRequest | None:
        return self._pending

    @property
    def cache(self) -> PreviewDecodeResult | None:
        return self._cache

    @property
    def session(self) -> Any | None:
        return self._session

    def start(self, request: PreviewRequest) -> bool:
        """Accept a request, superseding any different active request."""

        if self.closing:
            self._pending = None
            self._desired = None
            return False
        self._desired = request
        job = self._job
        if self._thread is not None and job is not None:
            if self._same_work(job.task, job.request, request) and not job.cancelled:
                return True
            self._pending = request
            self.cancel("superseded", clear_pending=False)
            return True
        return self._start_now(request)

    def cancel(self, reason: str = "canceled", *, clear_pending: bool = True) -> bool:
        del reason
        if clear_pending:
            self._pending = None
            self._desired = None
        job = self._job
        worker = self._worker
        if job is None or worker is None or job.cancelled:
            return False
        job.cancelled = True
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def close(self) -> None:
        """Idempotently stop accepting work and retire the decoder session."""

        self._closed = True
        self.cancel("closing", clear_pending=True)
        self.clear_cache()
        self.discard_session()
        if not self.busy:
            self.state_changed.emit("closed")

    def invalidate(self, *, clear_cache: bool = True, close_session: bool = True) -> None:
        self.cancel("invalidated", clear_pending=True)
        if clear_cache:
            self.clear_cache()
        if close_session:
            self.discard_session()

    def clear_cache(self) -> None:
        self._cache = None

    def discard_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None and not self.busy:
            session.close()

    def session_for(self, request: PreviewDecodeRequest) -> Any:
        session = self._session
        if session is not None and session.matches(
            request.media_path,
            expected_width=request.expected_width,
            expected_height=request.expected_height,
            expected_identity=request.expected_identity,
        ):
            return session
        if session is not None:
            self._session = None
            session.close()
        session = self._session_factory(request)
        self._session = session
        return session

    def handle_completed(self, result_object: object) -> None:
        job = self._job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
            or not isinstance(result_object, PreviewDecodeResult)
            or not same_preview_request(job.request, result_object.request)
        ):
            return
        job.result = result_object

    def handle_failed(self, request_object: object, message: str) -> None:
        job = self._job
        if (
            job is None
            or job.cancelled
            or not self.supervisor.is_current(job.token)
            or not isinstance(request_object, PreviewDecodeRequest)
            or not same_preview_request(job.request, request_object)
        ):
            return
        job.failure_detail = str(message) or "Preview decoder helper failed."

    def handle_canceled(self, request_object: object) -> None:
        job = self._job
        if (
            job is not None
            and isinstance(request_object, PreviewDecodeRequest)
            and same_preview_request(job.request, request_object)
        ):
            job.cancelled = True

    def handle_thread_finished(self) -> None:
        """Commit one terminal state only after its QThread has stopped."""

        job = self._job
        pending = self._pending
        worker = self._worker
        job_is_desired = bool(
            job is not None
            and self._desired is not None
            and self._same_work(job.task, job.request, self._desired)
        )
        self._worker = None
        self._thread = None
        self._job = None
        self._pending = None
        if worker is not None:
            worker.deleteLater()
        if job is not None:
            self.supervisor.finish(job.token)
            retire_session = bool(
                self.closing
                or job.cancelled
                or job.failure_detail
                or job.result is None
                or not job_is_desired
                or job.session is not self._session
            )
            if retire_session:
                self._retire_session(job.session)
        if self.closing:
            self.state_changed.emit("closed")
            self.idle_reached.emit()
            return
        if pending is not None and self._desired is not None and self._same_request(pending, self._desired):
            self._start_now(pending)
            return
        if job is not None and job_is_desired:
            if job.result is not None and not job.cancelled:
                self._cache = job.result
                self.result_ready.emit(job.task, job.result)
            elif job.failure_detail and not job.cancelled:
                self.failed.emit(job.task, job.request, job.failure_detail)
        self.state_changed.emit("idle")
        self.idle_reached.emit()

    def replace_job_for_testing(self, job: PreviewDecodeJob | None) -> None:
        self._job = job

    def replace_session_for_testing(self, session: Any | None) -> None:
        self._session = session

    def replace_cache_for_testing(self, cache: PreviewDecodeResult | None) -> None:
        self._cache = cache

    def _start_now(self, request: PreviewRequest) -> bool:
        if self._thread is not None or self.closing:
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        decode_request = request.decode_request
        try:
            session = self.session_for(decode_request)
        except Exception as exc:
            self.supervisor.finish(token)
            if self._same_request(request, self._desired):
                self.failed.emit(request.owner, decode_request, str(exc))
            self.state_changed.emit("idle")
            self.idle_reached.emit()
            return False
        thread = QThread(self)
        worker = self._worker_factory(decode_request, session)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self.handle_completed)
        worker.failed.connect(self.handle_failed)
        worker.canceled.connect(self.handle_canceled)
        bind_worker_retirement(worker, thread, worker.completed, worker.failed, worker.canceled)
        thread.finished.connect(self.handle_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._job = PreviewDecodeJob(token, request.owner, decode_request, session)
        self._thread = thread
        self._worker = worker
        self.state_changed.emit("running")
        thread.start()
        return True

    def _retire_session(self, session: Any) -> None:
        if self._session is session:
            self._session = None
        session.close()

    @staticmethod
    def _same_request(first: PreviewRequest, second: PreviewRequest) -> bool:
        return first.owner is second.owner and same_preview_request(
            first.decode_request,
            second.decode_request,
        )

    @staticmethod
    def _same_work(
        owner: object,
        decode_request: PreviewDecodeRequest,
        request: PreviewRequest,
    ) -> bool:
        return owner is request.owner and same_preview_request(decode_request, request.decode_request)
