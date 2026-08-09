from __future__ import annotations

"""Application-owned background execution for the physics workspace."""

import hashlib
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Any
from uuid import UUID

from PySide6.QtCore import QObject, QThread, Signal, Slot

from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor
from neo_tracker.kinematics.derivatives import derive_series
from neo_tracker.kinematics.export import export_csv, export_markdown, export_npz
from neo_tracker.kinematics.series import (
    TrackingResultSnapshot,
    TrackingSeriesBuilder,
    snapshot_tracker_results,
)
from neo_tracker.kinematics.smoothing import smooth_series
from neo_tracker.kinematics.types import DerivativeConfig, FitResult, FitStatus, SampleSeries


_REVISION_DOMAIN = b"neo-tracker/kinematics-source-revision/v1\0"
_OPERATIONS = frozenset({"build", "derivative", "smooth", "export"})
_EXPORT_FORMATS = frozenset({"csv", "npz", "markdown"})


def _hash_text(digest: Any, value: str) -> None:
    encoded = value.encode("utf-8")
    digest.update(struct.pack(">Q", len(encoded)))
    digest.update(encoded)


def tracking_source_revision(
    snapshots: Sequence[TrackingResultSnapshot],
) -> str:
    """Hash exactly the detached fields consumed by ``TrackingSeriesBuilder``."""

    if not isinstance(snapshots, Sequence):
        raise TypeError("tracking snapshots must be a sequence")
    digest = hashlib.sha256(_REVISION_DOMAIN)
    digest.update(struct.pack(">Q", len(snapshots)))
    for item in snapshots:
        if not isinstance(item, TrackingResultSnapshot):
            raise TypeError("source revision requires detached TrackingResultSnapshot values")
        digest.update(struct.pack(">qd", int(item.frame_index), float(item.time_s)))
        _hash_text(digest, item.status.strip().lower())
        for label, values in (
            ("state", item.state),
            ("filtered_state", item.filtered_state),
            ("filter_velocity", item.filter_velocity),
        ):
            _hash_text(digest, label)
            ordered = tuple(sorted(values.items()))
            digest.update(struct.pack(">Q", len(ordered)))
            for key, value in ordered:
                _hash_text(digest, key)
                digest.update(struct.pack(">d", float(value)))
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class KinematicsWorkspaceTask:
    owner: object
    task_id: str
    results_generation: int
    operation: str
    results: Sequence[object] = ()
    units: Mapping[str, str] = field(default_factory=dict)
    source: SampleSeries | None = None
    configuration: object = None
    export_paths: Mapping[str, str | Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        operation = str(self.operation).strip().lower()
        generation = self.results_generation
        if not task_id:
            raise ValueError("kinematics task_id must not be empty")
        try:
            canonical_task_id = str(UUID(task_id))
        except ValueError as exc:
            raise ValueError("kinematics task_id must be a canonical UUID") from exc
        if canonical_task_id != task_id:
            raise ValueError("kinematics task_id must be a canonical UUID")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("kinematics results_generation must be a non-negative integer")
        if operation not in _OPERATIONS:
            raise ValueError(f"unsupported kinematics operation: {operation}")
        if not isinstance(self.units, Mapping):
            raise TypeError("kinematics units must be a mapping")
        if not isinstance(self.export_paths, Mapping):
            raise TypeError("kinematics export_paths must be a mapping")
        normalized_units: dict[str, str] = {}
        for key, value in self.units.items():
            if not isinstance(key, str) or not key:
                raise TypeError("kinematics unit keys must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError(f"kinematics unit for {key!r} must be a string")
            normalized_units[key] = value
        units = MappingProxyType(normalized_units)
        export_paths = MappingProxyType(
            {str(key).strip().lower(): Path(value) for key, value in self.export_paths.items()}
        )
        if operation == "build":
            if not isinstance(self.results, Sequence):
                raise TypeError("kinematics build results must be a sequence")
            if self.source is not None:
                raise ValueError("kinematics build must not carry a source series")
        else:
            if not isinstance(self.source, SampleSeries):
                raise TypeError(f"kinematics {operation} requires a SampleSeries")
        if operation == "derivative" and not isinstance(self.configuration, DerivativeConfig):
            raise TypeError("kinematics derivative requires DerivativeConfig")
        if operation == "smooth" and not isinstance(self.configuration, Mapping):
            raise TypeError("kinematics smoothing requires a configuration mapping")
        if operation == "export":
            if set(export_paths) != _EXPORT_FORMATS:
                raise ValueError("kinematics export requires csv, npz, and markdown paths")
            if len(set(export_paths.values())) != len(_EXPORT_FORMATS):
                raise ValueError("kinematics export paths must be distinct")
            fit = self.configuration.get("fit_result") if isinstance(self.configuration, Mapping) else None
            if not isinstance(fit, FitResult) or fit.status is not FitStatus.OK:
                raise ValueError("kinematics export requires one successful FitResult")
        elif export_paths:
            raise ValueError("only kinematics export may carry export paths")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "export_paths", export_paths)
        if isinstance(self.configuration, Mapping):
            object.__setattr__(self, "configuration", MappingProxyType(dict(self.configuration)))


@dataclass(frozen=True)
class KinematicsWorkspaceOutput:
    task_id: str
    results_generation: int
    operation: str
    source_revision: str
    series: tuple[SampleSeries, ...] = ()
    exported_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        task_id = str(self.task_id).strip()
        operation = str(self.operation).strip().lower()
        revision = str(self.source_revision).strip()
        if not task_id or not revision:
            raise ValueError("kinematics output requires task_id and source_revision")
        if (
            isinstance(self.results_generation, bool)
            or not isinstance(self.results_generation, int)
            or self.results_generation < 0
        ):
            raise ValueError("kinematics output results_generation must be non-negative")
        if operation not in _OPERATIONS:
            raise ValueError(f"unsupported kinematics output operation: {operation}")
        series = tuple(self.series)
        if any(not isinstance(item, SampleSeries) for item in series):
            raise TypeError("kinematics output series must contain SampleSeries values")
        if any(item.source_revision != revision for item in series):
            raise ValueError("kinematics output series revision must match output revision")
        paths = tuple(Path(path) for path in self.exported_paths)
        if operation in {"derivative", "smooth"} and len(series) != 1:
            raise ValueError(f"kinematics {operation} output requires one series")
        if operation == "export" and (
            series
            or len(paths) != len(_EXPORT_FORMATS)
            or len(set(paths)) != len(_EXPORT_FORMATS)
        ):
            raise ValueError("kinematics export output requires exactly three paths and no series")
        if operation != "export" and paths:
            raise ValueError("only kinematics export output may contain exported paths")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "source_revision", revision)
        object.__setattr__(self, "series", series)
        object.__setattr__(self, "exported_paths", paths)


