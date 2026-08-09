from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from threading import Event, Lock
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.media import MediaInfo
from neo_tracker.project import (
    MAX_PROJECT_CONTAINER_ITEMS,
    MAX_PROJECT_FILE_BYTES,
    MAX_PROJECT_NESTING_DEPTH,
    MAX_PROJECT_RESULTS,
    MAX_PROJECT_TASKS,
    MAX_TASK_EDIT_HISTORY,
    MAX_TASK_RESULTS,
    TRACKING_RUN_HISTORY_LIMIT,
    NeoTrackerProject,
    ProjectTaskSnapshot,
    TrackingRunRecord,
    project_content_fingerprint,
    tracker_result_from_dict,
)
from neo_tracker.ui.media_probe_worker import probe_media_safely
from neo_tracker.ui.analysis_controller import AnalysisController, AnalysisSource
from neo_tracker.ui.project_controller import DesktopTask, ProjectTaskController
from neo_tracker.ui.review_diagnostics import (
    PreparedReviewDiagnostics,
    prepare_review_diagnostics,
)


ProjectLoader = Callable[[str | Path], NeoTrackerProject]


_PROJECT_OPEN_STAGE_SCHEMA = "neo-tracker-project-open/v1"
_PROJECT_OPEN_STAGE_MAX_BYTES = 96 * 1024 * 1024
_PROJECT_OPEN_STAGE_MAX_RECORD_BYTES = 8 * 1024 * 1024
_PROJECT_OPEN_REPLY_MAX_BYTES = 64 * 1024
_PROJECT_OPEN_LOAD_DEADLINE_S = 90.0
_PROJECT_OPEN_PROCESS_STOP_GRACE_S = 0.35
_PROJECT_OPEN_GC_GUARD_LOCK = Lock()
_PROJECT_OPEN_GC_GUARD_DEPTH = 0
_PROJECT_OPEN_GC_GUARD_THRESHOLDS: tuple[int, int, int] | None = None


def _acquire_project_open_gc_guard() -> None:
    """Delay expensive high-generation scans while any project is materializing."""

    global _PROJECT_OPEN_GC_GUARD_DEPTH, _PROJECT_OPEN_GC_GUARD_THRESHOLDS
    with _PROJECT_OPEN_GC_GUARD_LOCK:
        if _PROJECT_OPEN_GC_GUARD_DEPTH == 0:
            thresholds = gc.get_threshold()
            gc.set_threshold(
                thresholds[0],
                max(thresholds[1], 1_000_000),
                max(thresholds[2], 1_000_000),
            )
            _PROJECT_OPEN_GC_GUARD_THRESHOLDS = thresholds
        _PROJECT_OPEN_GC_GUARD_DEPTH += 1


def _release_project_open_gc_guard() -> None:
    global _PROJECT_OPEN_GC_GUARD_DEPTH, _PROJECT_OPEN_GC_GUARD_THRESHOLDS
    with _PROJECT_OPEN_GC_GUARD_LOCK:
        if _PROJECT_OPEN_GC_GUARD_DEPTH <= 0:
            raise RuntimeError("project-open GC guard release is unbalanced")
        _PROJECT_OPEN_GC_GUARD_DEPTH -= 1
        if _PROJECT_OPEN_GC_GUARD_DEPTH == 0:
            thresholds = _PROJECT_OPEN_GC_GUARD_THRESHOLDS
            _PROJECT_OPEN_GC_GUARD_THRESHOLDS = None
            if thresholds is not None:
                gc.set_threshold(*thresholds)


def _safe_json_loads(payload: bytes, *, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc
    try:
        return json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"{label} contains non-finite constant {token}")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} must contain valid JSON: {exc}") from exc


