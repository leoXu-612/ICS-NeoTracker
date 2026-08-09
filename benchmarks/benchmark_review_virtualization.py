from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.core import TrackerResult
from neo_tracker.ui.confidence_plot import ConfidencePlot
from neo_tracker.ui.results_table_model import ResultsTableModel
from neo_tracker.ui.review_controller import ReviewController


def _fixture(result_count: int) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / 120.0,
            state={"x_px": float(index), "y_px": float(index + 1)},
            filtered_state={"x_px": float(index), "y_px": float(index + 1)},
            confidence=(index % 101) / 100.0,
            status=(
                "lost"
                if index % 4093 == 0
                else ("manual" if index % 997 == 0 else "ok")
            ),
        )
        for index in range(max(0, int(result_count)))
    ]


def _measure(operation, rounds: int):
    operation()
    elapsed: list[float] = []
    for _ in range(max(3, int(rounds))):
        gc.collect()
        started = perf_counter()
        result = operation()
        elapsed.append((perf_counter() - started) * 1000.0)
        del result
    gc.collect()
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, median(elapsed), int(peak)


def _legacy_table_render(
    table_widget: QTableWidget,
    controller: ReviewController,
    results: list[TrackerResult],
    units: dict[str, str],
):
    table = controller.table(results, units)
    row_colors = {
        "manual": QColor("#edf9f1"),
        "attention": QColor("#fff8e6"),
        "lost": QColor("#fff0f0"),
    }
    table_widget.clear()
    table_widget.setColumnCount(len(table.headers))
    table_widget.setHorizontalHeaderLabels(table.headers)
    table_widget.setRowCount(len(table.rows))
    for row_index, row in enumerate(table.rows):
        for column_index, value in enumerate(row.values):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, row.result_index)
            if row.tone in row_colors:
                item.setBackground(row_colors[row.tone])
            table_widget.setItem(row_index, column_index, item)
    return table


def _current_table_render(
    model: ResultsTableModel,
    results: list[TrackerResult],
    units: dict[str, str],
):
    model.set_results(results, units)
    if results:
        visible_rows = sorted({0, len(results) // 2, len(results) - 1})
        for row in visible_rows:
            for column in range(model.columnCount()):
                model.data(model.index(row, column))
    return model


def _legacy_plot_mapping(x_values: list[float], y_values: list[float], plot: QRectF):
    def map_x(value: float) -> float:
        x_min = min(x_values)
        x_max = max(x_values)
        if x_max - x_min <= 1e-12:
            return plot.center().x()
        return plot.left() + plot.width() * ((value - x_min) / (x_max - x_min))

    return [
        QPointF(
            map_x(x_value),
            plot.bottom() - plot.height() * y_value,
        )
        for x_value, y_value in zip(x_values, y_values)
    ]


def _current_plot_mapping(plot_widget: ConfidencePlot, plot_rect: QRectF):
    indices = plot_widget._display_indices(plot_rect.width())
    x_min, x_max = plot_widget._x_range
    y_min, y_max = plot_widget._resolved_y_range
    return [
        QPointF(
            plot_widget._map_x(plot_widget._x_values[index], plot_rect, x_min, x_max),
            plot_widget._map_y(plot_widget._y_values[index], plot_rect, y_min, y_max),
        )
        for index in indices
    ]


def benchmark(result_count: int, rounds: int, plot_width: int) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    results = _fixture(result_count)
    units = {"x_px": "px", "y_px": "px"}
    controller = ReviewController()
    legacy_widget = QTableWidget()
    current_model = ResultsTableModel()
    legacy_table, legacy_table_ms, legacy_table_peak = _measure(
        lambda: _legacy_table_render(legacy_widget, controller, results, units),
        rounds,
    )
    measured_model, current_table_ms, current_table_peak = _measure(
        lambda: _current_table_render(current_model, results, units),
        rounds,
    )

    plot = ConfidencePlot()
    plot.resize(max(100, int(plot_width)), 104)
    plot.set_results(results, selected_frame=(len(results) - 1 if results else None))
    x_values = list(plot._x_values)
    y_values = list(plot._y_values)
    plot_rect = QRectF(0.0, 0.0, float(max(100, int(plot_width))), 58.0)
    legacy_points, legacy_plot_ms, legacy_plot_peak = _measure(
        lambda: _legacy_plot_mapping(x_values, y_values, plot_rect),
        rounds,
    )
    current_points, current_plot_ms, current_plot_peak = _measure(
        lambda: _current_plot_mapping(plot, plot_rect),
        rounds,
    )

    sample_rows = sorted({0, len(results) // 2, len(results) - 1}) if results else []
    table_parity = all(
        tuple(
            measured_model.data(measured_model.index(row, column))
            for column in range(measured_model.columnCount())
        )
        == legacy_table.rows[row].values
        for row in sample_rows
    )
    display_indices = plot._display_indices(plot_rect.width())
    envelope_preserved = True
    if len(results) > max(1, int(plot_rect.width())) * 2:
        pixel_columns = max(1, int(round(plot_rect.width())))
        display_set = set(display_indices)
        for bucket in range(pixel_columns):
            start = bucket * len(results) // pixel_columns
            end = min(len(results), max(start + 1, (bucket + 1) * len(results) // pixel_columns))
            bucket_indices = range(start, end)
            envelope_preserved = envelope_preserved and (
                min(bucket_indices, key=y_values.__getitem__) in display_set
                and max(range(start, end), key=y_values.__getitem__) in display_set
            )

    legacy_widget.close()
    plot.close()
    app.processEvents()
    return {
        "result_count": len(results),
        "column_count": measured_model.columnCount(),
        "rounds": max(3, int(rounds)),
        "table_render": {
            "previous_materialized_median_ms": legacy_table_ms,
            "current_virtual_median_ms": current_table_ms,
            "speedup": legacy_table_ms / current_table_ms,
            "time_reduction_percent": 100.0 * (legacy_table_ms - current_table_ms) / legacy_table_ms,
            "previous_python_peak_bytes": legacy_table_peak,
            "current_python_peak_bytes": current_table_peak,
            "python_peak_reduction_percent": 100.0
            * (legacy_table_peak - current_table_peak)
            / legacy_table_peak,
            "formatted_row_count": measured_model.cached_row_count,
            "sample_row_parity": table_parity,
        },
        "plot_mapping": {
            "previous_all_points_median_ms": legacy_plot_ms,
            "current_display_bound_median_ms": current_plot_ms,
            "speedup": legacy_plot_ms / current_plot_ms,
            "time_reduction_percent": 100.0 * (legacy_plot_ms - current_plot_ms) / legacy_plot_ms,
            "previous_python_peak_bytes": legacy_plot_peak,
            "current_python_peak_bytes": current_plot_peak,
            "python_peak_reduction_percent": 100.0
            * (legacy_plot_peak - current_plot_peak)
            / legacy_plot_peak,
            "full_point_count": len(x_values),
            "display_point_count": len(current_points),
            "first_last_preserved": bool(
                not results
                or (display_indices and display_indices[0] == 0 and display_indices[-1] == len(results) - 1)
            ),
            "per_pixel_min_max_preserved": envelope_preserved,
            "legacy_point_count": len(legacy_points),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark virtual Review table and display-bound plot mapping.")
    parser.add_argument("--results", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--plot-width", type=int, default=900)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds, args.plot_width), indent=2))


if __name__ == "__main__":
    main()
