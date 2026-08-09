from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt

from neo_tracker.kinematics import FitResult, FitStatus, SampleSeries
from neo_tracker.ui.analysis_workspace_controller import (
    AnalysisWorkspaceState,
    FitDraft,
    KinematicsOperationRequest,
)
from neo_tracker.ui.project_controller import DesktopTask
from neo_tracker.ui.selection_session import SelectionEvent, SelectionOrigin
from neo_tracker.ui.view_state import PhysicsWorkspaceState


class PhysicsWorkspaceMixin:
    """Integrate the physics workspace without expanding the legacy window shell."""

    @staticmethod
    def _physics_task_id(task: DesktopTask) -> str:
        return f"task:{id(task):x}"

    @staticmethod
    def _physics_result_identity(task: DesktopTask) -> str:
        return f"results:{id(task.pipeline.results):x}"

    def _reset_physics_context(self) -> None:
        task = self.current_task
        self._physics_series_owner_token = id(task)
        self._physics_series_by_id = {}
        self.physics_workspace.set_series(())
        self.fit_panel.set_series(())
        self.analysis_workspace_controller.set_series(task, ())
        self.physics_inspector.clear()
        self.selection_session.activate_context(
            self._physics_task_id(task),
            self._physics_result_identity(task),
            f"unbound:{id(task.pipeline.results):x}",
        )

    def set_physics_series(
        self,
        series: Sequence[SampleSeries],
        *,
        owner: DesktopTask | None = None,
        result_identity: str | None = None,
    ) -> bool:
        """Attach immutable engine output if it still belongs to the active task."""

        task = owner or self.current_task
        if task is not self.current_task:
            return False
        items = tuple(series)
        if not items:
            self._reset_physics_context()
            return True
        if any(not isinstance(item, SampleSeries) for item in items):
            raise TypeError("physics series must contain SampleSeries values")
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("physics series_id values must be unique")
        revisions = {item.source_revision for item in items}
        if len(revisions) != 1:
            raise ValueError("physics series must share one source revision")
        source_revision = next(iter(revisions))
        self._physics_series_owner_token = id(task)
        self._physics_series_by_id = {item.series_id: item for item in items}
        self.selection_session.activate_context(
            self._physics_task_id(task),
            result_identity or self._physics_result_identity(task),
            source_revision,
        )
        for item in items:
            outcome = self.selection_session.attach_series(item)
            if not outcome.accepted:
                return False
        self.physics_workspace.set_series(items)
        self.fit_panel.set_series(items)
        self.analysis_workspace_controller.set_series(task, items)
        self.physics_inspector.show_series(items[0])
        self.selection_session.select_frame(
            int(task.preview_frame_index),
            origin=SelectionOrigin.VIDEO,
            expected_source_revision=source_revision,
        )
        return True

    def set_kinematics_fit_operator(self, operator: object | None) -> None:
        """Inject the engine protocol without importing an engine implementation."""

        self.analysis_workspace_controller.set_fit_operator(operator)  # type: ignore[arg-type]

    def _physics_sample_activated(self, series_id: str, sample_index: int) -> None:
        state = self.selection_session.state
        self.selection_session.select_sample(
            series_id,
            int(sample_index),
            origin=SelectionOrigin.TABLE,
            expected_source_revision=state.source_revision,
        )

    def _physics_plot_sample_activated(self, series_id: str, sample_index: int) -> None:
        state = self.selection_session.state
        self.selection_session.select_sample(
            series_id,
            int(sample_index),
            origin=SelectionOrigin.PLOT,
            expected_source_revision=state.source_revision,
        )

    def _run_physics_fit(self, draft_object: object) -> None:
        if not isinstance(draft_object, FitDraft):
            return
        if self._background_tasks.closing:
            return
        if not self.analysis_workspace_controller.run_fit(
            self.current_task,
            draft_object,
        ):
            self.fit_panel.apply_state(self.analysis_workspace_controller.state)

    def _cancel_physics_fit(self) -> None:
        self.analysis_workspace_controller.cancel("Fit canceled by user.")

    def _physics_fit_draft_changed(self) -> None:
        state = self.analysis_workspace_controller.state
        if state.fit_result is not None or self.analysis_workspace_controller.busy:
            self.analysis_workspace_controller.invalidate(
                "Series, model, or true-time range changed. Run the fit again."
            )

    def _physics_fit_state_changed(self, state: AnalysisWorkspaceState) -> None:
        self.fit_panel.apply_state(state)
        self._update_physics_actions(state)
        source = self._physics_series_by_id.get(state.selected_series_id or "")
        if (
            source is None
            or state.fit_result is None
            or state.fit_result.status is not FitStatus.OK
        ):
            if source is not None:
                self.physics_workspace.plot.set_fit_result(source, None)
            return
        self.physics_workspace.plot.set_fit_result(
            source,
            state.fit_result,
            residual=state.residual_visible,
        )

    def _physics_fit_ready(self, result: object) -> None:
        if not isinstance(result, FitResult) or result.status is not FitStatus.OK:
            return
        state = self.analysis_workspace_controller.state
        fit_id = (
            f"fit:{state.source_revision}:{result.model.value}:"
            f"{result.range_start_s:.12g}:{result.range_end_s:.12g}"
        )
        self.selection_session.select_fit(
            fit_id,
            origin=SelectionOrigin.FIT,
            expected_source_revision=result.source_revision,
        )
        self.physics_inspector.show_fit(result)

    def _export_physics_analysis(self) -> None:
        if not self.analysis_workspace_controller.request_export():
            self.statusBar().showMessage(
                "Run a successful physics fit before exporting analysis.",
                5000,
            )

    def _create_physics_velocity(self) -> None:
        self.analysis_workspace_controller.request_derivative(1)

    def _create_physics_acceleration(self) -> None:
        self.analysis_workspace_controller.request_derivative(2)

    def _smooth_physics_series(self) -> None:
        self.analysis_workspace_controller.request_smoothing()

    def _show_physics_fit(self) -> None:
        self.physics_workspace.show_page("Fit")
        self.fit_panel.series_combo.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _toggle_physics_residual(self) -> None:
        state = self.analysis_workspace_controller.state
        self.analysis_workspace_controller.set_residual_visible(not state.residual_visible)

    def _physics_operation_requested(self, request: object) -> None:
        if not isinstance(request, KinematicsOperationRequest):
            return
        self.physicsOperationRequested.emit(request)
        labels = {
            "derivative": "Derivative request sent to the kinematics engine.",
            "smooth": "Smoothing request sent to the kinematics engine.",
            "export": "Physics export request sent to the kinematics engine.",
        }
        self.statusBar().showMessage(labels[request.operation], 5000)

    def _update_physics_actions(self, state: AnalysisWorkspaceState) -> None:
        has_series = bool(
            state.selected_series_id
            and state.selected_series_id in self._physics_series_by_id
        )
        mutable = has_series and state.status != "running" and not self._background_tasks.closing
        has_fit = bool(
            state.fit_result is not None
            and state.fit_result.status is FitStatus.OK
        )
        self._update_action(
            "physics.velocity",
            enabled=mutable,
            tool_tip="Request a gap-aware first derivative from the kinematics engine.",
        )
        self._update_action(
            "physics.acceleration",
            enabled=mutable,
            tool_tip="Request a gap-aware second derivative from the kinematics engine.",
        )
        self._update_action(
            "physics.smooth",
            enabled=mutable,
            tool_tip="Request segment-aware smoothing without implicit resampling.",
        )
        self._update_action(
            "physics.fit",
            enabled=mutable,
            tool_tip="Open model and true-time fit controls.",
        )
        self._update_action(
            "physics.export",
            enabled=has_fit,
            tool_tip="Request CSV, safe NPZ, and Markdown physics analysis export.",
        )
        self._update_action(
            "physics.residual",
            enabled=has_fit,
            text="Hide Residual" if state.residual_visible else "Show Residual",
            tool_tip="Show or hide the current fit residual layer.",
        )

    def _physics_workspace_page_changed(self, page: str) -> None:
        state = self.analysis_workspace_controller.state
        source = self._physics_series_by_id.get(state.selected_series_id or "")
        if page == "Plot":
            self.physics_inspector.show_plot(
                len(self._physics_series_by_id),
                range_s=self.physics_workspace.plot.selected_range,
            )
        elif page == "Fit" and state.fit_result is not None:
            self.physics_inspector.show_fit(state.fit_result)
        elif source is not None:
            self.physics_inspector.show_series(source)
        else:
            self.physics_inspector.clear()

    def _selection_session_changed(self, event: SelectionEvent) -> None:
        state = event.current
        if state.selected_task_id != self._physics_task_id(self.current_task):
            return
        self.physics_workspace.apply_selection(
            state.selected_series_id,
            state.selected_sample_index,
            state.selected_frame_index,
            state.selected_time_s,
            state.match.value,
        )
        source = self._physics_series_by_id.get(state.selected_series_id or "")
        if source is not None and state.selected_sample_index is not None:
            self.analysis_workspace_controller.select_series(source.series_id)
            self.physics_inspector.show_sample(
                source,
                state.selected_sample_index,
                match=state.match.value,
            )
        elif source is not None:
            self.physics_inspector.show_series(source)
        if (
            event.origin is not SelectionOrigin.VIDEO
            and state.selected_frame_index is not None
            and int(self.current_task.preview_frame_index) != int(state.selected_frame_index)
        ):
            self._applying_selection_revision = state.selection_revision
            try:
                self._preview_frame_changed(int(state.selected_frame_index))
            finally:
                self._applying_selection_revision = None

    def _physics_splitter_moved(self, _position: int, _index: int) -> None:
        if self.physics_workspace.collapsed:
            return
        sizes = self.workspace_splitter.sizes()
        if len(sizes) == 2 and sizes[1] >= 120:
            self.physics_workspace.remember_height(sizes[1])
            if not self._restoring_physics_layout and not self._canvas_focus_active:
                self._physics_layout_store.save(self.physics_workspace.layout_state())

    def _physics_workspace_layout_changed(self, state: object) -> None:
        splitter = getattr(self, "workspace_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        total = sum(sizes) or max(1, splitter.height())
        if self.physics_workspace.collapsed:
            lower = min(38, total)
        else:
            lower = min(self.physics_workspace.preferred_height, max(120, total - 180))
        splitter.setSizes([max(1, total - lower), lower])
        if (
            isinstance(state, PhysicsWorkspaceState)
            and not self._restoring_physics_layout
            and not self._canvas_focus_active
        ):
            self._physics_layout_store.save(state)

    @property
    def canvas_focus_active(self) -> bool:
        return self._canvas_focus_active

    def _restore_physics_layout(self) -> None:
        state = self._physics_layout_store.load()
        if state is None:
            return
        self._restoring_physics_layout = True
        try:
            self.physics_workspace.apply_layout_state(state)
            self._physics_workspace_layout_changed(state)
        finally:
            self._restoring_physics_layout = False

    def _toggle_canvas_focus(self) -> None:
        if not self._canvas_focus_active:
            self._canvas_focus_active = True
            self._canvas_focus_main_sizes = tuple(self.main_splitter.sizes())
            self._canvas_focus_sidebar_visible = self.right_sidebar.isVisible()
            self.right_sidebar.hide()
            self.main_splitter.setSizes([max(1, self.main_splitter.width()), 0])
            self.physics_workspace.set_canvas_focus(True)
            self._update_action(
                "view.canvas_focus",
                text="Exit Focus",
                tool_tip="Restore the inspector and physics workspace layout.",
            )
            self.canvas_focus_button.setAccessibleName("Exit canvas focus mode")
            self.statusBar().showMessage("Canvas Focus · inspector hidden · physics workspace collapsed")
            return
        self._canvas_focus_active = False
        if self._canvas_focus_sidebar_visible:
            self.right_sidebar.show()
        if self._canvas_focus_main_sizes:
            self.main_splitter.setSizes(list(self._canvas_focus_main_sizes))
        self.physics_workspace.set_canvas_focus(False)
        self._update_action(
            "view.canvas_focus",
            text="Canvas Focus",
            tool_tip="Temporarily enlarge the video canvas.",
        )
        self.canvas_focus_button.setAccessibleName("Enter canvas focus mode")
        self.statusBar().showMessage("Canvas Focus ended · workspace layout restored", 4000)

    def _physics_route_requested(self, route: str) -> None:
        if route == "Signal":
            for index in range(self.sidebar_tabs.count()):
                page = self.sidebar_tabs.widget(index)
                if page.objectName() == "signalTab":
                    self.sidebar_tabs.setCurrentIndex(index)
                    return
        if self.review_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.review_tab)
        if route == "Runs":
            self.review_history_tabs.setCurrentIndex(0)
        elif route == "Edits":
            self.review_history_tabs.setCurrentIndex(1)
