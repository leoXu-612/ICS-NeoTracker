from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pickle import dumps as pickle_dumps, loads as pickle_loads
from queue import Empty, SimpleQueue
from threading import Event as ThreadEvent, Semaphore, Thread
from time import perf_counter
from typing import Any, Protocol

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.core import TrackingPipeline, TrackerResult
from neo_tracker.media import EndOfMediaError, MediaIdentity, probe_media_identity


class FrameReader(Protocol):
    def read_frame(self, frame_index: int): ...


def _read_frame_time(reader: object) -> float | None:
    value = getattr(reader, "last_frame_time_s", None)
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("frame timestamp must be a finite non-negative number")
    value = float(value)
    if not np.isfinite(value) or value < 0.0:
        raise ValueError("frame timestamp must be a finite non-negative number")
    return value


DEFAULT_TRACKING_PREFETCH_FRAMES = 1
DEFAULT_TRACKING_PROGRESS_INTERVAL_S = 0.05
DEFAULT_TRACKING_CANCEL_GRACE_S = 0.35
DEFAULT_TRACKING_KILL_GRACE_S = 0.25
DEFAULT_TRACKING_RESULT_CHECKPOINT_FRAMES = 16
_PROCESS_POLL_INTERVAL_S = 0.02
TRACKING_SOURCE_CHANGED_PREFIX = "TRACKING_SOURCE_CHANGED: "
# Child-to-parent messages carry at most the debug-history retention cap (64 MiB)
# plus one full-frame heavy response; 128 MiB keeps every legitimate run below
# the bound while preventing an unbounded pickup from a runaway child.
TRACKING_IPC_MAX_MESSAGE_BYTES = 128 * 1024 * 1024


def _encode_isolated_message(
    message: object,
    limit: int = TRACKING_IPC_MAX_MESSAGE_BYTES,
) -> bytes:
    """Serialize one child message and fail closed when it exceeds the IPC limit."""

    payload = pickle_dumps(message)
    if len(payload) > limit:
        raise RuntimeError(
            f"isolated tracking IPC message exceeded the {limit}-byte limit"
        )
    return payload


def _receive_isolated_message(connection: Connection, limit: int) -> object:
    """Read one bounded child message; an oversized frame raises OSError."""

    payload = connection.recv_bytes(maxlength=limit)
    return pickle_loads(payload)


def _validate_isolated_terminal(message: object) -> tuple[object, ...]:
    """Return a terminal tuple with the exact shape both sides agree on."""

    if (
        not isinstance(message, tuple)
        or len(message) != 7
        or not isinstance(message[6], dict)
    ):
        raise ValueError("isolated tracking returned a malformed terminal result")
    return message


def _media_identity_failure(
    source_path: str,
    expected_identity: MediaIdentity | None,
    *,
    stage: str,
) -> str:
    """Return a dedicated failure when one tracking run loses source binding."""

    if not source_path or expected_identity is None:
        return ""
    current_identity = probe_media_identity(source_path)
    if current_identity == expected_identity:
        return ""
    if current_identity is None:
        detail = "the source could no longer be verified"
    else:
        detail = "the content identity no longer matches the pre-run probe"
    return (
        f"{TRACKING_SOURCE_CHANGED_PREFIX}Media source changed {stage}; {detail}: "
        f"{source_path}"
    )


@dataclass(frozen=True)
class _PrefetchedFrame:
    frame_index: int
    frame: np.ndarray | None
    input_s: float
    error: Exception | None = None
    time_s: float | None = None


