from __future__ import annotations

"""Frame-aligned immutable series construction from TrackerResult snapshots."""

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from .protocols import CancellationProbe, ProgressReporter
from .runtime import check_cancelled
from .types import ProcessingStep, SampleSeries
from .units import state_unit, velocity_unit


_INVALID_STATUSES = frozenset({"lost", "failed", "invalid", "cancelled"})
_PROGRESS_STRIDE = 1_024
_EMPTY_NUMERIC_MAPPING: Mapping[str, float] = MappingProxyType({})


def _cooperate(cancellation: CancellationProbe | None, *, phase: str) -> None:
    """Keep cancellable Python loops from monopolizing the interpreter."""

    check_cancelled(cancellation, phase=phase)
    if cancellation is not None:
        time.sleep(0.001)


@dataclass(frozen=True, slots=True)
class TrackingResultSnapshot:
    frame_index: int
    time_s: float
    state: Mapping[str, float]
    filtered_state: Mapping[str, float]
    filter_velocity: Mapping[str, float]
    status: str

    def __post_init__(self) -> None:
        if isinstance(self.frame_index, (bool, np.bool_)) or not isinstance(
            self.frame_index, (int, np.integer)
        ):
            raise TypeError("tracker result frame_index must be an integer")
        frame_index = int(self.frame_index)
        if frame_index < 0:
            raise ValueError("tracker result frame_index must be non-negative")
        if isinstance(self.time_s, (bool, np.bool_)):
            raise TypeError("tracker result time_s must be numeric")
        try:
            time_s = float(self.time_s)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError("tracker result time_s must be numeric") from exc
        if not isinstance(self.status, str):
            raise TypeError("tracker result status must be a string")
        object.__setattr__(self, "frame_index", frame_index)
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "state", _numeric_mapping(self.state, "tracker result state"))
        object.__setattr__(
            self,
            "filtered_state",
            _numeric_mapping(self.filtered_state, "tracker result filtered_state"),
        )
        object.__setattr__(
            self,
            "filter_velocity",
            _numeric_mapping(self.filter_velocity, "tracker result filter velocity"),
        )


def _field(item: object, name: str, default: object = None) -> object:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _numeric_mapping(value: object, name: str) -> Mapping[str, float]:
    if value is None:
        return _EMPTY_NUMERIC_MAPPING
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    copied: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise TypeError(f"{name} keys must be non-empty strings")
        if isinstance(item, (bool, np.bool_)):
            continue
        try:
            copied[key] = float(item)
        except (TypeError, ValueError, OverflowError):
            continue
    return MappingProxyType(copied) if copied else _EMPTY_NUMERIC_MAPPING


def _snapshot_one(item: object) -> TrackingResultSnapshot:
    if isinstance(item, TrackingResultSnapshot):
        return item
    debug = _field(item, "debug", {})
    filter_velocity: object = {}
    if isinstance(debug, Mapping):
        filter_debug = debug.get("filter", {})
        if isinstance(filter_debug, Mapping):
            filter_velocity = filter_debug.get("velocity", {})
    return TrackingResultSnapshot(
        frame_index=_field(item, "frame_index"),  # type: ignore[arg-type]
        time_s=_field(item, "time_s"),  # type: ignore[arg-type]
        state=_field(item, "state", {}),  # type: ignore[arg-type]
        filtered_state=_field(item, "filtered_state", {}),  # type: ignore[arg-type]
        filter_velocity=filter_velocity,  # type: ignore[arg-type]
        status=_field(item, "status", "unknown"),  # type: ignore[arg-type]
    )


