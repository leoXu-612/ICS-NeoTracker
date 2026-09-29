from __future__ import annotations

from neo_tracker.ui.language import tr

import math
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from neo_tracker.core import TrackerResult
from neo_tracker.ui.confidence_plot import ConfidencePlot


DIAGNOSTIC_MODES = (
    ("confidence", "Confidence"),
    ("velocity", "Velocity"),
    ("motion_mismatch", "Mismatch"),
    ("angular_response", "Angular response"),
)


def velocity_keys(results: Sequence[TrackerResult]) -> list[str]:
    keys: set[str] = set()
    for result in results:
        velocity = result.debug.get("filter", {}).get("velocity", {})
        if isinstance(velocity, dict):
            keys.update(str(key) for key, value in velocity.items() if _finite_float(value) is not None)
    return sorted(keys, key=lambda key: (key != "omega", key))


def motion_mismatch(result: TrackerResult) -> float | None:
    candidates = result.debug.get("candidates")
    if not isinstance(candidates, list):
        return None
    selected = next(
        (candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("selected")),
        None,
    )
    if not isinstance(selected, dict):
        return None
    score = _finite_float(selected.get("motion_score"))
    if score is None:
        return None
    return 1.0 - max(0.0, min(1.0, score))


def angular_response(result: TrackerResult) -> np.ndarray | None:
    debug_layers = result.debug.get("debug_layers")
    signal = debug_layers.get("theta_signal") if isinstance(debug_layers, dict) else None
    if not isinstance(signal, (np.ndarray, list, tuple)):
        return None
    try:
        values = np.asarray(signal, dtype=float)
    except (TypeError, ValueError):
        return None
    if values.ndim != 1 or values.size < 2 or not np.any(np.isfinite(values)):
        return None
    return values


@dataclass
class _DiagnosticSeries:
    title: str
    x_values: list[float]
    y_values: list[float]
    frame_targets: list[int | None]
    statuses: list[str]
    kind: str
    fixed_y_range: tuple[float, float] | None = None
    x_range: tuple[float, float] | None = None
    resolved_y_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class PreparedReviewDiagnostics:
    """Immutable-by-contract result indexes prepared outside the GUI thread."""

    results: tuple[TrackerResult, ...]
    results_by_frame: dict[int, TrackerResult]
    velocity_keys: tuple[str, ...]
    has_motion_mismatch: bool
    has_angular_response: bool
    confidence_values: list[tuple[int, float, str]]
    confidence_x_values: list[float]
    confidence_y_values: list[float]
    confidence_frame_targets: list[int | None]
    confidence_statuses: list[str]
    confidence_x_range: tuple[float, float]


def prepare_review_diagnostics(
    results: Sequence[TrackerResult],
    *,
    cooperate: Callable[[], None] | None = None,
) -> PreparedReviewDiagnostics:
    result_items = tuple(results)
    results_by_frame: dict[int, TrackerResult] = {}
    velocity_key_set: set[str] = set()
    has_motion_mismatch = False
    has_angular_response = False
    confidence_values: list[tuple[int, float, str]] = []
    confidence_x_values: list[float] = []
    confidence_y_values: list[float] = []
    confidence_frame_targets: list[int | None] = []
    confidence_statuses: list[str] = []
    x_min = math.inf
    x_max = -math.inf
    for index, result in enumerate(result_items, start=1):
        frame_index = int(result.frame_index)
        confidence = float(max(0.0, min(1.0, result.confidence)))
        status = result.status
        x_value = float(frame_index)
        results_by_frame[frame_index] = result
        confidence_values.append((frame_index, confidence, status))
        confidence_x_values.append(x_value)
        confidence_y_values.append(confidence)
        confidence_frame_targets.append(frame_index)
        confidence_statuses.append(status)
        x_min = min(x_min, x_value)
        x_max = max(x_max, x_value)
        velocity = result.debug.get("filter", {}).get("velocity", {})
        if isinstance(velocity, dict):
            velocity_key_set.update(
                str(key) for key, value in velocity.items() if _finite_float(value) is not None
            )
        if not has_motion_mismatch and motion_mismatch(result) is not None:
            has_motion_mismatch = True
        if not has_angular_response and angular_response(result) is not None:
            has_angular_response = True
        if cooperate is not None and (index % 256 == 0 or index == len(result_items)):
            cooperate()
    return PreparedReviewDiagnostics(
        results=result_items,
        results_by_frame=results_by_frame,
        velocity_keys=tuple(sorted(velocity_key_set, key=lambda key: (key != "omega", key))),
        has_motion_mismatch=has_motion_mismatch,
        has_angular_response=has_angular_response,
        confidence_values=confidence_values,
        confidence_x_values=confidence_x_values,
        confidence_y_values=confidence_y_values,
        confidence_frame_targets=confidence_frame_targets,
        confidence_statuses=confidence_statuses,
        confidence_x_range=(x_min, x_max) if confidence_x_values else (0.0, 1.0),
    )


