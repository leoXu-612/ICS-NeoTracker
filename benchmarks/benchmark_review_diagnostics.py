from __future__ import annotations

import argparse
import gc
import json
import math
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.core import TrackerResult
from neo_tracker.ui.confidence_plot import ConfidencePlot
from neo_tracker.ui.review_diagnostics import (
    DIAGNOSTIC_MODES,
    ReviewDiagnosticsPanel,
    angular_response,
    motion_mismatch,
    velocity_keys,
)


def _fixture(result_count: int) -> list[TrackerResult]:
    theta_signal = np.linspace(0.0, 1.0, 720, dtype=float)
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x_px": float(index), "theta": 0.5},
            filtered_state={"x_px": float(index), "theta": 0.5},
            confidence=(index % 101) / 100.0,
            status="manual" if index % 997 == 0 else "ok",
            debug={
                "filter": {"velocity": {"v_x_px": 120.0, "omega": 0.5}},
                "candidates": [{"selected": True, "motion_score": 0.9}],
                "debug_layers": {"theta_signal": theta_signal} if index == 0 else {},
            },
        )
        for index in range(max(0, int(result_count)))
    ]


def _measure(operation, rounds: int):
    operation()
    elapsed: list[float] = []
    for _ in range(max(3, int(rounds))):
        gc.collect()
        started = perf_counter()
        operation()
        elapsed.append((perf_counter() - started) * 1000.0)
    gc.collect()
    tracemalloc.start()
    operation()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return median(elapsed), int(peak)


def _legacy_set_series(
    plot: ConfidencePlot,
    title: str,
    x_values: list[float],
    y_values: list[float],
    *,
    frame_targets: list[int | None],
    statuses: list[str],
    kind: str,
    selected_frame: int | None,
    fixed_y_range: tuple[float, float] | None,
) -> None:
    size = min(len(x_values), len(y_values))
    points = [
        (float(x_values[index]), float(y_values[index]), index)
        for index in range(size)
        if math.isfinite(float(x_values[index])) and math.isfinite(float(y_values[index]))
    ]
    clean_x_values = [point[0] for point in points]
    clean_y_values = [point[1] for point in points]
    source_indices = [point[2] for point in points]
    clean_frame_targets = [
        frame_targets[index] if index < len(frame_targets) else None for index in source_indices
    ]
    clean_statuses = [statuses[index] if index < len(statuses) else "" for index in source_indices]
    plot._apply_series_data(
        title,
        clean_x_values,
        clean_y_values,
        frame_targets=clean_frame_targets,
        statuses=clean_statuses,
        kind=kind,
        selected_frame=selected_frame,
        fixed_y_range=fixed_y_range,
    )


def _legacy_show_confidence(plot: ConfidencePlot, selected_frame: int | None) -> None:
    _legacy_set_series(
        plot,
        "Confidence timeline",
        [float(frame_index) for frame_index, _confidence, _status in plot._values],
        [confidence for _frame_index, confidence, _status in plot._values],
        frame_targets=[frame_index for frame_index, _confidence, _status in plot._values],
        statuses=[status for _frame_index, _confidence, status in plot._values],
        kind="confidence",
        selected_frame=selected_frame,
        fixed_y_range=(0.0, 1.0),
    )


def _legacy_refresh(
    panel: ReviewDiagnosticsPanel,
    results: list[TrackerResult],
    state_units: dict[str, str],
    selected_frame: int | None,
) -> None:
    panel._results = list(results)  # type: ignore[assignment]
    panel._results_by_frame = {int(result.frame_index): result for result in panel._results}
    panel._state_units = dict(state_units)
    panel._selected_frame = int(selected_frame) if selected_frame is not None else None
    panel.plot._values = [
        (int(result.frame_index), float(max(0.0, min(1.0, result.confidence))), result.status)
        for result in panel._results
    ]
    _legacy_show_confidence(panel.plot, panel._selected_frame)

    current_mode = panel.mode
    available = {"confidence"}
    if velocity_keys(panel._results):
        available.add("velocity")
    if any(motion_mismatch(result) is not None for result in panel._results):
        available.add("motion_mismatch")
    if any(angular_response(result) is not None for result in panel._results):
        available.add("angular_response")
    panel.mode_combo.blockSignals(True)
    panel.mode_combo.clear()
    for key, label in DIAGNOSTIC_MODES:
        if key in available:
            panel.mode_combo.addItem(label, key)
    panel.mode_combo.setCurrentIndex(max(0, panel.mode_combo.findData(current_mode)))
    panel.mode_combo.blockSignals(False)

    current_key = str(panel.series_combo.currentData() or "")
    panel.series_combo.blockSignals(True)
    panel.series_combo.clear()
    for key in velocity_keys(panel._results):
        unit = panel._velocity_unit(key)
        visible_label = f"{key} ({unit})" if unit else key
        panel.series_combo.addItem(visible_label, key)
        panel.series_combo.setItemData(
            panel.series_combo.count() - 1,
            visible_label,
            role=Qt.ItemDataRole.ToolTipRole,
        )
    panel.series_combo.setCurrentIndex(max(0, panel.series_combo.findData(current_key)))
    panel.series_combo.blockSignals(False)
    panel.series_combo.setVisible(panel.mode == "velocity")
    _legacy_show_confidence(panel.plot, panel._selected_frame)
    panel._update_static_selection_status()