def snapshot_tracker_results(
    results: Sequence[object],
    *,
    cancellation: CancellationProbe | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[TrackingResultSnapshot, ...]:
    """Detach a live result sequence before numerical traversal starts."""

    if not isinstance(results, Sequence):
        raise TypeError("results_snapshot must be a sequence")
    total = len(results)
    check_cancelled(cancellation, phase="tracking result snapshot")
    snapshots: list[TrackingResultSnapshot] = []
    for index, item in enumerate(results):
        snapshots.append(_snapshot_one(item))
        completed = index + 1
        if completed % _PROGRESS_STRIDE == 0 or completed == total:
            _cooperate(cancellation, phase="tracking result snapshot")
            if progress is not None:
                progress(completed, total, "snapshot")
    return tuple(snapshots)


def _validate_axis(snapshots: tuple[TrackingResultSnapshot, ...]) -> tuple[np.ndarray, np.ndarray]:
    frames = np.fromiter((item.frame_index for item in snapshots), dtype=np.int64, count=len(snapshots))
    times = np.fromiter((item.time_s for item in snapshots), dtype=np.float64, count=len(snapshots))
    if not np.isfinite(times).all():
        raise ValueError("tracker result time_s values must be finite")
    if frames.size > 1 and np.any(np.diff(frames) <= 0):
        raise ValueError("tracker result frame indices must be strictly increasing")
    if times.size > 1 and np.any(np.diff(times) <= 0.0):
        raise ValueError("tracker result time_s values must be strictly increasing")
    return frames, times


def _series_name(source_kind: str, key: str) -> str:
    prefix = {
        "state": "Raw",
        "filtered_state": "Filtered",
        "filter_velocity": "Filter velocity",
    }[source_kind]
    return f"{prefix} {key}"


class TrackingSeriesBuilder:
    """Build deterministic base series without retaining live TrackerResult objects."""

    def __init__(self, *, units: Mapping[str, str] | None = None) -> None:
        supplied = units or {}
        if not isinstance(supplied, Mapping):
            raise TypeError("units must be a mapping")
        normalized: dict[str, str] = {}
        for key, value in supplied.items():
            if not isinstance(key, str) or not key:
                raise TypeError("unit keys must be non-empty strings")
            if not isinstance(value, str):
                raise TypeError(f"unit for {key!r} must be a string")
            normalized[key] = value
        self._units = MappingProxyType(normalized)

    def build_series(
        self,
        results_snapshot: Sequence[object],
        *,
        source_revision: str,
        cancellation: CancellationProbe | None = None,
        progress: ProgressReporter | None = None,
    ) -> tuple[SampleSeries, ...]:
        if not isinstance(source_revision, str) or not source_revision.strip():
            raise ValueError("source_revision must be a non-empty string")
        snapshots = snapshot_tracker_results(
            results_snapshot,
            cancellation=cancellation,
            progress=progress,
        )
        if not snapshots:
            return ()
        frames, times = _validate_axis(snapshots)
        source_mappings = (
            ("state", "state"),
            ("filtered_state", "filtered_state"),
            ("filter_velocity", "filter_velocity"),
        )
        keys: dict[str, tuple[str, ...]] = {}
        for source_kind, attribute in source_mappings:
            keys[source_kind] = tuple(
                sorted({key for item in snapshots for key in getattr(item, attribute).keys()})
            )

        result: list[SampleSeries] = []
        total_work = max(1, sum(len(items) for items in keys.values()) * len(snapshots))
        completed_work = 0
        for source_kind, attribute in source_mappings:
            for key in keys[source_kind]:
                values = np.full(len(snapshots), np.nan, dtype=np.float64)
                valid_mask = np.zeros(len(snapshots), dtype=bool)
                for index, item in enumerate(snapshots):
                    mapping = getattr(item, attribute)
                    value = mapping.get(key)
                    is_valid_status = item.status.strip().lower() not in _INVALID_STATUSES
                    if value is not None and is_valid_status and math.isfinite(value):
                        values[index] = value
                        valid_mask[index] = True
                    completed_work += 1
                    if completed_work % _PROGRESS_STRIDE == 0:
                        _cooperate(cancellation, phase="series build")
                        if progress is not None:
                            progress(min(completed_work, total_work), total_work, "series")
                unit = (
                    velocity_unit(key, self._units)
                    if source_kind == "filter_velocity"
                    else state_unit(key, self._units)
                )
                result.append(
                    SampleSeries(
                        series_id=f"{source_kind}:{key}",
                        name=_series_name(source_kind, key),
                        frame_indices=frames,
                        time_s=times,
                        values=values,
                        valid_mask=valid_mask,
                        unit=unit,
                        source_kind=source_kind,
                        source_revision=source_revision,
                        processing_chain=(ProcessingStep(source_kind, {"key": key}),),
                        metadata={
                            "key": key,
                            "valid_sample_count": int(np.count_nonzero(valid_mask)),
                        },
                    )
                )
        check_cancelled(cancellation, phase="series build")
        if progress is not None:
            progress(total_work, total_work, "complete")
        return tuple(result)


__all__ = [
    "TrackingResultSnapshot",
    "TrackingSeriesBuilder",
    "snapshot_tracker_results",
]
