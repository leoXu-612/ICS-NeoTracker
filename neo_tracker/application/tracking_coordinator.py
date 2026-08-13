from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from neo_tracker.application.job_state import TrackingJob
from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.core import TrackerResult
from neo_tracker.observations import observation_backend_info
from neo_tracker.project import (
    TRACKING_NOTE_LIMIT,
    TrackingRunRecord,
    append_tracking_run,
    utc_timestamp,
)
from neo_tracker.ui.project_controller import DesktopTask
from neo_tracker.ui.review_controller import ReviewController
from neo_tracker.ui.tracking_worker import (
    TRACKING_SOURCE_CHANGED_PREFIX,
    TrackingProgress,
    TrackingWorker,
)


ReaderFactory = Callable[[], Any]
WorkerFactory = Callable[["TrackingRequest", TrackingJob], Any]


@dataclass(frozen=True)
class TrackingRequest:
    """Immutable inputs and injection seams for one Full or Rerun operation."""

    task: DesktopTask
    frame_count: int
    fps: float
    mode: str
    start_frame: int = 0
    prefix: tuple[TrackerResult, ...] = ()
    anchor_frame: int | None = None
    previous_analysis_run: object | None = None
    reader_factory: ReaderFactory = lambda: None
    process_isolation: bool = False
    isolated_reader_factory: ReaderFactory | None = None


def _default_worker_factory(
    request: TrackingRequest,
    job: TrackingJob,
) -> TrackingWorker:
    prefix = list(request.prefix) if request.mode == "rerun" else None
    return TrackingWorker(
        pipeline=request.task.pipeline,
        reader_factory=request.reader_factory,
        frame_count=request.frame_count,
        fps=request.fps,
        start_frame=request.start_frame,
        prefix=prefix,
        process_isolation=request.process_isolation,
        isolated_reader_factory=request.isolated_reader_factory,
        expected_source_path=job.source_path,
        expected_source_identity=job.source_identity,
    )


