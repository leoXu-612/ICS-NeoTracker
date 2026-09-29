from __future__ import annotations

from functools import lru_cache
from typing import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from neo_tracker.core import TrackerResult
from neo_tracker.ui.review_controller import ReviewController
from neo_tracker.ui.language import tr


class ResultsTableModel(QAbstractTableModel):
    """Virtual tracking-results table with a bounded formatted-row cache."""

    _TONE_COLORS = {
        "manual": QColor("#edf9f1"),
        "attention": QColor("#fff8e6"),
        "lost": QColor("#fff0f0"),
    }

    def __init__(self, parent=None, *, row_cache_size: int = 1024) -> None:
        super().__init__(parent)
        self._results: list[TrackerResult] = []
        self._state_keys: tuple[str, ...] = ()
        self._state_headers: tuple[str, ...] = ()
        self._headers: tuple[str, ...] = ("Frame", "Time", "Conf", "Status")
        self._summary = "Run tracking to populate the review table."
        self._tracking_summary = "Results: none"
        self._summary_tooltip = ""
        self._confidence_total = 0.0
        self._status_counts: dict[str, int] = {}
        self._row_values = lru_cache(maxsize=max(32, int(row_cache_size)))(self._format_row)

    @property
    def state_headers(self) -> tuple[str, ...]:
        return self._state_headers

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def tracking_summary(self) -> str:
        return self._tracking_summary

    @property
    def summary_tooltip(self) -> str:
        return self._summary_tooltip

    @property
    def cached_row_count(self) -> int:
        return int(self._row_values.cache_info().currsize)

    def set_results(
        self,
        results: Sequence[TrackerResult],
        state_units: dict[str, str],
        tracking_note: str = "",
    ) -> None:
        """Reset the model around an immutable membership snapshot.

        Tracking can replace or grow its live list in a worker. A shallow tuple keeps
        table row membership stable until the next GUI-thread render without copying
        result payloads or pre-formatting every cell.
        """

        result_items = list(results)
        state_keys = tuple(ReviewController.result_state_keys(result_items))
        state_headers = tuple(
            ReviewController.state_key_label(key, state_units) for key in state_keys
        )
        clean_note = tracking_note.strip()
        self._confidence_total = sum(float(result.confidence) for result in result_items)
        self._status_counts = {}
        for result in result_items:
            self._status_counts[result.status] = self._status_counts.get(result.status, 0) + 1
        tracking_summary = self._tracking_summary_from_aggregates(result_items)
        summary = self._review_summary(tracking_summary, state_headers)
        if clean_note:
            compact_note = clean_note if len(clean_note) <= 240 else clean_note[:237] + "..."
            summary += f"\nRun note: {compact_note}"

        self.beginResetModel()
        self._results = result_items
        self._state_keys = state_keys
        self._state_headers = state_headers
        self._headers = ("Frame", "Time", *state_headers, "Conf", "Status")
        self._tracking_summary = tracking_summary
        self._summary = summary
        self._summary_tooltip = clean_note
        self._row_values.cache_clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._results)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._headers)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self._headers)
        ):
            return tr(self._headers[section])
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or index.row() < 0 or index.row() >= len(self._results):
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            values = self._row_values(row)
            return values[index.column()] if 0 <= index.column() < len(values) else None
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.BackgroundRole:
            tone = ReviewController.result_tone(self._results[row])
            return self._TONE_COLORS.get(tone)
        return None

    def refresh_row(self, result_index: int) -> None:
        row = int(result_index)
        if row < 0 or row >= len(self._results):
            return
        self._row_values.cache_clear()
        self.dataChanged.emit(
            self.index(row, 0),
            self.index(row, max(0, self.columnCount() - 1)),
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.BackgroundRole],
        )

    def refresh_result(self, result_index: int, result: TrackerResult) -> bool:
        """Replace one copy-on-write result without resetting the virtual table."""

        row = int(result_index)
        if row < 0 or row >= len(self._results):
            return False
        previous = self._results[row]
        if set(result.filtered_state) != set(previous.filtered_state):
            return False
        self._results[row] = result
        self._confidence_total += float(result.confidence) - float(previous.confidence)
        previous_count = self._status_counts.get(previous.status, 0) - 1
        if previous_count > 0:
            self._status_counts[previous.status] = previous_count
        else:
            self._status_counts.pop(previous.status, None)
        self._status_counts[result.status] = self._status_counts.get(result.status, 0) + 1
        self._tracking_summary = self._tracking_summary_from_aggregates(self._results)
        self._summary = self._review_summary(self._tracking_summary, self._state_headers)
        if self._summary_tooltip:
            compact_note = (
                self._summary_tooltip
                if len(self._summary_tooltip) <= 240
                else self._summary_tooltip[:237] + "..."
            )
            self._summary += f"\nRun note: {compact_note}"
        self.refresh_row(row)
        return True

    def _tracking_summary_from_aggregates(self, results: Sequence[TrackerResult]) -> str:
        if not results:
            return tr("Results: none")
        avg_confidence = self._confidence_total / len(results)
        status_summary = ", ".join(
            f"{tr(key)} {value}" for key, value in sorted(self._status_counts.items())
        )
        return tr("Results: {count} | avg confidence {confidence:.2f} | {statuses}",
                  count=len(results), confidence=avg_confidence, statuses=status_summary)

    @staticmethod
    def _review_summary(tracking_summary: str, state_headers: Sequence[str]) -> str:
        if tracking_summary == tr("Results: none"):
            return (
                "Run tracking to populate the review table. "
                "Use calibration and ROI first for better physical units."
            )
        keys = ", ".join(state_headers) if state_headers else "no numeric state"
        return f"{tracking_summary} | State columns: {keys}"

    def _format_row(self, row: int) -> tuple[str, ...]:
        result = self._results[row]
        values = [str(result.frame_index), f"{result.time_s:.6g}"]
        values.extend(
            ReviewController.format_state_value(result.filtered_state.get(key))
            for key in self._state_keys
        )
        values.extend((f"{result.confidence:.3f}", tr(result.status)))
        return tuple(values)
