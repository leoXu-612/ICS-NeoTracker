from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QTimer, Qt

from neo_tracker.application.kinematics_workspace_coordinator import (
    KinematicsWorkspaceJob,
    KinematicsWorkspaceOutput,
    KinematicsWorkspaceTask,
)
from neo_tracker.kinematics import FitRequest, FitResult, FitStatus, SampleSeries
from neo_tracker.project import (
    AnalysisDefinition,
    AnalysisSourceReference,
    AnalysisWorkspaceSnapshot,
)
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
        return task.task_id

    @staticmethod
    def _physics_result_identity(task: DesktopTask) -> str:
        return f"{task.task_id}:results:{task.results_generation}"

    def _reset_physics_context(self, *, schedule_build: bool = True) -> None:
        task = self.current_task
        coordinator = getattr(self, "_kinematics_workspace_coordinator", None)
        if coordinator is not None and coordinator.busy:
            coordinator.cancel()
        self._physics_pending_build = None
        self._physics_replay_queue = []
        self._physics_replay_active_id = None
        self._physics_replay_active_operation = None
        self._physics_definition_states = {}
        self._physics_series_owner_token = id(task)
        self._physics_series_by_id = {}
        self.physics_workspace.set_series(())
        self.fit_panel.set_series(())
        self.analysis_workspace_controller.set_series(task, ())
        self.physics_inspector.clear()
        self.selection_session.activate_context(
            self._physics_task_id(task),
            self._physics_result_identity(task),
            f"checking:{task.results_generation}" if task.pipeline.results else "empty",
        )
        if task.pipeline.results and schedule_build and not self._background_tasks.closing:
            self._physics_pending_build = (task, task.results_generation)
            QTimer.singleShot(0, self._start_pending_physics_build)
        self._refresh_draft_state()

    def refresh_physics_series(self) -> None:
        """Rebuild current physical series from detached Results in the background."""

        self._reset_physics_context(schedule_build=True)

    def _start_pending_physics_build(self) -> bool:
        pending = getattr(self, "_physics_pending_build", None)
        if pending is None:
            return False
        task, generation = pending
        if (
            task is not self.current_task
            or generation != task.results_generation
            or not task.pipeline.results
            or self._background_tasks.closing
        ):
            self._physics_pending_build = None
            return False
        coordinator = self._kinematics_workspace_coordinator
        if coordinator.busy:
            return False
        request = KinematicsWorkspaceTask(
            owner=task,
            task_id=task.task_id,
            results_generation=generation,
            operation="build",
            results=task.pipeline.results,
            units=dict(task.pipeline.state_model.units()),
        )
        if not coordinator.start(request):
            return False
        self._physics_pending_build = None
        self.physics_workspace.cursor_label.setText("true time — · checking Results source revision")
        self.physics_workspace.cursor_label.setAccessibleDescription(
            "Building immutable physical series from current tracking Results in the background."
        )
        self._update_physics_actions(self.analysis_workspace_controller.state)
        return True

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
        if len(items) > 64:
            raise ValueError("the physics workspace supports at most 64 attached series")
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
        self._refresh_draft_state()
        return True

    def _attach_physics_series(self, additions: Sequence[SampleSeries]) -> bool:
        items = tuple(self._physics_series_by_id.values()) + tuple(additions)
        if len(items) > 64:
            return False
        ids = [item.series_id for item in items]
        if len(ids) != len(set(ids)):
            return False
        accepted = self.set_physics_series(items, owner=self.current_task)
        if accepted and additions:
            selected = additions[-1]
            self.analysis_workspace_controller.select_series(selected.series_id)
            index = self.physics_workspace.series_combo.findData(selected.series_id)
            if index >= 0:
                self.physics_workspace.series_combo.setCurrentIndex(index)
            self.physics_inspector.show_series(selected)
        return accepted

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
        source = self._physics_series_by_id.get(draft_object.series_id)
        if source is None:
            return
        selection = self.selection_session.select_series(
            source.series_id,
            origin=SelectionOrigin.FIT,
            expected_source_revision=source.source_revision,
        )
        if not selection.accepted:
            return
        if not self.analysis_workspace_controller.run_fit(
            self.current_task,
            draft_object,
        ):
            self.fit_panel.apply_state(self.analysis_workspace_controller.state)
            return
        self.fit_panel.mark_draft_applied()
        self._refresh_draft_state()

    def _cancel_physics_fit(self) -> None:
        self.analysis_workspace_controller.cancel("Fit canceled by user.")

    def _physics_fit_draft_changed(self) -> None:
        state = self.analysis_workspace_controller.state
        if state.fit_result is not None or self.analysis_workspace_controller.busy:
            self.analysis_workspace_controller.invalidate(
                "Series, model, or true-time range changed. Run the fit again."
            )
        self._refresh_draft_state()

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
        replay_id = getattr(self, "_physics_replay_active_id", None)
        replay_operation = getattr(self, "_physics_replay_active_operation", None)
        if replay_id is not None and replay_operation == "fit":
            definition = self._physics_definition_by_id(replay_id)
            self._physics_replay_active_id = None
            self._physics_replay_active_operation = None
            if definition is not None:
                self._apply_replayed_definition_view(definition, fit_result=result)
            return
        request = state.active_request
        source = self._physics_series_by_id.get(result.series_id)
        if request is not None and source is not None:
            identity = (
                request.series_id,
                request.model.value,
                request.range_start_s,
                request.range_end_s,
                tuple(sorted(request.initial_parameters.items())),
                tuple(sorted(request.bounds.items())),
            )
            identity_text = repr(identity)
            if not request.use_valid_only:
                identity_text += ":all-finite"
            digest = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:20]
            try:
                definition = AnalysisDefinition(
                    analysis_id=f"fit:{digest}",
                    name=f"{result.model.value.title()} fit · {source.name}"[:256],
                    source_series=AnalysisSourceReference(
                        self.current_task.task_id,
                        source.series_id,
                        source.source_kind,
                        source.source_revision,
                    ),
                    fit_config=request.to_dict(),
                    selected_range_s=(result.range_start_s, result.range_end_s),
                    view_state={"page": "Fit", "residual_visible": False},
                    provenance={"engine": "neo-tracker-kinematics-v0.3"},
                )
            except (TypeError, ValueError) as exc:
                self.statusBar().showMessage(
                    f"Could not save fit definition: {exc}",
                    8000,
                )
                return
            self._store_physics_definition(definition)

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
        if self._background_tasks.closing:
            return
        source = self._physics_series_by_id.get(request.series_id)
        if source is None or source.source_revision != request.source_revision:
            self.statusBar().showMessage(
                "Physics request rejected because its Results revision is stale.",
                6000,
            )
            return
        export_paths: dict[str, Path] = {}
        if request.operation == "export":
            picker = self._physics_export_directory_picker
            if picker is None:
                self.statusBar().showMessage(
                    "Physics export request is ready for the application host.",
                    5000,
                )
                return
            directory_value = picker(self)
            if not directory_value:
                self.statusBar().showMessage("Physics export canceled.", 4000)
                return
            directory = Path(directory_value).expanduser()
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-._")
            stem = safe_name[:80] or "physics-analysis"
            export_paths = {
                "csv": directory / f"{stem}.csv",
                "npz": directory / f"{stem}.npz",
                "markdown": directory / f"{stem}.md",
            }
        task = KinematicsWorkspaceTask(
            owner=self.current_task,
            task_id=self.current_task.task_id,
            results_generation=self.current_task.results_generation,
            operation=request.operation,
            source=source,
            configuration=request.configuration,
            export_paths=export_paths,
        )
        if not self._kinematics_workspace_coordinator.start(task):
            self.statusBar().showMessage(
                "Finish or cancel the current physics operation before starting another.",
                6000,
            )
            return
        labels = {
            "derivative": "Computing derivative in the background…",
            "smooth": "Smoothing physical series in the background…",
            "export": "Exporting CSV, safe NPZ, and Markdown in the background…",
        }
        self.statusBar().showMessage(labels[request.operation])
        self._update_physics_actions(self.analysis_workspace_controller.state)

    def _kinematics_workspace_ready(
        self,
        job: KinematicsWorkspaceJob,
        output: KinematicsWorkspaceOutput,
    ) -> None:
        task = job.task
        if not self._physics_job_is_current(task, output):
            return
        if output.operation == "build":
            if output.series:
                if not self.set_physics_series(
                    output.series,
                    owner=self.current_task,
                    result_identity=self._physics_result_identity(self.current_task),
                ):
                    return
            else:
                self.selection_session.activate_context(
                    self.current_task.task_id,
                    self._physics_result_identity(self.current_task),
                    output.source_revision,
                )
            self._prepare_persisted_physics_definitions(output.source_revision)
            self.statusBar().showMessage(
                f"Built {len(output.series)} physical series from current Results.",
                5000,
            )
            return
        replay_id = getattr(self, "_physics_replay_active_id", None)
        self._physics_replay_active_id = None
        replay_operation = getattr(self, "_physics_replay_active_operation", None)
        self._physics_replay_active_operation = None
        if output.operation in {"derivative", "smooth"} and output.series:
            if not self._attach_physics_series(output.series):
                self.statusBar().showMessage(
                    "Derived series was rejected because its identity is ambiguous.",
                    6000,
                )
                return
            if replay_id is None:
                self._persist_operation_definition(job.task, output.series[0])
            elif replay_operation == output.operation:
                definition = self._physics_definition_by_id(replay_id)
                if definition is not None:
                    self._apply_replayed_definition_view(definition)
            self.statusBar().showMessage(
                f"Created {output.series[0].name}.",
                5000,
            )
        elif output.operation == "export":
            rendered = ", ".join(path.name for path in output.exported_paths)
            self.statusBar().showMessage(f"Exported physics analysis: {rendered}", 8000)

    def _kinematics_workspace_failed(
        self,
        job: KinematicsWorkspaceJob,
        message: str,
    ) -> None:
        if job.task.owner is self.current_task:
            self.statusBar().showMessage(f"Physics operation failed: {message}", 8000)
        self._mark_active_physics_replay("failed")
        self._physics_replay_active_id = None
        self._physics_replay_active_operation = None

    def _kinematics_workspace_canceled(self, job: KinematicsWorkspaceJob) -> None:
        self._mark_active_physics_replay("canceled")
        self._physics_replay_active_id = None
        self._physics_replay_active_operation = None

    def _kinematics_workspace_idle(self) -> None:
        if self._start_pending_physics_build():
            return
        if self._start_next_physics_replay():
            return
        self._update_physics_actions(self.analysis_workspace_controller.state)
        self._schedule_close_if_workers_stopped()

    def _physics_fit_idle(self) -> None:
        replay_id = getattr(self, "_physics_replay_active_id", None)
        if replay_id is not None and self._physics_replay_active_operation == "fit":
            if self.analysis_workspace_controller.state.fit_result is None:
                self._mark_active_physics_replay("failed")
            self._physics_replay_active_id = None
            self._physics_replay_active_operation = None
        if self._start_next_physics_replay():
            return
        self._update_physics_actions(self.analysis_workspace_controller.state)
        self._schedule_close_if_workers_stopped()

    def _physics_job_is_current(
        self,
        task: KinematicsWorkspaceTask,
        output: KinematicsWorkspaceOutput,
    ) -> bool:
        current = self.current_task
        return bool(
            task.owner is current
            and task.task_id == current.task_id == output.task_id
            and task.results_generation
            == current.results_generation
            == output.results_generation
            and all(
                item.source_revision == output.source_revision
                for item in output.series
            )
            and (
                output.operation == "build"
                or output.source_revision == self.selection_session.state.source_revision
            )
        )

    def _prepare_persisted_physics_definitions(self, source_revision: str) -> None:
        definitions = self.current_task.analysis_workspace.definitions
        current: list[AnalysisDefinition] = []
        stale = 0
        for definition in definitions:
            is_current = (
                definition.source_series.task_id == self.current_task.task_id
                and definition.source_series.source_revision == source_revision
            )
            state = "current" if is_current else "stale"
            if is_current and not definition.visible:
                state = "hidden"
            self._physics_definition_states[definition.analysis_id] = state
            if is_current:
                current.append(definition)
            else:
                stale += 1
        replay: list[tuple[str, AnalysisDefinition]] = []
        for item in current:
            if not item.visible:
                continue
            if item.derivative_config is not None:
                replay.append(("derivative", item))
            if item.smoothing_config is not None:
                replay.append(("smooth", item))
            if item.fit_config is not None:
                replay.append(("fit", item))
        self._physics_replay_queue = replay
        detail = (
            f"Restored {len(current)} current analysis definitions"
            + (f"; {stale} stale definition(s) remain disabled" if stale else "")
        )
        if definitions:
            self.physics_workspace.cursor_label.setToolTip(detail)
            self.physics_workspace.cursor_label.setAccessibleDescription(detail)

    def _start_next_physics_replay(self) -> bool:
        queue = getattr(self, "_physics_replay_queue", [])
        if (
            not queue
            or self._kinematics_workspace_coordinator.busy
            or self.analysis_workspace_controller.busy
        ):
            return False
        deferred_fit_count = 0
        for index, entry in enumerate(tuple(queue)):
            operation, definition = entry
            source_id = definition.source_series.series_id
            fit_request: FitRequest | None = None
            if operation == "fit":
                assert definition.fit_config is not None
                fit_request = FitRequest.from_dict(definition.fit_config)
                source_id = fit_request.series_id
            source = self._physics_series_by_id.get(source_id)
            if source is None:
                continue
            if source.source_revision != definition.source_series.source_revision:
                continue
            if operation == "fit":
                assert fit_request is not None
                if fit_request.source_revision != source.source_revision:
                    continue
                selection = self.selection_session.select_series(
                    source.series_id,
                    origin=SelectionOrigin.FIT,
                    expected_source_revision=source.source_revision,
                )
                if not selection.accepted:
                    continue
                draft = FitDraft(
                    series_id=fit_request.series_id,
                    model=fit_request.model.value,
                    range_start_s=fit_request.range_start_s,
                    range_end_s=fit_request.range_end_s,
                    initial_parameters=fit_request.initial_parameters,
                    bounds=fit_request.bounds,
                    use_valid_only=fit_request.use_valid_only,
                )
                if self.fit_panel.is_dirty():
                    self._physics_definition_states[definition.analysis_id] = "deferred"
                    deferred_fit_count += 1
                    continue
                if not self.fit_panel.restore_draft(draft):
                    continue
                if not self.analysis_workspace_controller.run_fit(self.current_task, draft):
                    continue
                del queue[index]
                self._physics_replay_active_id = definition.analysis_id
                self._physics_replay_active_operation = operation
                return True
            configuration = (
                definition.derivative_config
                if operation == "derivative"
                else definition.smoothing_config
            )
            if operation == "derivative":
                from neo_tracker.kinematics import DerivativeConfig

                configuration = DerivativeConfig.from_dict(configuration)
            task = KinematicsWorkspaceTask(
                owner=self.current_task,
                task_id=self.current_task.task_id,
                results_generation=self.current_task.results_generation,
                operation=operation,
                source=source,
                configuration=configuration,
            )
            if not self._kinematics_workspace_coordinator.start(task):
                return False
            del queue[index]
            self._physics_replay_active_id = definition.analysis_id
            self._physics_replay_active_operation = operation
            return True
        unresolved = len(queue)
        queue.clear()
        unavailable = unresolved - deferred_fit_count
        if deferred_fit_count:
            self.statusBar().showMessage(
                f"Deferred {deferred_fit_count} saved fit definition(s) because the Fit panel has unapplied settings.",
                8000,
            )
        elif unavailable:
            self.statusBar().showMessage(
                f"{unavailable} saved physics definition(s) could not be rebuilt because a source series is unavailable.",
                8000,
            )
        return False

    def _physics_definition_by_id(self, analysis_id: str) -> AnalysisDefinition | None:
        return next(
            (
                item
                for item in self.current_task.analysis_workspace.definitions
                if item.analysis_id == analysis_id
            ),
            None,
        )

    def _mark_active_physics_replay(self, state: str) -> None:
        analysis_id = getattr(self, "_physics_replay_active_id", None)
        if analysis_id is not None:
            self._physics_definition_states[analysis_id] = state

    def _apply_replayed_definition_view(
        self,
        definition: AnalysisDefinition,
        *,
        fit_result: FitResult | None = None,
    ) -> None:
        page = definition.view_state.get("page")
        if isinstance(page, str):
            try:
                self.physics_workspace.show_page(page)
            except ValueError:
                pass
        if definition.selected_range_s is not None:
            self.physics_workspace.plot.set_selected_range(*definition.selected_range_s)
        if fit_result is not None:
            residual_visible = definition.view_state.get("residual_visible", False)
            self.analysis_workspace_controller.set_residual_visible(
                bool(residual_visible)
            )
        self._physics_definition_states[definition.analysis_id] = "current"

    def _persist_operation_definition(
        self,
        task: KinematicsWorkspaceTask,
        result: SampleSeries,
    ) -> None:
        source = task.source
        if source is None:
            return
        derivative = (
            task.configuration.to_dict()
            if task.operation == "derivative" and hasattr(task.configuration, "to_dict")
            else None
        )
        smoothing = dict(task.configuration) if task.operation == "smooth" else None
        try:
            definition = AnalysisDefinition(
                analysis_id=result.series_id,
                name=result.name[:256],
                source_series=AnalysisSourceReference(
                    self.current_task.task_id,
                    source.series_id,
                    source.source_kind,
                    source.source_revision,
                ),
                derivative_config=derivative,
                smoothing_config=smoothing,
                visible=True,
                view_state={"page": self.physics_workspace.current_page},
                provenance={"engine": "neo-tracker-kinematics-v0.3"},
            )
        except (TypeError, ValueError) as exc:
            self.statusBar().showMessage(
                f"Could not save analysis definition: {exc}",
                8000,
            )
            return
        self._store_physics_definition(definition)

    def _store_physics_definition(self, definition: AnalysisDefinition) -> None:
        task = self.current_task
        definitions = list(task.analysis_workspace.definitions)
        for index, current in enumerate(definitions):
            if current.analysis_id == definition.analysis_id:
                definitions[index] = definition
                break
        else:
            definitions.append(definition)
        try:
            task.analysis_workspace = AnalysisWorkspaceSnapshot(tuple(definitions))
        except (TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"Could not save analysis definition: {exc}", 8000)
            return
        self._physics_definition_states[definition.analysis_id] = "current"
        self._mark_project_changed()

    def _update_physics_actions(self, state: AnalysisWorkspaceState) -> None:
        has_series = bool(
            state.selected_series_id
            and state.selected_series_id in self._physics_series_by_id
        )
        mutable = has_series and state.status != "running" and not self._background_tasks.closing
        mutable = mutable and not self._kinematics_workspace_coordinator.busy
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
