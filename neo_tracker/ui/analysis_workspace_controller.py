from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from PySide6.QtCore import QObject, Signal

from neo_tracker.application.kinematics_coordinator import (
    KinematicsFitCoordinator,
    KinematicsFitJob,
    KinematicsFitTask,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.kinematics import (
    DerivativeConfig,
    FitOperator,
    FitRequest,
    FitResult,
    FitStatus,
    SampleSeries,
)


@dataclass(frozen=True)
class FitDraft:
    series_id: str
    model: str
    range_start_s: float
    range_end_s: float
    initial_parameters: Mapping[str, float] = field(default_factory=dict)
    bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    use_valid_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", str(self.series_id).strip())
        object.__setattr__(self, "model", str(self.model).strip().lower())
        object.__setattr__(self, "initial_parameters", MappingProxyType(dict(self.initial_parameters)))
        object.__setattr__(
            self,
            "bounds",
            MappingProxyType({key: tuple(value) for key, value in self.bounds.items()}),
        )

    def to_request(self, series: SampleSeries) -> FitRequest:
        if series.series_id != self.series_id:
            raise ValueError("fit draft series does not match the selected series")
        return FitRequest(
            series_id=self.series_id,
            model=self.model,
            range_start_s=self.range_start_s,
            range_end_s=self.range_end_s,
            source_revision=series.source_revision,
            initial_parameters=self.initial_parameters,
            bounds=self.bounds,
            use_valid_only=self.use_valid_only,
        )


@dataclass(frozen=True)
class AnalysisWorkspaceState:
    series_ids: tuple[str, ...] = ()
    selected_series_id: str | None = None
    source_revision: str | None = None
    engine_available: bool = False
    status: str = "empty"
    message: str = "No physical series is available."
    active_request: FitRequest | None = None
    fit_result: FitResult | None = None
    residual_visible: bool = False


@dataclass(frozen=True)
class KinematicsOperationRequest:
    operation: str
    series_id: str
    source_revision: str
    configuration: object

    def __post_init__(self) -> None:
        operation = str(self.operation).strip().lower()
        series_id = str(self.series_id).strip()
        source_revision = str(self.source_revision).strip()
        if operation not in {"derivative", "smooth", "export"}:
            raise ValueError(f"unknown kinematics operation: {operation}")
        if not series_id or not source_revision:
            raise ValueError("kinematics operation requires series and source revision")
        configuration = self.configuration
        if operation == "derivative":
            if not isinstance(configuration, DerivativeConfig):
                raise TypeError("derivative operation requires DerivativeConfig")
        elif not isinstance(configuration, Mapping):
            raise TypeError(f"{operation} operation requires a configuration mapping")
        else:
            configuration = MappingProxyType(dict(configuration))
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "series_id", series_id)
        object.__setattr__(self, "source_revision", source_revision)
        object.__setattr__(self, "configuration", configuration)


