from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
import math

import numpy as np

from neo_tracker.kinematics import SampleSeries


class SelectionOrigin(str, Enum):
    SYSTEM = "system"
    VIDEO = "video"
    TABLE = "table"
    PLOT = "plot"
    DIAGNOSTIC = "diagnostic"
    FIT = "fit"


class SelectionMatch(str, Enum):
    EXACT = "exact"
    NEAREST = "nearest"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SelectionState:
    selected_task_id: str | None = None
    selected_result_identity: str | None = None
    source_revision: str | None = None
    selected_frame_index: int | None = None
    selected_time_s: float | None = None
    selected_series_id: str | None = None
    selected_sample_index: int | None = None
    selected_sample_valid: bool | None = None
    selected_fit_id: str | None = None
    match: SelectionMatch = SelectionMatch.UNAVAILABLE
    selection_revision: int = 0


@dataclass(frozen=True)
class SelectionEvent:
    previous: SelectionState
    current: SelectionState
    origin: SelectionOrigin


@dataclass(frozen=True)
class SelectionOutcome:
    accepted: bool
    changed: bool
    state: SelectionState
    reason: str = ""


@dataclass(frozen=True)
class _SeriesIndex:
    series: SampleSeries
    valid_time_indices: np.ndarray
    valid_times: np.ndarray

    @classmethod
    def build(cls, series: SampleSeries) -> "_SeriesIndex":
        valid = np.asarray(series.valid_mask, dtype=bool) & np.isfinite(series.time_s)
        valid_time_indices = np.flatnonzero(valid).astype(np.int64, copy=False)
        valid_time_indices.setflags(write=False)
        valid_times = np.asarray(series.time_s[valid_time_indices], dtype=np.float64)
        valid_times.setflags(write=False)
        return cls(series, valid_time_indices, valid_times)


SelectionListener = Callable[[SelectionEvent], None]