class ReviewDiagnosticsPanel(QWidget):
    frameActivated = Signal(int)
    _SERIES_CACHE_LIMIT = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[TrackerResult] = []
        self._results_by_frame: dict[int, TrackerResult] = {}
        self._velocity_key_counts: dict[str, int] = {}
        self._motion_mismatch_count = 0
        self._angular_response_count = 0
        self._velocity_keys: tuple[str, ...] = ()
        self._has_motion_mismatch = False
        self._has_angular_response = False
        self._series_cache: OrderedDict[tuple[str, str], _DiagnosticSeries] = OrderedDict()
        self._state_units: dict[str, str] = {}
        self._selected_frame: int | None = None

        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("reviewDiagnosticModeCombo")
        self.mode_combo.setAccessibleName(tr('Review diagnostic plot'))
        self.mode_combo.setToolTip(tr('Choose one diagnostic without adding more overlays to the preview.'))
        self.series_combo = QComboBox()
        self.series_combo.setObjectName("reviewDiagnosticSeriesCombo")
        self.series_combo.setAccessibleName(tr('Filter velocity series'))
        self.series_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.status_label = QLabel(tr('No tracking diagnostics'))
        self.status_label.setObjectName("reviewDiagnosticStatusLabel")
        self.status_label.setAccessibleName(tr('Selected diagnostic value'))
        self.plot = ConfidencePlot()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self.mode_combo)
        controls.addWidget(self.series_combo)
        controls.addWidget(self.status_label, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(controls)
        layout.addWidget(self.plot)

        self.mode_combo.currentIndexChanged.connect(lambda _index: self._mode_changed())
        self.series_combo.currentIndexChanged.connect(lambda _index: self._render())
        self.plot.frameActivated.connect(self.frameActivated.emit)
        self.setAccessibleName(tr('Review diagnostics'))
        self.set_results([], {}, None)

    @property
    def mode(self) -> str:
        return str(self.mode_combo.currentData() or "confidence")

    @property
    def cached_series_count(self) -> int:
        return len(self._series_cache)

    def set_results(
        self,
        results: Sequence[TrackerResult],
        state_units: dict[str, str],
        selected_frame: int | None,
    ) -> None:
        self._results = list(results)
        self._index_results()
        self._series_cache.clear()
        self._state_units = dict(state_units)
        self._selected_frame = int(selected_frame) if selected_frame is not None else None
        self.plot.set_results(self._results, self._selected_frame, render=False)
        self.setAccessibleDescription(
            tr('Diagnostics indexed from {v0:,} tracking results. Complete timeline navigation remains available.', v0=len(self._results))
        )
        self._rebuild_modes()

    def refresh_result(self, result_index: int, result: TrackerResult) -> bool:
        """Update one diagnostics row without rescanning the complete timeline."""

        index = int(result_index)
        if index < 0 or index >= len(self._results):
            return False
        previous = self._results[index]
        if int(previous.frame_index) != int(result.frame_index):
            return False

        previous_modes = tuple(self._available_modes())
        previous_mode = self.mode
        previous_velocity_keys = self._velocity_keys
        previous_velocity = self._result_velocity_keys(previous)
        current_velocity = self._result_velocity_keys(result)
        for key in previous_velocity - current_velocity:
            remaining = self._velocity_key_counts.get(key, 0) - 1
            if remaining > 0:
                self._velocity_key_counts[key] = remaining
            else:
                self._velocity_key_counts.pop(key, None)
        for key in current_velocity - previous_velocity:
            self._velocity_key_counts[key] = self._velocity_key_counts.get(key, 0) + 1

        previous_mismatch = motion_mismatch(previous) is not None
        current_mismatch = motion_mismatch(result) is not None
        self._motion_mismatch_count += int(current_mismatch) - int(previous_mismatch)
        previous_angular = angular_response(previous) is not None
        current_angular = angular_response(result) is not None
        self._angular_response_count += int(current_angular) - int(previous_angular)

        self._results[index] = result
        self._results_by_frame[int(result.frame_index)] = result
        self._velocity_keys = tuple(
            sorted(self._velocity_key_counts, key=lambda key: (key != "omega", key))
        )
        self._has_motion_mismatch = self._motion_mismatch_count > 0
        self._has_angular_response = self._angular_response_count > 0
        self.plot.refresh_result(index, result)
        self._refresh_cached_series(previous, result)

        if (
            tuple(self._available_modes()) != previous_modes
            or self._velocity_keys != previous_velocity_keys
        ):
            self._sync_mode_controls()
            if self.mode != previous_mode:
                self._render()
        if self.mode == "velocity":
            series = self._cached_series(("velocity", str(self.series_combo.currentData() or "")))
            if series is not None:
                self._show_series(series)
            else:
                self._render_velocity()
        elif self.mode == "motion_mismatch":
            series = self._cached_series(("motion_mismatch", ""))
            if series is not None:
                self._show_series(series)
        elif self.mode == "angular_response" and self._selected_frame == int(result.frame_index):
            self._render_angular_response()
        self._update_static_selection_status()
        return True

    def set_prepared_results(
        self,
        prepared: PreparedReviewDiagnostics,
        state_units: dict[str, str],
        selected_frame: int | None,
    ) -> None:
        self._results = prepared.results
        self._results_by_frame = prepared.results_by_frame
        self._velocity_keys = prepared.velocity_keys
        self._has_motion_mismatch = prepared.has_motion_mismatch
        self._has_angular_response = prepared.has_angular_response
        self._series_cache.clear()
        self._state_units = dict(state_units)
        self._selected_frame = int(selected_frame) if selected_frame is not None else None
        self.setAccessibleDescription(
            tr('Diagnostics indexed from {v0:,} tracking results. Complete timeline navigation remains available.', v0=len(self._results))
        )
        self._rebuild_modes(render=False)
        self.plot.set_prepared_confidence(
            prepared.confidence_values,
            prepared.confidence_x_values,
            prepared.confidence_y_values,
            prepared.confidence_frame_targets,
            prepared.confidence_statuses,
            selected_frame=self._selected_frame,
            x_range=prepared.confidence_x_range,
        )
        self._update_static_selection_status()

    def _index_results(self) -> None:
        results_by_frame: dict[int, TrackerResult] = {}
        velocity_key_counts: dict[str, int] = {}
        motion_mismatch_count = 0
        angular_response_count = 0
        for result in self._results:
            results_by_frame[int(result.frame_index)] = result
            for key in self._result_velocity_keys(result):
                velocity_key_counts[key] = velocity_key_counts.get(key, 0) + 1
            motion_mismatch_count += int(motion_mismatch(result) is not None)
            angular_response_count += int(angular_response(result) is not None)
        self._results_by_frame = results_by_frame
        self._velocity_key_counts = velocity_key_counts
        self._motion_mismatch_count = motion_mismatch_count
        self._angular_response_count = angular_response_count
        self._velocity_keys = tuple(
            sorted(velocity_key_counts, key=lambda key: (key != "omega", key))
        )
        self._has_motion_mismatch = motion_mismatch_count > 0
        self._has_angular_response = angular_response_count > 0

    @staticmethod
    def _result_velocity_keys(result: TrackerResult) -> set[str]:
        velocity = result.debug.get("filter", {}).get("velocity", {})
        if not isinstance(velocity, dict):
            return set()
        return {
            str(key) for key, value in velocity.items() if _finite_float(value) is not None
        }

    def _sync_mode_controls(self) -> None:
        current_mode = self.mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for key, label in self._available_modes():
            self.mode_combo.addItem(label, key)
        index = self.mode_combo.findData(current_mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self.mode_combo.blockSignals(False)
        self._rebuild_velocity_series()
        self.series_combo.setVisible(self.mode == "velocity")

    @staticmethod
    def _series_value(kind: str, key: str, result: TrackerResult) -> float | None:
        if kind == "velocity":
            velocity = result.debug.get("filter", {}).get("velocity", {})
            return _finite_float(velocity.get(key)) if isinstance(velocity, dict) else None
        if kind == "motion_mismatch":
            return motion_mismatch(result)
        return None

    def _refresh_cached_series(
        self,
        previous: TrackerResult,
        result: TrackerResult,
    ) -> None:
        frame_index = int(result.frame_index)
        for (kind, key), series in self._series_cache.items():
            previous_value = self._series_value(kind, key, previous)
            current_value = self._series_value(kind, key, result)
            try:
                series_index = series.frame_targets.index(frame_index)
            except ValueError:
                series_index = -1
            if previous_value is not None and current_value is None and series_index >= 0:
                for values in (
                    series.x_values,
                    series.y_values,
                    series.frame_targets,
                    series.statuses,
                ):
                    values.pop(series_index)
            elif current_value is not None and series_index >= 0:
                series.x_values[series_index] = float(frame_index)
                series.y_values[series_index] = current_value
                series.frame_targets[series_index] = frame_index
                series.statuses[series_index] = result.status
            elif current_value is not None:
                insertion = 0
                while insertion < len(series.x_values) and series.x_values[insertion] < frame_index:
                    insertion += 1
                series.x_values.insert(insertion, float(frame_index))
                series.y_values.insert(insertion, current_value)
                series.frame_targets.insert(insertion, frame_index)
                series.statuses.insert(insertion, result.status)
            series.x_range = None
            series.resolved_y_range = None

    def set_selected_frame(self, frame_index: int | None) -> None:
        self._selected_frame = int(frame_index) if frame_index is not None else None
        if self.mode == "angular_response":
            self._render_angular_response()
            return
        self.plot.set_selected_frame(self._selected_frame)
        self._update_static_selection_status()

    def select_mode(self, mode: str) -> bool:
        index = self.mode_combo.findData(str(mode))
        if index < 0:
            return False
        if index == self.mode_combo.currentIndex():
            self._render()
        else:
            self.mode_combo.setCurrentIndex(index)
        return True

    def _available_modes(self) -> list[tuple[str, str]]:
        available = {"confidence"}
        if self._velocity_keys:
            available.add("velocity")
        if self._has_motion_mismatch:
            available.add("motion_mismatch")
        if self._has_angular_response:
            available.add("angular_response")
        return [(key, label) for key, label in DIAGNOSTIC_MODES if key in available]

    def _rebuild_modes(self, *, render: bool = True) -> None:
        current_mode = self.mode
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for key, label in self._available_modes():
            self.mode_combo.addItem(label, key)
        index = self.mode_combo.findData(current_mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self.mode_combo.blockSignals(False)
        self._rebuild_velocity_series()
        if render:
            self._render()

    def _rebuild_velocity_series(self) -> None:
        current_key = str(self.series_combo.currentData() or "")
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        for key in self._velocity_keys:
            unit = self._velocity_unit(key)
            visible_label = f"{key} ({unit})" if unit else key
            self.series_combo.addItem(visible_label, key)
            self.series_combo.setItemData(
                self.series_combo.count() - 1,
                visible_label,
                role=Qt.ItemDataRole.ToolTipRole,
            )
        index = self.series_combo.findData(current_key)
        self.series_combo.setCurrentIndex(max(0, index))
        self.series_combo.blockSignals(False)
        self.series_combo.setVisible(self.mode == "velocity")

    def _mode_changed(self) -> None:
        self.series_combo.setVisible(self.mode == "velocity")
        self._render()

    def _render(self) -> None:
        if self.mode == "velocity":
            self._render_velocity()
        elif self.mode == "motion_mismatch":
            self._render_motion_mismatch()
        elif self.mode == "angular_response":
            self._render_angular_response()
        else:
            self._render_confidence()

    def _render_confidence(self) -> None:
        self.plot.show_confidence(self._selected_frame)
        self._update_static_selection_status()

    def _render_velocity(self) -> None:
        key = str(self.series_combo.currentData() or "")
        cache_key = ("velocity", key)
        series = self._cached_series(cache_key)
        if series is None:
            series = self._build_velocity_series(key)
            self._remember_series(cache_key, series)
        self.series_combo.setToolTip(series.title)
        self._show_series(series)
        self._update_static_selection_status()

    def _build_velocity_series(self, key: str) -> _DiagnosticSeries:
        frames: list[float] = []
        values: list[float] = []
        frame_targets: list[int | None] = []
        statuses: list[str] = []
        for result in self._results:
            velocity = result.debug.get("filter", {}).get("velocity", {})
            value = _finite_float(velocity.get(key)) if isinstance(velocity, dict) else None
            if value is None:
                continue
            frames.append(float(result.frame_index))
            values.append(value)
            frame_targets.append(int(result.frame_index))
            statuses.append(result.status)
        unit = self._velocity_unit(key)
        title = f"Filter velocity · {key}" if key else "Filter velocity"
        if unit:
            title += f" ({unit})"
        return _DiagnosticSeries(
            title,
            frames,
            values,
            frame_targets,
            statuses,
            "velocity",
        )

    def _render_motion_mismatch(self) -> None:
        cache_key = ("motion_mismatch", "")
        series = self._cached_series(cache_key)
        if series is None:
            series = self._build_motion_mismatch_series()
            self._remember_series(cache_key, series)
        self._show_series(series)
        self._update_static_selection_status()

    def _build_motion_mismatch_series(self) -> _DiagnosticSeries:
        frames: list[float] = []
        values: list[float] = []
        frame_targets: list[int | None] = []
        statuses: list[str] = []
        for result in self._results:
            value = motion_mismatch(result)
            if value is None:
                continue
            frames.append(float(result.frame_index))
            values.append(value)
            frame_targets.append(int(result.frame_index))
            statuses.append(result.status)
        return _DiagnosticSeries(
            "Motion mismatch · 1 − score",
            frames,
            values,
            frame_targets,
            statuses,
            "motion_mismatch",
            fixed_y_range=(0.0, 1.0),
        )

    def _cached_series(self, key: tuple[str, str]) -> _DiagnosticSeries | None:
        series = self._series_cache.get(key)
        if series is not None:
            self._series_cache.move_to_end(key)
        return series

    def _remember_series(self, key: tuple[str, str], series: _DiagnosticSeries) -> None:
        self._series_cache[key] = series
        self._series_cache.move_to_end(key)
        while len(self._series_cache) > self._SERIES_CACHE_LIMIT:
            self._series_cache.popitem(last=False)

    def _show_series(self, series: _DiagnosticSeries) -> None:
        self.plot._set_prevalidated_series(
            series.title,
            series.x_values,
            series.y_values,
            frame_targets=series.frame_targets,
            statuses=series.statuses,
            kind=series.kind,
            selected_frame=self._selected_frame,
            fixed_y_range=series.fixed_y_range,
            x_range=series.x_range,
            resolved_y_range=series.resolved_y_range,
            timeline=True,
        )
        if series.x_range is None:
            series.x_range = self.plot._x_range
        if series.resolved_y_range is None:
            series.resolved_y_range = self.plot._resolved_y_range

    def _update_static_selection_status(self) -> None:
        result = self._selected_result()
        if self.mode == "velocity":
            key = str(self.series_combo.currentData() or "")
            velocity = result.debug.get("filter", {}).get("velocity", {}) if result is not None else {}
            selected_value = _finite_float(velocity.get(key)) if isinstance(velocity, dict) else None
            if result is None or selected_value is None:
                self._set_status(f"{key or 'Velocity'} unavailable on the selected frame")
                return
            unit = self._velocity_unit(key)
            suffix = f" {unit}" if unit else ""
            self._set_status(f"Frame {result.frame_index} · {key} {selected_value:.4g}{suffix}")
            return
        if self.mode == "motion_mismatch":
            selected_value = motion_mismatch(result) if result is not None else None
            if result is None or selected_value is None:
                self._set_status("Motion mismatch unavailable on the selected frame")
                return
            evidence = self._evidence_suffix(result)
            self._set_status(
                f"Frame {result.frame_index} · mismatch {selected_value:.3f}{evidence}",
                detail=(
                    f"Frame {result.frame_index} · mismatch {selected_value:.3f} "
                    f"(1 − motion score){evidence}"
                ),
            )
            return
        if result is None:
            self._set_status("No selected tracking result")
            return
        self._set_status(
            f"Frame {result.frame_index} · confidence {result.confidence:.3f} · {result.status}"
        )

    def _render_angular_response(self) -> None:
        result = self._selected_result()
        signal = angular_response(result) if result is not None else None
        if result is None or signal is None:
            self.plot.set_series(
                "Angular response",
                [],
                [],
                kind="angular_response",
                selected_frame=self._selected_frame,
                fixed_y_range=(0.0, 1.0),
            )
            self._set_status("Angular response unavailable on the selected frame")
            return
        degrees = np.linspace(0.0, 360.0, signal.size, endpoint=False)
        theta = _finite_float(result.filtered_state.get("theta"))
        if theta is None:
            theta = _finite_float(result.state.get("theta"))
        tracked_degrees = math.degrees(theta % (2.0 * math.pi)) if theta is not None else None
        finite_mask = np.isfinite(signal)
        finite_signal = np.where(finite_mask, signal, -np.inf)
        peak_index = int(np.argmax(finite_signal))
        peak_degrees = float(degrees[peak_index])
        finite_values = signal[finite_mask]
        fixed_range = (
            (0.0, 1.0)
            if float(np.min(finite_values)) >= 0.0 and float(np.max(finite_values)) <= 1.0
            else None
        )
        self.plot.set_series(
            f"Angular response · frame {result.frame_index}",
            degrees.tolist(),
            signal.tolist(),
            kind="angular_response",
            selected_frame=result.frame_index,
            selected_x=tracked_degrees,
            fixed_y_range=fixed_range,
        )
        tracked = f" · tracked {tracked_degrees:.1f}°" if tracked_degrees is not None else ""
        self._set_status(
            f"Frame {result.frame_index} · peak {peak_degrees:.1f}°{tracked}"
            f"{self._evidence_suffix(result)}"
        )

    def _selected_result(self) -> TrackerResult | None:
        if self._selected_frame is None:
            return None
        return self._results_by_frame.get(self._selected_frame)

    def _velocity_unit(self, key: str) -> str:
        base_key = "theta" if key == "omega" else key[2:] if key.startswith("v_") else key
        unit = self._state_units.get(base_key, "")
        return f"{unit}/s" if unit else ""

    @staticmethod
    def _evidence_suffix(result: TrackerResult) -> str:
        return " · pre-edit evidence" if result.status.startswith("manual") else ""

    def _set_status(self, text: str, *, detail: str | None = None) -> None:
        full_detail = detail or text
        self.status_label.setText(tr(text))
        self.status_label.setToolTip(full_detail)
        self.status_label.setAccessibleDescription(full_detail)


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
