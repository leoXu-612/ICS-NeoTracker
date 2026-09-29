from __future__ import annotations

from neo_tracker.ui.language import tr

import json
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.ui.review_controller import ReviewController


EDIT_TYPES = ("manual_correction", "mark_lost", "rerun_after")
EDIT_TYPE_LABELS = {
    "manual_correction": "Corrected",
    "mark_lost": "Marked lost",
    "rerun_after": "Rerun",
}
EDIT_TYPE_COLORS = {
    "manual_correction": ("#eef5ff", "#245b92"),
    "mark_lost": ("#fff0f0", "#a12622"),
    "rerun_after": ("#edf9f1", "#216e39"),
}
SUPERSEDED_COLORS = ("#f0f1f3", "#646970")
OTHER_FILTER = "__other__"


@dataclass(frozen=True)
class EditHistorySelection:
    sequence: int
    entry: dict[str, object]

    @property
    def event_type(self) -> str:
        return str(self.entry.get("type", "edit"))

    @property
    def frame_index(self) -> int | None:
        value = self.entry.get("frame_index")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def is_superseded(self) -> bool:
        return ReviewController.edit_entry_is_superseded(self.entry)


def edit_history_title(selection: EditHistorySelection) -> str:
    detail = ReviewController.format_edit_history_entry(selection.entry)
    return f"#{selection.sequence} · {detail}"


class EditHistoryPanel(QWidget):
    jumpRequested = Signal(int)
    exportRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[dict[str, object]] = []
        self._visible: list[EditHistorySelection] = []

        self.event_filter = QComboBox()
        self.event_filter.setObjectName("editHistoryTypeFilter")
        self.event_filter.setAccessibleName(tr('Filter manual edits by action'))
        self.event_filter.setToolTip(tr('Show all manual edits or one action type.'))
        self.jump_button = QPushButton(tr('Jump to Frame'))
        self.jump_button.setObjectName("jumpToEditFrameButton")
        self.jump_button.setAccessibleName(tr('Jump to the frame for the selected edit'))
        self.export_button = QPushButton(tr('Export CSV'))
        self.export_button.setObjectName("exportEditHistoryButton")
        self.export_button.setAccessibleName(tr('Export visible manual edits to CSV'))
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("editHistoryList")
        self.list_widget.setAccessibleName(tr('Manual edit history'))
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.status_label = QLabel(tr('No manual edits recorded yet'))
        self.status_label.setObjectName("editHistoryStatusLabel")
        self.status_label.setAccessibleName(tr('Manual edit history status'))

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        controls.addWidget(self.event_filter, 1)
        controls.addWidget(self.jump_button)
        controls.addWidget(self.export_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(controls)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(self.status_label)

        self.event_filter.currentIndexChanged.connect(lambda _index: self._render())
        self.list_widget.itemSelectionChanged.connect(self._update_actions)
        self.jump_button.clicked.connect(self._request_jump)
        self.export_button.clicked.connect(self._request_export)
        self.set_records([])

    @property
    def visible_edits(self) -> tuple[EditHistorySelection, ...]:
        return tuple(self._visible)

    def selected_edit(self) -> EditHistorySelection | None:
        selected_items = self.list_widget.selectedItems()
        if len(selected_items) != 1:
            return None
        sequence = int(selected_items[0].data(Qt.ItemDataRole.UserRole))
        if not 1 <= sequence <= len(self._records):
            return None
        return EditHistorySelection(sequence, self._records[sequence - 1])

    def set_records(self, records: list[dict[str, object]]) -> None:
        self._records = [dict(record) for record in records]
        current_filter = str(self.event_filter.currentData() or "")
        counts = {
            event_type: sum(str(record.get("type", "edit")) == event_type for record in records)
            for event_type in EDIT_TYPES
        }
        other_count = sum(str(record.get("type", "edit")) not in EDIT_TYPES for record in records)
        self.event_filter.blockSignals(True)
        self.event_filter.clear()
        self.event_filter.addItem(f"All edits ({len(records)})", "")
        for event_type in EDIT_TYPES:
            self.event_filter.addItem(f"{EDIT_TYPE_LABELS[event_type]} ({counts[event_type]})", event_type)
        self.event_filter.addItem(f"Other ({other_count})", OTHER_FILTER)
        selected_index = self.event_filter.findData(current_filter)
        self.event_filter.setCurrentIndex(max(0, selected_index))
        self.event_filter.blockSignals(False)
        self._render()

    def show_all(self) -> None:
        all_index = self.event_filter.findData("")
        if all_index >= 0 and self.event_filter.currentIndex() != all_index:
            self.event_filter.setCurrentIndex(all_index)

    def select_sequence(self, sequence: int) -> bool:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == sequence:
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                return True
        return False

    def _matches_filter(self, selection: EditHistorySelection, event_filter: str) -> bool:
        if not event_filter:
            return True
        if event_filter == OTHER_FILTER:
            return selection.event_type not in EDIT_TYPES
        return selection.event_type == event_filter

    def _render(self) -> None:
        event_filter = str(self.event_filter.currentData() or "")
        self.list_widget.clear()
        self._visible = [
            EditHistorySelection(sequence, entry)
            for sequence, entry in enumerate(self._records, start=1)
            if self._matches_filter(EditHistorySelection(sequence, entry), event_filter)
        ]
        if not self._visible:
            text = "No edits yet" if not self._records else "No edits match this action"
            item = QListWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_widget.addItem(item)
        else:
            for selection in reversed(self._visible):
                item = QListWidgetItem(edit_history_title(selection))
                item.setData(Qt.ItemDataRole.UserRole, selection.sequence)
                item.setToolTip(json.dumps(selection.entry, indent=2, ensure_ascii=False, default=str))
                if selection.is_superseded:
                    background, foreground = SUPERSEDED_COLORS
                else:
                    background, foreground = EDIT_TYPE_COLORS.get(
                        selection.event_type,
                        ("#f5f5f7", "#303034"),
                    )
                item.setBackground(QColor(background))
                item.setForeground(QColor(foreground))
                self.list_widget.addItem(item)
        self._update_actions()

    def _update_actions(self) -> None:
        selection = self.selected_edit()
        visible_count = len(self._visible)
        total_count = len(self._records)
        frame_index = selection.frame_index if selection is not None else None
        self.jump_button.setEnabled(frame_index is not None)
        self.jump_button.setToolTip(
            (tr('Move the preview and result selection to frame {v0}.', v0=frame_index) if frame_index is not None else tr('Select an edit with an associated frame first.'))
        )
        self.export_button.setEnabled(visible_count > 0)
        self.export_button.setToolTip(tr('Export the {v0} visible manual edits to CSV.', v0=visible_count))
        if total_count == 0:
            status = "No manual edits recorded yet"
        else:
            superseded_count = sum(selection.is_superseded for selection in self._visible)
            status = f"Showing {visible_count} of {total_count}"
            if superseded_count:
                status += f" · {superseded_count} superseded"
            if selection is None:
                status += " · Select an edit to jump"
            elif frame_index is None:
                status += f" · Edit #{selection.sequence} has no frame"
            else:
                status += f" · Edit #{selection.sequence} · frame {frame_index}"
                if selection.is_superseded:
                    status += " · historical"
        self.status_label.setText(status)
        self.status_label.setToolTip(status)
        self.status_label.setAccessibleDescription(status)

    def _request_jump(self) -> None:
        selection = self.selected_edit()
        if selection is not None and selection.frame_index is not None:
            self.jumpRequested.emit(selection.frame_index)

    def _request_export(self) -> None:
        if self._visible:
            self.exportRequested.emit(tuple(self._visible))