@dataclass
class KinematicsWorkspaceJob:
    token: BackgroundTaskToken
    task: KinematicsWorkspaceTask
    cancelled: bool = False
    completed: bool = False
    failure_detail: str = ""


class _CancellationProbe:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class KinematicsWorkspaceWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self, task: KinematicsWorkspaceTask) -> None:
        super().__init__()
        self.task = task
        self._cancellation = _CancellationProbe()

    def request_cancel(self) -> None:
        self._cancellation.cancel()

    @Slot()
    def run(self) -> None:
        try:
            output = self._execute()
        except Exception as exc:
            if self._cancellation.is_cancelled():
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
            return
        if self._cancellation.is_cancelled():
            self.canceled.emit()
        else:
            self.completed.emit(output)

    def _execute(self) -> KinematicsWorkspaceOutput:
        task = self.task
        if task.operation == "build":
            snapshots = snapshot_tracker_results(
                task.results,
                cancellation=self._cancellation,
            )
            revision = tracking_source_revision(snapshots)
            series = TrackingSeriesBuilder(units=task.units).build_series(
                snapshots,
                source_revision=revision,
                cancellation=self._cancellation,
            )
            return KinematicsWorkspaceOutput(
                task.task_id,
                task.results_generation,
                task.operation,
                revision,
                series,
            )

        assert task.source is not None
        revision = task.source.source_revision
        if task.operation == "derivative":
            assert isinstance(task.configuration, DerivativeConfig)
            output = derive_series(
                task.source,
                task.configuration,
                cancellation=self._cancellation,
            )
            return KinematicsWorkspaceOutput(
                task.task_id,
                task.results_generation,
                task.operation,
                revision,
                (output,),
            )
        if task.operation == "smooth":
            assert isinstance(task.configuration, Mapping)
            output = smooth_series(
                task.source,
                window_length=int(task.configuration["window_length"]),
                polyorder=int(task.configuration["polyorder"]),
                uniformity_tolerance=float(task.configuration["uniformity_tolerance"]),
                cancellation=self._cancellation,
            )
            return KinematicsWorkspaceOutput(
                task.task_id,
                task.results_generation,
                task.operation,
                revision,
                (output,),
            )

        assert task.operation == "export"
        assert isinstance(task.configuration, Mapping)
        fit = task.configuration["fit_result"]
        assert isinstance(fit, FitResult)
        paths = task.export_paths
        exported = (
            export_csv(paths["csv"], task.source, fit, cancellation=self._cancellation),
            export_npz(paths["npz"], task.source, fit, cancellation=self._cancellation),
            export_markdown(
                paths["markdown"],
                task.source,
                fit,
                cancellation=self._cancellation,
            ),
        )
        return KinematicsWorkspaceOutput(
            task.task_id,
            task.results_generation,
            task.operation,
            revision,
            exported_paths=exported,
        )


