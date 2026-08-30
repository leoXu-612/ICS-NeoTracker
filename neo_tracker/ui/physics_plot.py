from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from neo_tracker.kinematics import FitResult, FitStatus, ProcessingStep, SampleSeries


@dataclass(frozen=True)
class DecimatedSeries:
    series_id: str
    name: str
    unit: str
    sample_indices: np.ndarray
    time_s: np.ndarray
    values: np.ndarray
    source_revision: str

    def __post_init__(self) -> None:
        for name in ("sample_indices", "time_s", "values"):
            value = np.ascontiguousarray(getattr(self, name))
            value.setflags(write=False)
            object.__setattr__(self, name, value)
        if not (len(self.sample_indices) == len(self.time_s) == len(self.values)):
            raise ValueError("decimated plot arrays must align")


def decimate_series(
    series: SampleSeries,
    *,
    width_px: int,
    selected_sample_index: int | None = None,
    range_sample_indices: tuple[int, int] | None = None,
    time_range: tuple[float, float] | None = None,
) -> DecimatedSeries:
    """Build a min/max envelope bounded by viewport width.

    Every horizontal bin contributes at most its minimum and maximum. Global
    endpoints, an explicit selection, range boundaries, and invalid gap
    sentinels are added without converting samples into Python row objects.
    """

    width = int(width_px)
    if width < 1 or width > 32_768:
        raise ValueError("plot width must be in [1, 32768]")
    count = len(series)
    if count == 0:
        empty_i = np.empty(0, dtype=np.int64)
        empty_f = np.empty(0, dtype=np.float64)
        return DecimatedSeries(
            series.series_id,
            series.name,
            series.unit,
            empty_i,
            empty_f,
            empty_f.copy(),
            series.source_revision,
        )

    finite_valid = (
        np.asarray(series.valid_mask, dtype=bool)
        & np.isfinite(series.time_s)
        & np.isfinite(series.values)
    )
    visible = finite_valid.copy()
    if time_range is not None:
        start, end = float(time_range[0]), float(time_range[1])
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError("plot time range must be finite and increasing")
        visible &= (series.time_s >= start) & (series.time_s <= end)
    visible_indices = np.flatnonzero(visible)
    if visible_indices.size == 0:
        empty_i = np.empty(0, dtype=np.int64)
        empty_f = np.empty(0, dtype=np.float64)
        return DecimatedSeries(
            series.series_id,
            series.name,
            series.unit,
            empty_i,
            empty_f,
            empty_f.copy(),
            series.source_revision,
        )

    visible_times = series.time_s[visible_indices]
    first_time, last_time = float(visible_times[0]), float(visible_times[-1])
    if last_time > first_time:
        bin_ids = np.floor(
            (visible_times - first_time) / (last_time - first_time) * max(1, width - 1)
        ).astype(np.int64)
        np.clip(bin_ids, 0, max(0, width - 1), out=bin_ids)
    else:
        bin_ids = np.zeros(visible_indices.size, dtype=np.int64)

    boundaries = np.r_[0, np.flatnonzero(np.diff(bin_ids)) + 1, visible_indices.size]
    chosen: list[int] = [int(visible_indices[0]), int(visible_indices[-1])]
    for begin, end in zip(boundaries[:-1], boundaries[1:]):
        indices = visible_indices[int(begin) : int(end)]
        values = series.values[indices]
        chosen.append(int(indices[int(np.argmin(values))]))
        chosen.append(int(indices[int(np.argmax(values))]))

    for special in (
        selected_sample_index,
        *(range_sample_indices or (None, None)),
    ):
        if special is None:
            continue
        index = int(special)
        if 0 <= index < count:
            if time_range is None or (
                math.isfinite(float(series.time_s[index]))
                and float(time_range[0]) <= float(series.time_s[index]) <= float(time_range[1])
            ):
                chosen.append(index)

    semantic = np.unique(np.asarray(chosen, dtype=np.int64))
    invalid_prefix = np.empty(count + 1, dtype=np.int64)
    invalid_prefix[0] = 0
    np.cumsum(~finite_valid, dtype=np.int64, out=invalid_prefix[1:])
    output: list[int] = []
    previous: int | None = None
    for index_value in semantic:
        index = int(index_value)
        if previous is not None and index > previous + 1:
            invalid_between = int(invalid_prefix[index] - invalid_prefix[previous + 1])
            if invalid_between > 0:
                output.append(-1)
        output.append(index)
        previous = index

    sample_indices = np.asarray(output, dtype=np.int64)
    times = np.full(sample_indices.size, np.nan, dtype=np.float64)
    values = np.full(sample_indices.size, np.nan, dtype=np.float64)
    real = sample_indices >= 0
    times[real] = series.time_s[sample_indices[real]]
    values[real] = series.values[sample_indices[real]]
    return DecimatedSeries(
        series.series_id,
        series.name,
        series.unit,
        sample_indices,
        times,
        values,
        series.source_revision,
    )