class SelectionSession:
    """Normalize cross-view selection into one immutable, revisioned transaction.

    The session has no Qt dependency. Views render a published event and may pass
    its revision back as ``caused_by_revision``; such echoes are explicitly
    ignored, preventing Video/Table/Plot feedback loops.
    """

    def __init__(self) -> None:
        self._state = SelectionState()
        self._series: dict[str, _SeriesIndex] = {}
        self._listeners: list[SelectionListener] = []
        self._notifying_revision: int | None = None

    @property
    def state(self) -> SelectionState:
        return self._state

    @property
    def series_ids(self) -> tuple[str, ...]:
        return tuple(self._series)

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def subscribe(self, listener: SelectionListener) -> Callable[[], None]:
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def activate_context(
        self,
        task_id: str,
        result_identity: str,
        source_revision: str,
    ) -> SelectionOutcome:
        task = self._required_text(task_id, "task id")
        result = self._required_text(result_identity, "result identity")
        revision = self._required_text(source_revision, "source revision")
        if (
            self._state.selected_task_id == task
            and self._state.selected_result_identity == result
            and self._state.source_revision == revision
        ):
            return self._unchanged()
        self._series.clear()
        return self._commit(
            SelectionState(
                selected_task_id=task,
                selected_result_identity=result,
                source_revision=revision,
                selection_revision=self._state.selection_revision,
            ),
            SelectionOrigin.SYSTEM,
        )

    def attach_series(self, series: SampleSeries) -> SelectionOutcome:
        guard = self._guard(expected_source_revision=series.source_revision)
        if guard is not None:
            return guard
        indexed = _SeriesIndex.build(series)
        self._series[series.series_id] = indexed
        if self._state.selected_series_id is not None:
            if self._state.selected_series_id != series.series_id:
                return self._unchanged()
            return self._remap_selected_series(indexed, SelectionOrigin.SYSTEM)
        return self._commit(
            replace(self._state, selected_series_id=series.series_id),
            SelectionOrigin.SYSTEM,
        )

    def replace_series(self, series: Sequence[SampleSeries]) -> SelectionOutcome:
        """Replace the complete attached set without publishing partial states."""

        items = tuple(series)
        if any(not isinstance(item, SampleSeries) for item in items):
            raise TypeError("selection series must contain SampleSeries values")
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("selection series_id values must be unique")
        guard = self._guard()
        if guard is not None:
            return guard
        if any(item.source_revision != self._state.source_revision for item in items):
            return self._rejected("stale-source-revision")

        indexed = {item.series_id: _SeriesIndex.build(item) for item in items}
        selected_id = self._state.selected_series_id
        if selected_id not in indexed:
            selected_id = next(iter(indexed), None)
        state = self._state
        if selected_id != state.selected_series_id or selected_id is None:
            state = replace(
                state,
                selected_series_id=selected_id,
                selected_sample_index=None,
                selected_sample_valid=None,
                selected_time_s=None,
                selected_fit_id=None,
                match=SelectionMatch.UNAVAILABLE,
            )
        if selected_id is not None and state.selected_frame_index is not None:
            state = self._state_for_frame(
                state,
                indexed[selected_id],
                state.selected_frame_index,
            )
        self._series = indexed
        return self._commit(state, SelectionOrigin.SYSTEM)

    def detach_series(self, series_id: str) -> SelectionOutcome:
        normalized = self._required_text(series_id, "series id")
        if normalized not in self._series:
            return self._unchanged()
        del self._series[normalized]
        if self._state.selected_series_id != normalized:
            return self._unchanged()
        replacement_id = next(iter(self._series), None)
        state = replace(
            self._state,
            selected_series_id=replacement_id,
            selected_sample_index=None,
            selected_sample_valid=None,
            selected_time_s=None,
            selected_fit_id=None,
            match=SelectionMatch.UNAVAILABLE,
        )
        if replacement_id is not None and state.selected_frame_index is not None:
            return self._commit(
                self._state_for_frame(
                    state,
                    self._series[replacement_id],
                    state.selected_frame_index,
                ),
                SelectionOrigin.SYSTEM,
            )
        return self._commit(state, SelectionOrigin.SYSTEM)

    def select_series(
        self,
        series_id: str,
        *,
        origin: SelectionOrigin | str,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome:
        guard = self._guard(expected_source_revision, caused_by_revision)
        if guard is not None:
            return guard
        normalized = self._required_text(series_id, "series id")
        try:
            indexed = self._series[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown series id: {normalized}") from exc
        state = replace(
            self._state,
            selected_series_id=normalized,
            selected_sample_index=None,
            selected_sample_valid=None,
            selected_time_s=None,
            selected_fit_id=None,
            match=SelectionMatch.UNAVAILABLE,
        )
        if state.selected_frame_index is not None:
            state = self._state_for_frame(state, indexed, state.selected_frame_index)
        return self._commit(state, self._origin(origin))

    def select_frame(
        self,
        frame_index: int,
        *,
        origin: SelectionOrigin | str,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome:
        guard = self._guard(expected_source_revision, caused_by_revision)
        if guard is not None:
            return guard
        if isinstance(frame_index, (bool, np.bool_)) or not isinstance(frame_index, (int, np.integer)):
            raise TypeError("frame index must be an integer")
        frame = int(frame_index)
        if frame < 0:
            raise ValueError("frame index must be non-negative")
        indexed = self._selected_series()
        if indexed is None:
            state = replace(
                self._state,
                selected_frame_index=frame,
                selected_time_s=None,
                selected_sample_index=None,
                selected_sample_valid=None,
                match=SelectionMatch.UNAVAILABLE,
            )
        else:
            state = self._state_for_frame(self._state, indexed, frame)
        return self._commit(state, self._origin(origin))

    def select_time(
        self,
        time_s: float,
        *,
        origin: SelectionOrigin | str,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome:
        guard = self._guard(expected_source_revision, caused_by_revision)
        if guard is not None:
            return guard
        time_value = float(time_s)
        if not math.isfinite(time_value):
            raise ValueError("selection time must be finite")
        indexed = self._selected_series()
        if indexed is None or indexed.valid_times.size == 0:
            state = replace(
                self._state,
                selected_frame_index=None,
                selected_time_s=time_value,
                selected_sample_index=None,
                selected_sample_valid=None,
                match=SelectionMatch.UNAVAILABLE,
            )
        else:
            valid_position = self._nearest_sorted(indexed.valid_times, time_value)
            sample_index = int(indexed.valid_time_indices[valid_position])
            sample_time = float(indexed.series.time_s[sample_index])
            state = replace(
                self._state,
                selected_frame_index=int(indexed.series.frame_indices[sample_index]),
                selected_time_s=sample_time,
                selected_sample_index=sample_index,
                selected_sample_valid=True,
                match=(
                    SelectionMatch.EXACT
                    if sample_time == time_value
                    else SelectionMatch.NEAREST
                ),
            )
        return self._commit(state, self._origin(origin))

    def select_sample(
        self,
        series_id: str,
        sample_index: int,
        *,
        origin: SelectionOrigin | str,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome:
        guard = self._guard(expected_source_revision, caused_by_revision)
        if guard is not None:
            return guard
        normalized = self._required_text(series_id, "series id")
        try:
            indexed = self._series[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown series id: {normalized}") from exc
        if isinstance(sample_index, (bool, np.bool_)) or not isinstance(sample_index, (int, np.integer)):
            raise TypeError("sample index must be an integer")
        index = int(sample_index)
        if not 0 <= index < len(indexed.series):
            raise IndexError(f"sample index is out of range: {index}")
        sample_time = float(indexed.series.time_s[index])
        state = replace(
            self._state,
            selected_series_id=normalized,
            selected_sample_index=index,
            selected_sample_valid=bool(indexed.series.valid_mask[index]),
            selected_frame_index=int(indexed.series.frame_indices[index]),
            selected_time_s=sample_time if math.isfinite(sample_time) else None,
            selected_fit_id=None,
            match=SelectionMatch.EXACT,
        )
        return self._commit(state, self._origin(origin))

    def select_fit(
        self,
        fit_id: str | None,
        *,
        origin: SelectionOrigin | str,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome:
        guard = self._guard(expected_source_revision, caused_by_revision)
        if guard is not None:
            return guard
        normalized = None if fit_id is None else self._required_text(fit_id, "fit id")
        return self._commit(
            replace(self._state, selected_fit_id=normalized),
            self._origin(origin),
        )

    def clear_selection(
        self,
        *,
        origin: SelectionOrigin | str = SelectionOrigin.SYSTEM,
    ) -> SelectionOutcome:
        return self._commit(
            replace(
                self._state,
                selected_frame_index=None,
                selected_time_s=None,
                selected_sample_index=None,
                selected_sample_valid=None,
                selected_fit_id=None,
                match=SelectionMatch.UNAVAILABLE,
            ),
            self._origin(origin),
        )

    def _remap_selected_series(
        self,
        indexed: _SeriesIndex,
        origin: SelectionOrigin,
    ) -> SelectionOutcome:
        if self._state.selected_frame_index is None:
            return self._unchanged()
        return self._commit(
            self._state_for_frame(
                self._state,
                indexed,
                self._state.selected_frame_index,
            ),
            origin,
        )

    def _state_for_frame(
        self,
        state: SelectionState,
        indexed: _SeriesIndex,
        frame_index: int,
    ) -> SelectionState:
        frames = indexed.series.frame_indices
        if frames.size == 0:
            return replace(
                state,
                selected_frame_index=int(frame_index),
                selected_time_s=None,
                selected_sample_index=None,
                selected_sample_valid=None,
                match=SelectionMatch.UNAVAILABLE,
            )
        sample_index = self._nearest_sorted(frames, int(frame_index))
        sample_frame = int(frames[sample_index])
        sample_time = float(indexed.series.time_s[sample_index])
        return replace(
            state,
            selected_frame_index=int(frame_index),
            selected_time_s=sample_time if math.isfinite(sample_time) else None,
            selected_sample_index=sample_index,
            selected_sample_valid=bool(indexed.series.valid_mask[sample_index]),
            match=(
                SelectionMatch.EXACT
                if sample_frame == int(frame_index)
                else SelectionMatch.NEAREST
            ),
        )

    def _selected_series(self) -> _SeriesIndex | None:
        series_id = self._state.selected_series_id
        return self._series.get(series_id) if series_id is not None else None

    @staticmethod
    def _nearest_sorted(values: np.ndarray, target: float | int) -> int:
        if values.size == 0:
            raise ValueError("cannot select from an empty series")
        right = int(np.searchsorted(values, target, side="left"))
        if right <= 0:
            return 0
        if right >= int(values.size):
            return int(values.size) - 1
        left = right - 1
        # The earlier source sample wins equal-distance ties.
        return left if target - values[left] <= values[right] - target else right

    def _guard(
        self,
        expected_source_revision: str | None = None,
        caused_by_revision: int | None = None,
    ) -> SelectionOutcome | None:
        if self._state.source_revision is None:
            return self._rejected("no-active-context")
        if (
            expected_source_revision is not None
            and str(expected_source_revision) != self._state.source_revision
        ):
            return self._rejected("stale-source-revision")
        if caused_by_revision is not None:
            caused = int(caused_by_revision)
            if caused == self._state.selection_revision:
                return self._unchanged("selection-echo")
            if caused < self._state.selection_revision:
                return self._rejected("stale-selection-revision")
            raise ValueError("caused-by revision cannot be newer than the current selection")
        if self._notifying_revision is not None:
            return self._unchanged("notification-reentry")
        return None

    def _commit(
        self,
        state: SelectionState,
        origin: SelectionOrigin,
    ) -> SelectionOutcome:
        comparable = replace(state, selection_revision=self._state.selection_revision)
        if comparable == self._state:
            return self._unchanged()
        previous = self._state
        current = replace(comparable, selection_revision=previous.selection_revision + 1)
        self._state = current
        event = SelectionEvent(previous, current, origin)
        self._notifying_revision = current.selection_revision
        try:
            for listener in tuple(self._listeners):
                listener(event)
        finally:
            self._notifying_revision = None
        return SelectionOutcome(True, True, current)

    def _unchanged(self, reason: str = "") -> SelectionOutcome:
        return SelectionOutcome(True, False, self._state, reason)

    def _rejected(self, reason: str) -> SelectionOutcome:
        return SelectionOutcome(False, False, self._state, reason)

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"{label} must not be empty")
        return normalized

    @staticmethod
    def _origin(origin: SelectionOrigin | str) -> SelectionOrigin:
        try:
            return SelectionOrigin(origin)
        except ValueError as exc:
            raise ValueError(f"unknown selection origin: {origin}") from exc