class KinematicsWorkspaceCoordinator(QObject):
    """Own one cancellable non-fit kinematics operation at a time."""

    started = Signal(object)
    output_ready = Signal(object, object)
    failed = Signal(object, str)
    canceled = Signal(object)
    state_changed = Signal(str)
    finished = Signal(object)
    idle_reached = Signal()

    TASK_KIND = "kinematics-analysis"

    def __init__(self, supervisor: TaskSupervisor) -> None:
        super().__init__()
        self.supervisor = supervisor
        self._thread: QThread | None = None
        self._worker: KinematicsWorkspaceWorker | None = None
        self._job: KinematicsWorkspaceJob | None = None
        self._terminal_received = False
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._thread is not None

    @property
    def job(self) -> KinematicsWorkspaceJob | None:
        return self._job

    def start(self, task: KinematicsWorkspaceTask) -> bool:
        if self.busy or self._closed or self.supervisor.closing:
            return False
        token = self.supervisor.start(self.TASK_KIND)
        if token is None:
            return False
        job = KinematicsWorkspaceJob(token, task)
        try:
            thread = QThread(self)
            worker = KinematicsWorkspaceWorker(task)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.completed.connect(self._handle_completed)
            worker.failed.connect(self._handle_failed)
            worker.canceled.connect(self._handle_canceled)
            worker.completed.connect(thread.quit)
            worker.failed.connect(thread.quit)
            worker.canceled.connect(thread.quit)
            worker.completed.connect(worker.deleteLater)
            worker.failed.connect(worker.deleteLater)
            worker.canceled.connect(worker.deleteLater)
            thread.finished.connect(self._handle_thread_finished)
            thread.finished.connect(thread.deleteLater)
        except Exception:
            self.supervisor.finish(token)
            raise
        self._job = job
        self._thread = thread
        self._worker = worker
        self._terminal_received = False
        self.state_changed.emit("running")
        self.started.emit(job)
        thread.start()
        return True

    def cancel(self) -> bool:
        job, worker = self._job, self._worker
        if job is None or worker is None or job.cancelled or self._terminal_received:
            return False
        job.cancelled = True
        worker.request_cancel()
        self.state_changed.emit("canceling")
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancel()
        if not self.busy:
            self.state_changed.emit("closed")
            self.idle_reached.emit()

    def _handle_completed(self, output: object) -> None:
        job = self._job
        if not self._accepts(job) or job.cancelled:
            return
        self._terminal_received = True
        if not isinstance(output, KinematicsWorkspaceOutput) or not self._matches(job.task, output):
            job.cancelled = True
            self.canceled.emit(job)
        else:
            job.completed = True
            self.output_ready.emit(job, output)
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
                    job.failure_detail = "Kinematics worker ended without a terminal result."
                    self.failed.emit(job, job.failure_detail)
            self.supervisor.finish(job.token)
            self.finished.emit(job)
        self.state_changed.emit("closed" if self._closed else "idle")
        self.idle_reached.emit()

    def _accepts(self, job: KinematicsWorkspaceJob | None) -> bool:
        return bool(
            job is not None
            and not self._terminal_received
            and self.supervisor.is_current(job.token)
        )

    @staticmethod
    def _matches(
        task: KinematicsWorkspaceTask,
        output: KinematicsWorkspaceOutput,
    ) -> bool:
        return bool(
            output.task_id == task.task_id
            and output.results_generation == task.results_generation
            and output.operation == task.operation
            and all(
                item.source_revision == output.source_revision
                for item in output.series
            )
            and (
                task.operation == "build"
                or (
                    task.source is not None
                    and output.source_revision == task.source.source_revision
                )
            )
        )


__all__ = [
    "KinematicsWorkspaceCoordinator",
    "KinematicsWorkspaceJob",
    "KinematicsWorkspaceOutput",
    "KinematicsWorkspaceTask",
    "KinematicsWorkspaceWorker",
    "tracking_source_revision",
]
