from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.project import TrackingRunRecord


OUTCOMES = ("complete", "partial", "canceled", "failed")
OUTCOME_COLORS = {
    "complete": ("#edf9f1", "#216e39"),
    "partial": ("#fff8e8", "#805b12"),
    "canceled": ("#f5f5f7", "#5f6368"),
    "failed": ("#fff0f0", "#a12622"),
}


@dataclass(frozen=True)
class RunHistorySelection:
    sequence: int
    record: TrackingRunRecord


def run_frame_range(record: TrackingRunRecord, *, empty: str = "no new frames") -> str:
    if record.end_frame is None:
        return empty
    return f"{record.start_frame}–{record.end_frame}"


def format_binary_bytes(byte_count: int) -> str:
    size = max(0, int(byte_count))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024.0:.1f} KiB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024.0 * 1024.0):.1f} MiB"
    return f"{size / (1024.0 * 1024.0 * 1024.0):.2f} GiB"


def run_review_cache_detail(record: TrackingRunRecord) -> str:
    if record.peak_debug_bytes <= 0:
        return ""
    detail = f"Review cache peaked at {format_binary_bytes(record.peak_debug_bytes)} during this run."
    target = record.pipeline_config.get("debug_history_max_bytes")
    if isinstance(target, int) and not isinstance(target, bool) and target > 0:
        detail += (
            f" Configured retained-data target: {format_binary_bytes(target)}."
            " The newest response may exceed the target so immediate Review remains available."
        )
    return detail


def run_input_pipeline_detail(record: TrackingRunRecord) -> str:
    if record.prefetch_frames <= 0:
        return ""
    return (
        f"Input and compute used a bounded {record.prefetch_frames}-frame pipeline. "
        "Stage averages can overlap and should not be added; "
        f"measured overlap was {record.stage_overlap_ms_per_frame:.2f} ms/frame."
    )


def format_prefetch_frames(frame_count: int) -> str:
    count = max(0, int(frame_count))
    return f"{count} frame" if count == 1 else f"{count} frames"


def format_compute_backend(record: TrackingRunRecord) -> str:
    return record.compute_backend or "Not recorded"


def format_run_source(record: TrackingRunRecord, *, basename: bool = False) -> str:
    if not record.source_path:
        return "Not recorded"
    return Path(record.source_path).name if basename else record.source_path


def format_run_source_identity(record: TrackingRunRecord) -> str:
    identity = record.source_identity
    if identity is None:
        return "Not recorded"
    verification = "full SHA-256" if identity.complete else "sampled SHA-256"
    return f"{verification} · {identity.sha256} · {identity.size_bytes:,} bytes"


def run_history_title(selection: RunHistorySelection) -> str:
    record = selection.record
    mode = "Full" if record.mode == "full" else "Rerun"
    frame_range = run_frame_range(record)
    if record.end_frame is not None:
        frame_range = f"frames {frame_range}"
    performance = (
        f" · Throughput {record.throughput_fps:.1f} fps"
        if record.has_performance_metrics
        else ""
    )
    return (
        f"#{selection.sequence} {mode} · {record.outcome.title()} · "
        f"{frame_range}{performance} · {record.duration_s:.2f} s"
    )


def run_history_tooltip(selection: RunHistorySelection) -> str:
    record = selection.record
    detail = (
        f"Started: {record.started_at}\n"
        f"Processed: {record.processed_frames} frames\n"
        f"Final results: {record.result_count}\n"
        f"Pipeline config: {record.pipeline_digest}\n"
        f"Compute backend: {format_compute_backend(record)}\n"
        f"Source: {format_run_source(record)}\n"
        f"Source identity: {format_run_source_identity(record)}"
    )
    if record.has_performance_metrics:
        detail += (
            f"\nThroughput: {record.throughput_fps:.1f} fps"
            f"\nInput: {record.input_ms_per_frame:.2f} ms/frame"
            f"\nCompute: {record.processing_ms_per_frame:.2f} ms/frame"
            f"\nTracking elapsed: {record.tracking_elapsed_s:.3f} s"
        )
        pipeline_detail = run_input_pipeline_detail(record)
        if pipeline_detail:
            detail += f"\n{pipeline_detail}"
        if record.peak_debug_bytes > 0:
            detail += f"\n{run_review_cache_detail(record)}"
    else:
        detail += "\nPerformance: not recorded"
    if record.note:
        detail += f"\nNote: {record.note}"
    return detail


def _display_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    return str(value)