class PrefetchedFrameStream:
    """Decode a bounded number of sequential frames ahead of tracking compute."""

    def __init__(
        self,
        *,
        reader_factory: Callable[[], FrameReader],
        start_frame: int,
        frame_count: int,
        cancel_requested: Any,
        prefetch_frames: int = DEFAULT_TRACKING_PREFETCH_FRAMES,
    ) -> None:
        self.reader_factory = reader_factory
        self.start_frame = max(0, int(start_frame))
        self.frame_count = max(self.start_frame, int(frame_count))
        self.cancel_requested = cancel_requested
        self.prefetch_frames = max(1, int(prefetch_frames))
        self._queue: SimpleQueue[_PrefetchedFrame] = SimpleQueue()
        self._slots = Semaphore(self.prefetch_frames)
        self._stop_requested = ThreadEvent()
        self._finished = ThreadEvent()
        self._thread: Thread | None = None
        self._reader_close_error: Exception | None = None
        self.last_frame_time_s: float | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._produce,
            name="neo-tracker-input-prefetch",
            daemon=True,
        )
        self._thread.start()

    def read(self, frame_index: int) -> tuple[np.ndarray, float] | None:
        expected = int(frame_index)
        while not self._should_stop():
            try:
                prefetched = self._queue.get(timeout=0.02)
            except Empty:
                if self._should_stop():
                    return None
                if self._finished.is_set():
                    raise RuntimeError(f"Input prefetch ended before frame {expected} was available")
                continue
            self._slots.release()
            if prefetched.frame_index != expected:
                raise RuntimeError(
                    f"Input prefetch returned frame {prefetched.frame_index}; expected {expected}"
                )
            if prefetched.error is not None:
                raise prefetched.error
            if prefetched.frame is None:
                raise RuntimeError(f"Input prefetch returned no data for frame {expected}")
            self.last_frame_time_s = prefetched.time_s
            return prefetched.frame, max(0.0, float(prefetched.input_s))
        return None

    def close(self) -> None:
        self._stop_requested.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join()
        if self._reader_close_error is not None:
            raise self._reader_close_error

    def __enter__(self) -> "PrefetchedFrameStream":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _should_stop(self) -> bool:
        return self.cancel_requested.is_set() or self._stop_requested.is_set()

    def _reserve_slot(self) -> bool:
        while not self._should_stop():
            if self._slots.acquire(timeout=0.02):
                return True
        return False

    def _produce(self) -> None:
        reader: FrameReader | None = None
        try:
            for frame_index in range(self.start_frame, self.frame_count):
                if not self._reserve_slot():
                    return
                if self._should_stop():
                    self._slots.release()
                    return
                try:
                    if reader is None:
                        reader = self.reader_factory()
                    read_for_processing = getattr(
                        reader,
                        "read_frame_for_processing",
                        reader.read_frame,
                    )
                    started = perf_counter()
                    frame = read_for_processing(frame_index)
                    time_s = _read_frame_time(reader)
                    input_s = perf_counter() - started
                except Exception as exc:
                    self._queue.put(_PrefetchedFrame(frame_index, None, 0.0, exc))
                    return
                if self._should_stop():
                    self._slots.release()
                    return
                self._queue.put(_PrefetchedFrame(frame_index, frame, input_s, time_s=time_s))
        finally:
            close = getattr(reader, "close", None)
            try:
                if callable(close):
                    close()
            except Exception as exc:
                self._reader_close_error = exc
            finally:
                self._finished.set()


@dataclass(frozen=True)
class TrackingProgress:
    completed: int
    total: int
    start_frame: int
    elapsed_s: float
    input_s: float
    processing_s: float
    retained_debug_bytes: int = 0
    retained_debug_frames: int = 0
    peak_retained_debug_bytes: int = 0
    debug_history_max_bytes: int = 0
    prefetch_frames: int = 0
    process_isolated: bool = False

    @property
    def processed_frames(self) -> int:
        return max(0, int(self.completed) - int(self.start_frame))

    @property
    def throughput_fps(self) -> float:
        return self.processed_frames / self.elapsed_s if self.elapsed_s > 0.0 else 0.0

    @property
    def eta_s(self) -> float:
        rate = self.throughput_fps
        remaining = max(0, int(self.total) - int(self.completed))
        return remaining / rate if rate > 0.0 else 0.0

    @property
    def input_ms_per_frame(self) -> float:
        count = self.processed_frames
        return 1000.0 * self.input_s / count if count else 0.0

    @property
    def processing_ms_per_frame(self) -> float:
        count = self.processed_frames
        return 1000.0 * self.processing_s / count if count else 0.0

    @property
    def stage_overlap_s(self) -> float:
        return max(0.0, self.input_s + self.processing_s - self.elapsed_s)

    @property
    def stage_overlap_ms_per_frame(self) -> float:
        count = self.processed_frames
        return 1000.0 * self.stage_overlap_s / count if count else 0.0