class PhysicsPlot(QWidget):
    """Small-dependency true-time plot whose paint path is O(viewport width)."""

    sampleActivated = Signal(str, int, float)
    timeActivated = Signal(float)
    rangeSelected = Signal(float, float)

    _COLORS = (QColor("#2F6F9F"), QColor("#477B68"), QColor("#8A641C"), QColor("#6C5B8E"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: tuple[SampleSeries, ...] = ()
        self._valid_indices: dict[str, np.ndarray] = {}
        self._prepared: tuple[DecimatedSeries, ...] = ()
        self._envelope_cache: OrderedDict[tuple[str, int], DecimatedSeries] = OrderedDict()
        self._selected_series_id: str | None = None
        self._selected_sample_index: int | None = None
        self._range_s: tuple[float, float] | None = None
        self._drag_start_x: float | None = None
        self._fit_series: SampleSeries | None = None
        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Physics true-time plot")
        self.setAccessibleDescription(
            "Physical series against stored true time. Invalid samples are visible as line gaps. "
            "Use Left and Right Arrow to move the selected sample."
        )

    @property
    def prepared_point_count(self) -> int:
        return sum(len(item.sample_indices) for item in self._prepared)

    @property
    def series_ids(self) -> tuple[str, ...]:
        return tuple(item.series_id for item in self._series)

    @property
    def selected_sample_index(self) -> int | None:
        return self._selected_sample_index

    @property
    def selected_range(self) -> tuple[float, float] | None:
        return self._range_s

    @property
    def time_range(self) -> tuple[float, float] | None:
        bounds = self._data_bounds()
        return None if bounds is None else bounds[:2]

    def set_series(self, series: Sequence[SampleSeries]) -> None:
        items = tuple(series)
        if len(items) > 8:
            raise ValueError("the physics plot supports at most eight visible series")
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("visible plot series_id values must be unique")
        revisions = {item.source_revision for item in items}
        if len(revisions) > 1:
            raise ValueError("visible plot series must share one source revision")
        if len(items) > 1 and (
            not items[0].unit or any(item.unit != items[0].unit for item in items[1:])
        ):
            raise ValueError("visible plot series must share one known unit")
        self._series = items
        self._valid_indices = {
            item.series_id: np.flatnonzero(
                item.valid_mask & np.isfinite(item.time_s) & np.isfinite(item.values)
            ).astype(np.int64, copy=False)
            for item in items
        }
        for indices in self._valid_indices.values():
            indices.setflags(write=False)
        self._selected_series_id = items[0].series_id if items else None
        self._selected_sample_index = None
        self._fit_series = None
        self._range_s = None
        self._envelope_cache.clear()
        self._prepare_envelopes()
        self.update()

    def set_fit_result(
        self,
        source: SampleSeries,
        result: FitResult | None,
        *,
        residual: bool = False,
    ) -> None:
        if result is None:
            self._fit_series = None
            self._prepare_envelopes()
            self.update()
            return
        if result.series_id != source.series_id or result.source_revision != source.source_revision:
            raise ValueError("fit result does not match the source series")
        if result.status is not FitStatus.OK:
            self._fit_series = None
            self._prepare_envelopes()
            self.update()
            return
        values = result.residuals if residual else result.predicted
        self._fit_series = SampleSeries(
            series_id=f"fit:{result.model.value}:{'residual' if residual else 'prediction'}",
            name=f"{result.model.value.title()} {'residual' if residual else 'fit'}",
            frame_indices=source.frame_indices,
            time_s=source.time_s,
            values=values,
            valid_mask=result.valid_mask,
            unit=source.unit,
            source_kind="residual" if residual else "fit",
            source_revision=source.source_revision,
            processing_chain=(
                ProcessingStep(
                    "fit_residual" if residual else "fit_prediction",
                    {"model": result.model.value},
                ),
            ),
        )
        self._envelope_cache.clear()
        self._prepare_envelopes()
        self.update()

    def set_selected_sample(self, sample_index: int | None, series_id: str | None = None) -> None:
        if sample_index is None:
            self._selected_sample_index = None
            self.update()
            return
        resolved_id = series_id or self._selected_series_id
        source = self._series_by_id(resolved_id)
        index = int(sample_index)
        if source is None or not 0 <= index < len(source):
            raise IndexError("selected plot sample is out of range")
        self._selected_series_id = source.series_id
        self._selected_sample_index = index
        self.update()

    def set_selected_range(self, start_s: float | None, end_s: float | None) -> None:
        if start_s is None or end_s is None:
            self._range_s = None
        else:
            start, end = sorted((float(start_s), float(end_s)))
            if not math.isfinite(start) or not math.isfinite(end) or start == end:
                raise ValueError("selected plot range must be finite and non-empty")
            self._range_s = (start, end)
        self.update()

    def export_image(self, path: str | Path) -> bool:
        target = Path(path)
        scale = max(1.0, float(self.devicePixelRatioF()))
        image = QPixmap(
            max(1, int(math.ceil(self.width() * scale))),
            max(1, int(math.ceil(self.height() * scale))),
        )
        image.setDevicePixelRatio(scale)
        image.fill(QColor("#FBFCFC"))
        self.render(image)
        return bool(image.save(str(target), "PNG"))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        dense_trace = self.prepared_point_count > max(512, self.width())
        # Dense scientific envelopes are already sub-pixel summaries. Raster
        # antialiasing those thousands of crossings can stall the GUI thread;
        # sparse traces retain antialiasing for presentation quality.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not dense_trace)
        painter.fillRect(self.rect(), QColor("#FBFCFC"))
        plot_rect = self._plot_rect()
        self._paint_legend(painter)
        painter.setPen(QPen(QColor("#D8DEE2"), 1.0))
        painter.drawRect(plot_rect)

        bounds = self._data_bounds()
        if bounds is None:
            painter.setPen(QColor("#626A70"))
            painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "Choose a physical series to plot")
            self._paint_focus_ring(painter)
            return
        time_min, time_max, value_min, value_max = bounds
        for fraction in (0.25, 0.5, 0.75):
            x = plot_rect.left() + plot_rect.width() * fraction
            y = plot_rect.top() + plot_rect.height() * fraction
            painter.setPen(QPen(QColor("#E5E9EB"), 1.0, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))
            painter.drawLine(QPointF(plot_rect.left(), y), QPointF(plot_rect.right(), y))

        if self._range_s is not None:
            range_start, range_end = self._range_s
            left = self._x_for_time(range_start, plot_rect, time_min, time_max)
            right = self._x_for_time(range_end, plot_rect, time_min, time_max)
            painter.fillRect(
                QRectF(min(left, right), plot_rect.top(), abs(right - left), plot_rect.height()),
                QColor(47, 111, 159, 24),
            )
            painter.setPen(QPen(QColor("#2F6F9F"), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(left, plot_rect.top()), QPointF(left, plot_rect.bottom()))
            painter.drawLine(QPointF(right, plot_rect.top()), QPointF(right, plot_rect.bottom()))

        endpoints: list[tuple[int, QPointF, QColor]] = []
        for layer_index, prepared in enumerate(self._prepared):
            color = self._COLORS[layer_index % len(self._COLORS)]
            if prepared.series_id.startswith("fit:"):
                pen = QPen(color, 1.5, Qt.PenStyle.DashLine)
            else:
                pen = QPen(color, 1.6)
            painter.setPen(pen)
            segment: list[QPointF] = []
            last_point: QPointF | None = None
            for time_s, value in zip(prepared.time_s, prepared.values):
                if not math.isfinite(float(time_s)) or not math.isfinite(float(value)):
                    if len(segment) > 1:
                        painter.drawPolyline(QPolygonF(segment))
                    elif segment:
                        painter.drawEllipse(segment[0], 2.0, 2.0)
                    segment.clear()
                    continue
                point = QPointF(
                    self._x_for_time(float(time_s), plot_rect, time_min, time_max),
                    self._y_for_value(float(value), plot_rect, value_min, value_max),
                )
                segment.append(point)
                last_point = point
            if len(segment) > 1:
                painter.drawPolyline(QPolygonF(segment))
            elif segment:
                painter.drawEllipse(segment[0], 2.0, 2.0)
            if last_point is not None:
                endpoints.append((layer_index, last_point, color))

        self._paint_layer_labels(painter, plot_rect, endpoints)
        self._paint_cursor(painter, plot_rect, bounds)
        painter.setPen(QColor("#626A70"))
        painter.drawText(
            QRectF(plot_rect.left(), plot_rect.bottom() + 8.0, plot_rect.width(), 20.0),
            Qt.AlignmentFlag.AlignCenter,
            "true time (s)",
        )
        unit = next((item.unit for item in self._series if item.unit), "unit unavailable")
        painter.drawText(QRectF(4.0, plot_rect.top(), 48.0, 20.0), Qt.AlignmentFlag.AlignRight, unit)
        self._paint_focus_ring(painter)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._prepare_envelopes()
        super().resizeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            super().keyPressEvent(event)
            return
        source = self._series_by_id(self._selected_series_id)
        if source is None:
            return
        valid = self._valid_indices.get(source.series_id)
        if valid is None or valid.size == 0:
            return
        current = self._selected_sample_index
        if current is None:
            position = 0 if event.key() == Qt.Key.Key_Right else int(valid.size) - 1
        elif event.key() == Qt.Key.Key_Right:
            position = min(int(valid.size) - 1, int(np.searchsorted(valid, current, side="right")))
        else:
            position = max(0, int(np.searchsorted(valid, current, side="left")) - 1)
        index = int(valid[position])
        self._selected_sample_index = index
        self.update()
        self.sampleActivated.emit(source.series_id, index, float(source.time_s[index]))
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._plot_rect().contains(event.position())
        ):
            self._drag_start_x = float(event.position().x())
            self._activate_x(self._drag_start_x)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start_x is not None:
            end_x = float(event.position().x())
            if abs(end_x - self._drag_start_x) >= 5.0:
                bounds = self._data_bounds()
                if bounds is not None:
                    plot_rect = self._plot_rect()
                    start_s = self._time_for_x(self._drag_start_x, plot_rect, bounds[0], bounds[1])
                    end_s = self._time_for_x(end_x, plot_rect, bounds[0], bounds[1])
                    self.set_selected_range(start_s, end_s)
                    assert self._range_s is not None
                    self.rangeSelected.emit(*self._range_s)
            self._drag_start_x = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _activate_x(self, x: float) -> None:
        source = self._series_by_id(self._selected_series_id)
        bounds = self._data_bounds()
        if source is None or bounds is None:
            return
        plot_rect = self._plot_rect()
        target = self._time_for_x(x, plot_rect, bounds[0], bounds[1])
        valid = self._valid_indices.get(source.series_id)
        if valid is None or valid.size == 0:
            return
        times = source.time_s[valid]
        right = int(np.searchsorted(times, target, side="left"))
        if right <= 0:
            position = 0
        elif right >= int(times.size):
            position = int(times.size) - 1
        else:
            left = right - 1
            position = left if target - times[left] <= times[right] - target else right
        index = int(valid[position])
        self._selected_sample_index = index
        self.update()
        time_s = float(source.time_s[index])
        self.timeActivated.emit(time_s)
        self.sampleActivated.emit(source.series_id, index, time_s)

    def _prepare_envelopes(self) -> None:
        width_bucket = max(64, min(32_768, ((max(1, self.width() - 78) + 31) // 32) * 32))
        layers = self._series + ((self._fit_series,) if self._fit_series is not None else ())
        prepared: list[DecimatedSeries] = []
        for source in layers:
            key = (source.series_id, width_bucket)
            envelope = self._envelope_cache.get(key)
            if envelope is None:
                envelope = decimate_series(source, width_px=width_bucket)
                self._envelope_cache[key] = envelope
                while len(self._envelope_cache) > 24:
                    self._envelope_cache.popitem(last=False)
            else:
                self._envelope_cache.move_to_end(key)
            prepared.append(envelope)
        self._prepared = tuple(prepared)
        self._update_accessible_description()

    def _plot_rect(self) -> QRectF:
        legend_rows = (len(self._prepared) + 3) // 4
        top = 18.0 + max(0, legend_rows - 1) * 16.0
        return QRectF(
            58.0,
            top,
            max(1.0, self.width() - 78.0),
            max(1.0, self.height() - top - 34.0),
        )

    def _paint_legend(self, painter: QPainter) -> None:
        count = len(self._prepared)
        if not count:
            return
        columns = min(4, count)
        cell_width = max(1.0, (self.width() - 16.0) / columns)
        metrics = painter.fontMetrics()
        painter.setPen(QColor("#20272C"))
        for index, prepared in enumerate(self._prepared):
            row, column = divmod(index, 4)
            rect = QRectF(
                8.0 + column * cell_width,
                1.0 + row * 16.0,
                max(1.0, cell_width - 4.0),
                15.0,
            )
            text = metrics.elidedText(
                f"{index + 1} {prepared.name}",
                Qt.TextElideMode.ElideRight,
                max(1, int(rect.width())),
            )
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

    @staticmethod
    def _paint_layer_labels(
        painter: QPainter,
        plot_rect: QRectF,
        endpoints: Sequence[tuple[int, QPointF, QColor]],
    ) -> None:
        count = len(endpoints)
        if not count:
            return
        for order, (layer_index, endpoint, color) in enumerate(endpoints):
            center_y = plot_rect.top() + (order + 0.5) * plot_rect.height() / count
            label = QRectF(plot_rect.right() + 2.0, center_y - 7.0, 16.0, 14.0)
            painter.setPen(QPen(color, 0.8))
            painter.drawLine(endpoint, QPointF(label.left(), label.center().y()))
            painter.fillRect(label, QColor("#FBFCFC"))
            painter.setPen(QColor("#20272C"))
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter, str(layer_index + 1))

    def _update_accessible_description(self) -> None:
        if not self._prepared:
            self.setAccessibleDescription("No physical series is available to plot.")
            return
        unit = self._prepared[0].unit or "unit unavailable"
        labels = "; ".join(
            f"{index} {prepared.name}"
            for index, prepared in enumerate(self._prepared, 1)
        )
        self.setAccessibleDescription(
            f"{len(self._prepared)} visible plot layers against stored true time with "
            f"vertical unit {unit}: {labels}. "
            "Invalid samples are visible as line gaps. Use Left and Right Arrow to move the "
            "selected sample."
        )

    def _data_bounds(self) -> tuple[float, float, float, float] | None:
        finite_times: list[np.ndarray] = []
        finite_values: list[np.ndarray] = []
        for prepared in self._prepared:
            mask = np.isfinite(prepared.time_s) & np.isfinite(prepared.values)
            if mask.any():
                finite_times.append(prepared.time_s[mask])
                finite_values.append(prepared.values[mask])
        if not finite_times:
            return None
        time_min = min(float(values.min()) for values in finite_times)
        time_max = max(float(values.max()) for values in finite_times)
        value_min = min(float(values.min()) for values in finite_values)
        value_max = max(float(values.max()) for values in finite_values)
        if time_max <= time_min:
            time_max = time_min + 1.0
        if value_max <= value_min:
            padding = max(1.0, abs(value_min) * 0.05)
            value_min -= padding
            value_max += padding
        else:
            padding = (value_max - value_min) * 0.06
            value_min -= padding
            value_max += padding
        return time_min, time_max, value_min, value_max

    def _paint_cursor(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        bounds: tuple[float, float, float, float],
    ) -> None:
        source = self._series_by_id(self._selected_series_id)
        index = self._selected_sample_index
        if source is None or index is None or not 0 <= index < len(source):
            return
        time_s = float(source.time_s[index])
        if not math.isfinite(time_s):
            return
        x = self._x_for_time(time_s, plot_rect, bounds[0], bounds[1])
        painter.setPen(QPen(QColor("#C55232"), 1.5))
        painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))
        value = float(source.values[index])
        if math.isfinite(value):
            y = self._y_for_value(value, plot_rect, bounds[2], bounds[3])
            painter.setBrush(QColor("#C55232"))
            painter.drawEllipse(QPointF(x, y), 3.5, 3.5)
        painter.drawText(
            QRectF(max(plot_rect.left(), x - 62.0), plot_rect.top() + 4.0, 124.0, 18.0),
            Qt.AlignmentFlag.AlignCenter,
            f"{time_s:.6f} s",
        )

    def _paint_focus_ring(self, painter: QPainter) -> None:
        if not self.hasFocus():
            return
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#2F6F9F"), 2.0))
        painter.drawRect(QRectF(self.rect()).adjusted(1.0, 1.0, -2.0, -2.0))
        painter.restore()

    def _series_by_id(self, series_id: str | None) -> SampleSeries | None:
        if series_id is None:
            return None
        return next((item for item in self._series if item.series_id == series_id), None)

    @staticmethod
    def _x_for_time(time_s: float, rect: QRectF, minimum: float, maximum: float) -> float:
        return rect.left() + (time_s - minimum) / (maximum - minimum) * rect.width()

    @staticmethod
    def _y_for_value(value: float, rect: QRectF, minimum: float, maximum: float) -> float:
        return rect.bottom() - (value - minimum) / (maximum - minimum) * rect.height()

    @staticmethod
    def _time_for_x(x: float, rect: QRectF, minimum: float, maximum: float) -> float:
        fraction = (max(rect.left(), min(rect.right(), x)) - rect.left()) / rect.width()
        return minimum + fraction * (maximum - minimum)
