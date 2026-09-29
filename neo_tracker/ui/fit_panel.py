from __future__ import annotations

from neo_tracker.ui.language import tr

import json
import math
from collections.abc import Sequence

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.kinematics import FitRequest, FitResult, FitStatus, SampleSeries
from neo_tracker.ui.analysis_workspace_controller import AnalysisWorkspaceState, FitDraft


class FitPanel(QWidget):
    """Fit request/result presentation; numerical fitting never runs in this widget."""

    runRequested = Signal(object)
    cancelRequested = Signal()
    residualToggled = Signal(bool)
    exportRequested = Signal()
    draftChanged = Signal()

    _MODELS = {
        "Linear": "linear",
        "Quadratic": "quadratic",
        "Exponential": "exponential",
        "Sinusoidal": "sinusoidal",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: dict[str, SampleSeries] = {}
        self._busy = False
        self._engine_available = True
        self._syncing = False
        self._draft_baseline: tuple[object, ...] | None = None
        self._request_baseline: FitRequest | None = None
        self._range_values = [0.0, 0.0]
        self.setAccessibleName(tr('Physics model fit'))
        self.setAccessibleDescription(
            tr('Choose a physical series, model, and true-time range. Fits run in the background.')
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)

        form = QFormLayout()
        self.series_combo = QComboBox()
        self.series_combo.setAccessibleName(tr('Fit physical series'))
        self.series_combo.currentIndexChanged.connect(self._series_changed)
        self.series_combo.currentIndexChanged.connect(self._emit_draft_changed)
        form.addRow(tr('Series'), self.series_combo)
        self.model_combo = QComboBox()
        self.model_combo.setAccessibleName(tr('Fit model'))
        for display, value in self._MODELS.items():
            self.model_combo.addItem(tr(display), value)
        self.model_combo.currentTextChanged.connect(self._model_changed)
        self.model_combo.currentTextChanged.connect(self._emit_draft_changed)
        form.addRow(tr('Model'), self.model_combo)

        range_row = QHBoxLayout()
        self.range_start_spin = self._range_spin("Fit range start in true seconds")
        self.range_end_spin = self._range_spin("Fit range end in true seconds")
        self.range_start_spin.valueChanged.connect(lambda value: self._range_edited(0, value))
        self.range_end_spin.valueChanged.connect(lambda value: self._range_edited(1, value))
        range_row.addWidget(self.range_start_spin)
        range_row.addWidget(QLabel(tr('to')))
        range_row.addWidget(self.range_end_spin)
        form.addRow(tr('True-time range'), range_row)
        self.valid_only_checkbox = QCheckBox(tr('Use only samples marked valid'))
        self.valid_only_checkbox.setChecked(True)
        self.valid_only_checkbox.setAccessibleName(tr('Fit valid samples only'))
        self.valid_only_checkbox.setToolTip(
            tr('Exclude finite samples that the tracking or processing pipeline marked invalid.')
        )
        self.valid_only_checkbox.toggled.connect(self._emit_draft_changed)
        form.addRow(tr('Samples'), self.valid_only_checkbox)

        self.initial_parameters_label = QLabel(tr('Initial parameters'))
        self.initial_parameters_edit = QLineEdit("{}")
        self.initial_parameters_edit.setAccessibleName(tr('Nonlinear initial parameters JSON'))
        self.initial_parameters_edit.setToolTip(
            tr('Named JSON values, for example {"omega": 6.28, "amplitude": 0.2}.')
        )
        self.initial_parameters_edit.textChanged.connect(self._emit_draft_changed)
        form.addRow(self.initial_parameters_label, self.initial_parameters_edit)
        self.bounds_label = QLabel(tr('Bounds'))
        self.bounds_edit = QLineEdit("{}")
        self.bounds_edit.setAccessibleName(tr('Nonlinear parameter bounds JSON'))
        self.bounds_edit.setToolTip(tr('Named JSON pairs, for example {"omega": [0.1, 20.0]}.'))
        self.bounds_edit.textChanged.connect(self._emit_draft_changed)
        form.addRow(self.bounds_label, self.bounds_edit)
        root.addLayout(form)

        action_row = QHBoxLayout()
        self.run_button = QPushButton(tr('Run Fit'))
        self.run_button.setObjectName("runPhysicsFitButton")
        self.run_button.setAccessibleName(tr('Run physics model fit'))
        self.run_button.clicked.connect(self._emit_run)
        self.cancel_button = QPushButton(tr('Cancel'))
        self.cancel_button.setObjectName("cancelPhysicsFitButton")
        self.cancel_button.setAccessibleName(tr('Cancel active physics model fit'))
        self.cancel_button.clicked.connect(self.cancelRequested)
        self.cancel_button.setEnabled(False)
        self.residual_checkbox = QCheckBox(tr('Show residual'))
        self.residual_checkbox.setAccessibleName(tr('Show fit residual plot'))
        self.residual_checkbox.toggled.connect(self.residualToggled)
        self.residual_checkbox.setEnabled(False)
        self.export_button = QPushButton(tr('Export'))
        self.export_button.setObjectName("exportPhysicsAnalysisButton")
        self.export_button.setAccessibleName(tr('Export physics fit analysis'))
        self.export_button.clicked.connect(self.exportRequested)
        self.export_button.setEnabled(False)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.residual_checkbox)
        action_row.addStretch(1)
        action_row.addWidget(self.export_button)
        root.addLayout(action_row)

        self.status_label = QLabel(tr('Choose a physical series.'))
        self.status_label.setObjectName("physicsFitStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName(tr('Physics fit status'))
        self.status_label.setAccessibleDescription(self.status_label.text())
        root.addWidget(self.status_label)
        self.summary_label = QLabel(tr('No fit result'))
        self.summary_label.setObjectName("physicsFitSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName(tr('Physics fit quality summary'))
        root.addWidget(self.summary_label)

        self.parameter_table = QTableWidget(0, 4)
        self.parameter_table.setHorizontalHeaderLabels((tr('Parameter'), tr('Value'), tr('Unit'), tr('Std. error')))
        self.parameter_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.parameter_table.horizontalHeader().setStretchLastSection(True)
        self.parameter_table.verticalHeader().setVisible(False)
        self.parameter_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.parameter_table.setAccessibleName(tr('Fit parameters and uncertainty'))
        root.addWidget(self.parameter_table, 1)
        self._model_changed(self.model_combo.currentText())
        self._update_controls()

    def set_series(self, series: Sequence[SampleSeries]) -> None:
        self._syncing = True
        items = tuple(series)
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            self._syncing = False
            raise ValueError("fit panel series_id values must be unique")
        previous_id = str(self.series_combo.currentData() or "")
        previous_source = self._series.get(previous_id)
        preserve_draft = bool(
            self._draft_baseline is not None
            and previous_source is not None
            and any(
                item.series_id == previous_id
                and item.source_revision == previous_source.source_revision
                for item in items
            )
        )
        self._series = {item.series_id: item for item in items}
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        for item in items:
            unit = item.unit or "unit unavailable"
            self.series_combo.addItem(f"{tr(item.name)} · {unit}", item.series_id)
        selected = self.series_combo.findData(previous_id)
        self.series_combo.setCurrentIndex(selected if selected >= 0 else (0 if items else -1))
        self.series_combo.blockSignals(False)
        if not preserve_draft:
            self._series_changed(self.series_combo.currentIndex())
        self.clear_result()
        self._set_status(
            "Choose a model and true-time range."
            if items
            else "No physical series is available."
        )
        self._update_controls()
        if not preserve_draft:
            self.mark_draft_applied()
        self._syncing = False

    def is_dirty(self) -> bool:
        if (
            not self._series
            or self._draft_baseline is None
            or self._draft_state() == self._draft_baseline
        ):
            return False
        try:
            draft = self.draft()
            return draft.to_request(self._series[draft.series_id]) != self._request_baseline
        except Exception:
            # Invalid edits remain a draft until corrected or explicitly discarded.
            return True

    def mark_draft_applied(self) -> None:
        self._draft_baseline = self._draft_state() if self._series else None
        try:
            draft = self.draft()
            self._request_baseline = draft.to_request(self._series[draft.series_id])
        except Exception:
            self._request_baseline = None

    def restore_draft(self, draft: FitDraft) -> bool:
        if self.is_dirty():
            return False
        source = self._series.get(draft.series_id)
        series_index = self.series_combo.findData(draft.series_id)
        model_index = self.model_combo.findData(draft.model)
        if source is None or series_index < 0 or model_index < 0:
            return False
        try:
            draft.to_request(source)
        except (TypeError, ValueError):
            return False
        self._syncing = True
        try:
            self.series_combo.setCurrentIndex(series_index)
            self.model_combo.setCurrentIndex(model_index)
            self._set_range(draft.range_start_s, draft.range_end_s)
            self.valid_only_checkbox.setChecked(draft.use_valid_only)
            self.initial_parameters_edit.setText(
                json.dumps(dict(draft.initial_parameters), sort_keys=True)
            )
            self.bounds_edit.setText(json.dumps(dict(draft.bounds), sort_keys=True))
            self.clear_result()
            self.mark_draft_applied()
        finally:
            self._syncing = False
        return True

    def revert_draft(self) -> None:
        baseline = self._draft_baseline
        if baseline is None:
            return
        series_id, model, range_start, range_end, initial, bounds, valid_only = baseline
        self._syncing = True
        try:
            series_index = self.series_combo.findData(series_id)
            if series_index >= 0:
                self.series_combo.setCurrentIndex(series_index)
            model_index = self.model_combo.findData(model)
            if model_index >= 0:
                self.model_combo.setCurrentIndex(model_index)
            self._set_range(float(range_start), float(range_end))
            self.initial_parameters_edit.setText(str(initial))
            self.bounds_edit.setText(str(bounds))
            self.valid_only_checkbox.setChecked(bool(valid_only))
        finally:
            self._syncing = False

    def draft(self) -> FitDraft:
        series_id = self.series_combo.currentData()
        if series_id is None or str(series_id) not in self._series:
            raise ValueError("Choose a physical series before running a fit.")
        model = str(self.model_combo.currentData())
        nonlinear = model in {"exponential", "sinusoidal"}
        initial = (
            self._numeric_mapping(self.initial_parameters_edit.text(), "Initial parameters")
            if nonlinear
            else {}
        )
        bounds_data = self._json_mapping(self.bounds_edit.text(), "Bounds") if nonlinear else {}
        bounds: dict[str, tuple[float, float]] = {}
        for key, value in bounds_data.items():
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError(f"Bounds.{key} must be a two-value JSON array.")
            if any(
                isinstance(bound, bool) or not isinstance(bound, (int, float))
                for bound in value
            ):
                raise ValueError(f"Bounds.{key} must contain numeric values.")
            bounds[key] = (float(value[0]), float(value[1]))
        return FitDraft(
            series_id=str(series_id),
            model=model,
            range_start_s=self._range_values[0],
            range_end_s=self._range_values[1],
            initial_parameters=initial,
            bounds=bounds,
            use_valid_only=self.valid_only_checkbox.isChecked(),
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if self._busy:
            self._set_status("Fit running in the background…")
        self.cancel_button.setEnabled(self._busy)
        self._update_controls()

    def apply_state(self, state: AnalysisWorkspaceState) -> None:
        self._engine_available = state.engine_available
        self.set_busy(state.status == "running")
        self._set_status(state.message)
        if state.fit_result is not None:
            self.show_result(state.fit_result)
        else:
            self.clear_result()
        self.residual_checkbox.blockSignals(True)
        self.residual_checkbox.setChecked(state.residual_visible)
        self.residual_checkbox.blockSignals(False)

    def show_result(self, result: FitResult) -> None:
        if result.status is not FitStatus.OK:
            self.clear_result()
            message = result.message or f"Fit ended with status: {result.status.value}."
            self._set_status(message)
            self.summary_label.setText(
                tr('{v0} · no plot or residual layer was created', v0=result.status.value.title())
            )
            self.summary_label.setAccessibleDescription(message)
            return
        display_rows = [
            (
                name,
                float(result.parameters[index]),
                result.parameter_units[index],
                float(result.standard_errors[index]),
            )
            for index, name in enumerate(result.parameter_names)
        ]
        if result.model.value == "quadratic" and "a" in result.parameter_names:
            index = result.parameter_names.index("a")
            unit = result.parameter_units[index]
            display_rows.append(
                (
                    "2a (acceleration for position)",
                    2.0 * float(result.parameters[index]),
                    unit,
                    2.0 * float(result.standard_errors[index]),
                )
            )
        self.parameter_table.setRowCount(len(display_rows))
        for row, (name, number, unit, error) in enumerate(display_rows):
            values = (
                name,
                format(number, ".10g"),
                unit or "unit unavailable",
                format(error, ".6g") if math.isfinite(error) else "unavailable",
            )
            for column, value in enumerate(values):
                self.parameter_table.setItem(row, column, QTableWidgetItem(value))
        self.summary_label.setText(
            tr('{v0} · R² {v1:.6g} · RMSE {v2:.6g} · {v3:,} samples · {v4:.6g}–{v5:.6g} s', v0=result.model.value.title(), v1=result.r_squared, v2=result.rmse, v3=result.sample_count, v4=result.range_start_s, v5=result.range_end_s)
        )
        detail = (
            f"{result.model.value} fit using {result.sample_count:,} samples. "
            f"R squared {result.r_squared:.6g}; RMSE {result.rmse:.6g}."
        )
        self.summary_label.setToolTip(detail)
        self.summary_label.setAccessibleDescription(detail)
        self.residual_checkbox.setEnabled(True)
        self.export_button.setEnabled(True)

    def clear_result(self) -> None:
        self.parameter_table.setRowCount(0)
        self.summary_label.setText(tr('No fit result'))
        self.summary_label.setToolTip(tr('No fit result is available.'))
        self.summary_label.setAccessibleDescription(tr('No fit result is available.'))
        self.residual_checkbox.setEnabled(False)
        self.export_button.setEnabled(False)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(tr(text))
        self.status_label.setAccessibleDescription(text)

    def _emit_run(self) -> None:
        try:
            draft = self.draft()
            # FitRequest performs the authoritative finite/range/model validation.
            draft.to_request(self._series[draft.series_id])
        except Exception as exc:
            self._set_status(f"Fit settings invalid: {exc}")
            return
        self.runRequested.emit(draft)

    def _series_changed(self, _index: int) -> None:
        source = self._series.get(str(self.series_combo.currentData()))
        if source is None:
            return
        valid_times = source.time_s[source.valid_mask]
        if valid_times.size:
            minimum, maximum = float(valid_times[0]), float(valid_times[-1])
            self._set_range(minimum, maximum)
        else:
            self._set_range(0.0, 0.0)
        self.clear_result()

    def _set_range(self, start: float, end: float) -> None:
        previous_syncing = self._syncing
        self._syncing = True
        try:
            for index, value in enumerate((start, end)):
                spin = (self.range_start_spin, self.range_end_spin)[index]
                with QSignalBlocker(spin):
                    spin.setValue(value)
                self._range_edited(index, min(spin.maximum(), max(spin.minimum(), value)))
        finally:
            self._syncing = previous_syncing

    def _range_edited(self, index: int, value: float) -> None:
        # ponytail: native edits keep nine decimals; a full-precision editor is needed for finer edits.
        self._range_values[index] = value
        spin = (self.range_start_spin, self.range_end_spin)[index]
        detail = f"Exact fit boundary: {value:.17g} s. Display and manual edits use nine decimal places."
        spin.setToolTip(detail)
        spin.setAccessibleDescription(detail)
        self._update_controls()
        self._emit_draft_changed()

    def _model_changed(self, model: str) -> None:
        nonlinear = self.model_combo.currentData() in {"exponential", "sinusoidal"}
        for widget in (
            self.initial_parameters_label,
            self.initial_parameters_edit,
            self.bounds_label,
            self.bounds_edit,
        ):
            widget.setVisible(nonlinear)

    def _emit_draft_changed(self, *_args: object) -> None:
        if not self._syncing:
            self.draftChanged.emit()

    def _draft_state(self) -> tuple[object, ...]:
        return (
            self.series_combo.currentData(),
            self.model_combo.currentData(),
            self._range_values[0],
            self._range_values[1],
            self.initial_parameters_edit.text(),
            self.bounds_edit.text(),
            self.valid_only_checkbox.isChecked(),
        )

    def _update_controls(self, *_args: object) -> None:
        available = bool(self._series) and self._engine_available and not self._busy
        valid_range = self._range_values[0] < self._range_values[1]
        self.series_combo.setEnabled(available)
        self.model_combo.setEnabled(available)
        self.range_start_spin.setEnabled(available)
        self.range_end_spin.setEnabled(available)
        self.valid_only_checkbox.setEnabled(available)
        self.initial_parameters_edit.setEnabled(available)
        self.bounds_edit.setEnabled(available)
        self.run_button.setEnabled(available and valid_range)
        if not self._series:
            detail = "Choose a physical series before running a fit."
        elif not valid_range:
            detail = "Choose an increasing true-time range before running a fit."
        elif not self._engine_available:
            detail = "The physics fit engine is unavailable."
        elif self._busy:
            detail = "A physics fit is already running."
        else:
            detail = "Run the selected model fit in the background."
        self.run_button.setToolTip(detail)
        self.run_button.setAccessibleDescription(detail)

    @staticmethod
    def _range_spin(accessible_name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e12, 1e12)
        spin.setDecimals(9)
        spin.setSingleStep(0.01)
        spin.setSuffix(" s")
        spin.setAccessibleName(accessible_name)
        return spin

    @staticmethod
    def _json_mapping(text: str, label: str) -> dict[str, object]:
        def unique_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"{label} contains duplicate key {key!r}.")
                result[key] = item
            return result

        try:
            value = json.loads(
                text or "{}",
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"{label} contains non-finite constant {token}.")
                ),
                object_pairs_hook=unique_mapping,
            )
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a JSON object.")
        return {str(key): item for key, item in value.items()}

    @classmethod
    def _numeric_mapping(cls, text: str, label: str) -> dict[str, float]:
        value = cls._json_mapping(text, label)
        result: dict[str, float] = {}
        for key, item in value.items():
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{label}.{key} must be numeric.")
            try:
                result[key] = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}.{key} must be numeric.") from exc
        return result