def flatten_pipeline_config(config: dict[str, object], prefix: str = "$") -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key in sorted(config):
        value = config[key]
        path = f"{prefix}.{key}"
        if isinstance(value, dict):
            flattened.update(flatten_pipeline_config(value, path))
        else:
            flattened[path] = _display_value(value)
    return flattened


def run_summary_rows(
    older: RunHistorySelection,
    newer: RunHistorySelection,
) -> list[tuple[str, str, str]]:
    first = older.record
    second = newer.record
    def performance_value(record: TrackingRunRecord, value: float, suffix: str) -> str:
        return f"{value:.3f} {suffix}" if record.has_performance_metrics else "—"

    fields = (
        ("Started (UTC)", first.started_at, second.started_at),
        ("Mode", first.mode.title(), second.mode.title()),
        ("Outcome", first.outcome.title(), second.outcome.title()),
        ("Frames", run_frame_range(first), run_frame_range(second)),
        ("Processed", f"{first.processed_frames} frames", f"{second.processed_frames} frames"),
        ("Final results", str(first.result_count), str(second.result_count)),
        ("Source path", format_run_source(first), format_run_source(second)),
        ("Source identity", format_run_source_identity(first), format_run_source_identity(second)),
        ("Compute backend", format_compute_backend(first), format_compute_backend(second)),
        ("Duration", f"{first.duration_s:.3f} s", f"{second.duration_s:.3f} s"),
        (
            "Tracking elapsed",
            performance_value(first, first.tracking_elapsed_s, "s"),
            performance_value(second, second.tracking_elapsed_s, "s"),
        ),
        (
            "Throughput",
            performance_value(first, first.throughput_fps, "fps"),
            performance_value(second, second.throughput_fps, "fps"),
        ),
        (
            "Input / frame",
            performance_value(first, first.input_ms_per_frame, "ms"),
            performance_value(second, second.input_ms_per_frame, "ms"),
        ),
        (
            "Compute / frame",
            performance_value(first, first.processing_ms_per_frame, "ms"),
            performance_value(second, second.processing_ms_per_frame, "ms"),
        ),
        (
            "Input prefetch",
            format_prefetch_frames(first.prefetch_frames) if first.prefetch_frames > 0 else "—",
            format_prefetch_frames(second.prefetch_frames) if second.prefetch_frames > 0 else "—",
        ),
        (
            "Input / compute overlap",
            performance_value(first, first.stage_overlap_ms_per_frame, "ms")
            if first.prefetch_frames > 0
            else "—",
            performance_value(second, second.stage_overlap_ms_per_frame, "ms")
            if second.prefetch_frames > 0
            else "—",
        ),
        (
            "Review cache peak",
            format_binary_bytes(first.peak_debug_bytes) if first.peak_debug_bytes > 0 else "—",
            format_binary_bytes(second.peak_debug_bytes) if second.peak_debug_bytes > 0 else "—",
        ),
        ("Config digest", first.pipeline_digest, second.pipeline_digest),
        ("Note", first.note or "—", second.note or "—"),
    )
    return list(fields)


def run_config_difference_rows(
    older: RunHistorySelection,
    newer: RunHistorySelection,
) -> list[tuple[str, str, str]]:
    first = flatten_pipeline_config(older.record.pipeline_config)
    second = flatten_pipeline_config(newer.record.pipeline_config)
    return [
        (path, first.get(path, "—"), second.get(path, "—"))
        for path in sorted(set(first) | set(second))
        if first.get(path) != second.get(path)
    ]


