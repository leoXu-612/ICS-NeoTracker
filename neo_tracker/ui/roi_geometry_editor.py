from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.config import validate_roi_config


class ROIGeometryEditor(QWidget):
    """Contextual, explicit-apply editor for all built-in ROI geometries."""

    configApplied = Signal(object)
    draftChanged = Signal(object)
    nodeSelectionChanged = Signal(object)

    _TYPE_LABELS = {
        "rectangle": "Rectangle",
        "circle": "Circle",
        "annulus": "Annulus",
        "polygon": "Polygon nodes",
        "curve_band": "Curve band nodes",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("roiGeometryEditor")
        self._loading = False
        self._roi_type = ""
        self._baseline: dict[str, object] | None = None

        self.type_label = QLabel("No editable ROI")
        self.type_label.setObjectName("roiGeometryTypeLabel")
        self.type_label.setAccessibleName("ROI geometry type")
        self.message_label = QLabel("Choose or draw an ROI to edit its geometry.")
        self.message_label.setObjectName("roiGeometryMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setAccessibleName("ROI geometry editor status")

        self.pages = QStackedWidget()
        self.pages.setObjectName("roiGeometryPages")
        self._page_by_type: dict[str, QWidget] = {}

        self.rectangle_x_spin = self._coordinate_spin("rectangleXSpin", "Rectangle X coordinate")
        self.rectangle_y_spin = self._coordinate_spin("rectangleYSpin", "Rectangle Y coordinate")
        self.rectangle_width_spin = self._positive_spin("rectangleWidthSpin", "Rectangle width")
        self.rectangle_height_spin = self._positive_spin("rectangleHeightSpin", "Rectangle height")
        rectangle_page = self._grid_page(
            (
                ("X", self.rectangle_x_spin, "Y", self.rectangle_y_spin),
                ("Width", self.rectangle_width_spin, "Height", self.rectangle_height_spin),
            )
        )
        self._add_page("rectangle", rectangle_page)

        self.circle_center_x_spin = self._coordinate_spin("circleCenterXSpin", "Circle center X")
        self.circle_center_y_spin = self._coordinate_spin("circleCenterYSpin", "Circle center Y")
        self.circle_radius_spin = self._positive_spin("circleRadiusSpin", "Circle radius")
        circle_page = self._grid_page(
            (
                ("Center X", self.circle_center_x_spin, "Center Y", self.circle_center_y_spin),
                ("Radius", self.circle_radius_spin, "", None),
            )
        )
        self._add_page("circle", circle_page)

        self.annulus_center_x_spin = self._coordinate_spin("annulusCenterXSpin", "Annulus center X")
        self.annulus_center_y_spin = self._coordinate_spin("annulusCenterYSpin", "Annulus center Y")
        self.annulus_inner_radius_spin = self._positive_spin("annulusInnerRadiusSpin", "Annulus inner radius")
        self.annulus_outer_radius_spin = self._positive_spin("annulusOuterRadiusSpin", "Annulus outer radius")
        annulus_page = self._grid_page(
            (
                ("Center X", self.annulus_center_x_spin, "Center Y", self.annulus_center_y_spin),
                ("Inner radius", self.annulus_inner_radius_spin, "Outer radius", self.annulus_outer_radius_spin),
            )
        )
        self._add_page("annulus", annulus_page)

        # Keep macOS accessibility re-entry inside Qt instead of the Python
        # QTableWidget wrapper, matching the native Results table boundary.
        self._native_widget_loader = QUiLoader(self)
        node_table = self._native_widget_loader.createWidget(
            "QTableWidget",
            self,
            "roiNodeTable",
        )
        if not isinstance(node_table, QTableWidget):
            raise RuntimeError("Qt could not create the native ROI node table")
        self.node_table = node_table
        self.node_table.setRowCount(0)
        self.node_table.setColumnCount(3)
        self.node_table.setHorizontalHeaderLabels(["Node", "X (px)", "Y (px)"])
        self.node_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.node_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.node_table.setAlternatingRowColors(True)
        self.node_table.setMaximumHeight(152)
        self.node_table.verticalHeader().setVisible(False)
        self.node_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.node_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.node_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.node_table.setAccessibleName("ROI node coordinates")
        self.node_table.setAccessibleDescription(
            "Select one node row to edit its X and Y pixel coordinates, insert a node after it, "
            "or remove it when the ROI remains valid."
        )
        self.node_table.itemSelectionChanged.connect(self._node_selection_changed)

        self.add_node_button = QPushButton("Insert After")
        self.add_node_button.setObjectName("addRoiNodeButton")
        self.add_node_button.setToolTip(
            "Insert a node after the selected node, midway along the next segment."
        )
        self.add_node_button.setAccessibleName("Insert ROI node after selection")
        self.add_node_button.clicked.connect(self._insert_after_selected_node)
        self.remove_node_button = QPushButton("Remove Selected")
        self.remove_node_button.setObjectName("removeRoiNodeButton")
        self.remove_node_button.setToolTip("Remove the selected node while preserving the minimum valid node count.")
        self.remove_node_button.setAccessibleName("Remove selected ROI node")
        self.remove_node_button.clicked.connect(self._remove_selected_node)

        node_actions = QHBoxLayout()
        node_actions.setContentsMargins(0, 0, 0, 0)
        node_actions.addWidget(self.add_node_button)
        node_actions.addWidget(self.remove_node_button)

        self.curve_band_half_width_spin = self._positive_spin(
            "curveBandHalfWidthSpin",
            "Current curve band half-width",
        )
        self.curve_band_half_width_spin.setValue(24.0)
        self.curve_width_row = QWidget()
        curve_width_layout = QFormLayout(self.curve_width_row)
        curve_width_layout.setContentsMargins(0, 0, 0, 0)
        curve_width_layout.addRow("Half-width", self.curve_band_half_width_spin)

        node_page = QWidget()
        node_layout = QVBoxLayout(node_page)
        node_layout.setContentsMargins(0, 0, 0, 0)
        node_layout.setSpacing(6)
        node_layout.addWidget(self.node_table)
        node_layout.addLayout(node_actions)
        node_layout.addWidget(self.curve_width_row)
        self._add_page("polygon", node_page)
        self._page_by_type["curve_band"] = node_page

        self.apply_button = QPushButton("Apply Geometry")
        self.apply_button.setObjectName("applyRoiGeometryButton")
        self.apply_button.setToolTip("Validate and apply these ROI values, then invalidate stale tracking results.")
        self.apply_button.setAccessibleName("Apply ROI geometry")
        self.apply_button.clicked.connect(self._apply)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setObjectName("revertRoiGeometryButton")
        self.revert_button.setToolTip("Discard unapplied geometry changes.")
        self.revert_button.setAccessibleName("Revert ROI geometry changes")
        self.revert_button.clicked.connect(self._revert)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.apply_button, 1)
        actions.addWidget(self.revert_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.type_label)
        layout.addWidget(self.pages)
        layout.addLayout(actions)
        layout.addWidget(self.message_label)

        self._set_enabled(False)
        self._set_status("Choose or draw an ROI to edit its geometry.", "empty")

    def set_config(self, config: dict[str, object] | None) -> None:
        roi_type = str(config.get("type", "")) if isinstance(config, dict) else ""
        if roi_type not in self._page_by_type:
            self._baseline = None
            self._roi_type = ""
            self.type_label.setText("No editable ROI")
            self._set_enabled(False)
            self._set_status("Choose or draw an ROI to edit its geometry.", "empty")
            self.draftChanged.emit(None)
            self.nodeSelectionChanged.emit(None)
            return

        self._loading = True
        try:
            self._roi_type = roi_type
            self._baseline = deepcopy(config)
            page = self._page_by_type[roi_type]
            self.pages.setCurrentWidget(page)
            self.pages.setFixedHeight(236 if roi_type in {"polygon", "curve_band"} else 82)
            self.type_label.setText(self._TYPE_LABELS[roi_type])
            self.curve_width_row.setVisible(roi_type == "curve_band")
            if roi_type == "rectangle":
                self._set_spin(self.rectangle_x_spin, config["x"])
                self._set_spin(self.rectangle_y_spin, config["y"])
                self._set_spin(self.rectangle_width_spin, config["width"])
                self._set_spin(self.rectangle_height_spin, config["height"])
            elif roi_type == "circle":
                center = config["center"]
                self._set_spin(self.circle_center_x_spin, center[0])  # type: ignore[index]
                self._set_spin(self.circle_center_y_spin, center[1])  # type: ignore[index]
                self._set_spin(self.circle_radius_spin, config["radius"])
            elif roi_type == "annulus":
                center = config["center"]
                self._set_spin(self.annulus_center_x_spin, center[0])  # type: ignore[index]
                self._set_spin(self.annulus_center_y_spin, center[1])  # type: ignore[index]
                self._set_spin(self.annulus_inner_radius_spin, config["inner_radius"])
                self._set_spin(self.annulus_outer_radius_spin, config["outer_radius"])
            else:
                key = "points" if roi_type == "polygon" else "polyline"
                self._populate_nodes(config[key])  # type: ignore[arg-type]
                if roi_type == "curve_band":
                    self._set_spin(self.curve_band_half_width_spin, config["half_width"])
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            self._baseline = None
            self._roi_type = ""
            self.type_label.setText("Invalid ROI geometry")
            self._set_enabled(False)
            self._set_status("The current ROI cannot be represented in the geometry editor.", "error")
            self.draftChanged.emit(None)
            self.nodeSelectionChanged.emit(None)
            return
        finally:
            self._loading = False

        self._baseline = self.current_config()
        self._set_enabled(True)
        self._update_node_actions_enabled()
        self._set_status("Edit values, then apply once to update the ROI.", "ready")
        self.draftChanged.emit(deepcopy(self.current_config()))
        self.nodeSelectionChanged.emit(None)

    def current_config(self) -> dict[str, object] | None:
        if not self._roi_type:
            return None
        if self._roi_type == "rectangle":
            return {
                "type": "rectangle",
                "x": self._rounded(self.rectangle_x_spin.value()),
                "y": self._rounded(self.rectangle_y_spin.value()),
                "width": self._rounded(self.rectangle_width_spin.value()),
                "height": self._rounded(self.rectangle_height_spin.value()),
            }
        if self._roi_type == "circle":
            return {
                "type": "circle",
                "center": [
                    self._rounded(self.circle_center_x_spin.value()),
                    self._rounded(self.circle_center_y_spin.value()),
                ],
                "radius": self._rounded(self.circle_radius_spin.value()),
            }
        if self._roi_type == "annulus":
            return {
                "type": "annulus",
                "center": [
                    self._rounded(self.annulus_center_x_spin.value()),
                    self._rounded(self.annulus_center_y_spin.value()),
                ],
                "inner_radius": self._rounded(self.annulus_inner_radius_spin.value()),
                "outer_radius": self._rounded(self.annulus_outer_radius_spin.value()),
            }
        points = self._node_values()
        if self._roi_type == "polygon":
            return {"type": "polygon", "points": points}
        return {
            "type": "curve_band",
            "polyline": points,
            "half_width": self._rounded(self.curve_band_half_width_spin.value()),
        }

    def show_applied(self) -> None:
        self._set_status("Geometry applied. Tracking results now need a fresh run.", "applied")

    def show_error(self, message: str) -> None:
        self._set_status(str(message), "error")

    def set_drawing_active(self, roi_label: str | None) -> None:
        if roi_label:
            self.pages.setEnabled(False)
            self.add_node_button.setEnabled(False)
            self.remove_node_button.setEnabled(False)
            self.apply_button.setEnabled(False)
            self.revert_button.setEnabled(False)
            self._set_status(
                f"Drawing a new {roi_label} ROI in the preview. Finish or cancel before editing values.",
                "drawing",
            )
            return
        if self._baseline is None:
            self._set_enabled(False)
            return
        self.pages.setEnabled(True)
        self._mark_dirty()

    def is_dirty(self) -> bool:
        current = self.current_config()
        return current is not None and current != self._baseline

    def selected_node_index(self) -> int | None:
        row = self.node_table.currentRow()
        if self._roi_type not in {"polygon", "curve_band"} or row < 0:
            return None
        return row

    def select_node(self, index: int | None) -> None:
        if index is None:
            self.node_table.clearSelection()
            return
        row = int(index)
        if self._roi_type not in {"polygon", "curve_band"} or not 0 <= row < self.node_table.rowCount():
            return
        self.node_table.selectRow(row)
        item = self.node_table.item(row, 0)
        if item is not None:
            self.node_table.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    def move_node(self, index: int, point: object) -> None:
        try:
            row = int(index)
            x = float(point[0])  # type: ignore[index]
            y = float(point[1])  # type: ignore[index]
        except (TypeError, ValueError, IndexError, OverflowError):
            return
        if self._roi_type not in {"polygon", "curve_band"} or not 0 <= row < self.node_table.rowCount():
            return
        x_spin = self.node_table.cellWidget(row, 1)
        y_spin = self.node_table.cellWidget(row, 2)
        if not isinstance(x_spin, QDoubleSpinBox) or not isinstance(y_spin, QDoubleSpinBox):
            return
        self.select_node(row)
        self._set_spin(x_spin, x)
        self._set_spin(y_spin, y)
        self._mark_dirty()

    def _apply(self) -> None:
        config = self.current_config()
        error = validate_roi_config(config)
        if error is not None:
            self._set_status(error, "error")
            return
        self.configApplied.emit(deepcopy(config))

    def _revert(self) -> None:
        if self._baseline is not None:
            self.set_config(deepcopy(self._baseline))

    def _mark_dirty(self, *_args: object) -> None:
        if self._loading or not self._roi_type:
            return
        dirty = self.is_dirty()
        self.apply_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        selected = self.selected_node_index()
        selected_prefix = f"Node {selected + 1} selected · " if selected is not None else ""
        if dirty:
            error = validate_roi_config(self.current_config())
            if error is None:
                suffix = " · Arrow keys: 1 px, Shift: 10 px." if selected is not None else "."
                self._set_status(f"{selected_prefix}Unapplied geometry changes{suffix}", "dirty")
            else:
                self._set_status(error, "error")
        elif selected is not None:
            self._set_status(
                f"Node {selected + 1} selected. Drag it, or use Arrow keys in the preview; Shift moves 10 px.",
                "selected",
            )
        else:
            self._set_status("Edit values, then apply once to update the ROI.", "ready")
        self._update_node_actions_enabled()
        self.draftChanged.emit(deepcopy(self.current_config()))

    def _insert_after_selected_node(self) -> None:
        points = self._node_values()
        row = self.node_table.currentRow()
        if not points or not 0 <= row < len(points):
            self._update_node_actions_enabled()
            return
        x, y = points[row]
        if row + 1 < len(points):
            next_x, next_y = points[row + 1]
            point = [self._rounded((x + next_x) / 2.0), self._rounded((y + next_y) / 2.0)]
        elif self._roi_type == "polygon":
            next_x, next_y = points[0]
            point = [self._rounded((x + next_x) / 2.0), self._rounded((y + next_y) / 2.0)]
        elif len(points) > 1:
            previous_x, previous_y = points[row - 1]
            point = [
                self._rounded(x + (x - previous_x) / 2.0),
                self._rounded(y + (y - previous_y) / 2.0),
            ]
        else:
            point = [self._rounded(x + 10.0), self._rounded(y + 10.0)]
        inserted_row = row + 1
        self._insert_node(inserted_row, point)
        self.node_table.selectRow(inserted_row)
        self._mark_dirty()

    def _remove_selected_node(self) -> None:
        row = self.node_table.currentRow()
        minimum = 3 if self._roi_type == "polygon" else 2
        if row < 0 or self.node_table.rowCount() <= minimum:
            self._update_node_actions_enabled()
            return
        self.node_table.removeRow(row)
        self._renumber_nodes()
        if self.node_table.rowCount():
            self.node_table.selectRow(min(row, self.node_table.rowCount() - 1))
        self._mark_dirty()

    def _populate_nodes(self, points: Any) -> None:
        self.node_table.setRowCount(0)
        for point in points:
            self._append_node(point)
        self.node_table.clearSelection()

    def _node_selection_changed(self) -> None:
        self._update_node_actions_enabled()
        if self._loading:
            return
        selected = self.selected_node_index()
        self.nodeSelectionChanged.emit(selected)
        self._mark_dirty()

    def _append_node(self, point: Any) -> None:
        row = self.node_table.rowCount()
        self._insert_node(row, point)

    def _insert_node(self, row: int, point: Any) -> None:
        self.node_table.insertRow(row)
        index_item = QTableWidgetItem(str(row + 1))
        index_item.setFlags(index_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        index_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.node_table.setItem(row, 0, index_item)
        x_spin = self._coordinate_spin(f"roiNodeX{row}", f"Node {row + 1} X coordinate")
        y_spin = self._coordinate_spin(f"roiNodeY{row}", f"Node {row + 1} Y coordinate")
        self._set_spin(x_spin, point[0])
        self._set_spin(y_spin, point[1])
        self.node_table.setCellWidget(row, 1, x_spin)
        self.node_table.setCellWidget(row, 2, y_spin)
        self._renumber_nodes()

    def _node_values(self) -> list[list[float]]:
        points: list[list[float]] = []
        for row in range(self.node_table.rowCount()):
            x_spin = self.node_table.cellWidget(row, 1)
            y_spin = self.node_table.cellWidget(row, 2)
            if isinstance(x_spin, QDoubleSpinBox) and isinstance(y_spin, QDoubleSpinBox):
                points.append([self._rounded(x_spin.value()), self._rounded(y_spin.value())])
        return points

    def _renumber_nodes(self) -> None:
        for row in range(self.node_table.rowCount()):
            item = self.node_table.item(row, 0)
            if item is not None:
                item.setText(str(row + 1))
            for column, axis in ((1, "X"), (2, "Y")):
                spin = self.node_table.cellWidget(row, column)
                if isinstance(spin, QDoubleSpinBox):
                    spin.setAccessibleName(f"Node {row + 1} {axis} coordinate")

    def _update_node_actions_enabled(self) -> None:
        editing_nodes = self._roi_type in {"polygon", "curve_band"}
        selected = self.node_table.currentRow() >= 0
        self.add_node_button.setEnabled(editing_nodes and selected)
        minimum = 3 if self._roi_type == "polygon" else 2
        self.remove_node_button.setEnabled(
            editing_nodes and selected and self.node_table.rowCount() > minimum
        )

    def _set_enabled(self, enabled: bool) -> None:
        self.pages.setEnabled(enabled)
        self.add_node_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.revert_button.setEnabled(False)
        self.remove_node_button.setEnabled(False)

    def _set_status(self, text: str, state: str) -> None:
        self.message_label.setText(text)
        self.message_label.setProperty("roiGeometryState", state)
        self.message_label.setToolTip(text)
        self.message_label.setAccessibleDescription(text)
        self.message_label.style().unpolish(self.message_label)
        self.message_label.style().polish(self.message_label)

    def _add_page(self, roi_type: str, page: QWidget) -> None:
        self._page_by_type[roi_type] = page
        self.pages.addWidget(page)

    def _grid_page(self, rows: tuple[tuple[str, QDoubleSpinBox, str, QDoubleSpinBox | None], ...]) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for row_index, (left_label, left_spin, right_label, right_spin) in enumerate(rows):
            layout.addWidget(QLabel(left_label), row_index, 0)
            layout.addWidget(left_spin, row_index, 1)
            if right_spin is not None:
                layout.addWidget(QLabel(right_label), row_index, 2)
                layout.addWidget(right_spin, row_index, 3)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        return page

    def _coordinate_spin(self, object_name: str, accessible_name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName(object_name)
        spin.setRange(-1_000_000.0, 1_000_000.0)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(" px")
        spin.setKeyboardTracking(False)
        spin.setAccessibleName(accessible_name)
        spin.valueChanged.connect(self._mark_dirty)
        return spin

    def _positive_spin(self, object_name: str, accessible_name: str) -> QDoubleSpinBox:
        spin = self._coordinate_spin(object_name, accessible_name)
        spin.setRange(0.001, 1_000_000.0)
        return spin

    @staticmethod
    def _set_spin(spin: QDoubleSpinBox, value: object) -> None:
        spin.blockSignals(True)
        spin.setValue(float(value))
        spin.blockSignals(False)

    @staticmethod
    def _rounded(value: float) -> float:
        return round(float(value), 3)
