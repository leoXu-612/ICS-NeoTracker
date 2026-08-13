from __future__ import annotations

from copy import deepcopy
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QLabel


def _response_overlay_rgba(
    response_map: np.ndarray,
    frame_size: tuple[int, int],
    output_size: tuple[int, int] | None = None,
) -> np.ndarray | None:
    """Colorize a response map at full or display-bounded resolution.

    Response maps are normally float32. Keeping the normalization in float32
    avoids the previous implicit float64 copy, while one reusable scratch plane
    prevents four additional channel-sized arithmetic temporaries. When the
    preview is smaller than the source frame, direct bilinear sampling limits
    all colorization planes to the physical display size while retaining the
    source-wide normalization range.
    """
    frame_width, frame_height = frame_size
    response = np.asarray(response_map)
    if response.ndim != 2 or response.shape != (frame_height, frame_width):
        return None
    if frame_width <= 0 or frame_height <= 0:
        return None
    source = np.asarray(response, dtype=np.float32)
    min_value = float(source.min())
    max_value = float(source.max())
    finite: np.ndarray | None = None
    if not np.isfinite(min_value) or not np.isfinite(max_value):
        finite = np.isfinite(source)
        if not np.any(finite):
            return None
        min_value = float(np.min(source, where=finite, initial=np.inf))
        max_value = float(np.max(source, where=finite, initial=-np.inf))
    value_range = max_value - min_value
    if value_range <= 1e-12:
        return None

    output_width, output_height = frame_width, frame_height
    if output_size is not None:
        output_width = min(frame_width, max(1, int(output_size[0])))
        output_height = min(frame_height, max(1, int(output_size[1])))

    working = source
    if (output_width, output_height) != (frame_width, frame_height):
        if finite is not None:
            working = np.array(source, dtype=np.float32, copy=True, order="C")
            working[~finite] = 0.0
        working = _resize_response_bilinear(working, (output_width, output_height))

    normalized = np.array(working, dtype=np.float32, copy=True, order="C")
    if finite is not None and working is source:
        normalized[~finite] = 0.0
    normalized -= np.float32(min_value)
    normalized *= np.float32(1.0 / value_range)
    np.clip(normalized, 0.0, 1.0, out=normalized)

    rgba = np.empty((output_height, output_width, 4), dtype=np.uint8)
    scratch = np.empty_like(normalized)

    np.multiply(normalized, np.float32(195.0), out=scratch)
    scratch += np.float32(60.0)
    np.clip(scratch, 0.0, 255.0, out=scratch)
    rgba[:, :, 0] = scratch

    np.multiply(normalized, np.float32(118.0), out=scratch)
    np.clip(scratch, 0.0, 255.0, out=scratch)
    rgba[:, :, 1] = scratch

    np.multiply(normalized, np.float32(-210.0), out=scratch)
    scratch += np.float32(255.0)
    np.clip(scratch, 0.0, 255.0, out=scratch)
    rgba[:, :, 2] = scratch

    np.multiply(normalized, np.float32(135.0), out=scratch)
    np.clip(scratch, 0.0, 135.0, out=scratch)
    rgba[:, :, 3] = scratch
    return rgba


def _resize_response_bilinear(
    source: np.ndarray,
    output_size: tuple[int, int],
) -> np.ndarray:
    """Sample a 2D float response at destination-pixel centers."""
    output_width, output_height = output_size
    source_height, source_width = source.shape
    if (output_width, output_height) == (source_width, source_height):
        return source

    x = (np.arange(output_width, dtype=np.float32) + np.float32(0.5)) * np.float32(
        source_width / output_width
    ) - np.float32(0.5)
    y = (np.arange(output_height, dtype=np.float32) + np.float32(0.5)) * np.float32(
        source_height / output_height
    ) - np.float32(0.5)
    np.clip(x, 0.0, float(source_width - 1), out=x)
    np.clip(y, 0.0, float(source_height - 1), out=y)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, source_width - 1)
    y1 = np.minimum(y0 + 1, source_height - 1)
    x_weight = x - x0
    y_weight = y - y0
    inverse_x = np.float32(1.0) - x_weight
    inverse_y = np.float32(1.0) - y_weight

    output = source[np.ix_(y0, x0)]
    output *= inverse_x[None, :]
    output *= inverse_y[:, None]
    scratch = source[np.ix_(y0, x1)]
    scratch *= x_weight[None, :]
    scratch *= inverse_y[:, None]
    output += scratch
    scratch = source[np.ix_(y1, x0)]
    scratch *= inverse_x[None, :]
    scratch *= y_weight[:, None]
    output += scratch
    scratch = source[np.ix_(y1, x1)]
    scratch *= x_weight[None, :]
    scratch *= y_weight[:, None]
    output += scratch
    return output