class RunHistoryPanel(QWidget):
    compareRequested = Signal(object)
    exportRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[TrackingRunRecord] = []
        self._visible: list[RunHistorySelection] = []

        self.outcome_filter = QComboBox()
        self.outcome_filter.setObjectName("runHistoryOutcomeFilter")
        self.outcome_filter.setAccessibleName("Filter tracking runs by outcome")
        self.outcome_filter.setToolTip("Show all tracking runs or one outcome.")
        self.compare_button = QPushButton("Compare 2")
        self.compare_button.setObjectName("compareTrackingRunsButton")
        self.compare_button.setAccessibleName("Compare two selected tracking runs")
        self.export_button = QPushButton("Export CSV")
        self.export_button.setObjectName("exportTrackingRunsButton")
        self.export_button.setAccessibleName("Export visible tracking runs to CSV")
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("runHistoryList")
        self.list_widget.setAccessibleName("Tracking run history")
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.performance_label = QLabel(
            "Select one run to inspect Backend, Input, Compute, and Review cache performance"
        )
        self.performance_label.setObjectName("runPerformanceLabel")
        self.performance_label.setProperty("performanceState", "empty")
        self.performance_label.setWordWrap(True)
        self.performance_label.setMinimumHeight(64)
        self.performance_label.setAccessibleName("Selected tracking run performance")
        self.status_label = QLabel("No tracking runs recorded yet")
        self.status_label.setObjectName("runHistoryStatusLabel")
        self.status_label.setAccessibleName("Tracking run history status")

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self.outcome_filter, 1)
        controls.addWidget(self.compare_button)
        controls.addWidget(self.export_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(controls)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.performance_label)
        layout.addWidget(self.status_label)

        self.outcome_filter.currentIndexChanged.connect(lambda _index: self._render())
        self.list_widget.itemSelectionChanged.connect(self._update_actions)
        self.compare_button.clicked.connect(self._request_compare)
        self.export_button.clicked.connect(self._request_export)
        self.set_records([])

    @property
    def visible_runs(self) -> tuple[RunHistorySelection, ...]:
        return tuple(self._visible)

    def selected_runs(self) -> tuple[RunHistorySelection, ...]:
        sequences = sorted(
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.list_widget.selectedItems()
        )
        return tuple(
            RunHistorySelection(sequence, self._records[sequence - 1])
            for sequence in sequences
            if 1 <= sequence <= len(self._records)
        )

    def set_records(self, records: list[TrackingRunRecord]) -> None:
        self._records = list(records)
        current_filter = str(self.outcome_filter.currentData() or "")
        counts = {outcome: sum(record.outcome == outcome for record in records) for outcome in OUTCOMES}
        self.outcome_filter.blockSignals(True)
        self.outcome_filter.clear()
        self.outcome_filter.addItem(f"All outcomes ({len(records)})", "")
        for outcome in OUTCOMES:
            self.outcome_filter.addItem(f"{outcome.title()} ({counts[outcome]})", outcome)
        selected_index = self.outcome_filter.findData(current_filter)
        self.outcome_filter.setCurrentIndex(max(0, selected_index))
        self.outcome_filter.blockSignals(False)
        self._render()

    def show_all(self) -> None:
        all_index = self.outcome_filter.findData("")
        if all_index >= 0 and self.outcome_filter.currentIndex() != all_index:
            self.outcome_filter.setCurrentIndex(all_index)

    def select_latest(self) -> None:
        """Select the newest visible run after a terminal tracking update."""

        if not self._records:
            return
        latest_sequence = len(self._records)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if int(item.data(Qt.ItemDataRole.UserRole) or 0) == latest_sequence:
                self.list_widget.setCurrentRow(row)
                return

    def _render(self) -> None:
        outcome = str(self.outcome_filter.currentData() or "")
        self.list_widget.clear()
        self._visible = [
            RunHistorySelection(sequence, record)
            for sequence, record in enumerate(self._records, start=1)
            if not outcome or record.outcome == outcome
        ]
        if not self._visible:
            text = "No tracking runs recorded yet" if not self._records else "No runs match this outcome"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_widget.addItem(item)
        else:
            for selection in reversed(self._visible):
                item = QListWidgetItem(run_history_title(selection))
                item.setData(Qt.ItemDataRole.UserRole, selection.sequence)
                item.setToolTip(run_history_tooltip(selection))
                background, foreground = OUTCOME_COLORS.get(
                    selection.record.outcome,
                    ("#f5f5f7", "#303034"),
                )
                item.setBackground(QColor(background))
                item.setForeground(QColor(foreground))
                self.list_widget.addItem(item)
        self._update_actions()

    def _update_actions(self) -> None:
        selections = self.selected_runs()
        selected_count = len(selections)
        visible_count = len(self._visible)
        total_count = len(self._records)
        self.compare_button.setEnabled(selected_count == 2)
        self.compare_button.setToolTip(
            "Compare metadata and pipeline configuration for the two selected runs."
            if selected_count == 2
            else f"Select exactly two runs to compare; {selected_count} selected."
        )
        self.export_button.setEnabled(visible_count > 0)
        self.export_button.setToolTip(f"Export the {visible_count} visible tracking runs to CSV.")
        if selected_count == 1:
            selection = selections[0]
            record = selection.record
            if record.has_performance_metrics:
                pipeline = (
                    f" · {record.prefetch_frames}-frame pipeline"
                    if record.prefetch_frames > 0
                    else ""
                )
                cache_peak = (
                    f" · Review peak {format_binary_bytes(record.peak_debug_bytes)}"
                    if record.peak_debug_bytes > 0
                    else ""
                )
                performance = (
                    f"Run #{selection.sequence} · Throughput {record.throughput_fps:.1f} fps\n"
                    f"Backend {format_compute_backend(record)}{pipeline}\n"
                    f"Input {record.input_ms_per_frame:.2f} ms/f · "
                    f"Compute {record.processing_ms_per_frame:.2f} ms/f{cache_peak}"
                )
                performance_state = "ready"
            elif record.processed_frames == 0:
                performance = f"Run #{selection.sequence} · No completed frames; per-frame performance unavailable"
                performance_state = "unavailable"
            else:
                performance = f"Run #{selection.sequence} · Performance not recorded for this legacy run"
                performance_state = "unavailable"
        elif selected_count > 1:
            performance = f"{selected_count} runs selected · Open Compare 2 for performance and config differences"
            performance_state = "selected"
        else:
            performance = "Select one run to inspect Backend, Input, Compute, and Review cache performance"
            performance_state = "empty"
        performance_detail = performance
        if selected_count == 1:
            performance_detail += f"\nCompute backend: {format_compute_backend(selections[0].record)}"
            pipeline_detail = run_input_pipeline_detail(selections[0].record)
            if pipeline_detail:
                performance_detail += f"\n{pipeline_detail}"
            cache_detail = run_review_cache_detail(selections[0].record)
            if cache_detail:
                performance_detail += f"\n{cache_detail}"
        self.performance_label.setText(performance)
        self.performance_label.setProperty("performanceState", performance_state)
        self.performance_label.setToolTip(performance_detail)
        self.performance_label.setAccessibleDescription(performance_detail)
        self.performance_label.style().unpolish(self.performance_label)
        self.performance_label.style().polish(self.performance_label)
        if total_count == 0:
            status = "No tracking runs recorded yet"
        else:
            status = f"Showing {visible_count} of {total_count} · {selected_count} selected"
            if selected_count != 2:
                status += " · Select 2 to compare"
        self.status_label.setText(status)
        self.status_label.setToolTip(status)
        self.status_label.setAccessibleDescription(status)

    def _request_compare(self) -> None:
        selections = self.selected_runs()
        if len(selections) == 2:
            self.compareRequested.emit(selections)

    def _request_export(self) -> None:
        if self._visible:
            self.exportRequested.emit(tuple(self._visible))