class AnalysisWorkspaceController(QObject):
    """Application boundary for immutable fit requests and background lifecycle."""

    stateChanged = Signal(object)
    fitResultReady = Signal(object)
    operationRequested = Signal(object)
    idleReached = Signal()

    def __init__(
        self,
        supervisor: TaskSupervisor,
        *,
        fit_operator: FitOperator | None = None,
        coordinator: KinematicsFitCoordinator | None = None,
    ) -> None:
        super().__init__()
        self._fit_operator = fit_operator
        self._coordinator = coordinator or KinematicsFitCoordinator(supervisor)
        self._coordinator.result_ready.connect(self._fit_ready)
        self._coordinator.failed.connect(self._fit_failed)
        self._coordinator.canceled.connect(self._fit_canceled)
        self._coordinator.idle_reached.connect(self.idleReached)
        self._owner: object | None = None
        self._series: dict[str, SampleSeries] = {}
        self._state = AnalysisWorkspaceState(engine_available=fit_operator is not None)

    @property
    def state(self) -> AnalysisWorkspaceState:
        return self._state

    @property
    def busy(self) -> bool:
        return self._coordinator.busy

    @property
    def coordinator(self) -> KinematicsFitCoordinator:
        return self._coordinator

    def set_fit_operator(self, operator: FitOperator | None) -> None:
        if operator is not None and not callable(getattr(operator, "fit", None)):
            raise TypeError("fit operator must implement FitOperator.fit")
        if self.busy:
            self.invalidate("Fit engine changed. Run the fit again.")
        self._fit_operator = operator
        if self._series and operator is not None and self._state.status in {"empty", "unavailable"}:
            self._replace_state(
                engine_available=True,
                status="ready",
                message="Choose a model and true-time range.",
            )
        elif self._series and operator is None:
            self._replace_state(
                engine_available=False,
                status="unavailable",
                message="The kinematics fit engine is unavailable.",
            )
        else:
            self._replace_state(engine_available=operator is not None)

    def set_series(self, owner: object, series: tuple[SampleSeries, ...]) -> None:
        items = tuple(series)
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("analysis workspace series_id values must be unique")
        revisions = {item.source_revision for item in items}
        if len(revisions) > 1:
            raise ValueError("analysis workspace series must share one source revision")
        selected = self._state.selected_series_id
        same_context = bool(
            owner is self._owner
            and revisions
            and self._state.source_revision == next(iter(revisions))
        )
        if self.busy and not same_context:
            self._coordinator.cancel("Physical series changed. Run the fit again.", state="stale")
        self._owner = owner
        self._series = {item.series_id: item for item in items}
        selected_id = selected if selected in self._series else next(iter(self._series), None)
        revision = next(iter(revisions), None)
        if not items:
            status, message = "empty", "No physical series is available."
        elif self._fit_operator is None:
            status, message = "unavailable", "The kinematics fit engine is unavailable."
        else:
            status, message = "ready", "Choose a model and true-time range."
        self._state = AnalysisWorkspaceState(
            series_ids=tuple(self._series),
            selected_series_id=selected_id,
            source_revision=revision,
            engine_available=self._fit_operator is not None,
            status=status,
            message=message,
        )
        self.stateChanged.emit(self._state)

    def select_series(self, series_id: str) -> bool:
        normalized = str(series_id)
        if normalized not in self._series:
            return False
        if normalized == self._state.selected_series_id:
            return True
        self.invalidate("Physical series changed. Run the fit again.")
        self._replace_state(selected_series_id=normalized)
        return True

    def run_fit(self, owner: object, draft: FitDraft) -> bool:
        if owner is not self._owner or self.busy:
            return False
        source = self._series.get(draft.series_id)
        if source is None:
            self._replace_state(status="failed", message="The selected physical series is unavailable.")
            return False
        operator = self._fit_operator
        if operator is None:
            self._replace_state(status="unavailable", message="The kinematics fit engine is unavailable.")
            return False
        try:
            request = draft.to_request(source)
            task = KinematicsFitTask(owner, source, request, operator)
        except Exception as exc:
            self._replace_state(status="failed", message=str(exc), active_request=None, fit_result=None)
            return False
        if not self._coordinator.start(task):
            return False
        self._replace_state(
            selected_series_id=source.series_id,
            status="running",
            message=f"Running {request.model.value} fit in the background…",
            active_request=request,
            fit_result=None,
            residual_visible=False,
        )
        return True

    def cancel(self, message: str = "Fit canceled.") -> bool:
        return self._coordinator.cancel(message, state="canceled")

    def invalidate(self, message: str) -> bool:
        changed = bool(self.busy or self._state.fit_result is not None or self._state.status != "dirty")
        if self.busy:
            self._coordinator.cancel(message, state="dirty")
        self._replace_state(
            status="dirty" if self._series else "empty",
            message=str(message),
            active_request=None,
            fit_result=None,
            residual_visible=False,
        )
        return changed

    def set_residual_visible(self, visible: bool) -> None:
        available = bool(
            self._state.fit_result is not None
            and self._state.fit_result.status is FitStatus.OK
        )
        self._replace_state(residual_visible=bool(visible) and available)

    def request_derivative(self, order: int) -> bool:
        source = self._selected_series()
        if source is None:
            return False
        config = DerivativeConfig(
            method="nonuniform_finite_difference",
            order=int(order),
            edge_policy="invalid",
            gap_policy="split",
        )
        self.operationRequested.emit(
            KinematicsOperationRequest(
                "derivative",
                source.series_id,
                source.source_revision,
                config,
            )
        )
        return True

    def request_smoothing(
        self,
        *,
        window_length: int = 11,
        polyorder: int = 3,
        uniformity_tolerance: float = 1e-3,
    ) -> bool:
        source = self._selected_series()
        if source is None:
            return False
        window = int(window_length)
        degree = int(polyorder)
        tolerance = float(uniformity_tolerance)
        if window < 3 or window % 2 == 0:
            raise ValueError("smoothing window_length must be an odd integer of at least 3")
        if degree < 0 or degree >= window:
            raise ValueError("smoothing polyorder must be non-negative and smaller than window_length")
        if not math.isfinite(tolerance) or not 0.0 <= tolerance < 1.0:
            raise ValueError("uniformity_tolerance must be finite and in [0, 1)")
        config = MappingProxyType(
            {
                "method": "savgol_uniform",
                "window_length": window,
                "polyorder": degree,
                "uniformity_tolerance": tolerance,
                "gap_policy": "split",
                "resample": False,
            }
        )
        self.operationRequested.emit(
            KinematicsOperationRequest(
                "smooth",
                source.series_id,
                source.source_revision,
                config,
            )
        )
        return True

    def request_export(self) -> bool:
        source = self._selected_series()
        result = self._state.fit_result
        if source is None or (result is not None and result.status is not FitStatus.OK):
            return False
        config = MappingProxyType(
            {
                "formats": ("csv", "npz", "markdown"),
                "fit_result": result,
            }
        )
        self.operationRequested.emit(
            KinematicsOperationRequest(
                "export",
                source.series_id,
                source.source_revision,
                config,
            )
        )
        return True

    def close(self) -> None:
        self._coordinator.close()

    def _fit_ready(self, job: KinematicsFitJob, result: FitResult) -> None:
        if not self._job_is_current(job):
            return
        successful = result.status is FitStatus.OK
        status = "complete" if successful else result.status.value
        message = result.message or (
            f"{result.model.value.title()} fit complete · {result.sample_count:,} samples."
            if successful
            else f"Fit ended with status: {result.status.value}."
        )
        self._replace_state(
            status=status,
            message=message,
            active_request=job.task.request,
            fit_result=result if successful else None,
            residual_visible=False,
        )
        if successful:
            self.fitResultReady.emit(result)

    def _fit_failed(self, job: KinematicsFitJob, message: str) -> None:
        if self._job_is_current(job):
            self._replace_state(
                status="failed",
                message=str(message),
                fit_result=None,
                residual_visible=False,
            )

    def _fit_canceled(self, job: KinematicsFitJob) -> None:
        if not self._job_is_current(job):
            return
        self._replace_state(
            status=job.cancel_state,
            message=job.cancel_message,
            active_request=None,
            fit_result=None,
            residual_visible=False,
        )

    def _job_is_current(self, job: KinematicsFitJob) -> bool:
        request = job.task.request
        return bool(
            job.task.owner is self._owner
            and self._state.source_revision == request.source_revision
            and request.series_id in self._series
            and self._series[request.series_id].source_revision == request.source_revision
        )

    def _selected_series(self) -> SampleSeries | None:
        return self._series.get(self._state.selected_series_id or "")

    def _replace_state(self, **changes: object) -> None:
        values = {
            "series_ids": self._state.series_ids,
            "selected_series_id": self._state.selected_series_id,
            "source_revision": self._state.source_revision,
            "engine_available": self._state.engine_available,
            "status": self._state.status,
            "message": self._state.message,
            "active_request": self._state.active_request,
            "fit_result": self._state.fit_result,
            "residual_visible": self._state.residual_visible,
        }
        values.update(changes)
        updated = AnalysisWorkspaceState(**values)  # type: ignore[arg-type]
        if updated == self._state:
            return
        self._state = updated
        self.stateChanged.emit(updated)
