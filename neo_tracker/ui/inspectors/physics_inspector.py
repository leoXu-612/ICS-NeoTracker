from __future__ import annotations

from neo_tracker.ui.language import tr

from collections.abc import Sequence
import math

from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.kinematics import FitResult, SampleSeries


class PhysicsInspector(QWidget):
    """Text-first provenance and quality inspector for the current analysis object."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(tr('Physics analysis inspector'))
        self.setAccessibleDescription(
            tr('Shows source, unit, validity, processing chain, fit range, parameters, and quality in text.')
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        self.object_label = QLabel(tr('No analysis selection'))
        self.object_label.setObjectName("physicsInspectorObject")
        self.object_label.setWordWrap(True)
        self.object_label.setAccessibleName(tr('Selected analysis object'))
        root.addWidget(self.object_label)

        form = QFormLayout()
        self.source_label = self._value_label("Analysis source")
        self.unit_label = self._value_label("Physical unit")
        self.validity_label = self._value_label("Sample validity")
        self.processing_label = self._value_label("Processing chain")
        self.range_label = self._value_label("Fit or plot range")
        self.quality_label = self._value_label("Fit quality")
        form.addRow(tr('Source'), self.source_label)
        form.addRow(tr('Unit'), self.unit_label)
        form.addRow(tr('Validity'), self.validity_label)
        form.addRow(tr('Processing'), self.processing_label)
        form.addRow(tr('Range'), self.range_label)
        form.addRow(tr('Quality'), self.quality_label)
        root.addLayout(form)

        actions_title = QLabel(tr('ANALYSIS ACTIONS'))
        actions_title.setObjectName("sectionLabel")
        root.addWidget(actions_title)
        actions = QGridLayout()
        self.create_velocity_button = QPushButton(tr('Create Velocity'))
        self.create_acceleration_button = QPushButton(tr('Create Acceleration'))
        self.smooth_series_button = QPushButton(tr('Smooth Series'))
        self.fit_model_button = QPushButton(tr('Fit Model'))
        self.export_analysis_button = QPushButton(tr('Export Analysis'))
        self.show_residual_button = QPushButton(tr('Show Residual'))
        buttons = (
            self.create_velocity_button,
            self.create_acceleration_button,
            self.smooth_series_button,
            self.fit_model_button,
            self.export_analysis_button,
            self.show_residual_button,
        )
        for button in buttons:
            button.setAccessibleName(button.text())
        self.create_velocity_button.setAccessibleDescription(
            tr('Request a gap-aware first derivative from the kinematics engine.')
        )
        self.create_acceleration_button.setAccessibleDescription(
            tr('Request a gap-aware second derivative from the kinematics engine.')
        )
        self.smooth_series_button.setAccessibleDescription(
            tr('Request explicit segment-aware smoothing without silent resampling.')
        )
        self.fit_model_button.setAccessibleDescription(tr('Open the background model fit controls.'))
        self.export_analysis_button.setAccessibleDescription(
            tr('Request CSV, safe NPZ, and Markdown export from the kinematics engine.')
        )
        self.show_residual_button.setAccessibleDescription(tr('Show or hide the current fit residual.'))
        for index, button in enumerate(buttons):
            actions.addWidget(button, index // 2, index % 2)
        root.addLayout(actions)
        root.addStretch(1)
        self.clear()

    def clear(self) -> None:
        self.object_label.setText(tr('No analysis selection'))
        self.source_label.setText(tr('No physical series'))
        self.unit_label.setText(tr('unit unavailable'))
        self.validity_label.setText(tr('No samples'))
        self.processing_label.setText(tr('None'))
        self.range_label.setText(tr('No range'))
        self.quality_label.setText(tr('No fit result'))
        self._describe_current()

    def show_series(self, series: SampleSeries) -> None:
        kind = "Derived Series" if series.is_derived else "Base Series"
        self.object_label.setText(tr('{v0} · {v1}', v0=kind, v1=series.name))
        self.source_label.setText(series.source_kind)
        self.unit_label.setText(series.unit or "unit unavailable")
        valid_count = int(series.valid_mask.sum())
        self.validity_label.setText(
            tr('{v0:,} valid · {v1:,} invalid/lost · {v2:,} aligned', v0=valid_count, v1=len(series) - valid_count, v2=len(series))
        )
        self.processing_label.setText(
            " → ".join(str(step) for step in series.processing_chain)
            if series.processing_chain
            else "Raw source snapshot"
        )
        valid_times = series.time_s[series.valid_mask]
        self.range_label.setText(
            (tr('{v0:.6g}–{v1:.6g} s', v0=float(valid_times[0]), v1=float(valid_times[-1])) if valid_times.size else tr('No valid true-time range'))
        )
        self.quality_label.setText(tr('Not a fit'))
        self._describe_current()

    def show_sample(
        self,
        series: SampleSeries,
        sample_index: int,
        *,
        match: str,
    ) -> None:
        index = int(sample_index)
        if not 0 <= index < len(series):
            raise IndexError("inspector sample index is out of range")
        valid = bool(series.valid_mask[index])
        value = float(series.values[index])
        rendered = format(value, ".10g") if valid and math.isfinite(value) else "unavailable"
        unit = series.unit or "unit unavailable"
        self.object_label.setText(tr('Sample · frame {v0}', v0=int(series.frame_indices[index])))
        self.source_label.setText(tr('{v0} · {v1}', v0=series.name, v1=series.source_kind))
        self.unit_label.setText(unit)
        self.validity_label.setText(
            tr('{v0} · {v1} match · value {v2} {v3}', v0='Valid' if valid else 'Invalid/lost', v1=match, v2=rendered, v3=unit)
        )
        self.processing_label.setText(
            " → ".join(str(step) for step in series.processing_chain)
            if series.processing_chain
            else "Raw source snapshot"
        )
        time_s = float(series.time_s[index])
        self.range_label.setText(
            (tr('true time {v0:.9g} s', v0=time_s) if math.isfinite(time_s) else tr('true time unavailable'))
        )
        self.quality_label.setText(tr('Not a fit'))
        self._describe_current()

    def show_fit(self, result: FitResult) -> None:
        self.object_label.setText(tr('Fit · {v0}', v0=result.model.value.title()))
        self.source_label.setText(result.series_id)
        self.range_label.setText(tr('{v0:.6g}–{v1:.6g} s', v0=result.range_start_s, v1=result.range_end_s))
        parameters = ", ".join(
            f"{name}={float(value):.8g} {unit or 'unit unavailable'}"
            for name, value, unit in zip(
                result.parameter_names,
                result.parameters,
                result.parameter_units,
            )
        )
        self.processing_label.setText(parameters or "No parameters")
        self.validity_label.setText(tr('{v0:,} fit samples · status {v1}', v0=result.sample_count, v1=result.status.value))
        self.quality_label.setText(tr('R² {v0:.6g} · RMSE {v1:.6g}', v0=result.r_squared, v1=result.rmse))
        self.unit_label.setText(
            ", ".join(unit or "unit unavailable" for unit in result.parameter_units)
            or "unit unavailable"
        )
        self._describe_current()

    def show_plot(
        self,
        series: Sequence[SampleSeries],
        *,
        range_s: tuple[float, float] | None,
    ) -> None:
        items = tuple(series)
        self.object_label.setText(tr('Plot'))
        names = " · ".join(
            f"{index} {item.name}" for index, item in enumerate(items, 1)
        )
        self.source_label.setText(
            f"{len(items)} visible physical series" + (f" · {names}" if names else "")
        )
        self.unit_label.setText(
            (items[0].unit or "unit unavailable") if items else "unit unavailable"
        )
        self.validity_label.setText(tr('Invalid/lost samples render as explicit gaps'))
        self.processing_label.setText(tr('Width-bounded min/max envelope'))
        self.range_label.setText(
            (tr('{v0:.6g}–{v1:.6g} s', v0=range_s[0], v1=range_s[1]) if range_s is not None else tr('No valid true-time range'))
        )
        self.quality_label.setText(tr('Cursor and range are preserved during decimation'))
        self._describe_current()

    def _describe_current(self) -> None:
        detail = " · ".join(
            (
                self.object_label.text(),
                self.source_label.text(),
                self.unit_label.text(),
                self.validity_label.text(),
                self.processing_label.text(),
                self.range_label.text(),
                self.quality_label.text(),
            )
        )
        self.setAccessibleDescription(detail)
        self.object_label.setToolTip(detail)

    @staticmethod
    def _value_label(accessible_name: str) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setAccessibleName(accessible_name)
        return label