def _read_project_json_for_stage(project_path: str) -> dict[str, Any]:
    path = Path(project_path)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not S_ISREG(file_stat.st_mode):
            raise ValueError(f"project source is not a regular file: {path}")
        if file_stat.st_size > MAX_PROJECT_FILE_BYTES:
            raise ValueError(
                f"project file is {file_stat.st_size:,} bytes, above the "
                f"{MAX_PROJECT_FILE_BYTES:,}-byte safety limit"
            )
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            payload = source.read(MAX_PROJECT_FILE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > MAX_PROJECT_FILE_BYTES:
        raise ValueError(
            f"project file grew above the {MAX_PROJECT_FILE_BYTES:,}-byte safety limit while reading"
        )
    parsed = _safe_json_loads(payload, label="project file")
    if not isinstance(parsed, dict):
        raise ValueError("project data must be a dictionary")
    if parsed.get("format") != "neo-tracker-project":
        raise ValueError("not a Neo-Tracker project file")
    return NeoTrackerProject._migrate_to_current(parsed)


def _write_project_open_stage(payload: dict[str, Any], stage_path: str) -> tuple[int, str]:
    """Write parsed project JSON as bounded records for streaming validation.

    JSON Lines keeps each result decode short when the GUI-side QThread rebuilds
    the project.  In contrast, one 40+ MiB ``json.loads`` call can retain the GIL
    long enough to stop Qt heartbeats even though it runs on a QThread.
    """

    project_header = dict(payload)
    media_paths = project_header.pop("media_paths", [])
    pipelines = project_header.pop("pipeline_library", [])
    tasks = project_header.pop("tasks", [])
    if not isinstance(media_paths, list):
        raise ValueError("project media_paths must be a list")
    if not isinstance(pipelines, list):
        raise ValueError("project pipeline_library must be a list")
    if not isinstance(tasks, list):
        raise ValueError("project tasks must be a list")
    if len(tasks) > MAX_PROJECT_TASKS:
        raise ValueError(f"project tasks must not exceed {MAX_PROJECT_TASKS} entries")
    header = {
        "schema": _PROJECT_OPEN_STAGE_SCHEMA,
        "project": project_header,
        "media_path_count": len(media_paths),
        "pipeline_count": len(pipelines),
        "task_count": len(tasks),
    }
    digest = sha256()
    byte_count = 0

    def write_record(target: Any, record: dict[str, object]) -> None:
        nonlocal byte_count
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        if len(encoded) > _PROJECT_OPEN_STAGE_MAX_RECORD_BYTES:
            raise ValueError(
                "project contains one record above the isolated-open safety limit "
                f"of {_PROJECT_OPEN_STAGE_MAX_RECORD_BYTES:,} bytes"
            )
        byte_count += len(encoded)
        if byte_count > _PROJECT_OPEN_STAGE_MAX_BYTES:
            raise ValueError(
                "isolated project-open staging exceeds the safety limit of "
                f"{_PROJECT_OPEN_STAGE_MAX_BYTES:,} bytes"
            )
        target.write(encoded)
        digest.update(encoded)

    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(stage_path, flags)
    with os.fdopen(descriptor, "wb") as target:
        write_record(target, header)
        for index, value in enumerate(media_paths):
            write_record(target, {"kind": "media_path", "index": index, "value": value})
        for index, value in enumerate(pipelines):
            write_record(target, {"kind": "pipeline", "index": index, "value": value})
        for task_index, task_value in enumerate(tasks):
            if not isinstance(task_value, dict):
                raise ValueError("project task must be a dictionary")
            task = dict(task_value)
            results = task.pop("results", [])
            edit_history = task.pop("edit_history", [])
            run_history = task.pop("run_history", [])
            if not isinstance(results, list):
                raise ValueError("project task results must be a list")
            if not isinstance(edit_history, list):
                raise ValueError("project task edit_history must be a list")
            if not isinstance(run_history, list):
                raise ValueError("project task run_history must be a list")
            write_record(
                target,
                {
                    "kind": "task",
                    "index": task_index,
                    "value": task,
                    "result_count": len(results),
                    "edit_history_count": len(edit_history),
                    "run_history_count": len(run_history),
                },
            )
            for index, value in enumerate(results):
                write_record(
                    target,
                    {
                        "kind": "result",
                        "task_index": task_index,
                        "index": index,
                        "value": value,
                    },
                )
            for index, value in enumerate(edit_history):
                write_record(
                    target,
                    {
                        "kind": "edit_history",
                        "task_index": task_index,
                        "index": index,
                        "value": value,
                    },
                )
            for index, value in enumerate(run_history):
                write_record(
                    target,
                    {
                        "kind": "run_history",
                        "task_index": task_index,
                        "index": index,
                        "value": value,
                    },
                )
        target.flush()
        os.fsync(target.fileno())
    return byte_count, digest.hexdigest()


def _isolated_project_load_entry(project_path: str, stage_path: str) -> dict[str, object]:
    """Validate and stage a project without sharing Python objects or pickle."""

    try:
        payload = _read_project_json_for_stage(project_path)
        byte_count, digest = _write_project_open_stage(payload, stage_path)
        return {"status": "ok", "bytes": byte_count, "sha256": digest}
    except BaseException as exc:  # child boundary must always report a stable failure
        return {"status": "error", "message": str(exc) or type(exc).__name__}


def _stop_load_process(process: Any) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(_PROJECT_OPEN_PROCESS_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        process.kill()
        try:
            process.wait(_PROJECT_OPEN_PROCESS_STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            pass


@dataclass
class _StreamingTaskSnapshot:
    base: ProjectTaskSnapshot
    expected_results: int
    expected_edits: int
    expected_runs: int
    results: list[Any]
    edit_history: list[dict[str, Any]]
    run_history: list[TrackingRunRecord]

    @property
    def complete(self) -> bool:
        return (
            len(self.results) == self.expected_results
            and len(self.edit_history) == self.expected_edits
            and len(self.run_history) == self.expected_runs
        )


def _project_structure_item_count(value: object, *, base_depth: int) -> int:
    """Validate one streamed value against the persisted project structure limits."""

    pending: list[tuple[object, int]] = [(value, base_depth)]
    item_count = 0
    while pending:
        current, depth = pending.pop()
        if isinstance(current, dict):
            if depth > MAX_PROJECT_NESTING_DEPTH:
                raise ValueError(
                    f"project data nesting must not exceed {MAX_PROJECT_NESTING_DEPTH} levels"
                )
            item_count += len(current)
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple)):
            if depth > MAX_PROJECT_NESTING_DEPTH:
                raise ValueError(
                    f"project data nesting must not exceed {MAX_PROJECT_NESTING_DEPTH} levels"
                )
            item_count += len(current)
            pending.extend((item, depth + 1) for item in current)
        if item_count > MAX_PROJECT_CONTAINER_ITEMS:
            raise ValueError(
                "project data contains more than "
                f"{MAX_PROJECT_CONTAINER_ITEMS:,} container items"
            )
    return item_count


def _read_project_open_stage(
    stage_path: Path,
    *,
    expected_bytes: int,
    expected_digest: str,
    cancel_requested: Event,
    progress: Callable[[str], None] | None = None,
) -> NeoTrackerProject:
    if progress is not None:
        progress("decoding")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(stage_path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not S_ISREG(file_stat.st_mode):
            raise ValueError("isolated project-open stage is not a regular file")
        if file_stat.st_size != expected_bytes or file_stat.st_size > _PROJECT_OPEN_STAGE_MAX_BYTES:
            raise ValueError("isolated project-open stage size does not match its bounded manifest")
        digest = sha256()
        byte_count = 0
        header: dict[str, Any] | None = None
        media_paths: list[object] = []
        pipelines: list[object] = []
        tasks: list[_StreamingTaskSnapshot] = []
        project_structure_items = 0
        total_results = 0
        expected_collection_counts: tuple[int, int, int] | None = None
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            for line_number, line in enumerate(source, start=1):
                if cancel_requested.is_set():
                    raise InterruptedError("project open canceled")
                if line_number % 256 == 0:
                    # Explicitly hand the GIL back to the Qt event loop. The
                    # per-record JSON/model work is individually small, but a
                    # sustained 100k-record stream can otherwise starve GUI
                    # timer delivery on some macOS scheduler runs.
                    cancel_requested.wait(0.0005)
                byte_count += len(line)
                if byte_count > _PROJECT_OPEN_STAGE_MAX_BYTES:
                    raise ValueError("isolated project-open stage grew above its safety limit")
                if len(line) > _PROJECT_OPEN_STAGE_MAX_RECORD_BYTES:
                    raise ValueError("isolated project-open stage contains an oversized record")
                digest.update(line)
                record = _safe_json_loads(line, label=f"isolated project-open record {line_number}")
                if not isinstance(record, dict):
                    raise ValueError("isolated project-open records must be dictionaries")
                if line_number == 1:
                    if record.get("schema") != _PROJECT_OPEN_STAGE_SCHEMA:
                        raise ValueError("isolated project-open stage has an unsupported schema")
                    project_value = record.get("project")
                    if not isinstance(project_value, dict):
                        raise ValueError("isolated project-open header has no project data")
                    project_structure_items += _project_structure_item_count(
                        project_value,
                        base_depth=0,
                    )
                    collection_counts: list[int] = []
                    for key in ("media_path_count", "pipeline_count", "task_count"):
                        value = record.get(key)
                        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                            raise ValueError(f"isolated project-open {key} is invalid")
                        collection_counts.append(value)
                    if collection_counts[2] > MAX_PROJECT_TASKS:
                        raise ValueError(
                            f"project tasks must not exceed {MAX_PROJECT_TASKS} entries"
                        )
                    expected_collection_counts = tuple(collection_counts)  # type: ignore[assignment]
                    header = record
                    continue
                kind = record.get("kind")
                index = record.get("index")
                if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                    raise ValueError("isolated project-open record index is invalid")
                if kind == "media_path":
                    if expected_collection_counts is None:
                        raise ValueError("isolated project-open stage has no collection manifest")
                    if (
                        pipelines
                        or tasks
                        or len(media_paths) >= expected_collection_counts[0]
                        or index != len(media_paths)
                    ):
                        raise ValueError("isolated project-open media paths are out of order")
                    media_paths.append(record.get("value"))
                    continue
                if kind == "pipeline":
                    if expected_collection_counts is None:
                        raise ValueError("isolated project-open stage has no collection manifest")
                    if (
                        len(media_paths) != expected_collection_counts[0]
                        or tasks
                        or len(pipelines) >= expected_collection_counts[1]
                        or index != len(pipelines)
                    ):
                        raise ValueError("isolated project-open pipelines are out of order")
                    pipeline_value = record.get("value")
                    project_structure_items += _project_structure_item_count(
                        pipeline_value,
                        base_depth=2,
                    )
                    pipelines.append(pipeline_value)
                    continue
                if kind == "task":
                    if expected_collection_counts is None:
                        raise ValueError("isolated project-open stage has no collection manifest")
                    if (
                        len(media_paths) != expected_collection_counts[0]
                        or len(pipelines) != expected_collection_counts[1]
                        or len(tasks) >= expected_collection_counts[2]
                        or index != len(tasks)
                        or not isinstance(record.get("value"), dict)
                    ):
                        raise ValueError("isolated project-open tasks are invalid or out of order")
                    if tasks and not tasks[-1].complete:
                        raise ValueError("isolated project-open task records are incomplete or interleaved")
                    counts: list[int] = []
                    for key in ("result_count", "edit_history_count", "run_history_count"):
                        value = record.get(key)
                        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                            raise ValueError(f"isolated project-open {key} is invalid")
                        counts.append(value)
                    if counts[0] > MAX_TASK_RESULTS:
                        raise ValueError(
                            f"project task results must not exceed {MAX_TASK_RESULTS:,} entries"
                        )
                    if counts[1] > MAX_TASK_EDIT_HISTORY:
                        raise ValueError(
                            "project task edit_history must not exceed "
                            f"{MAX_TASK_EDIT_HISTORY:,} entries"
                        )
                    if counts[2] > TRACKING_RUN_HISTORY_LIMIT:
                        raise ValueError(
                            "project task run_history must not exceed "
                            f"{TRACKING_RUN_HISTORY_LIMIT} entries"
                        )
                    total_results += counts[0]
                    if total_results > MAX_PROJECT_RESULTS:
                        raise ValueError(
                            f"project results must not exceed {MAX_PROJECT_RESULTS:,} total entries"
                        )
                    task_value = dict(record["value"])
                    project_structure_items += _project_structure_item_count(
                        task_value,
                        base_depth=2,
                    )
                    task_value["results"] = []
                    task_value["edit_history"] = []
                    task_value["run_history"] = []
                    base = ProjectTaskSnapshot.from_dict(task_value)
                    tasks.append(
                        _StreamingTaskSnapshot(
                            base=base,
                            expected_results=counts[0],
                            expected_edits=counts[1],
                            expected_runs=counts[2],
                            results=[],
                            edit_history=[],
                            run_history=[],
                        )
                    )
                    continue
                task_index = record.get("task_index")
                if (
                    isinstance(task_index, bool)
                    or not isinstance(task_index, int)
                    or task_index < 0
                    or task_index != len(tasks) - 1
                ):
                    raise ValueError("isolated project-open result references an invalid task")
                task = tasks[task_index]
                value = record.get("value")
                if kind == "result":
                    if len(task.results) >= task.expected_results or index != len(task.results):
                        raise ValueError("isolated project-open results are out of order")
                    project_structure_items += _project_structure_item_count(
                        value,
                        base_depth=4,
                    )
                    result = tracker_result_from_dict(value)
                    if task.results:
                        previous = task.results[-1]
                        if result.frame_index <= previous.frame_index:
                            raise ValueError(
                                "project task results must have unique increasing frame_index values"
                            )
                        if result.time_s < previous.time_s:
                            raise ValueError(
                                "project task results time_s values must be non-decreasing"
                            )
                    task.results.append(result)
                elif kind == "edit_history":
                    if len(task.results) != task.expected_results:
                        raise ValueError("isolated project-open edits precede required results")
                    if index != len(task.edit_history) or not isinstance(value, dict):
                        raise ValueError("isolated project-open edit history is invalid or out of order")
                    project_structure_items += _project_structure_item_count(
                        value,
                        base_depth=4,
                    )
                    task.edit_history.append(dict(value))
                elif kind == "run_history":
                    if (
                        len(task.results) != task.expected_results
                        or len(task.edit_history) != task.expected_edits
                        or index != len(task.run_history)
                    ):
                        raise ValueError("isolated project-open run history is out of order")
                    project_structure_items += _project_structure_item_count(
                        value,
                        base_depth=4,
                    )
                    task.run_history.append(TrackingRunRecord.from_dict(value))
                else:
                    raise ValueError(f"unsupported isolated project-open record kind: {kind!r}")
                if project_structure_items > MAX_PROJECT_CONTAINER_ITEMS:
                    raise ValueError(
                        "project data contains more than "
                        f"{MAX_PROJECT_CONTAINER_ITEMS:,} container items"
                    )
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if header is None or byte_count != expected_bytes or digest.hexdigest() != expected_digest:
        raise ValueError("isolated project-open stage failed integrity validation")
    project_data = header.get("project")
    if not isinstance(project_data, dict):
        raise ValueError("isolated project-open header has no project data")
    if expected_collection_counts != (len(media_paths), len(pipelines), len(tasks)):
        raise ValueError("isolated project-open collection counts do not match the manifest")
    if any(not task.complete for task in tasks):
        raise ValueError("isolated project-open task counts do not match the manifest")
    project_structure_items += len(media_paths) + len(pipelines) + len(tasks)
    project_structure_items += sum(
        len(task.results) + len(task.edit_history) + len(task.run_history)
        for task in tasks
    )
    if project_structure_items > MAX_PROJECT_CONTAINER_ITEMS:
        raise ValueError(
            "project data contains more than "
            f"{MAX_PROJECT_CONTAINER_ITEMS:,} container items"
        )
    project_data = dict(project_data)
    project_data["media_paths"] = media_paths
    project_data["pipeline_library"] = pipelines
    project_data["tasks"] = []
    if progress is not None:
        progress("materializing")
    project = NeoTrackerProject.from_dict(project_data)
    finalized_tasks = [
        replace(
            task.base,
            results=task.results,
            edit_history=task.edit_history,
            run_history=task.run_history,
        )
        for task in tasks
    ]
    return replace(project, tasks=finalized_tasks)


def load_project_isolated(
    path: str | Path,
    cancel_requested: Event,
    *,
    progress: Callable[[str], None] | None = None,
) -> NeoTrackerProject:
    """Load the production project format behind a bounded spawn boundary."""

    stage = tempfile.NamedTemporaryFile(prefix="neo-tracker-open-", suffix=".jsonl", delete=False)
    stage_path = Path(stage.name)
    stage.close()
    os.chmod(stage_path, 0o600)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "neo_tracker.ui.project_open_worker",
            "--isolated-load",
            str(path),
            str(stage_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = monotonic() + _PROJECT_OPEN_LOAD_DEADLINE_S
    try:
        if progress is not None:
            progress("validating")
        while process.poll() is None:
            if cancel_requested.is_set():
                raise InterruptedError("project open canceled")
            if monotonic() >= deadline:
                raise TimeoutError(
                    f"isolated project loader exceeded {_PROJECT_OPEN_LOAD_DEADLINE_S:g} seconds"
                )
            cancel_requested.wait(0.02)
        if process.stdout is None:
            raise RuntimeError("isolated project loader has no result stream")
        reply_bytes = process.stdout.read(_PROJECT_OPEN_REPLY_MAX_BYTES + 1)
        if len(reply_bytes) > _PROJECT_OPEN_REPLY_MAX_BYTES:
            raise ValueError("isolated project-open reply exceeds its safety limit")
        if process.returncode != 0:
            raise RuntimeError(
                f"isolated project loader exited unexpectedly with code {process.returncode}"
            )
        reply = _safe_json_loads(reply_bytes, label="isolated project-open reply")
        if not isinstance(reply, dict):
            raise ValueError("isolated project-open reply must be a dictionary")
        if reply.get("status") != "ok":
            raise ValueError(str(reply.get("message") or "isolated project loading failed"))
        expected_bytes = reply.get("bytes")
        expected_digest = reply.get("sha256")
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
            or expected_bytes > _PROJECT_OPEN_STAGE_MAX_BYTES
            or not isinstance(expected_digest, str)
            or len(expected_digest) != 64
        ):
            raise ValueError("isolated project-open reply contains an invalid stage manifest")
        return _read_project_open_stage(
            stage_path,
            expected_bytes=expected_bytes,
            expected_digest=expected_digest,
            cancel_requested=cancel_requested,
            progress=progress,
        )
    finally:
        if process.pid is not None:
            _stop_load_process(process)
        if process.stdout is not None:
            process.stdout.close()
        stage_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class PreparedProjectOpen:
    path: Path
    project: NeoTrackerProject
    tasks: tuple[DesktopTask, ...]
    fingerprint: str
    media_count: int
    review_diagnostics: PreparedReviewDiagnostics | None = None
    analysis_sources: tuple[AnalysisSource, ...] = ()


class ProjectOpenWorker(QObject):
    """Load, inspect, and materialize a project outside the Qt event loop."""

    progressed = Signal(str, int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        path: str | Path,
        controller: ProjectTaskController,
        *,
        project_loader: ProjectLoader = NeoTrackerProject.load,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.controller = controller
        self.project_loader = project_loader
        self._use_isolated_loader = project_loader == NeoTrackerProject.load
        self.media_probe = controller.media_probe
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def _cancel_if_requested(self) -> bool:
        if not self.cancellation_requested:
            return False
        self.canceled.emit()
        return True

    def _cooperate_or_cancel(self) -> None:
        """Briefly release the GIL while keeping long index scans cancellable."""

        if self._cancel_requested.wait(0.0001):
            raise InterruptedError("project open canceled")

    @Slot()
    def run(self) -> None:
        _acquire_project_open_gc_guard()
        try:
            if self._cancel_if_requested():
                return
            self.progressed.emit("loading", 0, 0, self.path.name)
            project = (
                load_project_isolated(
                    self.path,
                    self._cancel_requested,
                    progress=lambda phase: self.progressed.emit(phase, 0, 0, self.path.name),
                )
                if self._use_isolated_loader
                else self.project_loader(self.path)
            )
            if self._cancel_if_requested():
                return
            # Split the isolated decode handoff from task reconstruction so Qt
            # can deliver the phase/progress events before the next CPU stage.
            if self._cancel_requested.wait(0.003):
                self.canceled.emit()
                return

            snapshots = project.tasks or [
                ProjectTaskSnapshot(
                    media_path=media_path,
                    pipeline_key=self.controller.default_pipeline_key,
                )
                for media_path in project.media_paths
            ]
            media_paths = tuple(
                dict.fromkeys(
                    str(snapshot.media_path)
                    for snapshot in snapshots
                    if snapshot.media_path
                )
            )
            media_info_by_path: dict[str, MediaInfo] = {}
            total_media = len(media_paths)
            if total_media:
                self.progressed.emit("inspecting", 0, total_media, "")
            for index, media_path in enumerate(media_paths, start=1):
                if self._cancel_if_requested():
                    return
                media_info_by_path[media_path] = probe_media_safely(
                    media_path,
                    self.media_probe,
                    cancellation_requested=self._cancel_requested,
                )
                if self._cancel_if_requested():
                    return
                self.progressed.emit(
                    "inspecting",
                    index,
                    total_media,
                    Path(media_path).name,
                )

            tasks: list[DesktopTask] = []
            total_tasks = len(snapshots)
            task_progress_stride = max(1, total_tasks // 100)
            self.progressed.emit("preparing", 0, total_tasks, "")
            for index, snapshot in enumerate(snapshots, start=1):
                if self._cancel_if_requested():
                    return
                live_media_info = (
                    media_info_by_path[str(snapshot.media_path)]
                    if snapshot.media_path
                    else None
                )
                tasks.append(
                    self.controller.task_from_snapshot(
                        snapshot,
                        live_media_info=live_media_info,
                        persisted_debug_is_compact=True,
                    )
                )
                if index == total_tasks or index % task_progress_stride == 0:
                    self.progressed.emit("preparing", index, total_tasks, "")

            if self._cancel_requested.wait(0.003):
                self.canceled.emit()
                return

            canonical_project = NeoTrackerProject(
                name=self.path.stem,
                pipelines=list(project.pipelines),
                tasks=[self.controller.snapshot_from_task(task) for task in tasks],
                notes=project.notes,
            )
            review_diagnostics: PreparedReviewDiagnostics | None = None
            analysis_sources: tuple[AnalysisSource, ...] = ()
            if tasks:
                first_task = tasks[0]
                self.progressed.emit("indexing-review", 0, 1, "")
                review_diagnostics = prepare_review_diagnostics(
                    first_task.pipeline.results,
                    cooperate=self._cooperate_or_cancel,
                )
                self.progressed.emit("indexing-review", 1, 1, "")
                self.progressed.emit("indexing-analysis", 0, 1, "")
                analysis_sources = tuple(
                    AnalysisController.available_sources(
                        first_task.pipeline.results,
                        first_task.pipeline.state_model.units(),
                        first_task.media_path,
                        first_task.media_info,
                        cooperate=self._cooperate_or_cancel,
                    )
                )
                self.progressed.emit("indexing-analysis", 1, 1, "")
            self.progressed.emit("fingerprinting", 0, 1, "")
            fingerprint = project_content_fingerprint(
                canonical_project,
                cooperate=self._cooperate_or_cancel,
            )
            self.progressed.emit("fingerprinting", 1, 1, "")
            if self._cancel_if_requested():
                return
            self.completed.emit(
                PreparedProjectOpen(
                    path=self.path,
                    project=project,
                    tasks=tuple(tasks),
                    fingerprint=fingerprint,
                    media_count=total_media,
                    review_diagnostics=review_diagnostics,
                    analysis_sources=analysis_sources,
                )
            )
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
        finally:
            _release_project_open_gc_guard()


def _run_isolated_load_cli(arguments: list[str]) -> int:
    if len(arguments) != 3 or arguments[0] != "--isolated-load":
        return 2
    try:
        os.nice(10)
    except OSError:
        pass
    reply = _isolated_project_load_entry(arguments[1], arguments[2])
    encoded = json.dumps(reply, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > _PROJECT_OPEN_REPLY_MAX_BYTES:
        encoded = json.dumps(
            {"status": "error", "message": "isolated project-open reply exceeded its safety limit"}
        ).encode("utf-8")
    os.write(1, encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_isolated_load_cli(sys.argv[1:]))