class RunHistoryComparisonDialog(QDialog):
    def __init__(
        self,
        selections: tuple[RunHistorySelection, RunHistorySelection],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        older, newer = sorted(selections, key=lambda selection: selection.sequence)
        self.older = older
        self.newer = newer
        self.setWindowTitle("Compare Tracking Runs")
        self.resize(820, 620)

        title = QLabel(f"Run #{older.sequence} → Run #{newer.sequence}")
        title.setObjectName("runComparisonTitle")
        title.setAccessibleName(f"Comparing tracking run {older.sequence} with run {newer.sequence}")
        detail = QLabel(
            "Metadata and pipeline settings are compared below. Historical result arrays are not stored."
        )
        detail.setWordWrap(True)

        self._native_widget_loader = QUiLoader(self)
        self.summary_table = self._comparison_table(
            run_summary_rows(older, newer),
            older.sequence,
            newer.sequence,
            "runSummaryComparisonTable",
        )
        self.summary_table.setAccessibleName("Tracking run metadata comparison")
        self.summary_table.setAccessibleDescription(
            "Read each metadata field across the older and newer tracking runs."
        )
        differences = run_config_difference_rows(older, newer)
        self.config_table = self._comparison_table(
            differences or [("Configuration", "No differences", "No differences")],
            older.sequence,
            newer.sequence,
            "runConfigComparisonTable",
        )
        self.config_table.setAccessibleName("Tracking run pipeline configuration differences")
        self.config_table.setAccessibleDescription(
            "Read each changed pipeline setting across the older and newer tracking runs."
        )

        tabs = QTabWidget()
        tabs.addTab(self.summary_table, "Summary")
        tabs.addTab(self.config_table, f"Config changes ({len(differences)})")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    def _comparison_table(
        self,
        rows: list[tuple[str, str, str]],
        older_sequence: int,
        newer_sequence: int,
        object_name: str,
    ) -> QTableWidget:
        # These dialogs can remain open while external macOS accessibility
        # clients inspect them, so create their tables on Qt's native side too.
        created = self._native_widget_loader.createWidget(
            "QTableWidget",
            self,
            object_name,
        )
        if not isinstance(created, QTableWidget):
            raise RuntimeError("Qt could not create a native run comparison table")
        table = created
        table.setRowCount(len(rows))
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(("Field", f"Run #{older_sequence}", f"Run #{newer_sequence}"))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setWordWrap(False)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row_index, column_index, item)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        return table
