from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
from stat import S_ISREG
from typing import Any, Callable

import numpy as np

from neo_tracker.atomic_io import fsync_parent_directory
from neo_tracker.core import ObservationCandidate, TrackerResult, TrackingPipeline
from neo_tracker.media import MediaIdentity


_INLINE_THETA_SIGNAL_LIMIT = 4096
_PROJECT_FINGERPRINT_COOPERATE_STRIDE = 32
MAX_PROJECT_FILE_BYTES = 64 * 1024 * 1024
MAX_PROJECT_TASKS = 256
MAX_PROJECT_MEDIA_PATHS = 256
MAX_PROJECT_PIPELINES = 64
MAX_PROJECT_RESULTS = 250_000
MAX_TASK_RESULTS = 100_000
MAX_TASK_EDIT_HISTORY = 10_000
MAX_STATE_FIELDS = 128
MAX_PROJECT_NESTING_DEPTH = 64
MAX_PROJECT_CONTAINER_ITEMS = 5_000_000
PROJECT_NAME_LIMIT = 256
PROJECT_NOTES_LIMIT = 64 * 1024
PROJECT_MEDIA_PATH_LIMIT = 4096
PIPELINE_KEY_LIMIT = 128
RESULT_STATUS_LIMIT = 256
OBSERVATION_LABEL_LIMIT = 256
STATE_KEY_LIMIT = 128
PROJECT_FORMAT_VERSION = 2
TRACKING_OUTCOMES = frozenset({"", "complete", "partial", "canceled", "failed"})
TRACKING_RUN_MODES = frozenset({"full", "rerun"})
TRACKING_RUN_HISTORY_LIMIT = 20
TRACKING_RUN_COMPUTE_BACKEND_LIMIT = 128
TRACKING_RUN_SOURCE_PATH_LIMIT = 4096
TRACKING_NOTE_LIMIT = 4096


def _bounded_string(value: object, label: str, limit: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    if len(value) > limit:
        raise ValueError(f"{label} must not exceed {limit} characters")
    return value


def _validate_project_structure(value: object) -> None:
    """Bound recursive project containers before model materialization."""

    pending: list[tuple[object, int]] = [(value, 0)]
    container_items = 0
    while pending:
        current, depth = pending.pop()
        if isinstance(current, dict):
            if depth > MAX_PROJECT_NESTING_DEPTH:
                raise ValueError(
                    f"project data nesting must not exceed {MAX_PROJECT_NESTING_DEPTH} levels"
                )
            container_items += len(current)
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple)):
            if depth > MAX_PROJECT_NESTING_DEPTH:
                raise ValueError(
                    f"project data nesting must not exceed {MAX_PROJECT_NESTING_DEPTH} levels"
                )
            container_items += len(current)
            pending.extend((item, depth + 1) for item in current)
        if container_items > MAX_PROJECT_CONTAINER_ITEMS:
            raise ValueError(
                "project data contains more than "
                f"{MAX_PROJECT_CONTAINER_ITEMS:,} container items"
            )


def _bounded_list(value: object, label: str, limit: int) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    if len(value) > limit:
        raise ValueError(f"{label} must not exceed {limit:,} entries")
    return value