def _prepare_pipeline(
    pipeline: TrackingPipeline,
    prefix: list[TrackerResult] | None,
) -> None:
    if prefix is None:
        pipeline.reset()
        return
    pipeline.results = list(prefix)
    rebuild_debug_history = getattr(pipeline, "rebuild_debug_history", None)
    if callable(rebuild_debug_history):
        rebuild_debug_history()
    pipeline.tracker_filter.prime(prefix[-1] if prefix else None)


def _builtin_pipeline_supports_process_isolation(pipeline: object) -> bool:
    """Keep unadvertised plugin state in the legacy in-process execution path.

    A third-party adapter may rely on thread-local resources or an object that
    cannot be reconstructed by Python's spawn start method. Built-in pipeline
    components are dataclasses/regular Python objects with a stable pickle
    contract and are therefore safe to transfer to the tracking subprocess.
    """

    if not isinstance(pipeline, TrackingPipeline):
        return False
    components = (
        pipeline.roi,
        pipeline.coordinate_model,
        pipeline.observation_model,
        pipeline.state_model,
        pipeline.motion_model,
        pipeline.tracker_filter,
        pipeline.optimizer,
    )
    return all(
        component is None or type(component).__module__.startswith("neo_tracker.")
        for component in components
    )


def _pipeline_process_state(pipeline: TrackingPipeline) -> dict[str, object]:
    """Return mutated non-result state without retransmitting all results."""

    excluded = {
        "results",
        "_debug_retained_results",
        "_debug_retained_bytes",
        "_debug_cache_signature",
    }
    return {name: value for name, value in vars(pipeline).items() if name not in excluded}


def _pipeline_from_process_state(state: object) -> TrackingPipeline:
    """Rebuild one built-in pipeline from the validated spawn payload."""

    if not isinstance(state, dict):
        raise TypeError("isolated tracking pipeline state must be an object")
    constructor_names = {
        descriptor.name
        for descriptor in fields(TrackingPipeline)
        if descriptor.init and descriptor.name != "results"
    }
    constructor_state = {
        name: value
        for name, value in state.items()
        if name in constructor_names
    }
    missing = constructor_names - constructor_state.keys()
    if missing:
        raise ValueError(
            "isolated tracking pipeline state is missing " + ", ".join(sorted(missing))
        )
    pipeline = TrackingPipeline(**constructor_state)
    vars(pipeline).update(
        {
            name: value
            for name, value in state.items()
            if name not in constructor_names
        }
    )
    return pipeline


