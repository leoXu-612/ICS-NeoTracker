from __future__ import annotations

from copy import deepcopy
from math import hypot, isfinite

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CalibrationEditor(QWidget):
    """Non-modal draft editor for a two-point length calibration."""

    calibrationApplied = Signal(object)
    draftChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("calibrationEditor")
        self._loading = False
        self._drawing_active = False
        self._axis_direction_available = True
        self._baseline: dict[str, object] | None = None
        self._line: tuple[tuple[float, float], tuple[float, float]] | None = None

        self.summary_label = QLabel("Not set")
        self.summary_label.setObjectName("calibrationSummaryLabel")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("Calibration rod summary")

        self.length_spin = QDoubleSpinBox()
        self.length_spin.setObjectName("calibrationLengthSpin")
        self.length_spin.setRange(0.000001, 1_000_000.0)
        self.length_spin.setDecimals(6)
        self.length_spin.setValue(10.0)
        self.length_spin.setKeyboardTracking(False)
        self.length_spin.setAccessibleName("Calibration rod real length")
        self.length_spin.valueChanged.connect(self._mark_dirty)

        self.unit_combo = QComboBox()
        self.unit_combo.setObjectName("calibrationUnitCombo")
        self.unit_combo.setEditable(True)
        self.unit_combo.addItems(["mm", "cm", "m", "in"])
        self.unit_combo.setCurrentText("cm")
        self.unit_combo.setAccessibleName("Calibration length unit")
        self.unit_combo.setToolTip("Choose a common unit or type a short custom unit.")
        if self.unit_combo.lineEdit() is not None:
            self.unit_combo.lineEdit().setMaxLength(12)
        self.unit_combo.currentTextChanged.connect(self._mark_dirty)

        self.y_direction_combo = QComboBox()
        self.y_direction_combo.setObjectName("calibrationYDirectionCombo")
        self.y_direction_combo.addItem("Left of +X", "up")
        self.y_direction_combo.addItem("Right of +X", "down")
        self.y_direction_combo.setAccessibleName("Positive Y direction")
        self.y_direction_combo.setToolTip(
            "Choose which side of the rod's positive X direction contains positive Y."
        )
        self.y_direction_combo.currentIndexChanged.connect(self._mark_dirty)

        self.reverse_x_button = QPushButton("Reverse +X")
        self.reverse_x_button.setObjectName("reverseCalibrationXButton")
        self.reverse_x_button.setAccessibleName("Reverse positive X direction")
        self.reverse_x_button.setToolTip(
            "Swap the rod endpoints so positive X points in the opposite direction."
        )
        self.reverse_x_button.clicked.connect(self._reverse_x)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(5)
        form.addRow("Rod", self.summary_label)
        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_row.addWidget(self.length_spin, 2)
        value_row.addWidget(self.unit_combo, 1)
        form.addRow("Real length", value_row)
        self.y_direction_label = QLabel("Positive Y")
        axis_row = QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.setSpacing(6)
        axis_row.addWidget(self.y_direction_combo, 1)
        axis_row.addWidget(self.reverse_x_button)
        form.addRow(self.y_direction_label, axis_row)

        self.apply_button = QPushButton("Apply Calibration")
        self.apply_button.setObjectName("applyCalibrationButton")
        self.apply_button.setAccessibleName("Apply calibration")
        self.apply_button.setToolTip(
            "Apply this calibration, then invalidate stale tracking results."
        )
        self.apply_button.clicked.connect(self._apply)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setObjectName("revertCalibrationButton")
        self.revert_button.setAccessibleName("Revert calibration draft")
        self.revert_button.setToolTip("Discard unapplied calibration changes.")
        self.revert_button.clicked.connect(self._revert)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.apply_button, 1)
        actions.addWidget(self.revert_button)

        self.message_label = QLabel()
        self.message_label.setObjectName("calibrationEditorMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setAccessibleName("Calibration editor status")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.message_label)

        self.set_calibration(None)

    def set_calibration(self, config: dict[str, object] | None) -> None:
        parsed = self._normalized_config(config)
        self._loading = True
        try:
            self._baseline = deepcopy(parsed)
            if parsed is None:
                self._line = None
                self.length_spin.setValue(10.0)
                self.unit_combo.setCurrentText("cm")
                self.y_direction_combo.setCurrentIndex(0)
            else:
                self._line = self._line_from_config(parsed)
                self.length_spin.setValue(float(parsed["real_length"]))
                self.unit_combo.setCurrentText(str(parsed["unit"]))
                self.y_direction_combo.setCurrentIndex(
                    self.y_direction_combo.findData(str(parsed["y_positive"]))
                )
            self._render_summary()
        finally:
            self._loading = False
        self.apply_button.setEnabled(False)
        self.revert_button.setEnabled(False)
        self._sync_reverse_x_button()
        if parsed is None:
            self._set_status("Mark a rod in the preview, then enter its real length.", "empty")
        else:
            detail = "scale or axis direction" if self._axis_direction_available else "scale"
            self._set_status(f"Edit the {detail}, or mark a replacement rod.", "ready")

    def set_line(self, start: object, end: object) -> bool:
        line = self._normalized_line(start, end)
        if line is None:
            self._set_status("Calibration rod endpoints must be finite and distinct.", "error")
            return False
        self._line = line
        self._render_summary()
        self._sync_reverse_x_button()
        self._mark_dirty()
        return True

    def set_axis_direction_available(self, available: bool) -> None:
        self._axis_direction_available = bool(available)
        self.y_direction_label.setVisible(available)
        self.y_direction_combo.setVisible(available)
        self.reverse_x_button.setVisible(available)
        self._sync_reverse_x_button()

    def current_config(self) -> dict[str, object] | None:
        if self._line is None:
            return None
        start, end = self._line
        return {
            "start_px": [round(start[0], 3), round(start[1], 3)],
            "end_px": [round(end[0], 3), round(end[1], 3)],
            "real_length": round(float(self.length_spin.value()), 6),
            "unit": self.unit_combo.currentText().strip(),
            "y_positive": str(self.y_direction_combo.currentData()),
        }

    def is_dirty(self) -> bool:
        return self.current_config() != self._baseline

    def set_drawing_active(self, active: bool) -> None:
        was_active = self._drawing_active
        self._drawing_active = bool(active)
        self.length_spin.setEnabled(not active)
        self.unit_combo.setEnabled(not active)
        self.y_direction_combo.setEnabled(not active)
        self._sync_reverse_x_button()
        self.apply_button.setEnabled(False if active else self.is_dirty() and self._is_valid())
        self.revert_button.setEnabled(False if active else self.is_dirty())
        if active:
            self._set_status("Drag across a known length in the preview, then release.", "drawing")
        elif was_active:
            self._mark_dirty()

    def show_applied(self) -> None:
        detail = "Calibration and axis direction" if self._axis_direction_available else "Calibration"
        self._set_status(f"{detail} applied. Tracking results now need a fresh run.", "applied")

    def show_error(self, message: str) -> None:
        self._set_status(str(message), "error")

    def _mark_dirty(self, *_args: object) -> None:
        if self._loading or self._drawing_active:
            return
        dirty = self.is_dirty()
        valid = self._is_valid()
        self.apply_button.setEnabled(dirty and valid)
        self.revert_button.setEnabled(dirty)
        if self._line is None:
            self._set_status("Mark a rod in the preview, then enter its real length.", "empty")
            self.draftChanged.emit(None)
            return
        if not valid:
            self._set_status("Enter a printable unit of 1–12 characters before applying.", "error")
            self.draftChanged.emit(deepcopy(self.current_config()))
            return
        detail = self._scale_detail()
        if dirty:
            self._set_status(f"Unapplied calibration · {detail}", "dirty")
        else:
            self._set_status(f"Applied calibration · {detail}", "ready")
        self.draftChanged.emit(deepcopy(self.current_config()))

    def _apply(self) -> None:
        config = self.current_config()
        if config is None or not self._is_valid():
            self._set_status("Mark a valid rod and enter a non-empty unit before applying.", "error")
            return
        self.calibrationApplied.emit(deepcopy(config))

    def _revert(self) -> None:
        baseline = deepcopy(self._baseline)
        self.set_calibration(baseline)
        self.draftChanged.emit(deepcopy(baseline))

    def _reverse_x(self) -> None:
        if self._line is None or self._drawing_active or not self._axis_direction_available:
            return
        start, end = self._line
        self._line = end, start
        self._render_summary()
        self._mark_dirty()

    def _sync_reverse_x_button(self) -> None:
        self.reverse_x_button.setEnabled(
            self._axis_direction_available and self._line is not None and not self._drawing_active
        )

    def _render_summary(self) -> None:
        if self._line is None:
            text = "Not set"
        else:
            start, end = self._line
            pixel_length = hypot(end[0] - start[0], end[1] - start[1])
            text = (
                f"{pixel_length:.3f} px · "
                f"({start[0]:.1f}, {start[1]:.1f}) → ({end[0]:.1f}, {end[1]:.1f})"
            )
        self.summary_label.setText(text)
        self.summary_label.setToolTip(text)
        self.summary_label.setAccessibleDescription(text)

    def _scale_detail(self) -> str:
        if self._line is None:
            return "no rod"
        start, end = self._line
        pixel_length = hypot(end[0] - start[0], end[1] - start[1])
        real_length = float(self.length_spin.value())
        unit = self.unit_combo.currentText().strip()
        detail = (
            f"{pixel_length:.3f} px = {real_length:g} {unit} · "
            f"1 px = {real_length / pixel_length:.6g} {unit}"
        )
        if self._axis_direction_available:
            detail += f" · +Y {self.y_direction_combo.currentText().lower()}"
        return detail

    def _is_valid(self) -> bool:
        config = self.current_config()
        if config is None:
            return False
        unit = str(config["unit"]).strip()
        return (
            1 <= len(unit) <= 12
            and unit.isprintable()
            and config["y_positive"] in {"up", "down"}
        )

    def _set_status(self, text: str, state: str) -> None:
        self.message_label.setText(text)
        self.message_label.setProperty("calibrationState", state)
        self.message_label.setToolTip(text)
        self.message_label.setAccessibleDescription(text)
        self.message_label.style().unpolish(self.message_label)
        self.message_label.style().polish(self.message_label)

    @classmethod
    def _normalized_config(cls, config: dict[str, object] | None) -> dict[str, object] | None:
        if not isinstance(config, dict):
            return None
        line = cls._normalized_line(config.get("start_px"), config.get("end_px"))
        try:
            real_length = float(config["real_length"])
            unit = str(config["unit"]).strip()
            y_positive = str(config.get("y_positive", "up"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if (
            line is None
            or not isfinite(real_length)
            or real_length <= 0.0
            or not unit
            or y_positive not in {"up", "down"}
        ):
            return None
        start, end = line
        return {
            "start_px": [start[0], start[1]],
            "end_px": [end[0], end[1]],
            "real_length": real_length,
            "unit": unit,
            "y_positive": y_positive,
        }

    @staticmethod
    def _normalized_line(
        start: object,
        end: object,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        try:
            start_px = (float(start[0]), float(start[1]))  # type: ignore[index]
            end_px = (float(end[0]), float(end[1]))  # type: ignore[index]
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if not all(isfinite(value) for value in (*start_px, *end_px)):
            return None
        if hypot(end_px[0] - start_px[0], end_px[1] - start_px[1]) <= 1e-12:
            return None
        return start_px, end_px

    @staticmethod
    def _line_from_config(
        config: dict[str, object],
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        start = config["start_px"]
        end = config["end_px"]
        return (float(start[0]), float(start[1])), (float(end[0]), float(end[1]))  # type: ignore[index]
