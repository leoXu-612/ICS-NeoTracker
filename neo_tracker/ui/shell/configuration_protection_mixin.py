from __future__ import annotations

from neo_tracker.ui.project_controller import DesktopTask
from neo_tracker.ui.project_status_panel import build_config_result_protection_dialog


class ConfigurationProtectionMixin:
    """Keep configuration changes from replacing results or JSON drafts silently."""

    def _confirm_config_result_replacement(self, task: DesktopTask) -> bool:
        if not self._task_has_tracking_result_state(task):
            return True
        dialog = build_config_result_protection_dialog(
            self,
            task_name=task.title(),
            result_count=len(task.pipeline.results),
            edit_count=len(task.edit_history),
        )
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is not None and clicked.objectName() == "confirmConfigResultReplacementButton":
            return True
        self.statusBar().showMessage(
            "Configuration change canceled. Current Results/Edits are unchanged.",
            6000,
        )
        return False

    def _confirm_draft_replacement(self, action: str, replaced_drafts: tuple[str, ...]) -> bool:
        active_drafts = self._refresh_draft_state()
        conflicts = tuple(name for name in replaced_drafts if name in active_drafts)
        if not conflicts:
            return True
        if self._ask_unapplied_drafts(action, conflicts):
            return True
        self.statusBar().showMessage(
            "Configuration change canceled. Unapplied editor work is unchanged.",
            6000,
        )
        return False

    def _confirm_configuration_change(
        self,
        task: DesktopTask,
        action: str,
        replaced_drafts: tuple[str, ...] = ("Pipeline JSON",),
    ) -> bool:
        return self._confirm_draft_replacement(
            action, replaced_drafts
        ) and self._confirm_config_result_replacement(task)