def _isolated_tracking_process(
    connection: Connection,
    cancel_requested: Any,
    child_input: bytes,
    frame_count: int,
    fps: float,
    start_frame: int,
    prefetch_frames: int,
    progress_interval_s: float,
    checkpoint_frames: int,
    expected_source_path: str,
    expected_source_identity: MediaIdentity | None,
) -> None:
    """Decode and track in a disposable process, reporting bounded checkpoints."""

    completed = int(start_frame)
    reader: FrameReader | None = None
    prefetch: PrefetchedFrameStream | None = None
    ended_early = False
    cancelled_early = False
    completion_note = ""
    started_at = perf_counter()
    input_s = 0.0
    processing_s = 0.0
    retained_debug_bytes = 0
    retained_debug_frames = 0
    peak_retained_debug_bytes = 0
    last_progress_completed = int(start_frame)
    last_progress_at = float("-inf")
    unsent_results: list[TrackerResult] = []
    failure: Exception | None = None

    try:
        process_state, prefix, reader_factory = pickle_loads(child_input)
        pipeline = _pipeline_from_process_state(process_state)
        _prepare_pipeline(pipeline, prefix)
    except Exception as exc:
        try:
            connection.send_bytes(
                _encode_isolated_message(
                    (
                        "terminal",
                        completed,
                        False,
                        False,
                        "",
                        f"could not reconstruct isolated tracking input: {exc}",
                        {},
                    )
                )
            )
        except Exception:
            pass
        finally:
            connection.close()
        return

    def send(message: tuple[object, ...]) -> bool:
        try:
            payload = _encode_isolated_message(message)
        except Exception as exc:
            raise RuntimeError(f"isolated tracking IPC encode failed: {exc}") from exc
        try:
            connection.send_bytes(payload)
            return True
        except (BrokenPipeError, EOFError, OSError):
            cancel_requested.set()
            return False

    def checkpoint() -> bool:
        if not unsent_results:
            return True
        batch = list(unsent_results)
        unsent_results.clear()
        return send(("results", batch))

    def emit_progress(*, force: bool = False) -> None:
        nonlocal last_progress_completed, last_progress_at
        if completed <= start_frame or completed == last_progress_completed:
            return
        now = perf_counter()
        if not force and now - last_progress_at < progress_interval_s:
            return
        checkpoint()
        send(
            (
                "progress",
                completed,
                max(0.0, now - started_at),
                input_s,
                processing_s,
                retained_debug_bytes,
                retained_debug_frames,
                peak_retained_debug_bytes,
            )
        )
        last_progress_completed = completed
        last_progress_at = now

    try:
        identity_failure = _media_identity_failure(
            expected_source_path,
            expected_source_identity,
            stage="before decoder startup",
        )
        if identity_failure:
            raise RuntimeError(identity_failure)
        if prefetch_frames > 0:
            prefetch = PrefetchedFrameStream(
                reader_factory=reader_factory,
                start_frame=start_frame,
                frame_count=frame_count,
                cancel_requested=cancel_requested,
                prefetch_frames=prefetch_frames,
            )
            prefetch.start()
        else:
            reader = reader_factory()
            read_for_processing = getattr(reader, "read_frame_for_processing", reader.read_frame)
        total = max(0, frame_count - start_frame)
        progress_stride = max(1, total // 100)
        for frame_index in range(start_frame, frame_count):
            if cancel_requested.is_set():
                cancelled_early = True
                break
            try:
                if prefetch is not None:
                    prefetched = prefetch.read(frame_index)
                    if prefetched is None:
                        cancelled_early = True
                        break
                    frame, frame_input_s = prefetched
                    frame_time_s = prefetch.last_frame_time_s
                    input_s += frame_input_s
                else:
                    stage_started = perf_counter()
                    frame = read_for_processing(frame_index)
                    frame_time_s = _read_frame_time(reader)
                    input_s += perf_counter() - stage_started
            except EndOfMediaError as exc:
                ended_early = True
                completion_note = str(exc)
                break
            stage_started = perf_counter()
            pipeline.process_frame(
                frame,
                frame_index,
                frame_index / fps if frame_time_s is None else frame_time_s,
                compact_response_map=True,
            )
            processing_s += perf_counter() - stage_started
            result = pipeline.results[-1]
            unsent_results.append(result)
            usage = pipeline.debug_history_usage()
            retained_debug_bytes = max(0, int(usage[0]))
            retained_debug_frames = max(0, int(usage[1]))
            peak_retained_debug_bytes = max(peak_retained_debug_bytes, retained_debug_bytes)
            completed = frame_index + 1
            if len(unsent_results) >= checkpoint_frames:
                checkpoint()
            processed = completed - start_frame
            if completed == frame_count or processed % progress_stride == 0:
                emit_progress(force=completed == frame_count)
    except Exception as exc:
        failure = exc

    cleanup_error: Exception | None = None
    try:
        if prefetch is not None:
            prefetch.close()
        close = getattr(reader, "close", None)
        if callable(close):
            close()
    except Exception as exc:
        cleanup_error = exc
    if cleanup_error is not None:
        if failure is None:
            failure = RuntimeError(f"tracking input cleanup failed: {cleanup_error}")
        else:
            failure = RuntimeError(f"{failure}; tracking input cleanup also failed: {cleanup_error}")

    identity_failure = _media_identity_failure(
        expected_source_path,
        expected_source_identity,
        stage="before committing the terminal result",
    )
    if identity_failure:
        previous_failure = f"; prior tracking failure: {failure}" if failure is not None else ""
        failure = RuntimeError(f"{identity_failure}{previous_failure}")

    emit_progress(force=True)
    checkpoint()
    send(
        (
            "terminal",
            completed,
            cancelled_early,
            ended_early,
            completion_note,
            str(failure) if failure is not None else "",
            _pipeline_process_state(pipeline),
        )
    )
    connection.close()


class TrackingWorker(QObject):
    """Run bounded input-prefetched tracking without blocking the Qt event loop."""

    progress = Signal(object)
    completed = Signal(int, bool, bool, str)
    failed = Signal(str, int)

    def __init__(
        self,
        *,
        pipeline: TrackingPipeline,
        reader_factory: Callable[[], FrameReader],
        frame_count: int,
        fps: float,
        start_frame: int = 0,
        prefix: list[TrackerResult] | None = None,
        prefetch_frames: int = DEFAULT_TRACKING_PREFETCH_FRAMES,
        progress_interval_s: float = DEFAULT_TRACKING_PROGRESS_INTERVAL_S,
        process_isolation: bool = False,
        isolated_reader_factory: Callable[[], FrameReader] | None = None,
        cancel_grace_s: float = DEFAULT_TRACKING_CANCEL_GRACE_S,
        kill_grace_s: float = DEFAULT_TRACKING_KILL_GRACE_S,
        checkpoint_frames: int = DEFAULT_TRACKING_RESULT_CHECKPOINT_FRAMES,
        expected_source_path: str = "",
        expected_source_identity: MediaIdentity | None = None,
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.reader_factory = reader_factory
        self.frame_count = max(0, int(frame_count))
        self.fps = float(fps)
        self.start_frame = max(0, int(start_frame))
        self.prefix = list(prefix) if prefix is not None else None
        self.prefetch_frames = max(0, int(prefetch_frames))
        self.progress_interval_s = max(0.0, float(progress_interval_s))
        self.process_isolation = bool(process_isolation)
        self.isolated_reader_factory = isolated_reader_factory or reader_factory
        self.cancel_grace_s = max(0.0, float(cancel_grace_s))
        self.kill_grace_s = max(0.01, float(kill_grace_s))
        self.checkpoint_frames = max(1, int(checkpoint_frames))
        self.expected_source_path = str(expected_source_path)
        self.expected_source_identity = expected_source_identity
        self.process_isolation_used = False
        self.process_isolation_fallback_reason = ""
        self._isolated_child_input: bytes | None = None
        self._cancel_requested = ThreadEvent()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @Slot()
    def run(self) -> None:
        if not np.isfinite(self.fps) or self.fps <= 0.0:
            self.failed.emit("tracking fps must be finite and positive", self.start_frame)
            return
        if self._cancel_requested.is_set():
            self.completed.emit(self.start_frame, True, False, "")
            return
        try:
            if self.process_isolation:
                if self._can_use_process_isolation():
                    self.process_isolation_used = True
                    self._run_isolated()
                else:
                    reason = self.process_isolation_fallback_reason or "unknown compatibility failure"
                    self.failed.emit(
                        f"process isolation is required but unavailable: {reason}",
                        self.start_frame,
                    )
                return
            self._run_threaded()
        except Exception as exc:
            completed = self.start_frame
            if self.pipeline.results:
                try:
                    completed = max(
                        self.start_frame,
                        int(self.pipeline.results[-1].frame_index) + 1,
                    )
                except (AttributeError, TypeError, ValueError, OverflowError):
                    completed = self.start_frame
            self.failed.emit(f"tracking worker failed unexpectedly: {exc}", completed)
        finally:
            self._isolated_child_input = None

    def _can_use_process_isolation(self) -> bool:
        if not self.process_isolation:
            return False
        if not _builtin_pipeline_supports_process_isolation(self.pipeline):
            self.process_isolation_fallback_reason = (
                "third-party pipeline state has no declared spawn/pickle compatibility"
            )
            return False
        try:
            # A full run will reset stale results before spawn; do not serialize
            # them once just for capability detection. Rerun prefixes are real
            # child input and therefore remain part of this check.
            self._isolated_child_input = pickle_dumps(
                (
                    _pipeline_process_state(self.pipeline),
                    self.prefix,
                    self.isolated_reader_factory,
                )
            )
        except Exception as exc:
            self._isolated_child_input = None
            self.process_isolation_fallback_reason = f"tracking state is not spawn-serializable: {exc}"
            return False
        return True

    def _accept_isolated_results(self, results: list[TrackerResult]) -> None:
        if not isinstance(results, list) or len(results) > self.checkpoint_frames:
            raise RuntimeError(
                "isolated tracking returned an oversized result batch "
                f"(limit {self.checkpoint_frames})"
            )
        append_result = getattr(self.pipeline, "_append_result", None)
        if not callable(append_result):
            raise RuntimeError("isolated tracking requires the built-in result retention contract")
        expected = (
            int(self.pipeline.results[-1].frame_index) + 1
            if self.pipeline.results
            else self.start_frame
        )
        for result in results:
            if int(result.frame_index) != expected:
                raise RuntimeError(
                    f"isolated tracking returned frame {result.frame_index}; expected {expected}"
                )
            append_result(result)
            expected += 1

    def _emit_isolated_progress(
        self,
        message: tuple[object, ...],
        *,
        started_at: float,
    ) -> None:
        (
            _kind,
            completed,
            _child_elapsed_s,
            input_s,
            processing_s,
            retained_debug_bytes,
            retained_debug_frames,
            peak_retained_debug_bytes,
        ) = message
        self.progress.emit(
            TrackingProgress(
                completed=int(completed),
                total=self.frame_count,
                start_frame=self.start_frame,
                elapsed_s=max(0.0, perf_counter() - started_at),
                input_s=max(0.0, float(input_s)),
                processing_s=max(0.0, float(processing_s)),
                retained_debug_bytes=max(0, int(retained_debug_bytes)),
                retained_debug_frames=max(0, int(retained_debug_frames)),
                peak_retained_debug_bytes=max(0, int(peak_retained_debug_bytes)),
                debug_history_max_bytes=max(
                    0,
                    int(getattr(self.pipeline, "debug_history_max_bytes", 0)),
                ),
                prefetch_frames=self.prefetch_frames,
                process_isolated=True,
            )
        )

    def _run_isolated(self) -> None:
        completed = self.start_frame
        if not np.isfinite(self.fps) or self.fps <= 0.0:
            self.failed.emit("tracking fps must be finite and positive", completed)
            return
        if self._cancel_requested.is_set():
            self.completed.emit(completed, True, False, "")
            return

        identity_failure = _media_identity_failure(
            self.expected_source_path,
            self.expected_source_identity,
            stage="before isolated process startup",
        )
        if identity_failure:
            self.failed.emit(identity_failure, completed)
            return

        _prepare_pipeline(self.pipeline, self.prefix)
        child_input = self._isolated_child_input
        if child_input is None:
            raise RuntimeError("isolated tracking input was not prepared")
        context = get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process_cancel = context.Event()
        process = context.Process(
            target=_isolated_tracking_process,
            name="neo-tracker-isolated-tracking",
            args=(
                send_connection,
                process_cancel,
                child_input,
                self.frame_count,
                self.fps,
                self.start_frame,
                self.prefetch_frames,
                self.progress_interval_s,
                self.checkpoint_frames,
                self.expected_source_path,
                self.expected_source_identity,
            ),
            daemon=True,
        )
        started_at = perf_counter()
        terminal: tuple[object, ...] | None = None
        monitor_failure = ""
        cancel_started_at: float | None = None
        forced_stop = False
        killed = False
        shutdown_unresponsive = False
        pipe_eof = False
        process_exitcode: int | None = None
        try:
            process.start()
            self._isolated_child_input = None
            del child_input
            send_connection.close()
            while True:
                if self._cancel_requested.is_set():
                    process_cancel.set()
                    if cancel_started_at is None:
                        cancel_started_at = perf_counter()

                received = False
                try:
                    if receive_connection.poll(_PROCESS_POLL_INTERVAL_S):
                        message = _receive_isolated_message(
                            receive_connection,
                            limit=TRACKING_IPC_MAX_MESSAGE_BYTES,
                        )
                        received = True
                        kind = message[0]
                        if kind == "results":
                            self._accept_isolated_results(message[1])
                            completed = (
                                int(self.pipeline.results[-1].frame_index) + 1
                                if self.pipeline.results
                                else self.start_frame
                            )
                        elif kind == "progress":
                            self._emit_isolated_progress(message, started_at=started_at)
                        elif kind == "terminal":
                            terminal = message
                            break
                        else:
                            raise RuntimeError(f"unknown isolated tracking message {kind!r}")
                except EOFError:
                    pipe_eof = True
                except OSError as exc:
                    pipe_eof = True
                    process_cancel.set()
                    if cancel_started_at is None:
                        cancel_started_at = perf_counter()

                if (
                    cancel_started_at is not None
                    and process.is_alive()
                    and perf_counter() - cancel_started_at >= self.cancel_grace_s
                ):
                    process.terminate()
                    process.join(timeout=self.kill_grace_s)
                    forced_stop = True
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=self.kill_grace_s)
                        killed = True
                        if process.is_alive():
                            shutdown_unresponsive = True
                            monitor_failure = (
                                "isolated tracking subprocess remained alive after terminate and kill; "
                                "the operating system may still be completing uninterruptible I/O"
                            )
                            break

                if forced_stop and not process.is_alive():
                    break
                if not process.is_alive() and not received:
                    if pipe_eof:
                        break
                    # A final message can already be buffered after the child exits.
                    try:
                        if receive_connection.poll():
                            continue
                    except (EOFError, OSError):
                        pass
                    break
        except Exception as exc:
            monitor_failure = f"could not run isolated tracking: {exc}"
        finally:
            process_cancel.set()
            if process.pid is not None and process.is_alive() and not shutdown_unresponsive:
                process.terminate()
                process.join(timeout=self.kill_grace_s)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=self.kill_grace_s)
                    killed = True
            receive_connection.close()
            send_connection.close()
            if process.pid is not None and not process.is_alive():
                process_exitcode = process.exitcode
                process.close()

        if terminal is not None:
            try:
                terminal = _validate_isolated_terminal(terminal)
            except ValueError as exc:
                self.failed.emit(str(exc), completed)
                return
            (
                _kind,
                terminal_completed,
                cancelled,
                ended_early,
                completion_note,
                failure_message,
                process_state,
            ) = terminal
            completed = int(terminal_completed)
            vars(self.pipeline).update(process_state)
            self.pipeline.rebuild_debug_history()
            identity_failure = _media_identity_failure(
                self.expected_source_path,
                self.expected_source_identity,
                stage="after isolated processing",
            )
            if identity_failure:
                prior_failure = f"; prior tracking failure: {failure_message}" if failure_message else ""
                self.failed.emit(f"{identity_failure}{prior_failure}", completed)
            elif failure_message:
                self.failed.emit(str(failure_message), completed)
            else:
                self.completed.emit(
                    completed,
                    bool(cancelled),
                    bool(ended_early),
                    str(completion_note),
                )
            return

        if self.pipeline.results:
            self.pipeline.rebuild_debug_history()
            self.pipeline.tracker_filter.prime(self.pipeline.results[-1])
            completed = int(self.pipeline.results[-1].frame_index) + 1
        identity_failure = _media_identity_failure(
            self.expected_source_path,
            self.expected_source_identity,
            stage="after forced cancellation",
        )
        if identity_failure:
            self.failed.emit(identity_failure, completed)
            return
        if monitor_failure:
            self.failed.emit(monitor_failure, completed)
            return
        if forced_stop and self._cancel_requested.is_set():
            stop_method = "killed" if killed else "terminated"
            if completed >= self.frame_count:
                note = (
                    f"Tracking completed all {self.frame_count} frames; cleanup {stop_method} an "
                    f"unresponsive decoder subprocess after {self.cancel_grace_s:.2f}s."
                )
                self.completed.emit(completed, False, False, note)
                return
            note = (
                f"Cancellation {stop_method} an unresponsive decoder subprocess after "
                f"{self.cancel_grace_s:.2f}s; results through frame {completed - 1} were kept."
            )
            self.completed.emit(completed, True, False, note)
            return
        monitor_failure = (
            "isolated tracking subprocess exited without a terminal result"
            + (
                f" (exit code {process_exitcode})"
                if process_exitcode is not None
                else ""
            )
        )
        self.failed.emit(monitor_failure, completed)

    def _run_threaded(self) -> None:
        completed = self.start_frame
        reader: FrameReader | None = None
        prefetch: PrefetchedFrameStream | None = None
        ended_early = False
        cancelled_early = False
        completion_note = ""
        started_at = perf_counter()
        input_s = 0.0
        processing_s = 0.0
        retained_debug_bytes = 0
        retained_debug_frames = 0
        peak_retained_debug_bytes = 0
        last_progress_completed = self.start_frame
        last_progress_at = float("-inf")

        def emit_progress(*, force: bool = False) -> None:
            nonlocal last_progress_completed, last_progress_at
            if completed <= self.start_frame or completed == last_progress_completed:
                return
            now = perf_counter()
            if not force and now - last_progress_at < self.progress_interval_s:
                return
            self.progress.emit(
                TrackingProgress(
                    completed=completed,
                    total=self.frame_count,
                    start_frame=self.start_frame,
                    elapsed_s=max(0.0, now - started_at),
                    input_s=input_s,
                    processing_s=processing_s,
                    retained_debug_bytes=retained_debug_bytes,
                    retained_debug_frames=retained_debug_frames,
                    peak_retained_debug_bytes=peak_retained_debug_bytes,
                    debug_history_max_bytes=max(
                        0,
                        int(getattr(self.pipeline, "debug_history_max_bytes", 0)),
                    ),
                    prefetch_frames=self.prefetch_frames,
                )
            )
            last_progress_completed = completed
            last_progress_at = now

        failure: Exception | None = None
        try:
            if not np.isfinite(self.fps) or self.fps <= 0.0:
                raise ValueError("tracking fps must be finite and positive")
            if self._cancel_requested.is_set():
                self.completed.emit(completed, True, False, "")
                return
            identity_failure = _media_identity_failure(
                self.expected_source_path,
                self.expected_source_identity,
                stage="before decoder startup",
            )
            if identity_failure:
                raise RuntimeError(identity_failure)
            _prepare_pipeline(self.pipeline, self.prefix)

            if self.prefetch_frames > 0:
                prefetch = PrefetchedFrameStream(
                    reader_factory=self.reader_factory,
                    start_frame=self.start_frame,
                    frame_count=self.frame_count,
                    cancel_requested=self._cancel_requested,
                    prefetch_frames=self.prefetch_frames,
                )
                prefetch.start()
            else:
                reader = self.reader_factory()
                read_for_processing = getattr(reader, "read_frame_for_processing", reader.read_frame)
            total = max(0, self.frame_count - self.start_frame)
            progress_stride = max(1, total // 100)
            for frame_index in range(self.start_frame, self.frame_count):
                if self._cancel_requested.is_set():
                    cancelled_early = True
                    break
                try:
                    if prefetch is not None:
                        prefetched = prefetch.read(frame_index)
                        if prefetched is None:
                            cancelled_early = True
                            break
                        frame, frame_input_s = prefetched
                        frame_time_s = prefetch.last_frame_time_s
                        input_s += frame_input_s
                    else:
                        stage_started = perf_counter()
                        frame = read_for_processing(frame_index)
                        frame_time_s = _read_frame_time(reader)
                        input_s += perf_counter() - stage_started
                except EndOfMediaError as exc:
                    ended_early = True
                    completion_note = str(exc)
                    break
                stage_started = perf_counter()
                time_s = frame_index / self.fps if frame_time_s is None else frame_time_s
                if isinstance(self.pipeline, TrackingPipeline):
                    self.pipeline.process_frame(
                        frame,
                        frame_index,
                        time_s,
                        compact_response_map=True,
                    )
                else:  # Structural test doubles and third-party pipeline adapters.
                    self.pipeline.process_frame(frame, frame_index, time_s)
                processing_s += perf_counter() - stage_started
                usage = getattr(self.pipeline, "debug_history_usage", None)
                if callable(usage):
                    retained_debug_bytes, retained_debug_frames = usage()
                    retained_debug_bytes = max(0, int(retained_debug_bytes))
                    retained_debug_frames = max(0, int(retained_debug_frames))
                    peak_retained_debug_bytes = max(peak_retained_debug_bytes, retained_debug_bytes)
                completed = frame_index + 1
                processed = completed - self.start_frame
                if completed == self.frame_count or processed % progress_stride == 0:
                    emit_progress(force=completed == self.frame_count)
        except Exception as exc:
            failure = exc

        cleanup_error: Exception | None = None
        try:
            if prefetch is not None:
                prefetch.close()
            close = getattr(reader, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            cleanup_error = exc

        if cleanup_error is not None:
            if failure is None:
                failure = RuntimeError(f"tracking input cleanup failed: {cleanup_error}")
            else:
                failure = RuntimeError(f"{failure}; tracking input cleanup also failed: {cleanup_error}")

        identity_failure = _media_identity_failure(
            self.expected_source_path,
            self.expected_source_identity,
            stage="after threaded processing",
        )
        if identity_failure:
            previous_failure = f"; prior tracking failure: {failure}" if failure is not None else ""
            failure = RuntimeError(f"{identity_failure}{previous_failure}")

        emit_progress(force=True)
        if failure is not None:
            self.failed.emit(str(failure), completed)
        else:
            self.completed.emit(completed, cancelled_early, ended_early, completion_note)
