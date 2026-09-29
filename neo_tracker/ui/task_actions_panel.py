from __future__ import annotations

from neo_tracker.ui.language import tr

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class TaskActionsPanel(QWidget):
    """Safe, reversible task removal controls for the Media inspector."""

    removeConfirmed = Signal()
    undoRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = ""
        self._impact = ""
        self._confirming = False
        self._undo_title: str | None = None

        self.summary_label = QLabel(tr('No task selected'))
        self.summary_label.setObjectName("taskActionSummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName(tr('Selected project task summary'))

        self.message_label = QLabel()
        self.message_label.setObjectName("taskActionMessage")
        self.message_label.setProperty("taskActionState", "neutral")
        self.message_label.setWordWrap(True)
        self.message_label.setAccessibleName(tr('Task removal status'))
        self.message_label.hide()

        self.remove_button = QPushButton(tr('Remove Task…'))
        self.remove_button.setObjectName("removeTaskButton")
        self.remove_button.setToolTip(tr('Remove the selected task from this project without deleting its media file.'))
        self.remove_button.setAccessibleName(tr('Remove selected project task'))
        self.remove_button.clicked.connect(self._remove_clicked)

        self.cancel_button = QPushButton(tr('Cancel'))
        self.cancel_button.setObjectName("cancelTaskRemovalButton")
        self.cancel_button.setToolTip(tr('Keep the selected task in this project.'))
        self.cancel_button.setAccessibleName(tr('Cancel task removal'))
        self.cancel_button.clicked.connect(self._cancel_confirmation)
        self.cancel_button.hide()

        self.undo_button = QPushButton(tr('Undo Remove'))
        self.undo_button.setObjectName("undoTaskRemovalButton")
        self.undo_button.setToolTip(tr('Restore the most recently removed task with its results and history.'))
        self.undo_button.setAccessibleName(tr('Undo most recent task removal'))
        self.undo_button.clicked.connect(self.undoRequested.emit)
        self.undo_button.hide()

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(self.remove_button, 1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.undo_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.message_label)
        layout.addLayout(button_row)

    def set_current(
        self,
        title: str,
        *,
        result_count: int,
        edit_count: int,
        run_count: int,
        can_remove: bool,
    ) -> None:
        self._title = str(title)
        self._impact = self.impact_text(result_count, edit_count, run_count)
        self._confirming = False
        self.remove_button.setText(tr('Remove Task…'))
        self.remove_button.setProperty("taskDestructive", False)
        self.remove_button.setEnabled(bool(can_remove))
        self.cancel_button.hide()
        if can_remove:
            self.summary_label.setText(tr('Selected: {v0} · {v1}', v0=self._title, v1=self._impact))
        else:
            self.summary_label.setText(tr('No saved project task selected'))
        self._restore_feedback()
        self._refresh_remove_style()

    def show_removed(self, title: str) -> None:
        self._undo_title = str(title)
        self._confirming = False
        self.remove_button.setText(tr('Remove Task…'))
        self.remove_button.setProperty("taskDestructive", False)
        self.cancel_button.hide()
        self.undo_button.show()
        self._set_message(
            f"Removed {self._undo_title} from this project. The media file stays on disk.",
            "removed",
        )
        self._refresh_remove_style()

    def clear_undo(self) -> None:
        self._undo_title = None
        self.undo_button.hide()
        if not self._confirming:
            self.message_label.clear()
            self.message_label.hide()

    @staticmethod
    def impact_text(result_count: int, edit_count: int, run_count: int) -> str:
        counts = (
            max(0, int(result_count)),
            max(0, int(edit_count)),
            max(0, int(run_count)),
        )
        return f"{counts[0]} results · {counts[1]} edits · {counts[2]} runs"

    def _remove_clicked(self) -> None:
        if self._confirming:
            self.removeConfirmed.emit()
            return
        if not self.remove_button.isEnabled():
            return
        self._confirming = True
        self.remove_button.setText(tr('Confirm Remove'))
        self.remove_button.setProperty("taskDestructive", True)
        self.cancel_button.show()
        self._set_message(
            f"Remove {self._title}? {self._impact}. Only the project entry is removed; the media file stays on disk.",
            "warning",
        )
        self._refresh_remove_style()

    def _cancel_confirmation(self) -> None:
        self._confirming = False
        self.remove_button.setText(tr('Remove Task…'))
        self.remove_button.setProperty("taskDestructive", False)
        self.cancel_button.hide()
        self._restore_feedback()
        self._refresh_remove_style()

    def _restore_feedback(self) -> None:
        if self._undo_title is None:
            self.undo_button.hide()
            self.message_label.clear()
            self.message_label.hide()
            return
        self.undo_button.show()
        self._set_message(
            f"Removed {self._undo_title} from this project. The media file stays on disk.",
            "removed",
        )

    def _set_message(self, text: str, state: str) -> None:
        self.message_label.setText(tr(text))
        self.message_label.setProperty("taskActionState", state)
        self.message_label.setToolTip(text)
        self.message_label.setAccessibleDescription(text)
        self.message_label.show()
        self.message_label.style().unpolish(self.message_label)
        self.message_label.style().polish(self.message_label)

    def _refresh_remove_style(self) -> None:
        self.remove_button.style().unpolish(self.remove_button)
        self.remove_button.style().polish(self.remove_button)