def _legacy_velocity_render(panel: ReviewDiagnosticsPanel) -> None:
    key = "v_x_px"
    frames: list[float] = []
    values: list[float] = []
    statuses: list[str] = []
    for result in panel._results:
        velocity = result.debug.get("filter", {}).get("velocity", {})
        try:
            value = float(velocity.get(key)) if isinstance(velocity, dict) else math.nan
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value):
            continue
        frames.append(float(result.frame_index))
        values.append(value)
        statuses.append(result.status)
    _legacy_set_series(
        panel.plot,
        "Filter velocity · v_x_px (px/s)",
        frames,
        values,
        frame_targets=[int(frame) for frame in frames],
        statuses=statuses,
        kind="velocity",
        selected_frame=panel._selected_frame,
        fixed_y_range=None,
    )


def _legacy_mismatch_render(panel: ReviewDiagnosticsPanel) -> None:
    frames: list[float] = []
    values: list[float] = []
    statuses: list[str] = []
    for result in panel._results:
        value = motion_mismatch(result)
        if value is None:
            continue
        frames.append(float(result.frame_index))
        values.append(value)
        statuses.append(result.status)
    _legacy_set_series(
        panel.plot,
        "Motion mismatch · 1 − score",
        frames,
        values,
        frame_targets=[int(frame) for frame in frames],
        statuses=statuses,
        kind="motion_mismatch",
        selected_frame=panel._selected_frame,
        fixed_y_range=(0.0, 1.0),
    )


def _current_cold_render(panel: ReviewDiagnosticsPanel, mode: str) -> None:
    panel._series_cache.clear()
    panel.select_mode(mode)


def benchmark(result_count: int, rounds: int) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    results = _fixture(result_count)
    units = {"x_px": "px", "theta": "rad"}
    selected_frame = len(results) - 1 if results else None
    legacy_panel = ReviewDiagnosticsPanel()
    current_panel = ReviewDiagnosticsPanel()

    legacy_refresh_ms, legacy_refresh_peak = _measure(
        lambda: _legacy_refresh(legacy_panel, results, units, selected_frame),
        rounds,
    )
    current_refresh_ms, current_refresh_peak = _measure(
        lambda: current_panel.set_results(results, units, selected_frame),
        rounds,
    )

    _legacy_refresh(legacy_panel, results, units, selected_frame)
    current_panel.set_results(results, units, selected_frame)
    legacy_velocity_ms, legacy_velocity_peak = _measure(
        lambda: _legacy_velocity_render(legacy_panel),
        rounds,
    )
    current_velocity_cold_ms, current_velocity_cold_peak = _measure(
        lambda: _current_cold_render(current_panel, "velocity"),
        rounds,
    )
    current_panel.select_mode("velocity")
    current_velocity_cached_ms, current_velocity_cached_peak = _measure(
        lambda: current_panel.select_mode("velocity"),
        rounds,
    )

    legacy_mismatch_ms, legacy_mismatch_peak = _measure(
        lambda: _legacy_mismatch_render(legacy_panel),
        rounds,
    )
    current_mismatch_cold_ms, current_mismatch_cold_peak = _measure(
        lambda: _current_cold_render(current_panel, "motion_mismatch"),
        rounds,
    )
    current_panel.select_mode("motion_mismatch")
    current_mismatch_cached_ms, current_mismatch_cached_peak = _measure(
        lambda: current_panel.select_mode("motion_mismatch"),
        rounds,
    )

    _legacy_refresh(legacy_panel, results, units, selected_frame)
    current_panel.select_mode("confidence")
    parity = {
        "modes": [legacy_panel.mode_combo.itemData(index) for index in range(legacy_panel.mode_combo.count())]
        == [current_panel.mode_combo.itemData(index) for index in range(current_panel.mode_combo.count())],
        "velocity_keys": [
            legacy_panel.series_combo.itemData(index) for index in range(legacy_panel.series_combo.count())
        ]
        == [current_panel.series_combo.itemData(index) for index in range(current_panel.series_combo.count())],
        "confidence_values": legacy_panel.plot._values == current_panel.plot._values,
        "confidence_targets": legacy_panel.plot._frame_targets == current_panel.plot._frame_targets,
        "selected_result": legacy_panel._selected_result() is current_panel._selected_result(),
    }

    legacy_panel.close()
    current_panel.close()
    app.processEvents()

    def metrics(previous_ms: float, current_ms: float, previous_peak: int, current_peak: int) -> dict[str, float | int]:
        return {
            "previous_median_ms": previous_ms,
            "current_median_ms": current_ms,
            "speedup": previous_ms / current_ms,
            "time_reduction_percent": 100.0 * (previous_ms - current_ms) / previous_ms,
            "previous_python_peak_bytes": previous_peak,
            "current_python_peak_bytes": current_peak,
            "python_peak_reduction_percent": 100.0 * (previous_peak - current_peak) / previous_peak,
        }

    return {
        "result_count": len(results),
        "rounds": max(3, int(rounds)),
        "refresh": metrics(
            legacy_refresh_ms,
            current_refresh_ms,
            legacy_refresh_peak,
            current_refresh_peak,
        ),
        "velocity_cold": metrics(
            legacy_velocity_ms,
            current_velocity_cold_ms,
            legacy_velocity_peak,
            current_velocity_cold_peak,
        ),
        "velocity_cached": {
            "median_ms": current_velocity_cached_ms,
            "python_peak_bytes": current_velocity_cached_peak,
        },
        "mismatch_cold": metrics(
            legacy_mismatch_ms,
            current_mismatch_cold_ms,
            legacy_mismatch_peak,
            current_mismatch_cold_peak,
        ),
        "mismatch_cached": {
            "median_ms": current_mismatch_cached_ms,
            "python_peak_bytes": current_mismatch_cached_peak,
        },
        "series_cache_limit": current_panel._SERIES_CACHE_LIMIT,
        "parity": parity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Review diagnostics indexing and static-series reuse.")
    parser.add_argument("--results", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