class TrackingCoordinator(QObject):
    """Own TrackingWorker lifecycle, replacement gates, snapshots, and terminal state."""

    started = Signal(object)
    progressed = Signal(object, object)
    terminal_ready = Signal(object)
    state_changed = Signal(str)
    idle_reached = Signal()

    TASK_KIND = "tracking"

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
        self._job: TrackingJob | None = None
        self._terminal_received = False
        self._domain_finalized = False
        self._cancel_requested = False
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
        return token.generation if token is not None else self.supervisor.generation_for(self.TASK_KIND)

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def worker(self) -> Any | None:
        return self._worker

    @property
    def job(self) -> TrackingJob | None:
        return self._job

    def start(self, request: TrackingRequest) -> bool:
        if not self.can_start:
            return False
        if request.mode not in {"full", "rerun"}:
            raise ValueError(f"unsupported tracking mode: {request.mode}")
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        task = request.task
        job = TrackingJob(
            token=token,
            task=task,
            mode=request.mode,
            start_frame=max(0, int(request.start_frame)),
            prefix=list(request.prefix),
            anchor_frame=request.anchor_frame,
            completed=max(0, int(request.start_frame)),
            started_at=utc_timestamp(),
            started_monotonic=monotonic(),
            pipeline_config=task.pipeline.to_config(),
            previous_results=list(task.pipeline.results),
            previous_edit_history=[dict(entry) for entry in task.edit_history],
            previous_tracking_outcome=task.tracking_outcome,
            previous_tracking_note=task.tracking_note,
            previous_analysis_run=request.previous_analysis_run,
            source_path=str(task.media_path or ""),
            source_identity=(
                task.media_info.source_identity if task.media_info is not None else None
            ),
        )
        try:
            thread = QThread(self)
            worker = self._worker_factory(request, job)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(self.handle_progressed)
            worker.completed.connect(self.handle_completed)
            worker.failed.connect(self.handle_failed)
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.completed.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.finished.connect(self.handle_thread_finished)
            thread.finished.connect(thread.deleteLater)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._job = job
        self._thread = thread
        self._worker = worker
        self._terminal_received = False
        self._domain_finalized = False
        self._cancel_requested = False
        self.state_changed.emit("running")
        self.started.emit(job)
        thread.start()
        return True

    def cancel(self, reason: str = "canceled") -> bool:
        del reason
        worker = self._worker
        if worker is None or self._cancel_requested:
            return False
        self._cancel_requested = True
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def close(self) -> None:
        self._closed = True
        self.cancel("closing")
        if not self.busy:
            self.state_changed.emit("closed")
            self.idle_reached.emit()

    def handle_progressed(self, progress: object) -> None:
        job = self._job
        if (
            job is None
            or self._terminal_received
            or not self.supervisor.is_current(job.token)
            or not isinstance(progress, TrackingProgress)
            or int(progress.start_frame) != int(job.start_frame)
        ):
            return
        completed = int(progress.completed)
        if completed < job.start_frame or completed > int(progress.total):
            return
        job.completed = completed
        job.tracking_elapsed_s = max(0.0, float(progress.elapsed_s))
        job.tracking_input_s = max(0.0, float(progress.input_s))
        job.tracking_processing_s = max(0.0, float(progress.processing_s))
        job.tracking_peak_debug_bytes = max(
            job.tracking_peak_debug_bytes,
            max(0, int(progress.peak_retained_debug_bytes)),
        )
        job.tracking_prefetch_frames = max(0, int(progress.prefetch_frames))
        if completed > job.start_frame:
            self._commit_replacement(job)
        self.progressed.emit(job, progress)

    def handle_completed(
        self,
        completed: int,
        cancelled: bool,
        ended_early: bool,
        note: str,
    ) -> None:
        job = self._job
        if (
            job is None
            or self._terminal_received
            or not self.supervisor.is_current(job.token)
        ):
            return
        job.completed = max(job.start_frame, int(completed))
        job.cancelled = bool(cancelled)
        job.ended_early = bool(ended_early)
        job.completion_note = self._bounded_note(note)
        self._terminal_received = True
        self._finalize_domain_state(job)
        self._domain_finalized = True
        self.state_changed.emit("finishing")

    def handle_failed(self, message: str, completed: int) -> None:
        job = self._job
        if (
            job is None
            or self._terminal_received
            or not self.supervisor.is_current(job.token)
        ):
            return
        job.completed = max(job.start_frame, int(completed))
        job.failed = True
        job.source_changed = str(message).startswith(TRACKING_SOURCE_CHANGED_PREFIX)
        if job.source_changed:
            detail = str(message)[len(TRACKING_SOURCE_CHANGED_PREFIX) :].strip()
            job.completion_note = self._bounded_note(
                "Media source changed during tracking. All newly produced results from this run "
                f"were discarded. {detail}"
            )
        else:
            job.completion_note = self._bounded_note(message)
        if job.mode == "rerun" and not job.source_changed:
            job.task.pipeline.results = list(job.prefix)
            job.task.pipeline.rebuild_debug_history()
            job.task.pipeline.tracker_filter.prime(job.prefix[-1] if job.prefix else None)
        self._terminal_received = True
        self._finalize_domain_state(job)
        self._domain_finalized = True
        self.state_changed.emit("finishing")

    def handle_thread_finished(self) -> None:
        job = self._job
        terminal_received = self._terminal_received
        domain_finalized = self._domain_finalized
        self._worker = None
        self._thread = None
        self._job = None
        self._terminal_received = False
        self._domain_finalized = False
        self._cancel_requested = False
        if job is not None:
            self.supervisor.finish(job.token)
            if not terminal_received:
                job.failed = True
                job.completion_note = "Tracking worker ended without a terminal result."
            if not domain_finalized:
                self._finalize_domain_state(job)
            self.terminal_ready.emit(job)
        self.state_changed.emit("closed" if self.closing else "idle")
        self.idle_reached.emit()

    @staticmethod
    def _commit_replacement(job: TrackingJob) -> None:
        if job.result_replacement_committed:
            return
        if job.mode == "full":
            job.task.edit_history.clear()
        elif job.mode == "rerun":
            job.superseded_edit_count = ReviewController.supersede_manual_edits_from_frame(
                job.task.edit_history,
                job.start_frame,
                superseded_at=job.started_at,
            )
        job.result_replacement_committed = True

    @classmethod
    def _finalize_domain_state(cls, job: TrackingJob) -> None:
        task = job.task
        task.media_reader = None
        if job.failed:
            run_outcome = "failed"
        elif job.cancelled:
            run_outcome = "canceled"
        elif job.ended_early:
            run_outcome = "partial"
        else:
            run_outcome = "complete"
        if job.failed:
            run_note = job.completion_note or "Tracking failed."
        elif job.cancelled:
            run_note = job.completion_note or f"Tracking canceled after {job.completed} frames."
        elif job.ended_early:
            run_note = job.completion_note or f"Source ended early after {job.completed} frames."
        else:
            run_note = ""
        processed_frames = max(0, int(job.completed) - int(job.start_frame))
        restore_previous = job.source_changed or (
            processed_frames == 0
            and bool(
                job.previous_results
                or job.previous_edit_history
                or job.previous_tracking_outcome
                or job.previous_tracking_note
            )
        )
        if restore_previous:
            task.pipeline.results = list(job.previous_results)
            task.pipeline.rebuild_debug_history()
            task.pipeline.tracker_filter.prime(
                task.pipeline.results[-1] if task.pipeline.results else None
            )
            task.edit_history = [dict(entry) for entry in job.previous_edit_history]
            task.tracking_outcome = job.previous_tracking_outcome
            task.tracking_note = job.previous_tracking_note
            job.previous_result_state_restored = True
            restored_suffix = (
                "Previous current Results/Edits, outcome, and analysis were restored because the "
                "media source changed during this run."
                if job.source_changed
                else "Previous current Results/Edits were restored because no new frame completed."
            )
            prefix_limit = max(0, 4096 - len(restored_suffix) - 1)
            run_note = f"{run_note[:prefix_limit]} {restored_suffix}".strip()
        else:
            if processed_frames > 0:
                cls._commit_replacement(job)
            task.tracking_outcome = run_outcome
            task.tracking_note = run_note
        end_frame = int(job.completed) - 1 if processed_frames else None
        append_tracking_run(
            task.run_history,
            TrackingRunRecord(
                started_at=job.started_at,
                duration_s=max(0.0, monotonic() - job.started_monotonic),
                mode=job.mode,
                outcome=run_outcome,
                start_frame=int(job.start_frame),
                end_frame=end_frame,
                processed_frames=processed_frames,
                result_count=len(task.pipeline.results),
                note=run_note,
                pipeline_config=dict(job.pipeline_config),
                tracking_elapsed_s=job.tracking_elapsed_s,
                input_s=job.tracking_input_s,
                processing_s=job.tracking_processing_s,
                peak_debug_bytes=job.tracking_peak_debug_bytes,
                prefetch_frames=job.tracking_prefetch_frames,
                compute_backend=observation_backend_info(
                    task.pipeline.observation_model
                ).label,
                source_path=job.source_path,
                source_identity=job.source_identity,
            ),
        )
        job.run_outcome = run_outcome
        job.run_note = run_note
        job.processed_frames = processed_frames

    @staticmethod
    def _bounded_note(value: object) -> str:
        text = str(value)
        if len(text) <= TRACKING_NOTE_LIMIT:
            return text
        suffix = "\n[truncated to fit the project tracking-note limit]"
        return text[: TRACKING_NOTE_LIMIT - len(suffix)].rstrip() + suffix
