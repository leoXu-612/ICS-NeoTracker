from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from neo_tracker.core import TrackerResult


class ConfidencePlot(QWidget):
    """Compact, clickable plot used by the Review diagnostics panel.

    ``_values`` remains the confidence-series compatibility surface used by
    existing tests and integrations. Other diagnostic modes are provided via
    :meth:`set_series` without mutating tracking results.
    """

    frameActivated = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._values: list[tuple[int, float, str]] = []
        self._x_values: list[float] = []
        self._y_values: list[float] = []
        self._frame_targets: list[int | None] = []
        self._statuses: list[str] = []
        self._title = "Confidence"
        self._kind = "confidence"
        self._selected_frame: int | None = None
        self._selected_x: float | None = None
        self._fixed_y_range: tuple[float, float] | None = (0.0, 1.0)
        self._x_range: tuple[float, float] = (0.0, 1.0)
        self._resolved_y_range: tuple[float, float] = (0.0, 1.0)
        self._display_index_cache_key: tuple[int, int, int | None] | None = None
        self._display_index_cache: tuple[int, ...] = ()
        self._last_display_point_count = 0
        self._last_plot_rect: QRectF | None = None
        self.setMinimumHeight(86)
        self.setMaximumHeight(104)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Review diagnostic plot")
        self.setToolTip("Click a timeline point or use Left/Right to move to that frame.")

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def point_count(self) -> int:
        return len(self._x_values)

    @property
    def display_point_count(self) -> int:
        return int(self._last_display_point_count)

    def set_results(
        self,
        results: Sequence[TrackerResult],
        selected_frame: int | None = None,
        *,
        render: bool = True,
    ) -> None:
        self._values = [
            (int(result.frame_index), float(max(0.0, min(1.0, result.confidence))), result.status)
            for result in results
        ]
        if render:
            self.show_confidence(selected_frame)

    def refresh_result(self, result_index: int, result: TrackerResult) -> bool:
        """Refresh one confidence point after a copy-on-write review edit."""

        index = int(result_index)
        if index < 0 or index >= len(self._values):
            return False
        frame_index = int(result.frame_index)
        confidence = float(max(0.0, min(1.0, result.confidence)))
        status = result.status
        self._values[index] = (frame_index, confidence, status)
        if self._kind == "confidence" and index < len(self._x_values):
            self._x_values[index] = float(frame_index)
            self._y_values[index] = confidence
            self._frame_targets[index] = frame_index
            self._statuses[index] = status
            self._display_index_cache_key = None
            self._display_index_cache = ()
            self.update()
        return True

    def show_confidence(self, selected_frame: int | None = None) -> None:
        x_values: list[float] = []
        y_values: list[float] = []
        frame_targets: list[int | None] = []
        statuses: list[str] = []
        x_min = math.inf
        x_max = -math.inf
        for frame_index, confidence, status in self._values:
            x_value = float(frame_index)
            x_values.append(x_value)
            y_values.append(confidence)
            frame_targets.append(frame_index)
            statuses.append(status)
            x_min = min(x_min, x_value)
            x_max = max(x_max, x_value)
        self._apply_series_data(
            "Confidence timeline",
            x_values,
            y_values,
            frame_targets=frame_targets,
            statuses=statuses,
            kind="confidence",
            selected_frame=selected_frame,
            fixed_y_range=(0.0, 1.0),
            x_range=(x_min, x_max) if x_values else (0.0, 1.0),
            resolved_y_range=(0.0, 1.0),
            timeline=bool(frame_targets),
        )

    def set_prepared_confidence(
        self,
        values: list[tuple[int, float, str]],
        x_values: list[float],
        y_values: list[float],
        frame_targets: list[int | None],
        statuses: list[str],
        *,
        selected_frame: int | None,
        x_range: tuple[float, float],
    ) -> None:
        """Adopt worker-prepared confidence storage without GUI-thread rescans."""

        self._values = values
        self._set_prevalidated_series(
            "Confidence timeline",
            x_values,
            y_values,
            frame_targets=frame_targets,
            statuses=statuses,
            kind="confidence",
            selected_frame=selected_frame,
            fixed_y_range=(0.0, 1.0),
            x_range=x_range,
            resolved_y_range=(0.0, 1.0),
            timeline=bool(frame_targets),
        )

    def set_series(
        self,
        title: str,
        x_values: Sequence[float],
        y_values: Sequence[float],
        *,
        frame_targets: Sequence[int | None] | None = None,
        statuses: Sequence[str] | None = None,
        kind: str = "series",
        selected_frame: int | None = None,
        selected_x: float | None = None,
        fixed_y_range: tuple[float, float] | None = None,
    ) -> None:
        size = min(len(x_values), len(y_values))
        clean_x_values: list[float] = []
        clean_y_values: list[float] = []
        clean_frame_targets: list[int | None] = []
        clean_statuses: list[str] = []
        for index in range(size):
            x_value = float(x_values[index])
            y_value = float(y_values[index])
            if not math.isfinite(x_value) or not math.isfinite(y_value):
                continue
            clean_x_values.append(x_value)
            clean_y_values.append(y_value)
            clean_frame_targets.append(
                frame_targets[index]
                if frame_targets is not None and index < len(frame_targets)
                else None
            )
            clean_statuses.append(
                statuses[index] if statuses is not None and index < len(statuses) else ""
            )
        self._apply_series_data(
            title,
            clean_x_values,
            clean_y_values,
            frame_targets=clean_frame_targets,
            statuses=clean_statuses,
            kind=kind,
            selected_frame=selected_frame,
            selected_x=selected_x,
            fixed_y_range=fixed_y_range,
        )

    def _set_prevalidated_series(
        self,
        title: str,
        x_values: list[float],
        y_values: list[float],
        *,
        frame_targets: list[int | None],
        statuses: list[str],
        kind: str,
        selected_frame: int | None,
        fixed_y_range: tuple[float, float] | None = None,
        x_range: tuple[float, float] | None = None,
        resolved_y_range: tuple[float, float] | None = None,
        timeline: bool | None = None,
    ) -> None:
        """Adopt validated immutable-by-contract lists from the diagnostics cache."""

        self._apply_series_data(
            title,
            x_values,
            y_values,
            frame_targets=frame_targets,
            statuses=statuses,
            kind=kind,
            selected_frame=selected_frame,
            fixed_y_range=fixed_y_range,
            x_range=x_range,
            resolved_y_range=resolved_y_range,
            timeline=timeline,
        )

    def _apply_series_data(
        self,
        title: str,
        x_values: list[float],
        y_values: list[float],
        *,
        frame_targets: list[int | None],
        statuses: list[str],
        kind: str,
        selected_frame: int | None,
        selected_x: float | None = None,
        fixed_y_range: tuple[float, float] | None = None,
        x_range: tuple[float, float] | None = None,
        resolved_y_range: tuple[float, float] | None = None,
        timeline: bool | None = None,
    ) -> None:
        self._x_values = x_values
        self._y_values = y_values
        self._frame_targets = frame_targets
        self._statuses = statuses
        self._title = str(title)
        self._kind = str(kind)
        self._selected_frame = int(selected_frame) if selected_frame is not None else None
        self._selected_x = float(selected_x) if selected_x is not None else None
        self._fixed_y_range = fixed_y_range
        self._x_range = x_range or (
            (min(x_values), max(x_values)) if x_values else (0.0, 1.0)
        )
        self._resolved_y_range = resolved_y_range or self._calculate_y_range()
        self._display_index_cache_key = None
        self._display_index_cache = ()
        self._last_display_point_count = 0
        has_timeline = (
            any(frame is not None for frame in frame_targets) if timeline is None else bool(timeline)
        )
        interaction_tip = (
            "Click a timeline point or use Left/Right to move to that frame."
            if has_timeline
            else "Selected-frame diagnostic; choose another result row to update it."
        )
        display_tip = (
            " Dense timelines retain the first, last, selected, and per-pixel minimum/maximum points "
            "for display; the full data remains available for navigation."
            if len(self._x_values) > 2000
            else ""
        )
        self.setToolTip(f"{interaction_tip}{display_tip}")
        self.setAccessibleDescription(
            f"{self._title}. {len(self._x_values):,} data points.{display_tip}"
        )
        self.update()

    def set_selected_frame(self, frame_index: int | None) -> None:
        self._selected_frame = int(frame_index) if frame_index is not None else None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(8.0, 6.0, -8.0, -6.0)
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d4dde8"), 1))
        painter.drawRoundedRect(rect, 5.0, 5.0)
        plot = rect.adjusted(36.0, 18.0, -10.0, -20.0)
        self._last_plot_rect = plot
        painter.setPen(QColor("#526071"))
        painter.drawText(QPointF(rect.left() + 8.0, rect.top() + 15.0), self._title)
        if not self._x_values:
            painter.setPen(QColor("#6b7787"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "No diagnostic data")
            painter.end()
            return

        y_min, y_max = self._resolved_y_range
        x_min, x_max = self._x_range
        painter.setPen(QPen(QColor("#e3e9f1"), 1))
        for ratio in (0.25, 0.5, 0.75):
            y = plot.bottom() - plot.height() * ratio
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        if y_min < 0.0 < y_max:
            zero_y = self._map_y(0.0, plot, y_min, y_max)
            zero_pen = QPen(QColor("#c7d0dc"), 1)
            zero_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(zero_pen)
            painter.drawLine(QPointF(plot.left(), zero_y), QPointF(plot.right(), zero_y))

        display_indices = self._display_indices(plot.width())
        self._last_display_point_count = len(display_indices)
        points = [
            QPointF(
                self._map_x(self._x_values[index], plot, x_min, x_max),
                self._map_y(self._y_values[index], plot, y_min, y_max),
            )
            for index in display_indices
        ]
        painter.setPen(QPen(QColor("#007c89"), 1.8))
        line_path = QPainterPath()
        if points:
            line_path.moveTo(points[0])
            for point in points[1:]:
                line_path.lineTo(point)
            painter.drawPath(line_path)
        for point, index in zip(points, display_indices):
            y_value = self._y_values[index]
            status = self._statuses[index]
            frame_target = self._frame_targets[index]
            color = self._point_color(y_value, status)
            selected = frame_target is not None and frame_target == self._selected_frame
            if selected:
                painter.setPen(QPen(QColor("#ffb020"), 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(point, 5.2, 5.2)
            painter.setPen(QPen(color, 1))
            painter.setBrush(color)
            painter.drawEllipse(point, 2.6 if selected else 2.2, 2.6 if selected else 2.2)

        if self._selected_x is not None:
            selected_x = self._map_x(self._selected_x, plot, x_min, x_max)
            marker = QPen(QColor("#ffb020"), 1.5)
            marker.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(marker)
            painter.drawLine(QPointF(selected_x, plot.top()), QPointF(selected_x, plot.bottom()))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QColor("#526071"))
        painter.drawText(QPointF(plot.left(), rect.bottom() - 5.0), self._x_label(self._x_values[0]))
        right_label = self._x_label(self._x_values[-1])
        painter.drawText(QPointF(plot.right() - max(26.0, len(right_label) * 6.5), rect.bottom() - 5.0), right_label)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            frame_index = self._frame_at_x(event.position().x())
            if frame_index is not None:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.frameActivated.emit(frame_index)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right}:
            frames = [frame for frame in self._frame_targets if frame is not None]
            if frames:
                current = self._selected_frame
                if current in frames:
                    index = frames.index(current)
                else:
                    index = 0
                index += -1 if event.key() == Qt.Key.Key_Left else 1
                index = max(0, min(index, len(frames) - 1))
                self.frameActivated.emit(frames[index])
                event.accept()
                return
        super().keyPressEvent(event)

    def _frame_at_x(self, widget_x: float) -> int | None:
        plot = self._last_plot_rect
        if plot is None:
            return None
        x_min, x_max = self._x_range
        if plot.width() <= 0.0:
            return None
        ratio = max(0.0, min(1.0, (float(widget_x) - plot.left()) / plot.width()))
        target_x = x_min + ratio * (x_max - x_min)
        nearest: tuple[float, int] | None = None
        for x_value, frame in zip(self._x_values, self._frame_targets):
            if frame is None:
                continue
            distance = abs(x_value - target_x)
            if nearest is None or distance < nearest[0]:
                nearest = distance, int(frame)
        return nearest[1] if nearest is not None else None

    @staticmethod
    def _map_x(value: float, plot: QRectF, x_min: float, x_max: float) -> float:
        if x_max - x_min <= 1e-12:
            return plot.center().x()
        return plot.left() + plot.width() * ((value - x_min) / (x_max - x_min))

    @staticmethod
    def _map_y(value: float, plot: QRectF, y_min: float, y_max: float) -> float:
        if y_max - y_min <= 1e-12:
            return plot.center().y()
        return plot.bottom() - plot.height() * ((value - y_min) / (y_max - y_min))

    def _calculate_y_range(self) -> tuple[float, float]:
        if self._fixed_y_range is not None:
            return self._fixed_y_range
        if not self._y_values:
            return (0.0, 1.0)
        y_min = min(self._y_values)
        y_max = max(self._y_values)
        if y_max - y_min <= 1e-12:
            padding = max(1.0, abs(y_min) * 0.1)
        else:
            padding = (y_max - y_min) * 0.12
        return y_min - padding, y_max + padding

    def _display_indices(self, plot_width: float) -> tuple[int, ...]:
        point_count = len(self._x_values)
        if point_count == 0:
            return ()
        pixel_columns = max(1, int(round(max(1.0, plot_width))))
        cache_key = (point_count, pixel_columns, self._selected_frame)
        if self._display_index_cache_key == cache_key:
            return self._display_index_cache
        if point_count <= pixel_columns * 2:
            indices = tuple(range(point_count))
        else:
            selected: set[int] = {0, point_count - 1}
            for bucket in range(pixel_columns):
                start = bucket * point_count // pixel_columns
                end = max(start + 1, (bucket + 1) * point_count // pixel_columns)
                end = min(point_count, end)
                bucket_indices = range(start, end)
                selected.add(start)
                selected.add(end - 1)
                selected.add(min(bucket_indices, key=self._y_values.__getitem__))
                selected.add(max(range(start, end), key=self._y_values.__getitem__))
                special_status = next(
                    (
                        index
                        for index in range(start, end)
                        if self._statuses[index] in {"lost", "manual_lost", "manual"}
                    ),
                    None,
                )
                if special_status is not None:
                    selected.add(special_status)
            if self._selected_frame is not None:
                try:
                    selected.add(self._frame_targets.index(self._selected_frame))
                except ValueError:
                    pass
            indices = tuple(sorted(selected))
        self._display_index_cache_key = cache_key
        self._display_index_cache = indices
        return indices

    def _point_color(self, value: float, status: str) -> QColor:
        if self._kind != "confidence":
            return QColor("#007c89")
        return self._confidence_color(value, status)

    def _x_label(self, value: float) -> str:
        if self._kind == "angular_response":
            return f"{value:.0f}°"
        return str(int(round(value)))

    @staticmethod
    def _confidence_color(confidence: float, status: str) -> QColor:
        if status in {"lost", "manual_lost"}:
            return QColor("#94a3b8")
        if status == "manual":
            return QColor("#2563eb")
        if confidence < 0.4:
            return QColor("#dc2626")
        if confidence < 0.7:
            return QColor("#d97706")
        return QColor("#16a34a")
