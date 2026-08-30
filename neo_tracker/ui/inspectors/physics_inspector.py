from __future__ import annotations

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
        self.setAccessibleName("Physics analysis inspector")
        self.setAccessibleDescription(
            "Shows source, unit, validity, processing chain, fit range, parameters, and quality in text."
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        self.object_label = QLabel("No analysis selection")
        self.object_label.setObjectName("physicsInspectorObject")
        self.object_label.setWordWrap(True)
        self.object_label.setAccessibleName("Selected analysis object")
        root.addWidget(self.object_label)

        form = QFormLayout()
        self.source_label = self._value_label("Analysis source")
        self.unit_label = self._value_label("Physical unit")
        self.validity_label = self._value_label("Sample validity")
        self.processing_label = self._value_label("Processing chain")
        self.range_label = self._value_label("Fit or plot range")
        self.quality_label = self._value_label("Fit quality")
        form.addRow("Source", self.source_label)
        form.addRow("Unit", self.unit_label)
        form.addRow("Validity", self.validity_label)
        form.addRow("Processing", self.processing_label)
        form.addRow("Range", self.range_label)
        form.addRow("Quality", self.quality_label)
        root.addLayout(form)

        actions_title = QLabel("ANALYSIS ACTIONS")
        actions_title.setObjectName("sectionLabel")
        root.addWidget(actions_title)
        actions = QGridLayout()
        self.create_velocity_button = QPushButton("Create Velocity")
        self.create_acceleration_button = QPushButton("Create Acceleration")
        self.smooth_series_button = QPushButton("Smooth Series")
        self.fit_model_button = QPushButton("Fit Model")
        self.export_analysis_button = QPushButton("Export Analysis")
        self.show_residual_button = QPushButton("Show Residual")
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
            "Request a gap-aware first derivative from the kinematics engine."
        )
        self.create_acceleration_button.setAccessibleDescription(
            "Request a gap-aware second derivative from the kinematics engine."
        )
        self.smooth_series_button.setAccessibleDescription(
            "Request explicit segment-aware smoothing without silent resampling."
        )
        self.fit_model_button.setAccessibleDescription("Open the background model fit controls.")
        self.export_analysis_button.setAccessibleDescription(
            "Request CSV, safe NPZ, and Markdown export from the kinematics engine."
        )
        self.show_residual_button.setAccessibleDescription("Show or hide the current fit residual.")
        for index, button in enumerate(buttons):
            actions.addWidget(button, index // 2, index % 2)
        root.addLayout(actions)
        root.addStretch(1)
        self.clear()

    def clear(self) -> None:
        self.object_label.setText("No analysis selection")
        self.source_label.setText("No physical series")
        self.unit_label.setText("unit unavailable")
        self.validity_label.setText("No samples")
        self.processing_label.setText("None")
        self.range_label.setText("No range")
        self.quality_label.setText("No fit result")

    def show_series(self, series: SampleSeries) -> None:
        derived = bool(series.processing_chain or series.source_kind in {"derived", "fit", "residual"})
        kind = "Derived Series" if derived else "Base Series"
        self.object_label.setText(f"{kind} · {series.name}")
        self.source_label.setText(series.source_kind)
        self.unit_label.setText(series.unit or "unit unavailable")
        valid_count = int(series.valid_mask.sum())
        self.validity_label.setText(
            f"{valid_count:,} valid · {len(series) - valid_count:,} invalid/lost · {len(series):,} aligned"
        )
        self.processing_label.setText(
            " → ".join(str(step) for step in series.processing_chain)
            if series.processing_chain
            else "Raw source snapshot"
        )
        valid_times = series.time_s[series.valid_mask]
        self.range_label.setText(
            f"{float(valid_times[0]):.6g}–{float(valid_times[-1]):.6g} s"
            if valid_times.size
            else "No valid true-time range"
        )
        self.quality_label.setText("Not a fit")
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
        self.object_label.setText(f"Sample · frame {int(series.frame_indices[index])}")
        self.source_label.setText(f"{series.name} · {series.source_kind}")
        self.unit_label.setText(unit)
        self.validity_label.setText(
            f"{'Valid' if valid else 'Invalid/lost'} · {match} match · value {rendered} {unit}"
        )
        self.processing_label.setText(
            " → ".join(str(step) for step in series.processing_chain)
            if series.processing_chain
            else "Raw source snapshot"
        )
        time_s = float(series.time_s[index])
        self.range_label.setText(
            f"true time {time_s:.9g} s" if math.isfinite(time_s) else "true time unavailable"
        )
        self.quality_label.setText("Not a fit")
        self._describe_current()

    def show_fit(self, result: FitResult) -> None:
        self.object_label.setText(f"Fit · {result.model.value.title()}")
        self.source_label.setText(result.series_id)
        self.range_label.setText(f"{result.range_start_s:.6g}–{result.range_end_s:.6g} s")
        parameters = ", ".join(
            f"{name}={float(value):.8g} {unit or 'unit unavailable'}"
            for name, value, unit in zip(
                result.parameter_names,
                result.parameters,
                result.parameter_units,
            )
        )
        self.processing_label.setText(parameters or "No parameters")
        self.validity_label.setText(f"{result.sample_count:,} fit samples · status {result.status.value}")
        self.quality_label.setText(f"R² {result.r_squared:.6g} · RMSE {result.rmse:.6g}")
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
        self.object_label.setText("Plot")
        names = " · ".join(
            f"{index} {item.name}" for index, item in enumerate(items, 1)
        )
        self.source_label.setText(
            f"{len(items)} visible physical series" + (f" · {names}" if names else "")
        )
        self.unit_label.setText(
            (items[0].unit or "unit unavailable") if items else "unit unavailable"
        )
        self.validity_label.setText("Invalid/lost samples render as explicit gaps")
        self.processing_label.setText("Width-bounded min/max envelope")
        self.range_label.setText(
            f"{range_s[0]:.6g}–{range_s[1]:.6g} s" if range_s is not None else "Full true-time range"
        )
        self.quality_label.setText("Cursor and range are preserved during decimation")
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
