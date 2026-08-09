from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QWidget


class ProjectStatusPanel(QWidget):
    """Persistent project identity and save-state feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name_label = QLabel("Unsaved project")
        self.name_label.setObjectName("projectNameLabel")
        self.name_label.setAccessibleName("Current project")

        self.state_label = QLabel("Not saved yet")
        self.state_label.setObjectName("projectStateLabel")
        self.state_label.setProperty("projectState", "new")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setAccessibleName("Project save state")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.state_label)

    def set_state(self, project_path: str | Path | None, *, dirty: bool) -> None:
        path = Path(project_path) if project_path is not None else None
        if path is None:
            self.name_label.setText("Unsaved project")
            self.name_label.setToolTip("This project does not have a file yet.")
        else:
            self.name_label.setText(f"Project: {path.name}")
            self.name_label.setToolTip(str(path))
        self.name_label.setAccessibleDescription(self.name_label.toolTip())

        if dirty:
            text = "Unsaved changes"
            state = "dirty"
            detail = "Project content has changed since the last save."
        elif path is not None:
            text = "Saved"
            state = "saved"
            detail = "Project content matches the last saved version."
        else:
            text = "Not saved yet"
            state = "new"
            detail = "Add or change project content, then save it to create a project file."
        self.state_label.setText(text)
        self.state_label.setProperty("projectState", state)
        self.state_label.setToolTip(detail)
        self.state_label.setAccessibleDescription(detail)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)


def build_unsaved_changes_dialog(
    parent: QWidget,
    *,
    project_name: str,
    action: str,
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName("unsavedChangesDialog")
    dialog.setWindowTitle("Unsaved project changes")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(f"Save changes to {project_name} before {action}?")
    dialog.setInformativeText(
        "If you discard them, task changes, results, edits, calibration, and media relinks "
        "since the last save will be lost."
    )
    dialog.setStandardButtons(
        QMessageBox.StandardButton.Save
        | QMessageBox.StandardButton.Discard
        | QMessageBox.StandardButton.Cancel
    )
    dialog.setDefaultButton(QMessageBox.StandardButton.Save)
    dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
    return dialog


def build_unapplied_drafts_dialog(
    parent: QWidget,
    *,
    action: str,
    draft_names: Sequence[str],
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName("unappliedDraftsDialog")
    dialog.setWindowTitle("Unapplied editor work")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(f"Discard unfinished editor work before {action}?")
    draft_list = "\n".join(f"• {name}" for name in draft_names)
    dialog.setInformativeText(
        "The following work is not yet part of the saved project:\n"
        f"{draft_list}\n\n"
        "Finish or apply it before continuing, or explicitly discard these drafts."
    )
    discard_button = dialog.addButton("Discard Drafts", QMessageBox.ButtonRole.DestructiveRole)
    discard_button.setObjectName("discardDraftsButton")
    keep_button = dialog.addButton("Keep Editing", QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepEditingButton")
    dialog.setDefaultButton(keep_button)
    dialog.setEscapeButton(keep_button)
    return dialog


def build_result_replacement_dialog(
    parent: QWidget,
    *,
    task_name: str,
    result_count: int,
    edit_count: int,
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName("resultReplacementDialog")
    dialog.setAccessibleName("Confirm current tracking result replacement")
    dialog.setWindowTitle("Replace current tracking results")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(f"Replace the current Results/Edits for {task_name}?")
    result_label = "result" if result_count == 1 else "results"
    edit_label = "manual edit" if edit_count == 1 else "manual edits"
    dialog.setInformativeText(
        f"Starting a full tracking run replaces {result_count:,} current {result_label} "
        f"and {edit_count:,} {edit_label}. Runs keeps configuration and outcome audit only; "
        "it cannot restore the old result values or manual anchors."
    )
    replace_button = dialog.addButton("Run + Replace Results/Edits", QMessageBox.ButtonRole.DestructiveRole)
    replace_button.setObjectName("confirmResultReplacementButton")
    replace_button.setAccessibleDescription(
        "Start full tracking and permanently replace the current result values and manual edits."
    )
    keep_button = dialog.addButton("Keep Current Results", QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepCurrentResultsButton")
    keep_button.setAccessibleDescription("Cancel full tracking and leave current Results and Edits unchanged.")
    dialog.setDefaultButton(keep_button)
    dialog.setEscapeButton(keep_button)
    return dialog


def build_rerun_replacement_dialog(
    parent: QWidget,
    *,
    task_name: str,
    anchor_frame: int,
    start_frame: int,
    result_count: int,
    affected_edit_count: int,
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName("rerunReplacementDialog")
    dialog.setAccessibleName("Confirm later tracking result replacement")
    dialog.setWindowTitle("Replace later tracking results")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(f"Rerun after frame {anchor_frame} and replace the current tail for {task_name}?")
    result_label = "result" if result_count == 1 else "results"
    edit_label = "manual edit" if affected_edit_count == 1 else "manual edits"
    if affected_edit_count:
        edit_detail = (
            f"{affected_edit_count:,} active {edit_label} in that range will remain in Edits "
            "as historical audit and be marked superseded."
        )
    else:
        edit_detail = "No active manual edits in that range will be superseded."
    dialog.setInformativeText(
        f"Starting at frame {start_frame} replaces {result_count:,} later {result_label}. "
        f"{edit_detail} The selected frame and earlier Results/Edits stay current. "
        "Runs keeps configuration and outcome audit only; it cannot restore the old later result values."
    )
    replace_button = dialog.addButton(
        "Rerun + Replace Later Results",
        QMessageBox.ButtonRole.DestructiveRole,
    )
    replace_button.setObjectName("confirmRerunReplacementButton")
    replace_button.setAccessibleDescription(
        "Rerun from the next frame, replace the current later result values, and mark affected manual edits superseded."
    )
    keep_button = dialog.addButton("Keep Current Tail", QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepCurrentTailButton")
    keep_button.setAccessibleDescription(
        "Cancel the rerun and leave current Results and Edits unchanged."
    )
    dialog.setDefaultButton(keep_button)
    dialog.setEscapeButton(keep_button)
    return dialog