def _finite_float(value: object, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{label} must not exceed {maximum:g}")
    return result


def _frame_index(value: object, label: str = "tracker result frame_index") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return int(value)


def _tracking_outcome_from_data(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("project task tracking_outcome must be a string")
    outcome = value.strip().lower()
    if outcome not in TRACKING_OUTCOMES:
        allowed = ", ".join(sorted(item for item in TRACKING_OUTCOMES if item))
        raise ValueError(f"unsupported project task tracking_outcome: {outcome!r}; expected {allowed} or empty")
    return outcome


def _tracking_note_from_data(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("project task tracking_note must be a string")
    if len(value) > TRACKING_NOTE_LIMIT:
        raise ValueError(
            f"project task tracking_note must not exceed {TRACKING_NOTE_LIMIT} characters"
        )
    return value


def _tracking_compute_backend_from_data(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("tracking run compute_backend must be a string")
    backend = value.strip()
    if len(backend) > TRACKING_RUN_COMPUTE_BACKEND_LIMIT:
        raise ValueError(
            f"tracking run compute_backend must not exceed {TRACKING_RUN_COMPUTE_BACKEND_LIMIT} characters"
        )
    return backend


def _tracking_source_path_from_data(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("tracking run source_path must be a string")
    if len(value) > TRACKING_RUN_SOURCE_PATH_LIMIT:
        raise ValueError(
            f"tracking run source_path must not exceed {TRACKING_RUN_SOURCE_PATH_LIMIT} characters"
        )
    return value


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def pipeline_config_digest(config: dict[str, Any]) -> str:
    canonical = json.dumps(_json_safe(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _tracking_run_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("tracking run started_at must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("tracking run started_at must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("tracking run started_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _tracking_run_mode(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("tracking run mode must be a string")
    mode = value.strip().lower()
    if mode not in TRACKING_RUN_MODES:
        raise ValueError(f"unsupported tracking run mode: {mode!r}; expected full or rerun")
    return mode


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"tracking run {label} must be a non-negative integer")
    return int(value)


def _nonnegative_duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("tracking run duration_s must be a finite non-negative number")
    duration = float(value)
    if not isfinite(duration) or duration < 0.0:
        raise ValueError("tracking run duration_s must be a finite non-negative number")
    return duration


def _nonnegative_run_seconds(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"tracking run {label} must be a finite non-negative number")
    seconds = float(value)
    if not isfinite(seconds) or seconds < 0.0:
        raise ValueError(f"tracking run {label} must be a finite non-negative number")
    return seconds


def _state_to_dict(state: dict[str, float] | None) -> dict[str, float] | None:
    if state is None:
        return None
    if len(state) > MAX_STATE_FIELDS:
        raise ValueError(f"tracker state must not exceed {MAX_STATE_FIELDS} fields")
    result: dict[str, float] = {}
    for key, value in sorted(state.items(), key=lambda item: str(item[0])):
        safe_key = _bounded_string(str(key), "tracker state key", STATE_KEY_LIMIT, allow_empty=False)
        result[safe_key] = _finite_float(value, f"tracker state value for {safe_key!r}")
    return result


def _point_to_list(point: tuple[float, float] | None) -> list[float] | None:
    if point is None:
        return None
    return [float(point[0]), float(point[1])]


def _point_from_data(data: object) -> tuple[float, float] | None:
    if data is None:
        return None
    if not isinstance(data, (list, tuple)) or len(data) != 2:
        raise ValueError("observation image_point must contain two finite numbers")
    return (
        _finite_float(data[0], "observation image_point x"),
        _finite_float(data[1], "observation image_point y"),
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return {
            "type": "ndarray",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "omitted": True,
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class TrackingRunRecord:
    started_at: str
    duration_s: float
    mode: str
    outcome: str
    start_frame: int
    end_frame: int | None
    processed_frames: int
    result_count: int
    note: str = ""
    pipeline_config: dict[str, Any] = field(default_factory=dict)
    tracking_elapsed_s: float = 0.0
    input_s: float = 0.0
    processing_s: float = 0.0
    peak_debug_bytes: int = 0
    prefetch_frames: int = 0
    compute_backend: str = ""
    source_path: str = ""
    source_identity: MediaIdentity | None = None

    def __post_init__(self) -> None:
        started_at = _tracking_run_timestamp(self.started_at)
        duration_s = _nonnegative_duration(self.duration_s)
        mode = _tracking_run_mode(self.mode)
        outcome = _tracking_outcome_from_data(self.outcome)
        if not outcome:
            raise ValueError("tracking run outcome must be a stable non-empty outcome")
        start_frame = _nonnegative_int(self.start_frame, "start_frame")
        processed_frames = _nonnegative_int(self.processed_frames, "processed_frames")
        result_count = _nonnegative_int(self.result_count, "result_count")
        note = _tracking_note_from_data(self.note)
        tracking_elapsed_s = _nonnegative_run_seconds(self.tracking_elapsed_s, "tracking_elapsed_s")
        input_s = _nonnegative_run_seconds(self.input_s, "input_s")
        processing_s = _nonnegative_run_seconds(self.processing_s, "processing_s")
        peak_debug_bytes = _nonnegative_int(self.peak_debug_bytes, "peak_debug_bytes")
        prefetch_frames = _nonnegative_int(self.prefetch_frames, "prefetch_frames")
        compute_backend = _tracking_compute_backend_from_data(self.compute_backend)
        source_path = _tracking_source_path_from_data(self.source_path)
        if self.source_identity is not None and not isinstance(self.source_identity, MediaIdentity):
            raise ValueError("tracking run source_identity must be a MediaIdentity or None")
        if not isinstance(self.pipeline_config, dict) or not self.pipeline_config:
            raise ValueError("tracking run pipeline_config must be a non-empty dictionary")
        pipeline_config = _json_safe(self.pipeline_config)
        if not isinstance(pipeline_config, dict) or not pipeline_config:
            raise ValueError("tracking run pipeline_config must be a non-empty dictionary")
        if self.end_frame is None:
            if processed_frames != 0:
                raise ValueError("tracking run end_frame is required when frames were processed")
        else:
            end_frame = _nonnegative_int(self.end_frame, "end_frame")
            expected_end = start_frame + processed_frames - 1
            if processed_frames == 0 or end_frame != expected_end:
                raise ValueError("tracking run frame range must match processed_frames")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "duration_s", duration_s)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "start_frame", start_frame)
        object.__setattr__(self, "processed_frames", processed_frames)
        object.__setattr__(self, "result_count", result_count)
        object.__setattr__(self, "note", note)
        object.__setattr__(self, "pipeline_config", pipeline_config)
        object.__setattr__(self, "tracking_elapsed_s", tracking_elapsed_s)
        object.__setattr__(self, "input_s", input_s)
        object.__setattr__(self, "processing_s", processing_s)
        object.__setattr__(self, "peak_debug_bytes", peak_debug_bytes)
        object.__setattr__(self, "prefetch_frames", prefetch_frames)
        object.__setattr__(self, "compute_backend", compute_backend)
        object.__setattr__(self, "source_path", source_path)

    @property
    def pipeline_digest(self) -> str:
        return pipeline_config_digest(self.pipeline_config)

    @property
    def has_performance_metrics(self) -> bool:
        return self.processed_frames > 0 and self.tracking_elapsed_s > 0.0

    @property
    def throughput_fps(self) -> float:
        if not self.has_performance_metrics:
            return 0.0
        return self.processed_frames / self.tracking_elapsed_s

    @property
    def input_ms_per_frame(self) -> float:
        return 1000.0 * self.input_s / self.processed_frames if self.processed_frames else 0.0

    @property
    def processing_ms_per_frame(self) -> float:
        return 1000.0 * self.processing_s / self.processed_frames if self.processed_frames else 0.0

    @property
    def stage_overlap_s(self) -> float:
        return max(0.0, self.input_s + self.processing_s - self.tracking_elapsed_s)

    @property
    def stage_overlap_ms_per_frame(self) -> float:
        return 1000.0 * self.stage_overlap_s / self.processed_frames if self.processed_frames else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": _tracking_run_timestamp(self.started_at),
            "duration_s": _nonnegative_duration(self.duration_s),
            "mode": _tracking_run_mode(self.mode),
            "outcome": _tracking_outcome_from_data(self.outcome),
            "start_frame": _nonnegative_int(self.start_frame, "start_frame"),
            "end_frame": self.end_frame,
            "processed_frames": _nonnegative_int(self.processed_frames, "processed_frames"),
            "result_count": _nonnegative_int(self.result_count, "result_count"),
            "note": _tracking_note_from_data(self.note),
            "pipeline_config": _json_safe(self.pipeline_config),
            "tracking_elapsed_s": _nonnegative_run_seconds(self.tracking_elapsed_s, "tracking_elapsed_s"),
            "input_s": _nonnegative_run_seconds(self.input_s, "input_s"),
            "processing_s": _nonnegative_run_seconds(self.processing_s, "processing_s"),
            "peak_debug_bytes": _nonnegative_int(self.peak_debug_bytes, "peak_debug_bytes"),
            "prefetch_frames": _nonnegative_int(self.prefetch_frames, "prefetch_frames"),
            "compute_backend": _tracking_compute_backend_from_data(self.compute_backend),
            "source_path": _tracking_source_path_from_data(self.source_path),
            "source_identity": self.source_identity.to_dict() if self.source_identity is not None else None,
        }

    @classmethod
    def from_dict(cls, data: object) -> "TrackingRunRecord":
        if not isinstance(data, dict):
            raise ValueError("tracking run record must be a dictionary")
        pipeline_config = data.get("pipeline_config")
        if not isinstance(pipeline_config, dict):
            raise ValueError("tracking run pipeline_config must be a non-empty dictionary")
        source_identity_data = data.get("source_identity")
        source_identity = MediaIdentity.from_dict(source_identity_data)
        if source_identity_data is not None and source_identity is None:
            raise ValueError("tracking run source_identity must be a valid media identity")
        return cls(
            started_at=data.get("started_at"),  # type: ignore[arg-type]
            duration_s=data.get("duration_s"),  # type: ignore[arg-type]
            mode=data.get("mode"),  # type: ignore[arg-type]
            outcome=data.get("outcome"),  # type: ignore[arg-type]
            start_frame=data.get("start_frame"),  # type: ignore[arg-type]
            end_frame=data.get("end_frame"),  # type: ignore[arg-type]
            processed_frames=data.get("processed_frames"),  # type: ignore[arg-type]
            result_count=data.get("result_count"),  # type: ignore[arg-type]
            note=data.get("note", ""),  # type: ignore[arg-type]
            pipeline_config=dict(pipeline_config),
            tracking_elapsed_s=data.get("tracking_elapsed_s", 0.0),  # type: ignore[arg-type]
            input_s=data.get("input_s", 0.0),  # type: ignore[arg-type]
            processing_s=data.get("processing_s", 0.0),  # type: ignore[arg-type]
            peak_debug_bytes=data.get("peak_debug_bytes", 0),  # type: ignore[arg-type]
            prefetch_frames=data.get("prefetch_frames", 0),  # type: ignore[arg-type]
            compute_backend=data.get("compute_backend", ""),  # type: ignore[arg-type]
            source_path=data.get("source_path", ""),  # type: ignore[arg-type]
            source_identity=source_identity,
        )


def append_tracking_run(history: list[TrackingRunRecord], record: TrackingRunRecord) -> None:
    if not isinstance(record, TrackingRunRecord):
        raise TypeError("tracking history entries must be TrackingRunRecord objects")
    history.append(record)
    overflow = len(history) - TRACKING_RUN_HISTORY_LIMIT
    if overflow > 0:
        del history[:overflow]


def observation_to_dict(observation: ObservationCandidate | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    return {
        "state": _state_to_dict(observation.state) or {},
        "score": float(observation.score),
        "image_point": _point_to_list(observation.image_point),
        "label": observation.label,
        "raw": _json_safe(observation.raw),
    }


def observation_from_dict(data: object) -> ObservationCandidate | None:
    if not isinstance(data, dict):
        return None
    return ObservationCandidate(
        state=_state_to_dict(data.get("state") if isinstance(data.get("state"), dict) else {}) or {},
        score=_finite_float(data.get("score", 0.0), "observation score", minimum=0.0, maximum=1.0),
        image_point=_point_from_data(data.get("image_point")),
        label=_bounded_string(data.get("label", "candidate"), "observation label", OBSERVATION_LABEL_LIMIT),
        raw=data.get("raw") if isinstance(data.get("raw"), dict) else {},
    )


def tracker_result_to_dict(result: TrackerResult) -> dict[str, Any]:
    debug = _json_safe(result.debug)
    debug_layers = result.debug.get("debug_layers")
    theta_signal = debug_layers.get("theta_signal") if isinstance(debug_layers, dict) else None
    safe_debug_layers = debug.get("debug_layers") if isinstance(debug, dict) else None
    if (
        isinstance(theta_signal, np.ndarray)
        and theta_signal.ndim == 1
        and theta_signal.size <= _INLINE_THETA_SIGNAL_LIMIT
        and isinstance(safe_debug_layers, dict)
    ):
        safe_debug_layers["theta_signal"] = theta_signal.tolist()
    return {
        "frame_index": int(result.frame_index),
        "time_s": float(result.time_s),
        "state": _state_to_dict(result.state) or {},
        "filtered_state": _state_to_dict(result.filtered_state) or {},
        "confidence": float(result.confidence),
        "status": result.status,
        "observation": observation_to_dict(result.observation),
        "prediction": _state_to_dict(result.prediction),
        "debug": debug,
    }


def tracker_result_from_dict(data: object) -> TrackerResult:
    if not isinstance(data, dict):
        raise ValueError("tracker result must be a dictionary")
    return TrackerResult(
        frame_index=_frame_index(data.get("frame_index", 0)),
        time_s=_finite_float(data.get("time_s", 0.0), "tracker result time_s", minimum=0.0),
        state=_state_to_dict(data.get("state") if isinstance(data.get("state"), dict) else {}) or {},
        filtered_state=_state_to_dict(
            data.get("filtered_state") if isinstance(data.get("filtered_state"), dict) else {}
        )
        or {},
        confidence=_finite_float(
            data.get("confidence", 0.0),
            "tracker result confidence",
            minimum=0.0,
            maximum=1.0,
        ),
        status=_bounded_string(data.get("status", "unknown"), "tracker result status", RESULT_STATUS_LIMIT),
        observation=observation_from_dict(data.get("observation")),
        prediction=_state_to_dict(data.get("prediction") if isinstance(data.get("prediction"), dict) else None),
        debug=data.get("debug") if isinstance(data.get("debug"), dict) else {},
    )


@dataclass
class ProjectTaskSnapshot:
    media_path: str | None
    pipeline_key: str
    preview_frame_index: int = 0
    media_info: dict[str, Any] | None = None
    roi: dict[str, Any] | None = None
    calibration_rod: dict[str, Any] | None = None
    pipeline_config: dict[str, Any] | None = None
    results: list[TrackerResult] = field(default_factory=list)
    edit_history: list[dict[str, Any]] = field(default_factory=list)
    tracking_outcome: str = ""
    tracking_note: str = ""
    run_history: list[TrackingRunRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if len(self.results) > MAX_TASK_RESULTS:
            raise ValueError(f"project task results must not exceed {MAX_TASK_RESULTS:,} entries")
        if len(self.edit_history) > MAX_TASK_EDIT_HISTORY:
            raise ValueError(
                f"project task edit_history must not exceed {MAX_TASK_EDIT_HISTORY:,} entries"
            )
        if len(self.run_history) > TRACKING_RUN_HISTORY_LIMIT:
            raise ValueError(f"project task run_history must not exceed {TRACKING_RUN_HISTORY_LIMIT} entries")
        return {
            "media_path": self.media_path,
            "pipeline_key": self.pipeline_key,
            "preview_frame_index": int(self.preview_frame_index),
            "media_info": _json_safe(self.media_info),
            "roi": _json_safe(self.roi),
            "calibration_rod": _json_safe(self.calibration_rod),
            "pipeline_config": _json_safe(self.pipeline_config),
            "results": [tracker_result_to_dict(result) for result in self.results],
            "edit_history": _json_safe(self.edit_history),
            "tracking_outcome": _tracking_outcome_from_data(self.tracking_outcome),
            "tracking_note": _tracking_note_from_data(self.tracking_note),
            "run_history": [record.to_dict() for record in self.run_history],
        }

    @classmethod
    def from_dict(cls, data: object) -> "ProjectTaskSnapshot":
        if not isinstance(data, dict):
            raise ValueError("project task must be a dictionary")
        result_data = _bounded_list(data.get("results", []), "project task results", MAX_TASK_RESULTS)
        results = [tracker_result_from_dict(item) for item in result_data]
        for previous, current in zip(results, results[1:]):
            if current.frame_index <= previous.frame_index:
                raise ValueError("project task results must have unique increasing frame_index values")
            if current.time_s < previous.time_s:
                raise ValueError("project task results time_s values must be non-decreasing")
        history_data = _bounded_list(
            data.get("edit_history", []),
            "project task edit_history",
            MAX_TASK_EDIT_HISTORY,
        )
        if not all(isinstance(item, dict) for item in history_data):
            raise ValueError("project task edit_history entries must be dictionaries")
        edit_history = [dict(item) for item in history_data]
        run_history_data = data.get("run_history", [])
        if not isinstance(run_history_data, list):
            raise ValueError("project task run_history must be a list")
        if len(run_history_data) > TRACKING_RUN_HISTORY_LIMIT:
            raise ValueError(f"project task run_history must not exceed {TRACKING_RUN_HISTORY_LIMIT} entries")
        run_history = [TrackingRunRecord.from_dict(item) for item in run_history_data]
        return cls(
            media_path=(
                _bounded_string(data["media_path"], "project task media_path", PROJECT_MEDIA_PATH_LIMIT)
                if data.get("media_path") is not None
                else None
            ),
            pipeline_key=_bounded_string(
                data.get("pipeline_key", ""),
                "project task pipeline_key",
                PIPELINE_KEY_LIMIT,
            ),
            preview_frame_index=int(data.get("preview_frame_index", 0)),
            media_info=data.get("media_info") if isinstance(data.get("media_info"), dict) else None,
            roi=data.get("roi") if isinstance(data.get("roi"), dict) else None,
            calibration_rod=data.get("calibration_rod") if isinstance(data.get("calibration_rod"), dict) else None,
            pipeline_config=data.get("pipeline_config") if isinstance(data.get("pipeline_config"), dict) else None,
            results=results,
            edit_history=edit_history,
            tracking_outcome=_tracking_outcome_from_data(data.get("tracking_outcome")),
            tracking_note=_tracking_note_from_data(data.get("tracking_note")),
            run_history=run_history,
        )


@dataclass
class NeoTrackerProject:
    name: str
    media_paths: list[str] = field(default_factory=list)
    pipelines: list[TrackingPipeline | dict[str, Any]] = field(default_factory=list)
    tasks: list[ProjectTaskSnapshot] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        if len(self.tasks) > MAX_PROJECT_TASKS:
            raise ValueError(f"project tasks must not exceed {MAX_PROJECT_TASKS} entries")
        if len(self.media_paths) > MAX_PROJECT_MEDIA_PATHS:
            raise ValueError(f"project media_paths must not exceed {MAX_PROJECT_MEDIA_PATHS} entries")
        if len(self.pipelines) > MAX_PROJECT_PIPELINES:
            raise ValueError(f"project pipeline_library must not exceed {MAX_PROJECT_PIPELINES} entries")
        total_results = sum(len(task.results) for task in self.tasks)
        if total_results > MAX_PROJECT_RESULTS:
            raise ValueError(f"project results must not exceed {MAX_PROJECT_RESULTS:,} total entries")
        name = _bounded_string(self.name, "project name", PROJECT_NAME_LIMIT)
        notes = _bounded_string(self.notes, "project notes", PROJECT_NOTES_LIMIT)
        media_paths = [
            _bounded_string(path, "project media path", PROJECT_MEDIA_PATH_LIMIT)
            for path in self.media_paths
        ]
        task_media_paths = [task.media_path for task in self.tasks if task.media_path]
        return {
            "format": "neo-tracker-project",
            "version": PROJECT_FORMAT_VERSION,
            "name": name,
            "media_paths": media_paths or task_media_paths,
            "pipeline_library": [self._pipeline_config(pipeline) for pipeline in self.pipelines],
            "tasks": [task.to_dict() for task in self.tasks],
            "notes": notes,
        }

    @staticmethod
    def _pipeline_config(pipeline: TrackingPipeline | dict[str, Any]) -> dict[str, Any]:
        if isinstance(pipeline, TrackingPipeline):
            return _json_safe(pipeline.to_config())
        if isinstance(pipeline, dict):
            return _json_safe(pipeline)
        raise TypeError("project pipeline library entries must be pipeline objects or config dictionaries")

    def save(self, path: str | Path) -> None:
        target = Path(path)
        payload = self.to_dict()
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, indent=2, allow_nan=False)
                temporary.flush()
                payload_bytes = temporary_path.stat().st_size
                if payload_bytes > MAX_PROJECT_FILE_BYTES:
                    raise ValueError(
                        "project file would be "
                        f"{payload_bytes:,} bytes, above the {MAX_PROJECT_FILE_BYTES:,}-byte safety limit"
                    )
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
            fsync_parent_directory(target)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def from_dict(cls, data: object) -> "NeoTrackerProject":
        if not isinstance(data, dict):
            raise ValueError("project data must be a dictionary")
        _validate_project_structure(data)
        if data.get("format") != "neo-tracker-project":
            raise ValueError("not a Neo-Tracker project file")
        migrated = cls._migrate_to_current(data)
        tasks_data = _bounded_list(migrated.get("tasks", []), "project tasks", MAX_PROJECT_TASKS)
        tasks = [ProjectTaskSnapshot.from_dict(item) for item in tasks_data]
        total_results = sum(len(task.results) for task in tasks)
        if total_results > MAX_PROJECT_RESULTS:
            raise ValueError(f"project results must not exceed {MAX_PROJECT_RESULTS:,} total entries")
        media_paths_data = _bounded_list(
            migrated.get("media_paths", []),
            "project media_paths",
            MAX_PROJECT_MEDIA_PATHS,
        )
        media_paths = [
            _bounded_string(path, "project media path", PROJECT_MEDIA_PATH_LIMIT)
            for path in media_paths_data
        ]
        library_data = _bounded_list(
            migrated.get("pipeline_library", []),
            "project pipeline_library",
            MAX_PROJECT_PIPELINES,
        )
        if not all(isinstance(item, dict) for item in library_data):
            raise ValueError("project pipeline_library must be a list of configuration dictionaries")
        return cls(
            name=_bounded_string(
                migrated.get("name", "Untitled experiment"),
                "project name",
                PROJECT_NAME_LIMIT,
            ),
            media_paths=media_paths,
            pipelines=[dict(item) for item in library_data],
            tasks=tasks,
            notes=_bounded_string(migrated.get("notes", ""), "project notes", PROJECT_NOTES_LIMIT),
        )

    @staticmethod
    def _migrate_to_current(data: dict[str, Any]) -> dict[str, Any]:
        try:
            version = int(data.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("project version must be an integer") from exc
        if version < 1 or version > PROJECT_FORMAT_VERSION:
            raise ValueError(f"unsupported Neo-Tracker project version: {version}")
        migrated = dict(data)
        if version == 1:
            legacy_pipelines = migrated.pop("pipelines", [])
            if not isinstance(legacy_pipelines, list) or not all(isinstance(item, dict) for item in legacy_pipelines):
                raise ValueError("version 1 project pipelines must be a list of configuration dictionaries")
            migrated["pipeline_library"] = [dict(item) for item in legacy_pipelines]
            migrated["version"] = PROJECT_FORMAT_VERSION
        return migrated

    @classmethod
    def load(cls, path: str | Path) -> "NeoTrackerProject":
        project_path = Path(path)
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(project_path, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not S_ISREG(file_stat.st_mode):
                raise ValueError(f"project source is not a regular file: {project_path}")
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
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("project file must be valid UTF-8 JSON") from exc
        try:
            parsed = json.loads(
                text,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"project JSON contains non-finite constant {token}")
                ),
            )
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"project file must contain valid JSON: {exc}") from exc
        return cls.from_dict(parsed)


def project_content_fingerprint(
    project: NeoTrackerProject,
    *,
    cooperate: Callable[[], None] | None = None,
) -> str:
    """Return the exact persisted-content baseline used by the desktop UI.

    The persisted collections are canonicalized and hashed one record at a
    time.  This avoids retaining a second project-sized result dictionary graph
    solely for dirty-state comparison and lets background callers cooperate
    with the GUI event loop between bounded records.
    """

    if len(project.tasks) > MAX_PROJECT_TASKS:
        raise ValueError(f"project tasks must not exceed {MAX_PROJECT_TASKS} entries")
    if len(project.media_paths) > MAX_PROJECT_MEDIA_PATHS:
        raise ValueError(f"project media_paths must not exceed {MAX_PROJECT_MEDIA_PATHS} entries")
    if len(project.pipelines) > MAX_PROJECT_PIPELINES:
        raise ValueError(f"project pipeline_library must not exceed {MAX_PROJECT_PIPELINES} entries")
    total_results = sum(len(task.results) for task in project.tasks)
    if total_results > MAX_PROJECT_RESULTS:
        raise ValueError(f"project results must not exceed {MAX_PROJECT_RESULTS:,} total entries")
    name = _bounded_string(project.name, "project name", PROJECT_NAME_LIMIT)
    notes = _bounded_string(project.notes, "project notes", PROJECT_NOTES_LIMIT)
    media_paths = [
        _bounded_string(path, "project media path", PROJECT_MEDIA_PATH_LIMIT)
        for path in project.media_paths
    ]
    resolved_media_paths = media_paths or [
        task.media_path for task in project.tasks if task.media_path
    ]
    pipeline_configs = [project._pipeline_config(pipeline) for pipeline in project.pipelines]
    digest = sha256()

    def add_record(kind: str, value: object) -> None:
        encoded_kind = kind.encode("ascii")
        encoded_value = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(encoded_kind).to_bytes(4, "big"))
        digest.update(encoded_kind)
        digest.update(len(encoded_value).to_bytes(8, "big"))
        digest.update(encoded_value)

    add_record(
        "project",
        {
            "format": "neo-tracker-project",
            "version": PROJECT_FORMAT_VERSION,
            "name": name,
            "notes": notes,
            "media_path_count": len(resolved_media_paths),
            "pipeline_count": len(pipeline_configs),
            "task_count": len(project.tasks),
        },
    )
    record_index = 0

    def cooperate_after_record() -> None:
        nonlocal record_index
        record_index += 1
        if (
            cooperate is not None
            and record_index % _PROJECT_FINGERPRINT_COOPERATE_STRIDE == 0
        ):
            cooperate()

    for media_path in resolved_media_paths:
        add_record("media_path", media_path)
        cooperate_after_record()
    for pipeline in pipeline_configs:
        add_record("pipeline", pipeline)
        cooperate_after_record()
    for task in project.tasks:
        if len(task.results) > MAX_TASK_RESULTS:
            raise ValueError(f"project task results must not exceed {MAX_TASK_RESULTS:,} entries")
        if len(task.edit_history) > MAX_TASK_EDIT_HISTORY:
            raise ValueError(
                f"project task edit_history must not exceed {MAX_TASK_EDIT_HISTORY:,} entries"
            )
        if len(task.run_history) > TRACKING_RUN_HISTORY_LIMIT:
            raise ValueError(
                f"project task run_history must not exceed {TRACKING_RUN_HISTORY_LIMIT} entries"
            )
        task_record = {
            "media_path": task.media_path,
            "pipeline_key": task.pipeline_key,
            "preview_frame_index": 0,
            "media_info": _json_safe(task.media_info),
            "roi": _json_safe(task.roi),
            "calibration_rod": _json_safe(task.calibration_rod),
            "pipeline_config": _json_safe(task.pipeline_config),
            "tracking_outcome": _tracking_outcome_from_data(task.tracking_outcome),
            "tracking_note": _tracking_note_from_data(task.tracking_note),
            "result_count": len(task.results),
            "edit_history_count": len(task.edit_history),
            "run_history_count": len(task.run_history),
        }
        add_record("task", task_record)
        cooperate_after_record()
        for result in task.results:
            add_record("result", tracker_result_to_dict(result))
            cooperate_after_record()
        for edit in task.edit_history:
            add_record("edit_history", _json_safe(edit))
            cooperate_after_record()
        for run in task.run_history:
            add_record("run_history", run.to_dict())
            cooperate_after_record()
    if cooperate is not None and record_index % _PROJECT_FINGERPRINT_COOPERATE_STRIDE:
        cooperate()
    return digest.hexdigest()