class PreviewCanvas(QLabel):
    DEFAULT_ACCESSIBLE_DESCRIPTION = (
        "Video preview. Select and drag ROI nodes; arrow keys move a selected node by 1 pixel "
        "and Shift moves 10 pixels."
    )

    roiSelected = Signal(object)
    calibrationRodSelected = Signal(object)
    manualPointSelected = Signal(object)
    colorSampleSelected = Signal(object)
    selectionModeChanged = Signal(object)
    roiNodeSelected = Signal(object)
    roiNodeMoved = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._frame_pixmap: QPixmap | None = None
        self._frame_size: tuple[int, int] | None = None
        self.roi_rect: tuple[float, float, float, float] | None = None
        self.roi_config: dict[str, object] | None = None
        self.calibration_line: tuple[tuple[float, float], tuple[float, float]] | None = None
        self.calibration_has_axis = False
        self.calibration_y_positive = "up"
        self.trajectory_points: list[tuple[float, float]] = []
        self.measurement_points: list[tuple[float, float]] = []
        self.candidate_points: list[tuple[float, float, float, bool]] = []
        self.current_tracking_point: tuple[float, float] | None = None
        self.observation_point: tuple[float, float] | None = None
        self.prediction_point: tuple[float, float] | None = None
        self.response_map: np.ndarray | None = None
        self._response_overlay_cache_ready = False
        self._response_overlay_cache_source: np.ndarray | None = None
        self._response_overlay_cache_frame_size: tuple[int, int] | None = None
        self._response_overlay_cache_output_size: tuple[int, int] | None = None
        self._response_overlay_cache_image: QImage | None = None
        self.editable_roi_config: dict[str, object] | None = None
        self.editable_roi_applied_config: dict[str, object] | None = None
        self.selected_roi_node_index: int | None = None
        self._dragged_roi_node_index: int | None = None
        self._dragged_roi_node_origin: QPointF | None = None
        self._dragged_roi_node_has_moved = False
        self._selection_mode: str | None = None
        self._drag_start: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._polygon_points: list[tuple[float, float]] = []
        self._curve_band_points: list[tuple[float, float]] = []
        self._curve_band_half_width: float = 24.0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Video preview and ROI editor")
        self.setAccessibleDescription(self.DEFAULT_ACCESSIBLE_DESCRIPTION)

    def has_frame(self) -> bool:
        return self._frame_pixmap is not None and self._frame_size is not None

    def selection_mode(self) -> str | None:
        return self._selection_mode

    def set_frame(self, frame: np.ndarray) -> None:
        self._set_frame_image(frame, QImage.Format.Format_RGB888)

    def set_bgr_frame(self, frame: np.ndarray) -> None:
        """Display an OpenCV BGR frame without a full-frame RGB conversion."""

        self._set_frame_image(frame, QImage.Format.Format_BGR888)

    def _set_frame_image(self, frame: np.ndarray, image_format: QImage.Format) -> None:
        pixels = np.ascontiguousarray(frame)
        height, width = pixels.shape[:2]
        if self._frame_size != (width, height):
            self._invalidate_response_overlay_cache()
        bytes_per_line = int(pixels.strides[0])
        image = QImage(pixels.data, width, height, bytes_per_line, image_format).copy()
        self._frame_pixmap = QPixmap.fromImage(image)
        self._frame_size = (width, height)
        self.setText("")
        self.setToolTip("")
        self.setAccessibleDescription(self.DEFAULT_ACCESSIBLE_DESCRIPTION)
        self.setPixmap(QPixmap())
        self.update()

    def clear_message(self, text: str, detail: str | None = None) -> None:
        self._frame_pixmap = None
        self._frame_size = None
        self._set_selection_mode(None)
        self._drag_start = None
        self._drag_current = None
        self._polygon_points = []
        self._curve_band_points = []
        self.roi_config = None
        self.roi_rect = None
        self.trajectory_points = []
        self.measurement_points = []
        self.candidate_points = []
        self.current_tracking_point = None
        self.observation_point = None
        self.prediction_point = None
        self.response_map = None
        self._invalidate_response_overlay_cache()
        self.editable_roi_config = None
        self.editable_roi_applied_config = None
        self.selected_roi_node_index = None
        self._dragged_roi_node_index = None
        self._dragged_roi_node_origin = None
        self._dragged_roi_node_has_moved = False
        self.setPixmap(QPixmap())
        self.setText(text)
        description = str(detail or text).strip()
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        self.unsetCursor()
        self.update()

    def set_roi_rect(self, rect: tuple[float, float, float, float] | None) -> None:
        self.roi_rect = rect
        self.roi_config = (
            {"type": "rectangle", "x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}
            if rect is not None
            else None
        )
        self.update()

    def set_roi_config(self, config: dict[str, object] | None) -> None:
        self.roi_config = dict(config) if isinstance(config, dict) else None
        self.roi_rect = self._rect_from_roi_config(self.roi_config)
        self.update()

    def set_editable_roi_config(
        self,
        config: dict[str, object] | None,
        selected_node_index: int | None = None,
        applied_config: dict[str, object] | None = None,
    ) -> None:
        roi_type = config.get("type") if isinstance(config, dict) else None
        if roi_type not in {"polygon", "curve_band"}:
            self.editable_roi_config = None
            self.editable_roi_applied_config = None
            self.selected_roi_node_index = None
            self._dragged_roi_node_index = None
            self._dragged_roi_node_origin = None
            self._dragged_roi_node_has_moved = False
            if self._selection_mode is None:
                self.unsetCursor()
            self.update()
            return
        editable = deepcopy(config)
        points = self._editable_roi_points(editable)
        self.editable_roi_config = editable
        self.editable_roi_applied_config = deepcopy(applied_config) if isinstance(applied_config, dict) else None
        if selected_node_index is None or not 0 <= int(selected_node_index) < len(points):
            self.selected_roi_node_index = None
        else:
            self.selected_roi_node_index = int(selected_node_index)
        self.update()

    def set_selected_roi_node(self, index: int | None) -> None:
        points = self._editable_roi_points(self.editable_roi_config)
        if index is None or not 0 <= int(index) < len(points):
            self.selected_roi_node_index = None
        else:
            self.selected_roi_node_index = int(index)
        self.update()

    def set_calibration_line(self, line: tuple[tuple[float, float], tuple[float, float]] | None) -> None:
        self.calibration_line = line
        self.update()

    def set_calibration_axis(self, available: bool, y_positive: str = "up") -> None:
        self.calibration_has_axis = bool(available)
        self.calibration_y_positive = "down" if y_positive == "down" else "up"
        self.update()

    def set_tracking_overlay(
        self,
        trajectory_points: list[tuple[float, float]],
        current_point: tuple[float, float] | None,
        observation_point: tuple[float, float] | None = None,
        response_map: np.ndarray | None = None,
        candidate_points: list[tuple[float, float, float, bool]] | None = None,
        prediction_point: tuple[float, float] | None = None,
        measurement_points: list[tuple[float, float]] | None = None,
    ) -> None:
        self.trajectory_points = list(trajectory_points)
        self.current_tracking_point = current_point
        self.observation_point = observation_point
        # Treat every overlay handoff as a new revision. Built-in results use
        # immutable arrays, while this also keeps custom/plugin observations
        # correct if they reuse and mutate one array object between handoffs.
        self._invalidate_response_overlay_cache()
        self.response_map = response_map
        self.candidate_points = list(candidate_points or [])
        self.prediction_point = prediction_point
        self.measurement_points = list(measurement_points or [])
        self.update()

    def begin_roi_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("roi_rectangle")

    def begin_circular_roi_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("roi_circle")

    def begin_annular_roi_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("roi_annulus")

    def begin_polygon_roi_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("roi_polygon")

    def begin_curve_band_roi_selection(self, half_width: float = 24.0) -> bool:
        if not self.has_frame():
            return False
        self._curve_band_half_width = max(1.0, float(half_width))
        return self._begin_selection("roi_curve_band")

    def finish_polygon_roi_selection(self) -> bool:
        if self._selection_mode != "roi_polygon" or len(self._polygon_points) < 3:
            return False
        points = [[float(x), float(y)] for x, y in self._polygon_points]
        self.roiSelected.emit({"type": "polygon", "points": points})
        self.cancel_selection()
        return True

    def finish_curve_band_roi_selection(self) -> bool:
        if self._selection_mode != "roi_curve_band" or len(self._curve_band_points) < 2:
            return False
        polyline = [[float(x), float(y)] for x, y in self._curve_band_points]
        self.roiSelected.emit(
            {"type": "curve_band", "polyline": polyline, "half_width": float(self._curve_band_half_width)}
        )
        self.cancel_selection()
        return True

    def cancel_selection(self) -> None:
        self._set_selection_mode(None)
        self._dragged_roi_node_index = None
        self._dragged_roi_node_origin = None
        self._dragged_roi_node_has_moved = False
        self._drag_start = None
        self._drag_current = None
        self._polygon_points = []
        self._curve_band_points = []
        self.unsetCursor()
        self.update()

    def begin_calibration_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("calibration")

    def begin_manual_point_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("manual_point")

    def begin_color_sample_selection(self) -> bool:
        if not self.has_frame():
            return False
        return self._begin_selection("sample_color")

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._frame_pixmap is None or self._frame_size is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = self._display_rect()
        painter.drawPixmap(target.toRect(), self._frame_pixmap)
        self._paint_response_map(painter)
        if self.editable_roi_config is None:
            self._paint_roi_config(painter, self.roi_config, QColor("#25d366"), "ROI")
        else:
            applied_config = self.editable_roi_applied_config or self.roi_config
            if applied_config is not None and applied_config != self.editable_roi_config:
                applied_color = QColor("#25d366")
                applied_color.setAlpha(150)
                self._paint_roi_config(
                    painter,
                    applied_config,
                    applied_color,
                    "Applied",
                    label_below=True,
                )
            self._paint_roi_config(
                painter,
                self.editable_roi_config,
                QColor("#4dabf7"),
                "Editable ROI",
                selected_node_index=self.selected_roi_node_index,
                editable_nodes=True,
            )
        self._paint_calibration_line(painter, self.calibration_line, QColor("#4dabf7"), "Calibration")
        self._paint_tracking_overlay(painter)
        if self._drag_start is not None and self._drag_current is not None:
            if self._selection_mode == "roi_rectangle":
                drag_rect = self._normalized_image_rect(self._drag_start, self._drag_current)
                self._paint_roi(painter, drag_rect, QColor("#ffd166"), "Selecting")
            elif self._selection_mode in {"roi_circle", "roi_annulus"}:
                radius = self._distance(self._drag_start, self._drag_current)
                config: dict[str, object] = {
                    "type": "circle" if self._selection_mode == "roi_circle" else "annulus",
                    "center": [self._drag_start[0], self._drag_start[1]],
                    "radius": radius,
                    "outer_radius": radius,
                    "inner_radius": max(1.0, radius * 0.65),
                }
                self._paint_roi_config(painter, config, QColor("#ffd166"), "Selecting")
            elif self._selection_mode == "calibration":
                self._paint_calibration_line(
                    painter,
                    (self._drag_start, self._drag_current),
                    QColor("#ffd166"),
                    "Selecting",
                )
            elif self._selection_mode == "manual_point":
                self._paint_tracking_point(painter, self._drag_current, QColor("#ffd166"), "Correction")
            elif self._selection_mode == "sample_color":
                self._paint_tracking_point(painter, self._drag_current, QColor("#ffd166"), "Sample")
        if self._selection_mode == "roi_polygon":
            preview_points = list(self._polygon_points)
            if self._drag_current is not None:
                preview_points.append(self._drag_current)
            self._paint_polygon_roi(painter, preview_points, QColor("#ffd166"), "Selecting", close=False)
        elif self._selection_mode == "roi_curve_band":
            preview_points = list(self._curve_band_points)
            if self._drag_current is not None:
                preview_points.append(self._drag_current)
            self._paint_curve_band_roi(
                painter,
                preview_points,
                self._curve_band_half_width,
                QColor("#ffd166"),
                "Selecting",
            )
        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key_deltas = {
            Qt.Key.Key_Left: (-1.0, 0.0),
            Qt.Key.Key_Right: (1.0, 0.0),
            Qt.Key.Key_Up: (0.0, -1.0),
            Qt.Key.Key_Down: (0.0, 1.0),
        }
        modifiers = event.modifiers()
        allowed_modifiers = {Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier}
        if (
            self._selection_mode is None
            and self.selected_roi_node_index is not None
            and event.key() in key_deltas
            and modifiers in allowed_modifiers
        ):
            points = self._editable_roi_points(self.editable_roi_config)
            index = self.selected_roi_node_index
            if 0 <= index < len(points):
                step = 10.0 if modifiers == Qt.KeyboardModifier.ShiftModifier else 1.0
                dx, dy = key_deltas[event.key()]
                x = points[index][0] + dx * step
                y = points[index][1] + dy * step
                if self._frame_size is not None:
                    frame_width, frame_height = self._frame_size
                    max_x = max(0.0, float(frame_width - 1))
                    max_y = max(0.0, float(frame_height - 1))
                    if 0.0 <= points[index][0] <= max_x:
                        x = min(max(x, 0.0), max_x)
                    if 0.0 <= points[index][1] <= max_y:
                        y = min(max(y, 0.0), max_y)
                point = (round(x, 3), round(y, 3))
                if self._move_editable_roi_node(index, point):
                    self.roiNodeMoved.emit((index, point))
                    self.update()
                    event.accept()
                    return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._selection_mode in {"roi_polygon", "roi_curve_band"}:
            if event.button() == Qt.MouseButton.RightButton:
                self._finish_polyline_selection()
                event.accept()
                return
            if event.button() != Qt.MouseButton.LeftButton:
                super().mousePressEvent(event)
                return
            point = self._widget_to_image(event.position(), clamp=True)
            if point is None:
                return
            if self._selection_mode == "roi_polygon":
                self._polygon_points.append(point)
            else:
                self._curve_band_points.append(point)
            self._drag_current = point
            self.update()
            event.accept()
            return
        if self._selection_mode is None and event.button() == Qt.MouseButton.LeftButton:
            node_index = self._editable_roi_node_at(event.position())
            if node_index is not None:
                self.selected_roi_node_index = node_index
                self._dragged_roi_node_index = node_index
                self._dragged_roi_node_origin = QPointF(event.position())
                self._dragged_roi_node_has_moved = False
                self.roiNodeSelected.emit(node_index)
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                event.accept()
                return
            if self.editable_roi_config is not None:
                self.selected_roi_node_index = None
                self.roiNodeSelected.emit(None)
                self.update()
        if (
            self._selection_mode
            not in {"roi_rectangle", "roi_circle", "roi_annulus", "calibration", "manual_point", "sample_color"}
            or event.button() != Qt.MouseButton.LeftButton
        ):
            super().mousePressEvent(event)
            return
        point = self._widget_to_image(event.position())
        if point is None:
            return
        self._drag_start = point
        self._drag_current = point
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._selection_mode in {"roi_polygon", "roi_curve_band"}:
            point = self._widget_to_image(event.position(), clamp=True)
            if point is None:
                return
            self._drag_current = point
            self.update()
            event.accept()
            return
        if self._selection_mode is None and self._dragged_roi_node_index is not None:
            if not self._dragged_roi_node_has_moved and self._dragged_roi_node_origin is not None:
                distance = float(
                    np.hypot(
                        event.position().x() - self._dragged_roi_node_origin.x(),
                        event.position().y() - self._dragged_roi_node_origin.y(),
                    )
                )
                if distance < 2.0:
                    event.accept()
                    return
                self._dragged_roi_node_has_moved = True
            point = self._widget_to_image(event.position(), clamp=True)
            if point is None:
                return
            node_index = self._dragged_roi_node_index
            if self._move_editable_roi_node(node_index, point):
                self.roiNodeMoved.emit((node_index, point))
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
            event.accept()
            return
        if self._selection_mode is None and self.editable_roi_config is not None:
            if self._editable_roi_node_at(event.position()) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
        if (
            self._selection_mode
            not in {"roi_rectangle", "roi_circle", "roi_annulus", "calibration", "manual_point", "sample_color"}
            or self._drag_start is None
        ):
            super().mouseMoveEvent(event)
            return
        point = self._widget_to_image(event.position(), clamp=True)
        if point is None:
            return
        self._drag_current = point
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._selection_mode in {"roi_polygon", "roi_curve_band"}:
            event.accept()
            return
        if (
            self._selection_mode is None
            and self._dragged_roi_node_index is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            node_index = self._dragged_roi_node_index
            moved = self._dragged_roi_node_has_moved
            if not moved and self._dragged_roi_node_origin is not None:
                moved = float(
                    np.hypot(
                        event.position().x() - self._dragged_roi_node_origin.x(),
                        event.position().y() - self._dragged_roi_node_origin.y(),
                    )
                ) >= 2.0
            point = self._widget_to_image(event.position(), clamp=True) if moved else None
            if point is not None and self._move_editable_roi_node(node_index, point):
                self.roiNodeMoved.emit((node_index, point))
            self._dragged_roi_node_index = None
            self._dragged_roi_node_origin = None
            self._dragged_roi_node_has_moved = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            event.accept()
            return
        if (
            self._selection_mode
            not in {"roi_rectangle", "roi_circle", "roi_annulus", "calibration", "manual_point", "sample_color"}
            or event.button() != Qt.MouseButton.LeftButton
        ):
            super().mouseReleaseEvent(event)
            return
        point = self._widget_to_image(event.position(), clamp=True)
        if point is not None and self._drag_start is not None:
            if self._selection_mode == "roi_rectangle":
                rect = self._normalized_image_rect(self._drag_start, point)
                if rect[2] >= 3.0 and rect[3] >= 3.0:
                    self.roiSelected.emit(rect)
            elif self._selection_mode == "roi_circle":
                radius = self._distance(self._drag_start, point)
                if radius >= 3.0:
                    self.roiSelected.emit(
                        {
                            "type": "circle",
                            "center": [self._drag_start[0], self._drag_start[1]],
                            "radius": radius,
                        }
                    )
            elif self._selection_mode == "roi_annulus":
                outer_radius = self._distance(self._drag_start, point)
                if outer_radius >= 6.0:
                    self.roiSelected.emit(
                        {
                            "type": "annulus",
                            "center": [self._drag_start[0], self._drag_start[1]],
                            "inner_radius": max(1.0, outer_radius * 0.65),
                            "outer_radius": outer_radius,
                        }
                    )
            elif self._selection_mode == "calibration":
                length = float(np.linalg.norm(np.asarray(point) - np.asarray(self._drag_start)))
                if length >= 3.0:
                    self.calibrationRodSelected.emit((self._drag_start, point))
            elif self._selection_mode == "manual_point":
                self.manualPointSelected.emit(point)
            elif self._selection_mode == "sample_color":
                self.colorSampleSelected.emit(point)
        self.cancel_selection()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._selection_mode is None and self._dragged_roi_node_index is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._selection_mode in {"roi_polygon", "roi_curve_band"} and event.button() == Qt.MouseButton.LeftButton:
            point = self._widget_to_image(event.position(), clamp=True)
            points = self._active_polyline_points()
            if point is not None and (not points or self._distance(points[-1], point) > 1.0):
                points.append(point)
            self._finish_polyline_selection()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _begin_selection(self, mode: str) -> bool:
        self._set_selection_mode(mode)
        self._dragged_roi_node_index = None
        self._dragged_roi_node_origin = None
        self._dragged_roi_node_has_moved = False
        self._drag_start = None
        self._drag_current = None
        self._polygon_points = []
        self._curve_band_points = []
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()
        return True

    def _set_selection_mode(self, mode: str | None) -> None:
        if self._selection_mode == mode:
            return
        self._selection_mode = mode
        self.selectionModeChanged.emit(mode)

    def _finish_polyline_selection(self) -> bool:
        if self._selection_mode == "roi_polygon":
            return self.finish_polygon_roi_selection()
        if self._selection_mode == "roi_curve_band":
            return self.finish_curve_band_roi_selection()
        return False

    def _active_polyline_points(self) -> list[tuple[float, float]]:
        if self._selection_mode == "roi_curve_band":
            return self._curve_band_points
        return self._polygon_points

    def _display_rect(self) -> QRectF:
        if self._frame_size is None:
            return QRectF()
        frame_w, frame_h = self._frame_size
        contents = self.contentsRect()
        if frame_w <= 0 or frame_h <= 0 or contents.width() <= 0 or contents.height() <= 0:
            return QRectF()
        scale = min(contents.width() / frame_w, contents.height() / frame_h)
        width = frame_w * scale
        height = frame_h * scale
        left = contents.left() + (contents.width() - width) * 0.5
        top = contents.top() + (contents.height() - height) * 0.5
        return QRectF(left, top, width, height)

    def _widget_to_image(self, point: QPointF, clamp: bool = False) -> tuple[float, float] | None:
        if self._frame_size is None:
            return None
        target = self._display_rect()
        if target.width() <= 0 or target.height() <= 0:
            return None
        if not clamp and not target.contains(point):
            return None
        frame_w, frame_h = self._frame_size
        x = (point.x() - target.left()) / target.width() * frame_w
        y = (point.y() - target.top()) / target.height() * frame_h
        if clamp:
            x = max(0.0, min(float(frame_w), x))
            y = max(0.0, min(float(frame_h), y))
        return x, y

    def _image_rect_to_widget(self, rect: tuple[float, float, float, float]) -> QRectF | None:
        if self._frame_size is None:
            return None
        frame_w, frame_h = self._frame_size
        if frame_w <= 0 or frame_h <= 0:
            return None
        target = self._display_rect()
        x, y, width, height = rect
        return QRectF(
            target.left() + x / frame_w * target.width(),
            target.top() + y / frame_h * target.height(),
            width / frame_w * target.width(),
            height / frame_h * target.height(),
        )

    def _paint_roi(
        self,
        painter: QPainter,
        rect: tuple[float, float, float, float] | None,
        color: QColor,
        label: str,
        *,
        label_below: bool = False,
    ) -> None:
        if rect is None:
            return
        widget_rect = self._image_rect_to_widget(rect)
        if widget_rect is None:
            return
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawRect(widget_rect)
        label_y = 32.0 if label_below else 16.0
        painter.drawText(widget_rect.topLeft() + QPointF(6.0, label_y), label)

    def _paint_roi_config(
        self,
        painter: QPainter,
        config: dict[str, object] | None,
        color: QColor,
        label: str,
        *,
        selected_node_index: int | None = None,
        editable_nodes: bool = False,
        label_below: bool = False,
    ) -> None:
        if not isinstance(config, dict):
            return
        roi_type = config.get("type")
        if roi_type == "rectangle":
            rect = self._rect_from_roi_config(config)
            self._paint_roi(painter, rect, color, label, label_below=label_below)
            return
        if roi_type == "circle":
            try:
                center = self._point_from_config(config["center"])
                radius = float(config["radius"])
            except Exception:
                return
            self._paint_circle_roi(
                painter,
                center,
                radius,
                color,
                label,
                label_below=label_below,
            )
            return
        if roi_type == "annulus":
            try:
                center = self._point_from_config(config["center"])
                inner_radius = float(config["inner_radius"])
                outer_radius = float(config["outer_radius"])
            except Exception:
                return
            self._paint_circle_roi(
                painter,
                center,
                outer_radius,
                color,
                label,
                label_below=label_below,
            )
            self._paint_circle_roi(painter, center, inner_radius, color, "")
            return
        if roi_type == "polygon":
            try:
                points = self._points_from_config(config["points"])
            except Exception:
                return
            self._paint_polygon_roi(
                painter,
                points,
                color,
                label,
                close=True,
                selected_node_index=selected_node_index,
                editable_nodes=editable_nodes,
                label_below=label_below,
            )
            return
        if roi_type == "curve_band":
            try:
                points = self._points_from_config(config["polyline"], minimum=2)
                half_width = float(config["half_width"])
            except Exception:
                return
            self._paint_curve_band_roi(
                painter,
                points,
                half_width,
                color,
                label,
                selected_node_index=selected_node_index,
                editable_nodes=editable_nodes,
                label_below=label_below,
            )

    def _paint_circle_roi(
        self,
        painter: QPainter,
        center: tuple[float, float],
        radius: float,
        color: QColor,
        label: str,
        *,
        label_below: bool = False,
    ) -> None:
        center_widget = self._image_point_to_widget(center)
        radius_widget = self._image_radius_to_widget(radius)
        if center_widget is None or radius_widget is None:
            return
        painter.setPen(QPen(color, 2))
        painter.drawEllipse(center_widget, radius_widget, radius_widget)
        painter.drawLine(center_widget + QPointF(-5.0, 0.0), center_widget + QPointF(5.0, 0.0))
        painter.drawLine(center_widget + QPointF(0.0, -5.0), center_widget + QPointF(0.0, 5.0))
        if label:
            label_y = 16.0 if label_below else -8.0
            painter.drawText(center_widget + QPointF(8.0, label_y), label)

    def _paint_polygon_roi(
        self,
        painter: QPainter,
        image_points: list[tuple[float, float]],
        color: QColor,
        label: str,
        close: bool,
        selected_node_index: int | None = None,
        editable_nodes: bool = False,
        label_below: bool = False,
    ) -> None:
        widget_points = [
            point
            for point in (self._image_point_to_widget(image_point) for image_point in image_points)
            if point is not None
        ]
        if not widget_points:
            return
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in zip(widget_points[:-1], widget_points[1:]):
            painter.drawLine(start, end)
        if close and len(widget_points) >= 3:
            painter.drawLine(widget_points[-1], widget_points[0])
        self._paint_roi_nodes(
            painter,
            widget_points,
            color,
            selected_node_index=selected_node_index,
            editable=editable_nodes,
        )
        if close and len(widget_points) >= 3:
            painter.drawPolygon(QPolygonF(widget_points))
        if label:
            label_y = 18.0 if label_below else -6.0
            anchor = widget_points[0] + QPointF(6.0, label_y)
            painter.drawText(anchor, f"{label} ({len(widget_points)} pts)" if len(widget_points) >= 3 else label)

    def _paint_curve_band_roi(
        self,
        painter: QPainter,
        image_points: list[tuple[float, float]],
        half_width: float,
        color: QColor,
        label: str,
        selected_node_index: int | None = None,
        editable_nodes: bool = False,
        label_below: bool = False,
    ) -> None:
        widget_points = [
            point
            for point in (self._image_point_to_widget(image_point) for image_point in image_points)
            if point is not None
        ]
        if not widget_points:
            return
        band_width = self._image_radius_to_widget(max(1.0, float(half_width)) * 2.0)
        if band_width is not None and len(widget_points) >= 2:
            band_color = QColor(color)
            band_color.setAlpha(65)
            band_pen = QPen(band_color, max(3.0, band_width))
            band_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            band_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(band_pen)
            for start, end in zip(widget_points[:-1], widget_points[1:]):
                painter.drawLine(start, end)
        line_pen = QPen(color, 2)
        line_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        line_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(line_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in zip(widget_points[:-1], widget_points[1:]):
            painter.drawLine(start, end)
        self._paint_roi_nodes(
            painter,
            widget_points,
            color,
            selected_node_index=selected_node_index,
            editable=editable_nodes,
        )
        if label:
            suffix = f" ({len(widget_points)} pts, {float(half_width):.1f}px)" if len(widget_points) >= 2 else ""
            label_y = 18.0 if label_below else -6.0
            painter.drawText(widget_points[0] + QPointF(6.0, label_y), f"{label}{suffix}")

    @staticmethod
    def _paint_roi_nodes(
        painter: QPainter,
        widget_points: list[QPointF],
        color: QColor,
        *,
        selected_node_index: int | None,
        editable: bool,
    ) -> None:
        if not editable:
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for point in widget_points:
                painter.drawEllipse(point, 3.2, 3.2)
            return
        for index, point in enumerate(widget_points):
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#ffffff"), 4.0))
            painter.drawEllipse(point, 5.2, 5.2)
            painter.setBrush(color)
            painter.setPen(QPen(color, 2.0))
            painter.drawEllipse(point, 4.6, 4.6)
            if index == selected_node_index:
                selected = QColor("#ffd166")
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(selected, 3.0))
                painter.drawEllipse(point, 9.0, 9.0)
                painter.drawText(point + QPointF(11.0, -10.0), f"Node {index + 1}")
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_calibration_line(
        self,
        painter: QPainter,
        line: tuple[tuple[float, float], tuple[float, float]] | None,
        color: QColor,
        label: str,
    ) -> None:
        if line is None:
            return
        start = self._image_point_to_widget(line[0])
        end = self._image_point_to_widget(line[1])
        if start is None or end is None:
            return
        painter.setPen(QPen(color, 2))
        painter.drawLine(start, end)
        painter.drawEllipse(start, 3.5, 3.5)
        painter.drawEllipse(end, 3.5, 3.5)
        if self.calibration_has_axis:
            painter.drawText(end + QPointF(6.0, -6.0), "+X")
            dx, dy = end.x() - start.x(), end.y() - start.y()
            length = math.hypot(dx, dy)
            if length > 1e-6:
                sign = 1.0 if self.calibration_y_positive == "up" else -1.0
                y_end = start + QPointF(sign * dy * 24.0 / length, -sign * dx * 24.0 / length)
                painter.drawLine(start, y_end)
                painter.drawText(y_end + QPointF(6.0, -6.0), "+Y")
        mid = QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
        painter.drawText(mid + QPointF(6.0, -6.0), label)

    def _paint_tracking_overlay(self, painter: QPainter) -> None:
        if self.trajectory_points:
            painter.setPen(QPen(QColor("#00a7b5"), 2))
            widget_points = [
                point
                for point in (self._image_point_to_widget(image_point) for image_point in self.trajectory_points)
                if point is not None
            ]
            for start, end in zip(widget_points[:-1], widget_points[1:]):
                painter.drawLine(start, end)
            painter.setPen(QPen(QColor("#00a7b5"), 1))
            for point in widget_points[:: max(1, len(widget_points) // 36)]:
                painter.drawEllipse(point, 2.2, 2.2)
        self._paint_measurement_trajectory(painter)
        self._paint_candidate_points(painter)
        self._paint_prediction_point(painter, self.prediction_point)
        self._paint_observation_point(painter, self.observation_point)
        self._paint_tracking_point(painter, self.current_tracking_point, QColor("#ffb020"), "Current")

    def _paint_measurement_trajectory(self, painter: QPainter) -> None:
        if not self.measurement_points:
            return
        widget_points = [
            point
            for point in (self._image_point_to_widget(image_point) for image_point in self.measurement_points)
            if point is not None
        ]
        if not widget_points:
            return
        pen = QPen(QColor("#f97316"), 1.6)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in zip(widget_points[:-1], widget_points[1:]):
            painter.drawLine(start, end)
        painter.setPen(QPen(QColor("#f97316"), 1.2))
        for point in widget_points[:: max(1, len(widget_points) // 32)]:
            painter.drawRect(QRectF(point.x() - 2.5, point.y() - 2.5, 5.0, 5.0))

    def _paint_candidate_points(self, painter: QPainter) -> None:
        if not self.candidate_points:
            return
        for index, (x, y, score, selected) in enumerate(self.candidate_points, start=1):
            point = self._image_point_to_widget((x, y))
            if point is None:
                continue
            score = max(0.0, min(1.0, float(score)))
            radius = 3.2 + 3.0 * score
            color = QColor("#e11d48") if selected else QColor("#f472b6")
            color.setAlpha(235 if selected else 210)
            halo = QColor("#ffffff")
            halo.setAlpha(225)
            painter.setPen(QPen(halo, 3.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(point, radius, radius)
            painter.setPen(QPen(color, 2.0 if selected else 1.6))
            painter.drawEllipse(point, radius, radius)
            painter.setBrush(color if selected else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(point, 2.1 if selected else 1.3, 2.1 if selected else 1.3)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color, 1.0))
            painter.drawText(point + QPointF(radius + 4.0, -radius - 2.0), f"C{index}")

    def _paint_prediction_point(self, painter: QPainter, image_point: tuple[float, float] | None) -> None:
        if image_point is None:
            return
        point = self._image_point_to_widget(image_point)
        if point is None:
            return
        pen = QPen(QColor("#a855f7"), 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, 8.0, 8.0)
        painter.drawLine(point + QPointF(-10.0, -10.0), point + QPointF(10.0, 10.0))
        painter.drawLine(point + QPointF(-10.0, 10.0), point + QPointF(10.0, -10.0))

    def _paint_observation_point(self, painter: QPainter, image_point: tuple[float, float] | None) -> None:
        if image_point is None:
            return
        point = self._image_point_to_widget(image_point)
        if point is None:
            return
        painter.setPen(QPen(QColor("#4dabf7"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, 7.0, 7.0)

    def _paint_response_map(self, painter: QPainter) -> None:
        if self.response_map is None or self._frame_size is None:
            return
        image = self._response_overlay_image()
        if image is None:
            return
        painter.drawImage(self._display_rect(), image)

    def _response_overlay_image(self) -> QImage | None:
        response_map = self.response_map
        frame_size = self._frame_size
        if response_map is None or frame_size is None:
            return None
        output_size = self._response_overlay_output_size(frame_size)
        if (
            self._response_overlay_cache_ready
            and self._response_overlay_cache_source is response_map
            and self._response_overlay_cache_frame_size == frame_size
            and self._response_overlay_cache_output_size == output_size
        ):
            return self._response_overlay_cache_image

        rgba = _response_overlay_rgba(response_map, frame_size, output_size)
        image: QImage | None = None
        if rgba is not None:
            output_width, output_height = output_size
            image = QImage(
                rgba.data,
                output_width,
                output_height,
                output_width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
        self._response_overlay_cache_ready = True
        self._response_overlay_cache_source = response_map
        self._response_overlay_cache_frame_size = frame_size
        self._response_overlay_cache_output_size = output_size
        self._response_overlay_cache_image = image
        return image

    def _response_overlay_output_size(self, frame_size: tuple[int, int]) -> tuple[int, int]:
        frame_width, frame_height = frame_size
        target = self._display_rect()
        device_scale = max(1.0, float(self.devicePixelRatioF()))
        output_width = min(frame_width, max(1, int(math.ceil(target.width() * device_scale))))
        output_height = min(frame_height, max(1, int(math.ceil(target.height() * device_scale))))
        return output_width, output_height

    def _invalidate_response_overlay_cache(self) -> None:
        self._response_overlay_cache_ready = False
        self._response_overlay_cache_source = None
        self._response_overlay_cache_frame_size = None
        self._response_overlay_cache_output_size = None
        self._response_overlay_cache_image = None

    def _paint_tracking_point(
        self,
        painter: QPainter,
        image_point: tuple[float, float] | None,
        color: QColor,
        label: str,
    ) -> None:
        if image_point is None:
            return
        point = self._image_point_to_widget(image_point)
        if point is None:
            return
        painter.setPen(QPen(color, 2))
        painter.drawEllipse(point, 5.0, 5.0)
        painter.drawLine(point + QPointF(-8.0, 0.0), point + QPointF(8.0, 0.0))
        painter.drawLine(point + QPointF(0.0, -8.0), point + QPointF(0.0, 8.0))
        painter.drawText(point + QPointF(8.0, -8.0), label)

    def _image_point_to_widget(self, point: tuple[float, float]) -> QPointF | None:
        if self._frame_size is None:
            return None
        frame_w, frame_h = self._frame_size
        if frame_w <= 0 or frame_h <= 0:
            return None
        target = self._display_rect()
        return QPointF(
            target.left() + point[0] / frame_w * target.width(),
            target.top() + point[1] / frame_h * target.height(),
        )

    def _image_radius_to_widget(self, radius: float) -> float | None:
        if self._frame_size is None:
            return None
        frame_w, frame_h = self._frame_size
        if frame_w <= 0 or frame_h <= 0:
            return None
        target = self._display_rect()
        return float(radius) * min(target.width() / frame_w, target.height() / frame_h)

    def _editable_roi_node_at(self, widget_point: QPointF, tolerance: float = 11.0) -> int | None:
        points = self._editable_roi_points(self.editable_roi_config)
        nearest_index: int | None = None
        nearest_distance = float(tolerance)
        for index, image_point in enumerate(points):
            point = self._image_point_to_widget(image_point)
            if point is None:
                continue
            distance = float(
                np.hypot(widget_point.x() - point.x(), widget_point.y() - point.y())
            )
            if distance <= nearest_distance:
                nearest_index = index
                nearest_distance = distance
        return nearest_index

    @staticmethod
    def _editable_roi_points(config: dict[str, object] | None) -> list[tuple[float, float]]:
        if not isinstance(config, dict):
            return []
        roi_type = config.get("type")
        key = "points" if roi_type == "polygon" else "polyline" if roi_type == "curve_band" else ""
        if not key:
            return []
        try:
            return PreviewCanvas._points_from_config(config[key], minimum=2)
        except Exception:
            return []

    def _move_editable_roi_node(self, index: int, point: tuple[float, float]) -> bool:
        config = self.editable_roi_config
        if not isinstance(config, dict):
            return False
        roi_type = config.get("type")
        key = "points" if roi_type == "polygon" else "polyline" if roi_type == "curve_band" else ""
        if not key:
            return False
        points = config.get(key)
        if not isinstance(points, list) or not 0 <= int(index) < len(points):
            return False
        updated = deepcopy(points)
        updated[int(index)] = [round(float(point[0]), 3), round(float(point[1]), 3)]
        config[key] = updated
        self.selected_roi_node_index = int(index)
        return True

    @staticmethod
    def _normalized_image_rect(
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        x0, y0 = start
        x1, y1 = end
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return left, top, right - left, bottom - top

    @staticmethod
    def _rect_from_roi_config(config: dict[str, object] | None) -> tuple[float, float, float, float] | None:
        if not isinstance(config, dict) or config.get("type") != "rectangle":
            return None
        try:
            return (
                float(config["x"]),
                float(config["y"]),
                float(config["width"]),
                float(config["height"]),
            )
        except Exception:
            return None

    @staticmethod
    def _point_from_config(value: object) -> tuple[float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("point must contain two coordinates")
        return float(value[0]), float(value[1])

    @staticmethod
    def _points_from_config(value: object, minimum: int = 3) -> list[tuple[float, float]]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("points must be a sequence")
        points = [PreviewCanvas._point_from_config(point) for point in value]
        if len(points) < minimum:
            raise ValueError(f"ROI needs at least {minimum} points")
        return points

    @staticmethod
    def _distance(start: tuple[float, float], end: tuple[float, float]) -> float:
        return float(np.linalg.norm(np.asarray(end, dtype=float) - np.asarray(start, dtype=float)))
