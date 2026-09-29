from __future__ import annotations

from neo_tracker.ui.language import tr

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QWidget


class ProjectStatusPanel(QWidget):
    """Persistent project identity and save-state feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name_label = QLabel(tr('Unsaved project'))
        self.name_label.setObjectName("projectNameLabel")
        self.name_label.setAccessibleName(tr('Current project'))

        self.state_label = QLabel(tr('Not saved yet'))
        self.state_label.setObjectName("projectStateLabel")
        self.state_label.setProperty("projectState", "new")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setAccessibleName(tr('Project save state'))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.state_label)

    def set_state(self, project_path: str | Path | None, *, dirty: bool) -> None:
        path = Path(project_path) if project_path is not None else None
        if path is None:
            self.name_label.setText(tr('Unsaved project'))
            self.name_label.setToolTip(tr('This project does not have a file yet.'))
        else:
            self.name_label.setText(tr('Project: {v0}', v0=path.name))
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
        self.state_label.setText(tr(text))
        self.state_label.setProperty("projectState", state)
        self.state_label.setToolTip(tr(detail))
        self.state_label.setAccessibleDescription(tr(detail))
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
    dialog.setWindowTitle(tr('Unsaved project changes'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(tr('Save changes to {v0} before {v1}?', v0=project_name, v1=tr(action)))
    dialog.setInformativeText(
        tr('If you discard them, task changes, results, edits, calibration, and media relinks since the last save will be lost.')
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
    dialog.setWindowTitle(tr('Unapplied editor work'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(tr('Discard unfinished editor work before {v0}?', v0=tr(action)))
    draft_list = "\n".join(f"• {name}" for name in draft_names)
    dialog.setInformativeText(
        tr('The following work is not yet part of the saved project:\n{v0}\n\nFinish or apply it before continuing, or explicitly discard these drafts.', v0=draft_list)
    )
    discard_button = dialog.addButton(tr('Discard Drafts'), QMessageBox.ButtonRole.DestructiveRole)
    discard_button.setObjectName("discardDraftsButton")
    keep_button = dialog.addButton(tr('Keep Editing'), QMessageBox.ButtonRole.RejectRole)
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
    dialog.setAccessibleName(tr('Confirm current tracking result replacement'))
    dialog.setWindowTitle(tr('Replace current tracking results'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(tr('Replace the current Results/Edits for {v0}?', v0=task_name))
    result_label = "result" if result_count == 1 else "results"
    edit_label = "manual edit" if edit_count == 1 else "manual edits"
    dialog.setInformativeText(
        tr('Starting a full tracking run replaces {v0:,} current {v1} and {v2:,} {v3}. Runs keeps configuration and outcome audit only; it cannot restore the old result values or manual anchors.', v0=result_count, v1=result_label, v2=edit_count, v3=edit_label)
    )
    replace_button = dialog.addButton(tr('Run + Replace Results/Edits'), QMessageBox.ButtonRole.DestructiveRole)
    replace_button.setObjectName("confirmResultReplacementButton")
    replace_button.setAccessibleDescription(
        tr('Start full tracking and permanently replace the current result values and manual edits.')
    )
    keep_button = dialog.addButton(tr('Keep Current Results'), QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepCurrentResultsButton")
    keep_button.setAccessibleDescription(tr('Cancel full tracking and leave current Results and Edits unchanged.'))
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
    dialog.setAccessibleName(tr('Confirm later tracking result replacement'))
    dialog.setWindowTitle(tr('Replace later tracking results'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(tr('Rerun after frame {v0} and replace the current tail for {v1}?', v0=anchor_frame, v1=task_name))
    result_label = "result" if result_count == 1 else "results"
    edit_label = "manual edit" if affected_edit_count == 1 else "manual edits"
    if affected_edit_count:
        edit_detail = tr("{count:,} active {label} in that range will remain in Edits as historical audit and be marked superseded.",
                         count=affected_edit_count, label=edit_label)
    else:
        edit_detail = tr("No active manual edits in that range will be superseded.")
    dialog.setInformativeText(
        tr('Starting at frame {v0} replaces {v1:,} later {v2}. {v3} The selected frame and earlier Results/Edits stay current. Runs keeps configuration and outcome audit only; it cannot restore the old later result values.', v0=start_frame, v1=result_count, v2=result_label, v3=edit_detail)
    )
    replace_button = dialog.addButton(
        tr('Rerun + Replace Later Results'),
        QMessageBox.ButtonRole.DestructiveRole,
    )
    replace_button.setObjectName("confirmRerunReplacementButton")
    replace_button.setAccessibleDescription(
        tr('Rerun from the next frame, replace the current later result values, and mark affected manual edits superseded.')
    )
    keep_button = dialog.addButton(tr('Keep Current Tail'), QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepCurrentTailButton")
    keep_button.setAccessibleDescription(
        tr('Cancel the rerun and leave current Results and Edits unchanged.')
    )
    dialog.setDefaultButton(keep_button)
    dialog.setEscapeButton(keep_button)
    return dialog


def build_config_result_protection_dialog(
    parent: QWidget,
    *,
    task_name: str,
    result_count: int,
    edit_count: int,
) -> QMessageBox:
    dialog = QMessageBox(parent)
    dialog.setObjectName("configResultProtectionDialog")
    dialog.setAccessibleName(tr('Confirm current tracking result replacement'))
    dialog.setWindowTitle(tr('Replace current tracking results'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(
        tr('Apply this configuration change and replace the current Results/Edits for {v0}?', v0=task_name)
    )
    if result_count or edit_count:
        result_label = "result" if result_count == 1 else "results"
        edit_label = "manual edit" if edit_count == 1 else "manual edits"
        affected = tr("{count:,} current {label} and {edits:,} {edit_label}",
                      count=result_count, label=result_label, edits=edit_count, edit_label=edit_label)
    else:
        affected = tr("the current tracking result summary")
    dialog.setInformativeText(
        tr('This configuration change replaces {v0}. Runs keeps configuration and outcome audit only; it cannot restore the old result values or manual anchors.', v0=affected)
    )
    replace_button = dialog.addButton(
        tr('Apply + Replace Results/Edits'),
        QMessageBox.ButtonRole.DestructiveRole,
    )
    replace_button.setObjectName("confirmConfigResultReplacementButton")
    replace_button.setAccessibleDescription(
        tr('Apply the configuration change and permanently replace the current result values and manual edits.')
    )
    keep_button = dialog.addButton(tr('Keep Current Results'), QMessageBox.ButtonRole.RejectRole)
    keep_button.setObjectName("keepConfigCurrentResultsButton")
    keep_button.setAccessibleDescription(
        tr('Cancel the configuration change and leave current Results and Edits unchanged.')
    )
    dialog.setDefaultButton(keep_button)
    dialog.setEscapeButton(keep_button)
    return dialog
