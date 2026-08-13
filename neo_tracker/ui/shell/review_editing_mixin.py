from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtWidgets import QMessageBox

from neo_tracker.core import TrackerResult
from neo_tracker.ui.project_controller import DesktopTask


@dataclass(frozen=True)
class ReviewUndo:
    task: DesktopTask
    result_index: int
    result: TrackerResult
    history_length: int
    history_entry: dict[str, object]
    expected_generation: int
    previous_dirty: bool
    previous_saved_fingerprint: str | None
    previous_current_fingerprint: str | None
    edit_revision: int


class ReviewEditingMixin:
    """One-level, session-only undo for copy-on-write Review edits."""

    def _start_manual_correction(self) -> None:
        if self._analysis_thread is not None:
            self.statusBar().showMessage(
                "Cancel or finish Signal processing before correcting a result.", 6000
            )
            return
        if not self.current_task.pipeline.results:
            QMessageBox.information(self, "Correct point", "Run tracking before correcting a result.")
            return
        if not self.preview_label.begin_manual_point_selection():
            QMessageBox.information(
                self, "Correct point", "Load a readable video frame before correcting a point."
            )
            return
        if self.review_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.review_tab)
        self.statusBar().showMessage("Click the corrected point in the preview canvas.", 6000)

    def _manual_point_selected(self, point: object) -> None:
        if self._analysis_thread is not None:
            self.preview_label.cancel_selection()
            self.statusBar().showMessage(
                "Signal processing is using a stable result snapshot; correct the point after it finishes.",
                6000,
            )
            return
        try:
            point_px = (float(point[0]), float(point[1]))  # type: ignore[index]
        except Exception:
            return
        index = self._current_result_index()
        if index is None:
            QMessageBox.information(
                self, "Correct point", "No tracking result matches the current frame."
            )
            return
        task = self.current_task
        previous_result = task.pipeline.results[index]
        try:
            edit = self.review_controller.manual_correction(task.pipeline, index, point_px)
        except Exception as exc:
            QMessageBox.warning(self, "Correct point", f"Could not map manual point:\n{exc}")
            return
        self._finish_review_edit(task, index, previous_result, edit, "manual correction")
        self.statusBar().showMessage(
            f"Corrected frame {task.pipeline.results[index].frame_index}.", 6000
        )

    def _mark_current_result_lost(self) -> None:
        if self._analysis_thread is not None:
            self.statusBar().showMessage(
                "Cancel or finish Signal processing before changing result status.", 6000
            )
            return
        index = self._current_result_index()
        if index is None:
            QMessageBox.information(
                self, "Mark lost", "Select a result row or move to a tracked frame first."
            )
            return
        task = self.current_task
        previous_result = task.pipeline.results[index]
        edit = self.review_controller.mark_lost(
            task.pipeline.results, index, pipeline=task.pipeline
        )
        self._finish_review_edit(task, index, previous_result, edit, "marking a result lost")
        self.statusBar().showMessage(
            f"Marked frame {task.pipeline.results[index].frame_index} as lost.", 6000
        )

    def _finish_review_edit(
        self,
        task: DesktopTask,
        index: int,
        previous_result: TrackerResult,
        edit: Any,
        reason: str,
    ) -> None:
        result = task.pipeline.results[index]
        previous_project_state = (
            self._project_dirty,
            self._saved_project_fingerprint,
            self._current_project_fingerprint,
        )
        task.mark_results_changed()
        self._reset_physics_context()
        self._record_review_edit(task, edit)
        self._review_undo = ReviewUndo(
            task,
            index,
            previous_result,
            len(task.edit_history),
            task.edit_history[-1],
            task.results_generation,
            *previous_project_state,
            self._project_content_revision,
        )
        self.analysis_controller.refresh_result_source_cache(
            task.pipeline.results, index, previous_result, result
        )
        if self.analysis_controller.has_result or self._analysis_thread is not None:
            self._clear_analysis_result(
                f"Tracking data changed after {reason}. Run processing again.", state="dirty"
            )
        self._refresh_edited_result(task, index, previous_result)
        self._refresh_analysis_sources()
        self._render_preview()
        self._sync_review_undo_action()

    def _review_undo_is_current(self) -> bool:
        undo = self._review_undo
        return bool(
            undo is not None
            and undo.task is self.current_task
            and undo.expected_generation == undo.task.results_generation
            and len(undo.task.edit_history) == undo.history_length
            and bool(undo.task.edit_history)
            and undo.task.edit_history[-1] is undo.history_entry
            and 0 <= undo.result_index < len(undo.task.pipeline.results)
        )

    def _sync_review_undo_action(self) -> None:
        if self._review_undo is not None and not self._review_undo_is_current():
            self._review_undo = None
        self._set_action_enabled(
            "review.undo",
            bool(
                self._review_undo_is_current()
                and self._analysis_thread is None
                and self._tracking_thread is None
                and not self.current_task.media_identity_requires_review
            ),
        )

    def _undo_review_edit(self) -> bool:
        if not self._review_undo_is_current():
            self._review_undo = None
            self._sync_review_undo_action()
            return False
        undo = self._review_undo
        assert undo is not None
        task = undo.task
        current_result = task.pipeline.results[undo.result_index]
        task.pipeline.results[undo.result_index] = undo.result
        task.edit_history.pop()
        task.mark_results_changed()
        self._review_undo = None
        self.review_controller.refresh_overlay_result(
            task.pipeline, undo.result_index, previous_result=current_result
        )
        self._reset_physics_context()
        self.analysis_controller.refresh_result_source_cache(
            task.pipeline.results, undo.result_index, current_result, undo.result
        )
        if self.analysis_controller.has_result or self._analysis_thread is not None:
            self._clear_analysis_result(
                "Tracking data changed after undoing a Review edit. Run processing again.",
                state="dirty",
            )
        self._render_edit_history(task)
        self._refresh_edited_result(task, undo.result_index, current_result)
        self._refresh_analysis_sources()
        self._render_preview()
        if (
            self._project_content_revision == undo.edit_revision
            and self._saved_project_fingerprint == undo.previous_saved_fingerprint
            and self._current_project_fingerprint is None
        ):
            self._project_content_revision += 1
            self._saved_project_fingerprint = undo.previous_saved_fingerprint
            self._current_project_fingerprint = undo.previous_current_fingerprint
            self._apply_project_state(undo.previous_dirty)
        else:
            self._mark_project_changed()
        self._sync_review_undo_action()
        self.statusBar().showMessage(
            f"Undid the most recent Review edit on frame {undo.result.frame_index}.", 6000
        )
        return True
