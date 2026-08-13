from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.application.job_state import ReviewResponseJob
from neo_tracker.application.qt_worker_lifecycle import bind_worker_retirement
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.ui.review_response import (
    ReviewResponse,
    ReviewResponseRequest,
    ReviewResponseService,
)
from neo_tracker.ui.review_response_worker import ReviewResponseWorker


WorkerFactory = Callable[[ReviewResponseRequest], Any]


def _default_worker_factory(request: ReviewResponseRequest) -> ReviewResponseWorker:
    return ReviewResponseWorker(request)


class ReviewResponseCoordinator(QObject):
    """Own historical response recomputation, latest-only queueing, and LRU commit."""

    started = Signal(object)
    completed = Signal(object, object)
    response_ready = Signal(object, object)
    failed = Signal(object, str)
    canceled = Signal(object)
    state_changed = Signal(str)
    idle_reached = Signal()

    TASK_KIND = "review-response"

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        service: ReviewResponseService | None = None,
        worker_factory: WorkerFactory = _default_worker_factory,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self.service = service or ReviewResponseService(max_entries=4)
        self._worker_factory = worker_factory
        self._thread: QThread | None = None
        self._worker: Any | None = None
        self._job: ReviewResponseJob | None = None
        self._pending_request: ReviewResponseRequest | None = None
        self._completed_response: ReviewResponse | None = None
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
    def job(self) -> ReviewResponseJob | None:
        return self._job

    @property
    def pending_request(self) -> ReviewResponseRequest | None:
        return self._pending_request

    def queue(self, request: ReviewResponseRequest) -> bool:
        if self.closing or not self.request_is_valid(request):
            self._pending_request = None
            return False
        job = self._job
        if self.busy and job is not None:
            if self.same_request(job.request, request) and not job.cancelled:
                return True
            self._pending_request = request
            self._cancel_active()
            return True
        return self._start(request)

    def start(self, request: ReviewResponseRequest) -> bool:
        return self.queue(request)

    def cancel(self, reason: str = "canceled") -> bool:
        del reason
        return self._cancel_active()

    def cancel_requests(
        self,
        *,
        owner: object | None = None,
        first_frame: int | None = None,
        clear_pending: bool,
    ) -> None:
        threshold = int(first_frame) if first_frame is not None else None

        def matches(request: ReviewResponseRequest) -> bool:
            return bool(
                (owner is None or request.owner is owner)
                and (threshold is None or request.result.frame_index >= threshold)
            )

        if self._job is not None and matches(self._job.request):
            self._cancel_active()
        if clear_pending and self._pending_request is not None and matches(
            self._pending_request
        ):
            self._pending_request = None

    def invalidate(
        self,
        owner: object | None = None,
        first_frame: int | None = None,
    ) -> None:
        if owner is None:
            self.service.clear()
        else:
            self.service.invalidate(owner, first_frame)
        self.cancel_requests(
            owner=owner,
            first_frame=first_frame,
            clear_pending=True,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending_request = None
        self._cancel_active()
        if not self.busy:
            self.service.clear()
            self.state_changed.emit("closed")
            self.idle_reached.emit()

    def _start(self, request: ReviewResponseRequest) -> bool:
        if self.busy or self.closing or not self.request_is_valid(request):
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        job = ReviewResponseJob(token=token, request=request)
        try:
            thread = QThread(self)
            worker = self._worker_factory(request)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._handle_completed)
            worker.failed.connect(self._handle_failed)
            worker.canceled.connect(self._handle_canceled)
            bind_worker_retirement(worker, thread, worker.completed, worker.failed, worker.canceled)
            thread.finished.connect(self._handle_thread_finished)
            thread.finished.connect(thread.deleteLater)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._job = job
        self._thread = thread
        self._worker = worker
        self._completed_response = None
        self.state_changed.emit("running")
        self.started.emit(job)
        thread.start()
        return True

    def _cancel_active(self) -> bool:
        job = self._job
        worker = self._worker
        if job is None or worker is None or job.cancelled:
            return False
        job.cancelled = True
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def _handle_completed(self, response_object: object) -> None:
        job = self._job
        if (
            job is None
            or not self.supervisor.is_current(job.token)
            or job.cancelled
            or not isinstance(response_object, ReviewResponse)
        ):
            return
        if not self.request_is_valid(job.request):
            job.cancelled = True
            self.canceled.emit(job.request)
            return
        self._completed_response = response_object
        job.completed = True

    def _handle_failed(self, message: str) -> None:
        job = self._job
        if job is not None and self.supervisor.is_current(job.token) and not job.cancelled:
            job.failure_detail = f"Could not recompute the response map: {message}"

    def _handle_canceled(self) -> None:
        job = self._job
        if job is not None and self.supervisor.is_current(job.token):
            job.cancelled = True
            self.canceled.emit(job.request)

    def _handle_thread_finished(self) -> None:
        job = self._job
        response = self._completed_response
        pending = self._pending_request
        worker = self._worker
        self._worker = None
        self._thread = None
        self._job = None
        self._pending_request = None
        self._completed_response = None
        if worker is not None:
            worker.deleteLater()
        if job is not None:
            self.supervisor.finish(job.token)

        if self.closing:
            self.service.clear()
            self.state_changed.emit("closed")
            self.idle_reached.emit()
            return
        if pending is not None and self.request_is_valid(pending) and self._start(pending):
            return
        if (
            job is not None
            and job.completed
            and response is not None
            and self.request_is_valid(job.request)
        ):
            self.service.commit(job.request, response, fresh_for_lookup=True)
            self.completed.emit(job.request, response)
            self.response_ready.emit(job.request, response)
        elif (
            job is not None
            and job.failure_detail
            and self.request_is_valid(job.request)
        ):
            self.failed.emit(job.request, job.failure_detail)
        self.state_changed.emit("idle")
        self.idle_reached.emit()

    @staticmethod
    def same_request(
        first: ReviewResponseRequest,
        second: ReviewResponseRequest,
    ) -> bool:
        return bool(
            first.owner is second.owner
            and first.pipeline_token == second.pipeline_token
            and first.result is second.result
        )

    @staticmethod
    def request_is_valid(request: ReviewResponseRequest) -> bool:
        pipeline = getattr(request.owner, "pipeline", None)
        results = getattr(pipeline, "results", ())
        return bool(
            pipeline is not None
            and id(pipeline) == request.pipeline_token
            and any(result is request.result for result in results)
        )
