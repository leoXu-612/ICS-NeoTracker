from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from time import monotonic

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableView,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.application.job_state import (
    AnalysisJob,
    MediaProbeJob,
    PreviewDecodeJob,
    ProjectOpenJob,
    ProjectSaveJob,
    ReviewResponseJob,
    TrackingJob,
)
from neo_tracker.application.analysis_coordinator import (
    AnalysisCoordinator,
    AnalysisRequest,
)
from neo_tracker.application.media_import_coordinator import (
    MediaImportCoordinator,
    MediaImportRequest,
)
from neo_tracker.application.kinematics_workspace_coordinator import (
    KinematicsWorkspaceCoordinator,
)
from neo_tracker.application.playback_coordinator import PlaybackCoordinator
from neo_tracker.application.preview_coordinator import PreviewCoordinator, PreviewRequest
from neo_tracker.application.project_io_coordinator import (
    ProjectIOCoordinator,
    ProjectOpenRequest,
    ProjectSaveRequest,
)
from neo_tracker.application.review_response_coordinator import (
    ReviewResponseCoordinator,
)
from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.application.tracking_coordinator import (
    TrackingCoordinator,
    TrackingRequest,
)
from neo_tracker.config import apply_pipeline_config, validate_roi_config
from neo_tracker.core import TrackingPipeline, TrackerResult
from neo_tracker.coordinates import (
    AnnularCoordinate,
    ImageCoordinate,
    LinearWorldCoordinate,
    PathCoordinate,
    PolarCoordinate,
)
from neo_tracker.export import (
    write_edit_history_csv,
    write_csv as write_tracking_csv,
    write_markdown_report,
    write_tracking_run_csv,
)
from neo_tracker.analysis import (
    AnalysisConfig,
    FFTResult,
    SignalSeries,
    STFTResult,
)
from neo_tracker.media import MediaIdentity, MediaInfo, MediaReader, has_media_backend
from neo_tracker.kinematics import KinematicsEngineRuntime, SampleSeries
from neo_tracker.observations import ColorBlobObservation, observation_backend_info
from neo_tracker.presets import PresetDescriptor, default_preset_registry
from neo_tracker.project import (
    NeoTrackerProject,
    ProjectTaskSnapshot,
    project_content_fingerprint,
)
from neo_tracker.ui.analysis_controller import AnalysisController, AnalysisRun, AnalysisSource
from neo_tracker.ui.analysis_workspace_controller import (
    AnalysisWorkspaceController,
)
from neo_tracker.ui.analysis_worker import AnalysisWorker
from neo_tracker.ui.calibration_editor import CalibrationEditor
from neo_tracker.ui.edit_history_panel import EditHistoryPanel, EditHistorySelection
from neo_tracker.ui.media_relink_panel import MediaRelinkPanel
from neo_tracker.ui.media_probe_worker import probe_media_for_ui
from neo_tracker.ui.isolated_media import PreviewDecoderSession
from neo_tracker.ui.preview_canvas import PreviewCanvas
from neo_tracker.ui.preview_decode_worker import (
    PreviewDecodeRequest,
    PreviewDecodeResult,
    same_preview_request,
)
from neo_tracker.ui.project_open_worker import PreparedProjectOpen
from neo_tracker.ui.project_save_worker import CompletedProjectSave, save_project
from neo_tracker.ui.project_status_panel import (
    ProjectStatusPanel,
    build_rerun_replacement_dialog,
    build_result_replacement_dialog,
    build_unapplied_drafts_dialog,
    build_unsaved_changes_dialog,
)
from neo_tracker.ui.project_controller import (
    CalibrationRod,
    DesktopTask,
    MediaRelinkAssessment,
    ProjectTaskController,
    first_config_diff,
    short_config_value,
)
from neo_tracker.ui.shell.configuration_protection_mixin import ConfigurationProtectionMixin
from neo_tracker.ui.roi_geometry_editor import ROIGeometryEditor
from neo_tracker.ui.run_history_panel import (
    RunHistoryComparisonDialog,
    RunHistoryPanel,
    RunHistorySelection,
)
from neo_tracker.ui.task_actions_panel import TaskActionsPanel
from neo_tracker.ui.review_controller import ReviewController, ReviewEdit
from neo_tracker.ui.review_diagnostics import (
    PreparedReviewDiagnostics,
    ReviewDiagnosticsPanel,
    angular_response,
)
from neo_tracker.ui.review_response import (
    ReviewResponse,
    ReviewResponseRequest,
    ReviewResponseService,
)
from neo_tracker.ui.review_response_worker import ReviewResponseWorker
from neo_tracker.ui.results_table_model import ResultsTableModel
from neo_tracker.ui.fit_panel import FitPanel
from neo_tracker.ui.inspectors import PhysicsInspector
from neo_tracker.ui.action_registry import ActionRegistry
from neo_tracker.ui.selection_session import (
    SelectionOrigin,
    SelectionSession,
)
from neo_tracker.ui.shell import ApplicationShell
from neo_tracker.ui.shell.bindings import (
    CoordinatorCompatibilityMixin,
    PRIMARY_BUTTON_ATTRIBUTES,
)
from neo_tracker.ui.shell.physics_workspace_mixin import PhysicsWorkspaceMixin
from neo_tracker.ui.shell.preview_selection_mixin import PreviewSelectionMixin
from neo_tracker.ui.shell.review_editing_mixin import ReviewEditingMixin, ReviewUndo
from neo_tracker.ui.tracking_worker import (
    TRACKING_SOURCE_CHANGED_PREFIX,
    TrackingProgress,
    TrackingWorker,
)
from neo_tracker.ui.view_state import (
    PhysicsWorkspaceStateStore,
    ViewState,
)
from neo_tracker.ui.workspaces import PhysicsWorkspace


_PROJECT_OPEN_DEFERRED_RESULTS_THRESHOLD = 10_000
_TASK_SWITCH_BLOCKING_KINDS = frozenset({"tracking", "analysis", "kinematics-fit", "kinematics-analysis"})
_PROJECT_OPEN_BLOCKING_KINDS = _TASK_SWITCH_BLOCKING_KINDS | frozenset({"media-probe", "project-open", "project-save"})
_PROJECT_OPEN_BLOCKED_ACTIONS = (
    "media.add",
    "project.save",
    "tracking.export_csv",
    "tracking.export_report",
    "analysis.export_csv",
    "analysis.export_npz",
    "physics.export",
    "tracking.run",
    "analysis.run",
    "physics.velocity",
    "physics.acceleration",
    "physics.smooth",
    "physics.fit",
    "review.correct",
    "review.mark_lost",
    "review.undo",
    "review.rerun",
    "review.jump",
    "physics.residual",
)


@dataclass(frozen=True)
class PipelineStep:
    title: str
    module: str
    purpose: str


class ElidingLabel(QLabel):
    """A QLabel that keeps its full semantic text while painting a compact ellipsis."""

    def __init__(self, text: str = "", *, auto_tooltip: bool = False) -> None:
        self._full_text = ""
        self._auto_tooltip = bool(auto_tooltip)
        super().__init__("")
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = str(text)
        if self._auto_tooltip:
            self.setToolTip(self._full_text)
        self._sync_display_text()

    def text(self) -> str:
        return self._full_text

    def displayedText(self) -> str:  # noqa: N802
        return QLabel.text(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._sync_display_text()
        super().resizeEvent(event)

    def _sync_display_text(self) -> None:
        available_width = max(0, self.contentsRect().width())
        displayed = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            available_width,
        )
        if QLabel.text(self) != displayed:
            QLabel.setText(self, displayed)


class NeoTrackerWindow(
    ConfigurationProtectionMixin,
    PreviewSelectionMixin,
    ReviewEditingMixin,
    PhysicsWorkspaceMixin,
    CoordinatorCompatibilityMixin,
    QMainWindow,
):
    physicsOperationRequested = Signal(object)

    def __init__(
        self,
        *,
        physics_layout_store: PhysicsWorkspaceStateStore | None = None,
        physics_export_directory_picker: Callable[[QWidget], str] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Neo-Tracker")
        self.resize(1280, 780)
        self._physics_layout_store = (
            physics_layout_store or PhysicsWorkspaceStateStore.application_default()
        )
        self._physics_export_directory_picker = physics_export_directory_picker
        self._restoring_physics_layout = False
        self._canvas_focus_active = False
        self._canvas_focus_main_sizes: tuple[int, ...] = ()
        self._canvas_focus_sidebar_visible = True
        self.registry = default_preset_registry()
        self.default_pipeline_key = next(iter(self.registry))
        self.project_controller = ProjectTaskController(
            self.registry,
            self.default_pipeline_key,
            media_probe=probe_media_for_ui,
        )
        self.scratch_task = self._new_task(None, self.default_pipeline_key)
        self.tasks: list[DesktopTask] = []
        self.current_task: DesktopTask = self.scratch_task
        self.project_path: Path | None = None
        self._project_pipeline_library: list[TrackingPipeline | dict[str, object]] = []
        self._project_notes = ""
        self._explicit_empty_project = False
        self._last_removed_task: tuple[
            DesktopTask,
            int,
            bool,
            str | None,
            str | None,
            int,
        ] | None = None
        self._review_undo: ReviewUndo | None = None
        self._saved_project_fingerprint: str | None = None
        self._current_project_fingerprint: str | None = None
        self._project_dirty = False
        self._project_content_revision = 0
        self._advanced_config_dirty = False
        self._close_requested_by_user = False
        self._tracking_previous_sidebar_tab: QWidget | None = None
        self._project_open_apply_defer_heavy_views = False
        self._project_open_deferred_generation = 0
        self._project_open_deferred_task: DesktopTask | None = None
        self._project_open_deferred_results: list[TrackerResult] | None = None
        self._project_open_deferred_state_units: dict[str, str] = {}
        self._project_open_deferred_selected_frame: int | None = None
        self._project_open_deferred_analysis_source: AnalysisSource | None = None
        self._project_open_prepared_diagnostics: PreparedReviewDiagnostics | None = None
        self._project_open_prepared_analysis_sources: tuple[AnalysisSource, ...] = ()
        self._project_open_diagnostics_pending = False
        self._project_open_analysis_pending = False
        self._last_project_open_apply_ms = 0.0
        self._retired_project_tasks: list[DesktopTask] = []

        self._playback_coordinator = PlaybackCoordinator()
        self._playback_coordinator.advance_requested.connect(self._advance_playback)
        self._project_open_diagnostics_timer = QTimer(self)
        self._project_open_diagnostics_timer.setSingleShot(True)
        self._project_open_diagnostics_timer.timeout.connect(
            self._hydrate_project_open_diagnostics
        )
        self._project_open_analysis_timer = QTimer(self)
        self._project_open_analysis_timer.setSingleShot(True)
        self._project_open_analysis_timer.timeout.connect(
            self._hydrate_project_open_analysis_sources
        )
        self._retired_project_tasks_timer = QTimer(self)
        self._retired_project_tasks_timer.setSingleShot(True)
        self._retired_project_tasks_timer.timeout.connect(
            self._release_retired_project_task
        )

        self.preview_label = PreviewCanvas()
        self.preview_title_label = ElidingLabel("No media loaded", auto_tooltip=True)
        self.playback_status_label = QLabel("Playing")
        self.backend_label = QLabel()
        self.tracking_status_label = QLabel("No media")
        self.tracking_summary_label = ElidingLabel("Results: none")
        self.task_list = QListWidget()
        self.task_actions_panel = TaskActionsPanel()
        self.sidebar_tabs = QTabWidget()
        self.media_tab: QWidget | None = None
        self.tracking_tab: QWidget | None = None
        self.calibration_tab: QWidget | None = None
        self.review_tab: QWidget | None = None
        self.preset_combo = QComboBox()
        self.preset_description = QTextBrowser()
        self.marker_swatch_label = QLabel()
        self.marker_sample_label = QLabel("Marker controls are available for color marker presets.")
        self.sample_marker_button = QPushButton("Sample")
        self.color_tolerance_spin = QDoubleSpinBox()
        self.color_max_candidates_spin = QSpinBox()
        self.color_min_area_spin = QSpinBox()
        for spin in (self.color_tolerance_spin, self.color_max_candidates_spin, self.color_min_area_spin):
            spin.setKeyboardTracking(False)
        self.marker_controls_widget = QWidget()
        self.tracking_backend_label = QLabel("Detecting…")
        self.tracking_performance_title_label = QLabel("Live performance")
        self.tracking_performance_label = QLabel("Measuring…")
        self.module_summary_list = QListWidget()
        self.workflow_list = QListWidget()
        self.advanced_config_view = QPlainTextEdit()
        self.media_path_label = QLabel("No media selected")
        self.media_path_label.setAccessibleName("Current media path")
        self.media_fps_title_label = QLabel("Source FPS")
        self.media_frames_title_label = QLabel("Frames")
        self.media_resolution_title_label = QLabel("Resolution")
        self.media_fps_label = QLabel("-")
        self.media_frames_label = QLabel("-")
        self.media_resolution_label = QLabel("-")
        self.media_duration_label = QLabel("-")
        self.project_status_panel = ProjectStatusPanel()
        self.project_name_label = self.project_status_panel.name_label
        self.project_state_label = self.project_status_panel.state_label
        self.global_project_dirty_label = ElidingLabel("Unsaved")
        self.global_project_dirty_label.setObjectName("globalProjectDirtyLabel")
        self.global_project_dirty_label.setAccessibleName("Project has unsaved changes")
        self.global_project_dirty_label.setAccessibleDescription(
            "Project content has changed since the last save."
        )
        self.global_project_dirty_label.hide()
        self.global_draft_label = ElidingLabel("Draft")
        self.global_draft_label.setObjectName("globalDraftLabel")
        self.global_draft_label.setAccessibleName("Unapplied editor work")
        self.global_draft_label.hide()
        self.open_project_button = QPushButton("Open Project")
        self.save_project_button = QPushButton("Save Project")
        self.add_media_button = QPushButton("Add media")
        self.media_probe_status_label = QLabel("Inspecting media…")
        self.media_probe_status_label.setObjectName("mediaProbeStatusLabel")
        self.media_probe_status_label.setAccessibleName("Media import status")
        self.media_probe_status_label.hide()
        self.media_relink_panel = MediaRelinkPanel()
        self.media_relink_section_label = self._section_label("MEDIA SOURCE")
        self._media_relink_task: DesktopTask | None = None
        self._media_relink_candidate_path: str | None = None
        self._media_relink_candidate_info: MediaInfo | None = None
        self._media_relink_assessment: MediaRelinkAssessment | None = None
        self.preview_frame_spin = QSpinBox()
        self.play_button = QPushButton("Play")
        self.previous_frame_button = QPushButton("Previous")
        self.next_frame_button = QPushButton("Next")
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.run_tracking_button = QPushButton("Run Tracking")
        self.export_tracking_csv_button = QPushButton("Export CSV")
        self.export_report_button = QPushButton("Report")
        self.roi_status_label = QLabel("Default ROI from selected preset")
        self.scale_status_label = QLabel("Pixel units")
        self.curve_half_width_spin = QDoubleSpinBox()
        self.roi_geometry_editor = ROIGeometryEditor()
        self.calibration_editor = CalibrationEditor()
        self.rod_status_label = self.calibration_editor.summary_label
        self.reset_roi_button = QPushButton("Reset ROI")
        self.reset_calibration_button = QPushButton("Reset Calibration")
        self.mark_calibration_button = QPushButton("Mark Rod")
        self.finish_roi_drawing_button = QPushButton("Finish Drawing")
        self.cancel_roi_drawing_button = QPushButton("Cancel Drawing")
        self.review_controller = ReviewController()
        # External macOS accessibility clients call into Qt from AppKit. A table
        # constructed directly through PySide uses QTableViewWrapper's Python
        # virtual table; Qt 6.11.1 can jump back into that wrapper while servicing
        # a hierarchy request and crash natively. QUiLoader creates the same
        # standard widget in Qt/C++, so accessibility re-entry stays in Qt while
        # the Python ResultsTableModel remains fully virtualized.
        self._native_widget_loader = QUiLoader(self)
        results_table = self._native_widget_loader.createWidget(
            "QTableView",
            self,
            "resultsTable",
        )
        if not isinstance(results_table, QTableView):
            raise RuntimeError("Qt could not create the native tracking results table")
        self.results_table = results_table
        self.results_model = ResultsTableModel(self.results_table)
        self.edit_history_panel = EditHistoryPanel()
        self.edit_history_list = self.edit_history_panel.list_widget
        self.run_history_panel = RunHistoryPanel()
        self.run_history_list = self.run_history_panel.list_widget
        self.review_history_tabs = QTabWidget()
        self.review_summary_label = QLabel("Run tracking to populate the review table.")
        self.review_selection_label = QLabel("No result selected")
        self.review_selection_detail_label = QLabel("Select a result row or move to a tracked frame.")
        self.correct_point_button = QPushButton("Correct Point")
        self.mark_lost_button = QPushButton("Mark Lost")
        self.undo_review_edit_button = QPushButton("Undo Edit")
        self.rerun_after_button = QPushButton("Rerun After…")
        self.jump_to_result_button = QPushButton("Jump to Row")
        self.show_response_checkbox = QCheckBox("Response")
        self.show_observation_checkbox = QCheckBox("Observation")
        self.show_measurement_checkbox = QCheckBox("Measured")
        self.show_candidates_checkbox = QCheckBox("Candidates")
        self.show_prediction_checkbox = QCheckBox("Prediction")
        self.candidate_summary_label = QLabel("Candidates: none")
        self.response_status_label = QLabel("Response overlay: off")
        self.review_diagnostics_panel = ReviewDiagnosticsPanel()
        self.confidence_plot = self.review_diagnostics_panel.plot
        self._review_render_task_token: int | None = None
        self.analysis_source_combo = QComboBox()
        self.analysis_method_combo = QComboBox()
        self.analysis_detrend_combo = QComboBox()
        self.analysis_window_combo = QComboBox()
        self.analysis_sample_rate_spin = QDoubleSpinBox()
        self.analysis_freq_min_spin = QDoubleSpinBox()
        self.analysis_freq_max_spin = QDoubleSpinBox()
        self.analysis_stft_window_spin = QSpinBox()
        self.analysis_stft_overlap_spin = QDoubleSpinBox()
        self.analysis_parameters_form: QFormLayout | None = None
        self.analysis_result_view = QPlainTextEdit()
        self.analysis_source_detail_label = QLabel("No signal source available")
        self.refresh_analysis_sources_button = QPushButton("Refresh sources")
        self.analysis_status_label = QLabel("No source")
        self.run_analysis_button = QPushButton("Run processing")
        self.analysis_export_csv_button = QPushButton("Export CSV")
        self.analysis_export_npz_button = QPushButton("Export NPZ")
        self.analysis_controller = AnalysisController()
        self.selection_session = SelectionSession()
        self._selection_unsubscribe = self.selection_session.subscribe(
            self._selection_session_changed
        )
        self._applying_selection_revision: int | None = None
        self._physics_series_owner_token: int | None = None
        self._physics_series_by_id: dict[str, SampleSeries] = {}
        self.physics_workspace = PhysicsWorkspace()
        self.canvas_focus_button = self.physics_workspace.focus_button
        self.fit_panel = FitPanel()
        self.physics_inspector = PhysicsInspector()
        self.create_velocity_button = self.physics_inspector.create_velocity_button
        self.create_acceleration_button = self.physics_inspector.create_acceleration_button
        self.smooth_series_button = self.physics_inspector.smooth_series_button
        self.fit_model_button = self.physics_inspector.fit_model_button
        self.export_physics_analysis_button = self.physics_inspector.export_analysis_button
        self.show_residual_button = self.physics_inspector.show_residual_button
        self.physics_workspace.set_fit_widget(self.fit_panel)
        self.physics_workspace.seriesActivated.connect(self._physics_series_activated)
        self.physics_workspace.sampleActivated.connect(self._physics_sample_activated)
        self.physics_workspace.plotSampleActivated.connect(
            self._physics_plot_sample_activated
        )
        self.physics_workspace.pageRouteRequested.connect(self._physics_route_requested)
        self.physics_workspace.pageChanged.connect(self._physics_workspace_page_changed)
        self.physics_workspace.rangeSelected.connect(self._physics_range_selected)
        self.physics_workspace.plotImageExportRequested.connect(
            self._export_physics_plot_image
        )
        self.physics_workspace.layoutStateChanged.connect(
            self._physics_workspace_layout_changed
        )
        self.fit_panel.runRequested.connect(self._run_physics_fit)
        self.fit_panel.cancelRequested.connect(self._cancel_physics_fit)
        self.fit_panel.draftChanged.connect(self._physics_fit_draft_changed)
        self.validate_json_button = QPushButton("Validate")
        self.apply_json_button = QPushButton("Apply JSON")
        self.reset_json_button = QPushButton("Reset View")
        self.json_status_label = QLabel("Synced")
        self.json_validation_message = QLabel()
        self._syncing_advanced_config = False
        self.project_loader = NeoTrackerProject.load
        self.project_saver = save_project
        self._response_mode_routing_requested = False
        self._task_supervisor = TaskSupervisor()
        self._background_tasks = self._task_supervisor
        self._kinematics_runtime = KinematicsEngineRuntime()
        self._kinematics_workspace_coordinator = KinematicsWorkspaceCoordinator(
            self._task_supervisor
        )
        self._kinematics_workspace_coordinator.output_ready.connect(
            self._kinematics_workspace_ready
        )
        self._kinematics_workspace_coordinator.failed.connect(
            self._kinematics_workspace_failed
        )
        self._kinematics_workspace_coordinator.canceled.connect(
            self._kinematics_workspace_canceled
        )
        self._kinematics_workspace_coordinator.idle_reached.connect(
            self._kinematics_workspace_idle
        )
        self.analysis_workspace_controller = AnalysisWorkspaceController(
            self._task_supervisor,
            fit_operator=self._kinematics_runtime,
        )
        self.analysis_workspace_controller.stateChanged.connect(
            self._physics_fit_state_changed
        )
        self.analysis_workspace_controller.fitResultReady.connect(
            self._physics_fit_ready
        )
        self.analysis_workspace_controller.operationRequested.connect(
            self._physics_operation_requested
        )
        self.analysis_workspace_controller.idleReached.connect(
            self._physics_fit_idle
        )
        self._media_import_coordinator = MediaImportCoordinator(self._task_supervisor)
        self._media_import_coordinator.progressed.connect(self._media_probe_progressed)
        self._media_import_coordinator.completed.connect(self._media_import_completed)
        self._media_import_coordinator.failed.connect(self._media_import_failed)
        self._media_import_coordinator.canceled.connect(self._media_import_canceled)
        self._media_import_coordinator.idle_reached.connect(self._media_import_idle)
        self._project_io_coordinator = ProjectIOCoordinator(self._task_supervisor)
        self._project_io_coordinator.open_progressed.connect(self._project_open_progressed)
        self._project_io_coordinator.open_prepared.connect(self._project_io_open_prepared)
        self._project_io_coordinator.open_failed.connect(self._project_io_open_failed)
        self._project_io_coordinator.open_canceled.connect(self._project_io_open_canceled)
        self._project_io_coordinator.save_completed.connect(self._project_io_save_completed)
        self._project_io_coordinator.save_failed.connect(self._project_io_save_failed)
        self._project_io_coordinator.idle_reached.connect(self._project_io_idle)
        self._tracking_coordinator = TrackingCoordinator(self._task_supervisor)
        self._tracking_coordinator.started.connect(self._tracking_started)
        self._tracking_coordinator.progressed.connect(self._tracking_progressed)
        self._tracking_coordinator.terminal_ready.connect(self._tracking_terminal_ready)
        self._tracking_coordinator.state_changed.connect(
            self._tracking_coordinator_state_changed
        )
        self._tracking_coordinator.idle_reached.connect(self._tracking_idle)
        self._preview_coordinator = PreviewCoordinator(self._task_supervisor)
        self._preview_coordinator.result_ready.connect(self._preview_coordinator_completed)
        self._preview_coordinator.failed.connect(self._preview_coordinator_failed)
        self._preview_coordinator.idle_reached.connect(self._preview_coordinator_idle)
        self._analysis_coordinator = AnalysisCoordinator(self._task_supervisor)
        self._analysis_coordinator.started.connect(self._analysis_started)
        self._analysis_coordinator.stage_changed.connect(self._analysis_stage_changed)
        self._analysis_coordinator.result_ready.connect(self._analysis_completed)
        self._analysis_coordinator.failed.connect(self._analysis_failed)
        self._analysis_coordinator.finished.connect(self._analysis_thread_finished)
        self._analysis_coordinator.idle_reached.connect(self._analysis_idle)
        self._review_response_coordinator = ReviewResponseCoordinator(
            self._task_supervisor,
            service=ReviewResponseService(max_entries=4),
        )
        self._review_response_coordinator.response_ready.connect(
            self._review_response_completed
        )
        self._review_response_coordinator.failed.connect(self._review_response_failed)
        self._review_response_coordinator.idle_reached.connect(
            self._review_response_idle
        )

        self._build_ui()
        self._restore_physics_layout()
        self._reset_physics_context()
        self._application_shell = ApplicationShell(self)
        self.action_registry.bind_button("physics.export", self.fit_panel.export_button)
        self.action_registry.bind_button("physics.residual", self.fit_panel.residual_checkbox)
        self._update_physics_actions(self.analysis_workspace_controller.state)
        self._apply_style()
        self._load_presets()
        self._render_task(refresh_project_state=False)
        self._set_project_clean()

    @property
    def action_registry(self) -> ActionRegistry:
        return self._application_shell.registry

    @property
    def view_state(self) -> ViewState:
        return self._application_shell.view_state

    def _update_action(
        self,
        key: str,
        *,
        enabled: bool | None = None,
        text: str | None = None,
        tool_tip: str | None = None,
        icon: QIcon | None = None,
    ) -> None:
        shell = getattr(self, "_application_shell", None)
        if shell is not None:
            shell.update_action(
                key,
                enabled=enabled,
                text=text,
                tool_tip=tool_tip,
            )
            if icon is not None:
                shell.set_icon(key, icon)
            return
        button = getattr(self, PRIMARY_BUTTON_ATTRIBUTES[key])
        if enabled is not None:
            button.setEnabled(enabled)
        if text is not None:
            button.setText(text)
        if tool_tip is not None:
            button.setToolTip(tool_tip)
        if icon is not None:
            button.setIcon(icon)

    def _set_action_enabled(self, key: str, enabled: bool) -> None:
        self._update_action(key, enabled=enabled)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 14)
        root_layout.setSpacing(12)

        top_toolbar = QFrame()
        top_toolbar.setObjectName("topToolbar")
        top_toolbar.setMinimumHeight(54)
        top_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        app_header = QHBoxLayout(top_toolbar)
        app_header.setContentsMargins(12, 8, 12, 8)
        app_header.setSpacing(8)
        app_title = QLabel("Neo-Tracker")
        app_title.setObjectName("appTitle")
        app_title_font = app_title.font()
        if app_title_font.pointSizeF() > 0:
            app_title_font.setPointSizeF(app_title_font.pointSizeF() * 1.45)
            app_title.setFont(app_title_font)
        self.preview_title_label.setObjectName("mediaTitle")
        self.tracking_status_label.setObjectName("statusChip")
        self.tracking_summary_label.setObjectName("summaryChip")
        self.preview_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.global_project_dirty_label.setMaximumWidth(92)
        self.global_project_dirty_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.global_draft_label.setMaximumWidth(170)
        self.global_draft_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.tracking_summary_label.setMinimumWidth(130)
        self.tracking_summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.run_tracking_button.setObjectName("runTrackingButton")
        self.export_tracking_csv_button.setObjectName("exportTrackingButton")
        self.run_tracking_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.export_tracking_csv_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_report_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.run_tracking_button.setToolTip("Run the selected video tracking pipeline.")
        self.export_tracking_csv_button.setToolTip("Export the current tracking results as CSV.")
        self.export_report_button.setToolTip("Create a Markdown report for the current task.")
        self.run_tracking_button.setAccessibleName("Run video tracking")
        self.run_tracking_button.setAccessibleDescription(
            "Starts tracking in the background. While tracking, this button cancels the active run."
        )
        self.export_tracking_csv_button.setAccessibleName("Export current tracking results to CSV")
        self.export_tracking_csv_button.setAccessibleDescription(
            "Save the current task's tracking results as a CSV file."
        )
        self.export_report_button.setAccessibleName("Create tracking report")
        self.export_report_button.setAccessibleDescription(
            "Create a Markdown report for the current media task and tracking history."
        )
        self.tracking_status_label.setAccessibleName("Tracking status")
        self.tracking_summary_label.setAccessibleName("Tracking progress and result summary")
        app_header.addWidget(app_title)
        app_header.addWidget(self.global_project_dirty_label)
        app_header.addWidget(self.global_draft_label)
        app_header.addWidget(self.preview_title_label, 1)
        app_header.addWidget(self.tracking_status_label)
        app_header.addWidget(self.tracking_summary_label, 1)
        app_header.addWidget(self.run_tracking_button)
        app_header.addWidget(self.export_tracking_csv_button)
        app_header.addWidget(self.export_report_button)
        root_layout.addWidget(top_toolbar)

        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.splitterMoved.connect(self._physics_splitter_moved)
        root_layout.addWidget(self.workspace_splitter, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        self.main_splitter = splitter
        self.workspace_splitter.addWidget(splitter)

        preview_panel = QWidget()
        preview_panel.setObjectName("previewPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 10, 0)
        preview_layout.setSpacing(8)
        preview_header = QHBoxLayout()
        title = QLabel("Preview")
        title.setObjectName("previewTitle")
        preview_header.addWidget(title)
        preview_header.addStretch(1)
        self.playback_status_label.setObjectName("playbackStatusLabel")
        self.playback_status_label.setProperty("playbackState", "smooth")
        self.playback_status_label.setAccessibleName("Preview status")
        self.playback_status_label.hide()
        preview_header.addWidget(self.playback_status_label)
        preview_layout.addLayout(preview_header)

        self.preview_label.setObjectName("previewCanvas")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(480, 270)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setText("No media loaded\nAdd a video or WAV file from the Media tab.")
        self.preview_label.roiSelected.connect(self._roi_selected)
        self.preview_label.calibrationRodSelected.connect(self._calibration_rod_selected)
        self.preview_label.manualPointSelected.connect(self._manual_point_selected)
        self.preview_label.colorSampleSelected.connect(self._color_sample_selected)
        self.preview_label.selectionModeChanged.connect(self._preview_selection_mode_changed)
        self.preview_label.roiNodeSelected.connect(self._preview_roi_node_selected)
        self.preview_label.roiNodeMoved.connect(self._preview_roi_node_moved)
        preview_layout.addWidget(self.preview_label, 1)

        transport_bar = QFrame()
        transport_bar.setObjectName("transportBar")
        preview_controls = QHBoxLayout(transport_bar)
        preview_controls.setContentsMargins(10, 8, 10, 8)
        preview_controls.setSpacing(8)
        self.previous_frame_button.setObjectName("transportButton")
        self.play_button.setObjectName("transportButton")
        self.next_frame_button.setObjectName("transportButton")
        self.previous_frame_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.next_frame_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.previous_frame_button.setToolTip("Step to the previous video frame.")
        self.previous_frame_button.setAccessibleName("Previous video frame")
        self.previous_frame_button.setAccessibleDescription("Move the preview one frame backward.")
        self.play_button.setToolTip(
            "Play or pause the video preview. Playback stays aligned to source time and may skip preview frames if rendering falls behind."
        )
        self.play_button.setAccessibleName("Play video preview")
        self.next_frame_button.setToolTip("Step to the next video frame.")
        self.next_frame_button.setAccessibleName("Next video frame")
        self.next_frame_button.setAccessibleDescription("Move the preview one frame forward.")
        self.preview_frame_spin.setRange(0, 0)
        self.preview_frame_spin.setAccessibleName("Preview frame number")
        self.preview_frame_spin.setAccessibleDescription(
            "Enter a source frame number to move the preview to that frame."
        )
        self.preview_frame_spin.valueChanged.connect(self._preview_frame_selected_by_user)
        self.frame_slider.setObjectName("frameSlider")
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setAccessibleName("Preview frame timeline")
        self.frame_slider.setAccessibleDescription(
            "Move along the source timeline to select a video frame for preview and review."
        )
        self.frame_slider.valueChanged.connect(self._preview_frame_selected_by_user)
        preview_controls.addWidget(self.previous_frame_button)
        preview_controls.addWidget(self.play_button)
        preview_controls.addWidget(self.next_frame_button)
        frame_label = QLabel("Frame")
        frame_label.setObjectName("previewFrameLabel")
        frame_label.setBuddy(self.preview_frame_spin)
        preview_controls.addWidget(frame_label)
        preview_controls.addWidget(self.preview_frame_spin)
        preview_controls.addWidget(self.frame_slider, 1)
        preview_layout.addWidget(transport_bar)
        splitter.addWidget(preview_panel)

        sidebar = QWidget()
        sidebar.setObjectName("rightSidebar")
        self.right_sidebar = sidebar
        sidebar.setMinimumWidth(450)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 0, 0, 0)
        sidebar_layout.setSpacing(8)
        self.sidebar_tabs.setObjectName("sidebarTabs")
        self.sidebar_tabs.setMinimumHeight(300)
        sidebar_layout.addWidget(self.sidebar_tabs)
        splitter.addWidget(sidebar)
        splitter.setSizes([820, 500])
        self.workspace_splitter.addWidget(self.physics_workspace)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([460, self.physics_workspace.preferred_height])

        self._build_media_tab()
        self._build_tracking_tab()
        self._build_review_tab()
        self._build_processing_tab()
        self._build_calibration_tab()
        self._build_workflow_tab()
        self._build_advanced_tab()
        self.sidebar_tabs.setAccessibleName("Neo-Tracker workflow sections")
        self.sidebar_tabs.setAccessibleDescription(
            "Choose Media, Tracking, Review, Signal, Calibration, Flow, or Pipeline JSON. Review also exposes physics inspection."
        )
        self.sidebar_tabs.tabBar().setAccessibleName("Neo-Tracker workflow section tabs")
        self.sidebar_tabs.currentChanged.connect(self._sidebar_tab_changed)

        self.setCentralWidget(root)
        self.task_list.setAccessibleName("Media tasks")
        self.task_list.setAccessibleDescription(
            "Choose the video or audio task to preview, track, review, or process."
        )
        self.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f5f5f7;
            }
            QWidget {
                color: #1d1d1f;
                font-family: "Helvetica Neue", "Arial", sans-serif;
            }
            QLabel {
                background: transparent;
            }
            QWidget#appRoot {
                background: #f5f5f7;
            }
            QFrame#topToolbar {
                background: #fbfbfd;
                border: 1px solid #d8d8de;
                border-radius: 12px;
            }
            QLabel#appTitle {
                font-weight: 700;
                color: #111113;
                padding-right: 10px;
            }
            QLabel#mediaTitle {
                color: #66666c;
                padding: 4px 0;
            }
            QLabel#projectStateLabel {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 9px;
                color: #6e6e73;
                font-weight: 650;
                padding: 3px 8px;
            }
            QLabel#projectStateLabel[projectState="saved"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#projectStateLabel[projectState="dirty"] {
                background: #fff8e8;
                border-color: #ead7a1;
                color: #805b12;
            }
            QLabel#globalProjectDirtyLabel {
                background: #fff8e8;
                border: 1px solid #ead7a1;
                border-radius: 9px;
                color: #805b12;
                font-weight: 650;
                padding: 3px 8px;
            }
            QLabel#globalDraftLabel {
                background: #eef5ff;
                border: 1px solid #d5e5f8;
                border-radius: 9px;
                color: #245b92;
                font-weight: 650;
                padding: 3px 8px;
            }
            QPushButton#saveProjectButton[projectDirty="true"] {
                background: #0071e3;
                border-color: #0071e3;
                color: #ffffff;
                font-weight: 650;
            }
            QPushButton#saveProjectButton[projectDirty="true"]:hover {
                background: #0067d1;
            }
            QPushButton#saveProjectButton:disabled {
                background: #f1f1f4;
                border-color: #d8d8de;
                color: #99999f;
                font-weight: 600;
            }
            QLabel#statusChip, QLabel#summaryChip {
                background: #ffffff;
                border: 1px solid #d6d6dc;
                border-radius: 11px;
                padding: 5px 10px;
                color: #303034;
            }
            QLabel#statusChip[trackingOutcome="complete"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#statusChip[trackingOutcome="partial"] {
                background: #fff6df;
                border-color: #f0d59a;
                color: #8a5a00;
            }
            QLabel#statusChip[trackingOutcome="failed"] {
                background: #fff0f0;
                border-color: #f2c4c4;
                color: #a12622;
            }
            QLabel#statusChip[trackingOutcome="canceled"] {
                background: #f5f5f7;
                border-color: #d8d8de;
                color: #5f6368;
            }
            QLabel#statusChip[trackingOutcome="running"] {
                background: #eef5ff;
                border-color: #d5e5f8;
                color: #245b92;
            }
            QLabel#jsonStatusLabel, QLabel#mediaProbeStatusLabel {
                color: #6e6e73;
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                padding: 4px 8px;
            }
            QLabel#jsonStatusLabel[jsonState="valid"] {
                color: #216e39;
                background: #edf9f1;
                border-color: #ccebd6;
            }
            QLabel#jsonStatusLabel[jsonState="edited"] {
                color: #6a4d00;
                background: #fff8e6;
                border-color: #ead8a4;
            }
            QLabel#jsonStatusLabel[jsonState="invalid"] {
                color: #a12622;
                background: #fff0f0;
                border-color: #f2c4c4;
            }
            QLabel#jsonValidationMessage {
                color: #8f1d1a;
                background: #fff4f4;
                border: 1px solid #efcaca;
                border-radius: 8px;
                padding: 7px 9px;
            }
            QLabel#analysisSourceDetailLabel {
                color: #6e6e73;
                padding: 2px 1px 5px 1px;
            }
            QLabel#reviewSelectionLabel {
                color: #245b92;
                background: #eef5ff;
                border: 1px solid #d5e5f8;
                border-radius: 8px;
                font-weight: 650;
                padding: 7px 9px 3px 9px;
                border-bottom-left-radius: 0;
                border-bottom-right-radius: 0;
            }
            QLabel#reviewSelectionDetailLabel {
                color: #35516f;
                background: #eef5ff;
                border: 1px solid #d5e5f8;
                border-top: 0;
                border-radius: 8px;
                padding: 2px 9px 7px 9px;
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
            QLabel#reviewSelectionLabel[reviewTone="manual"],
            QLabel#reviewSelectionDetailLabel[reviewTone="manual"] {
                color: #216e39;
                background: #edf9f1;
                border-color: #ccebd6;
            }
            QLabel#reviewSelectionLabel[reviewTone="attention"],
            QLabel#reviewSelectionDetailLabel[reviewTone="attention"] {
                color: #7a5100;
                background: #fff8e6;
                border-color: #ead8a4;
            }
            QLabel#reviewSelectionLabel[reviewTone="lost"],
            QLabel#reviewSelectionDetailLabel[reviewTone="lost"] {
                color: #a12622;
                background: #fff0f0;
                border-color: #f2c4c4;
            }
            QLabel#reviewSelectionLabel[reviewTone="empty"],
            QLabel#reviewSelectionDetailLabel[reviewTone="empty"] {
                color: #6e6e73;
                background: #f5f5f7;
                border-color: #dedee3;
            }
            QLabel#analysisStatusLabel {
                color: #5f6368;
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                padding: 5px 8px;
            }
            QLabel#analysisStatusLabel[analysisState="ready"] {
                color: #245b92;
                background: #eef5ff;
                border-color: #d5e5f8;
            }
            QLabel#analysisStatusLabel[analysisState="running"] {
                color: #245b92;
                background: #eef5ff;
                border-color: #9fc5ee;
            }
            QLabel#analysisStatusLabel[analysisState="dirty"] {
                color: #6a4d00;
                background: #fff8e6;
                border-color: #ead8a4;
            }
            QLabel#analysisStatusLabel[analysisState="complete"] {
                color: #216e39;
                background: #edf9f1;
                border-color: #ccebd6;
            }
            QLabel#analysisStatusLabel[analysisState="failed"] {
                color: #a12622;
                background: #fff0f0;
                border-color: #f2c4c4;
            }
            QLabel#analysisStatusLabel[analysisState="canceled"] {
                color: #5f6368;
                background: #f5f5f7;
                border-color: #d8d8de;
            }
            QLabel#candidateSummaryLabel {
                background: #eef5ff;
                border: 1px solid #d5e5f8;
                border-radius: 7px;
                color: #35516f;
                padding: 5px 8px;
            }
            QLabel#trackingBackendLabel,
            QLabel#trackingPerformanceLabel,
            QLabel#runPerformanceLabel {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #5f6368;
                padding: 4px 7px;
            }
            QLabel#trackingBackendLabel[backendState="accelerated"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#trackingBackendLabel[backendState="optimized"],
            QLabel#trackingBackendLabel[backendState="native"] {
                background: #eef5ff;
                border-color: #d5e5f8;
                color: #245b92;
            }
            QLabel#trackingBackendLabel[backendState="fallback"] {
                background: #fff8e8;
                border-color: #f0d89c;
                color: #805b12;
            }
            QLabel#trackingPerformanceLabel[performanceState="running"],
            QLabel#trackingPerformanceLabel[performanceState="finishing"],
            QLabel#runPerformanceLabel[performanceState="ready"],
            QLabel#runPerformanceLabel[performanceState="selected"] {
                background: #eef5ff;
                border-color: #d5e5f8;
                color: #245b92;
            }
            QLabel#trackingPerformanceLabel[performanceState="cancelling"] {
                background: #fff8e8;
                border-color: #f0d89c;
                color: #805b12;
            }
            QLabel#runPerformanceLabel[performanceState="unavailable"] {
                background: #fff8e8;
                border-color: #f0d89c;
                color: #805b12;
            }
            QLabel#responseStatusLabel {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #6e6e73;
                padding: 5px 8px;
            }
            QLabel#responseStatusLabel[responseState="stored"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#responseStatusLabel[responseState="cached"] {
                background: #eef5ff;
                border-color: #d5e5f8;
                color: #245b92;
            }
            QLabel#responseStatusLabel[responseState="recomputed"] {
                background: #f4efff;
                border-color: #ded2f5;
                color: #6941a5;
            }
            QLabel#responseStatusLabel[responseState="loading"] {
                background: #eef5ff;
                border-color: #9fc5ee;
                color: #245b92;
            }
            QLabel#responseStatusLabel[responseState="diagnostic"] {
                background: #eef5ff;
                border-color: #d5e5f8;
                color: #245b92;
            }
            QLabel#reviewDiagnosticStatusLabel {
                color: #526071;
                padding: 3px 2px;
            }
            QLabel#runComparisonTitle {
                color: #202124;
                font-weight: 700;
                padding: 2px 0 3px 0;
            }
            QLabel#roiGeometryTypeLabel {
                color: #303034;
                font-weight: 650;
                padding: 0 1px 1px 1px;
            }
            QLabel#roiGeometryMessage {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #6e6e73;
                padding: 5px 8px;
            }
            QLabel#roiGeometryMessage[roiGeometryState="dirty"] {
                background: #fff8e8;
                border-color: #ead7a1;
                color: #805b12;
            }
            QLabel#roiGeometryMessage[roiGeometryState="error"] {
                background: #fff0f0;
                border-color: #efcaca;
                color: #a12622;
            }
            QLabel#roiGeometryMessage[roiGeometryState="applied"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#roiGeometryMessage[roiGeometryState="drawing"] {
                background: #eef5ff;
                border-color: #b7d3f1;
                color: #245b92;
            }
            QLabel#roiGeometryMessage[roiGeometryState="selected"] {
                background: #eef5ff;
                border-color: #b7d3f1;
                color: #245b92;
            }
            QLabel#calibrationSummaryLabel {
                color: #303034;
            }
            QLabel#calibrationEditorMessage {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #6e6e73;
                padding: 5px 8px;
            }
            QLabel#calibrationEditorMessage[calibrationState="dirty"] {
                background: #fff8e8;
                border-color: #ead7a1;
                color: #805b12;
            }
            QLabel#calibrationEditorMessage[calibrationState="error"] {
                background: #fff0f0;
                border-color: #efcaca;
                color: #a12622;
            }
            QLabel#calibrationEditorMessage[calibrationState="applied"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#calibrationEditorMessage[calibrationState="drawing"] {
                background: #eef5ff;
                border-color: #b7d3f1;
                color: #245b92;
            }
            QLabel#mediaRelinkStatus {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #6e6e73;
                font-weight: 650;
                padding: 4px 8px;
            }
            QLabel#mediaRelinkStatus[mediaRelinkState="ready"],
            QLabel#mediaRelinkStatus[mediaRelinkState="match"],
            QLabel#mediaRelinkStatus[mediaRelinkState="applied"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QLabel#mediaRelinkStatus[mediaRelinkState="missing"],
            QLabel#mediaRelinkStatus[mediaRelinkState="mismatch"],
            QLabel#mediaRelinkStatus[mediaRelinkState="unverified"] {
                background: #fff8e8;
                border-color: #ead7a1;
                color: #805b12;
            }
            QLabel#mediaRelinkStatus[mediaRelinkState="incompatible"],
            QLabel#mediaRelinkStatus[mediaRelinkState="unavailable"] {
                background: #fff0f0;
                border-color: #efcaca;
                color: #a12622;
            }
            QLabel#mediaRelinkDetail,
            QLabel#mediaRelinkCandidate {
                color: #52525a;
                padding: 1px 2px;
            }
            QLabel#mediaRelinkDifferences {
                background: #fff8e8;
                border: 1px solid #ead7a1;
                border-radius: 7px;
                color: #805b12;
                padding: 4px 8px;
            }
            QLabel#taskActionSummary {
                color: #52525a;
                padding: 1px 2px;
            }
            QLabel#taskActionMessage {
                background: #f5f5f7;
                border: 1px solid #dedee3;
                border-radius: 7px;
                color: #6e6e73;
                padding: 4px 8px;
            }
            QLabel#taskActionMessage[taskActionState="warning"] {
                background: #fff8e8;
                border-color: #ead7a1;
                color: #805b12;
            }
            QLabel#taskActionMessage[taskActionState="removed"] {
                background: #edf9f1;
                border-color: #ccebd6;
                color: #216e39;
            }
            QPushButton#removeTaskButton[taskDestructive="true"] {
                background: #c9342b;
                border-color: #c9342b;
                color: #ffffff;
                font-weight: 650;
            }
            QPushButton#removeTaskButton[taskDestructive="true"]:hover {
                background: #b52a23;
                border-color: #b52a23;
            }
            QPushButton#applyRoiGeometryButton {
                background: #0071e3;
                border-color: #0071e3;
                color: #ffffff;
                font-weight: 650;
            }
            QPushButton#applyRoiGeometryButton:hover {
                background: #0067d1;
            }
            QPushButton#applyRoiGeometryButton:disabled {
                background: #f0f0f3;
                border-color: #dedee3;
                color: #9a9aa1;
            }
            QPushButton#applyCalibrationButton {
                background: #0071e3;
                border-color: #0071e3;
                color: #ffffff;
                font-weight: 650;
            }
            QPushButton#applyCalibrationButton:hover {
                background: #0067d1;
            }
            QPushButton#applyCalibrationButton:disabled {
                background: #f0f0f3;
                border-color: #dedee3;
                color: #9a9aa1;
            }
            QPushButton#applyMediaRelinkButton {
                background: #0071e3;
                border-color: #0071e3;
                color: #ffffff;
                font-weight: 650;
            }
            QPushButton#applyMediaRelinkButton:hover {
                background: #0067d1;
            }
            QPushButton#applyMediaRelinkButton:disabled {
                background: #f0f0f3;
                border-color: #dedee3;
                color: #9a9aa1;
            }
            QLabel#sectionLabel {
                color: #6e6e73;
                font-weight: 700;
                letter-spacing: 0px;
                padding: 8px 0 2px 1px;
            }
            QLabel#previewTitle {
                color: #303034;
                font-weight: 700;
            }
            QLabel#playbackStatusLabel {
                background: #eef5ff;
                border: 1px solid #d5e5f8;
                border-radius: 9px;
                color: #245b92;
                font-weight: 650;
                padding: 3px 8px;
            }
            QLabel#playbackStatusLabel[playbackState="catchup"] {
                background: #fff8e8;
                border-color: #f0d89c;
                color: #805b12;
            }
            QLabel#playbackStatusLabel[playbackState="error"] {
                background: #fff0f1;
                border-color: #f4c2c7;
                color: #a61b2b;
            }
            QLabel#previewCanvas {
                background: #111114;
                border: 1px solid #202025;
                border-radius: 12px;
                color: #b8b8bf;
            }
            QFrame#transportBar {
                background: #fbfbfd;
                border: 1px solid #d8d8de;
                border-radius: 12px;
            }
            QWidget#rightSidebar {
                background: #f5f5f7;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
                top: -1px;
            }
            QTabBar::tab {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 6px 10px;
                margin-right: 2px;
                color: #3c3c43;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                border: 1px solid #d6d6dc;
                color: #0071e3;
                font-weight: 600;
            }
            QTabBar::tab:hover {
                background: #ececf1;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d2d2d7;
                border-radius: 8px;
                min-height: 24px;
                padding: 5px 10px;
                color: #1d1d1f;
            }
            QPushButton:hover {
                background: #f0f0f4;
                border-color: #c4c4cc;
            }
            QPushButton:focus,
            QComboBox:focus,
            QSpinBox:focus,
            QDoubleSpinBox:focus,
            QPlainTextEdit:focus,
            QTextBrowser:focus,
            QListWidget:focus,
            QTableView:focus {
                border: 2px solid #0071e3;
            }
            QCheckBox:focus {
                background: #e5f1ff;
                border: 1px solid #0071e3;
                border-radius: 5px;
            }
            QTabBar:focus {
                border: 2px solid #0071e3;
                border-radius: 9px;
            }
            QPushButton:disabled {
                background: #f0f0f3;
                border-color: #dedee3;
                color: #9a9aa1;
            }
            QPushButton#runTrackingButton {
                background: #0071e3;
                border-color: #0071e3;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#runTrackingButton:hover {
                background: #0067d1;
            }
            QPushButton#runTrackingButton:disabled {
                background: #dcecff;
                border-color: #d2e5fb;
                color: #7898bd;
            }
            QPushButton#runTrackingButton[trackingBusy="true"] {
                background: #c9342b;
                border-color: #c9342b;
                color: #ffffff;
            }
            QPushButton#runTrackingButton[trackingBusy="true"]:hover {
                background: #b52a23;
                border-color: #b52a23;
            }
            QPushButton#transportButton {
                min-width: 78px;
            }
            QPlainTextEdit, QTextBrowser, QListWidget, QTableView, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #ffffff;
                border: 1px solid #d6d6dc;
                border-radius: 8px;
                padding: 3px;
                selection-background-color: #cce4ff;
            }
            QTableView {
                gridline-color: #ebebef;
                alternate-background-color: #fafafa;
                border-radius: 9px;
            }
            QListWidget#taskList::item:disabled {
                color: #9a9aa1;
                padding: 10px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #0b4f9c;
            }
            QHeaderView::section {
                background: #f3f3f6;
                border: none;
                border-right: 1px solid #dedee3;
                padding: 6px 7px;
                font-weight: 600;
                color: #303034;
            }
            QCheckBox {
                spacing: 6px;
                color: #303034;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #d6d6dc;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #0071e3;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            """
        )

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _add_sidebar_page(self, content: QWidget, label: str, object_name: str) -> QScrollArea:
        """Keep every workflow reachable without allowing a tall tab to enlarge the window."""

        content.setObjectName(f"{object_name}Content")
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        page = QScrollArea()
        page.setObjectName(object_name)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setWidgetResizable(True)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        page.setAccessibleName(f"{label} workflow")
        page.setWidget(content)
        self.sidebar_tabs.addTab(page, label)
        return page

    def _sidebar_tab_changed(self, _index: int) -> None:
        self._sync_roi_node_editing()
        current = self.sidebar_tabs.currentWidget()
        if current is self.review_tab:
            self._hydrate_project_open_diagnostics()
        elif current is not None and current.objectName() == "signalTab":
            self._hydrate_project_open_analysis_sources()

    def _build_media_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.add_media_button.setObjectName("addMediaButton")
        self.add_media_button.setToolTip("Add video or WAV files to the current project.")
        self.open_project_button.setToolTip("Open a saved Neo-Tracker project.")
        self.save_project_button.setToolTip("Save media paths, settings, calibration, and results.")
        self.open_project_button.setObjectName("openProjectButton")
        self.save_project_button.setObjectName("saveProjectButton")
        self.open_project_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.save_project_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        project_row = QHBoxLayout()
        project_row.addWidget(self.add_media_button)
        project_row.addWidget(self.open_project_button)
        project_row.addWidget(self.save_project_button)
        layout.addLayout(project_row)
        layout.addWidget(self.media_probe_status_label)
        layout.addWidget(self.project_status_panel)

        layout.addWidget(self._section_label("TASKS"))
        self.task_list.setObjectName("taskList")
        self.task_list.currentItemChanged.connect(self._task_changed)
        layout.addWidget(self.task_list, 1)
        self._show_empty_task_list_placeholder()
        self.task_actions_panel.removeConfirmed.connect(self._remove_current_task)
        self.task_actions_panel.undoRequested.connect(self._undo_removed_task)
        layout.addWidget(self.task_actions_panel)

        self.media_relink_panel.browseRequested.connect(self._choose_media_relink)
        self.media_relink_panel.applyRequested.connect(self._apply_media_relink)
        self.media_relink_panel.cancelRequested.connect(self._cancel_media_relink)
        layout.addWidget(self.media_relink_section_label)
        layout.addWidget(self.media_relink_panel)

        info_form = QFormLayout()
        info_form.addRow("Media backend", self.backend_label)
        info_form.addRow("Path", self.media_path_label)
        info_form.addRow(self.media_fps_title_label, self.media_fps_label)
        info_form.addRow(self.media_frames_title_label, self.media_frames_label)
        info_form.addRow(self.media_resolution_title_label, self.media_resolution_label)
        info_form.addRow("Duration", self.media_duration_label)
        layout.addLayout(info_form)

        layout.addStretch(1)
        self.media_tab = self._add_sidebar_page(tab, "Media", "mediaTab")

    def _build_tracking_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.preset_combo.setObjectName("presetCombo")
        self.preset_combo.setToolTip("Choose the tracking pipeline preset for this task.")
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        layout.addWidget(self._section_label("PRESET"))
        layout.addWidget(self.preset_combo)

        self.preset_description.setObjectName("presetDescription")
        self.preset_description.setMaximumHeight(92)
        self.preset_description.setOpenExternalLinks(False)
        layout.addWidget(self.preset_description)

        self.tracking_backend_label.setObjectName("trackingBackendLabel")
        self.tracking_backend_label.setAccessibleName("Observation compute backend")
        self.tracking_performance_label.setObjectName("trackingPerformanceLabel")
        self.tracking_performance_label.setProperty("performanceState", "running")
        self.tracking_performance_label.setAccessibleName("Live tracking performance")
        self.tracking_performance_label.setWordWrap(False)
        self.tracking_performance_label.setMinimumHeight(64)
        self.tracking_performance_label.hide()
        backend_form = QFormLayout()
        backend_form.addRow("Compute backend", self.tracking_backend_label)
        backend_form.addRow(self.tracking_performance_title_label, self.tracking_performance_label)
        self.tracking_performance_title_label.hide()
        layout.addLayout(backend_form)

        marker_form = QFormLayout(self.marker_controls_widget)
        marker_form.setContentsMargins(0, 0, 0, 0)
        marker_row = QHBoxLayout()
        self.marker_swatch_label.setObjectName("markerSampleSwatch")
        self.marker_swatch_label.setFixedSize(34, 22)
        self.marker_swatch_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.marker_sample_label.setObjectName("markerSampleLabel")
        self.marker_sample_label.setWordWrap(True)
        self.sample_marker_button.setObjectName("sampleMarkerButton")
        self.sample_marker_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton))
        self.sample_marker_button.setToolTip("Click a marker in the preview to sample its color.")
        self.sample_marker_button.clicked.connect(self._start_color_sampling)
        marker_row.addWidget(self.marker_swatch_label)
        marker_row.addWidget(self.marker_sample_label, 1)
        marker_row.addWidget(self.sample_marker_button)
        marker_form.addRow("Marker color", marker_row)

        self.color_tolerance_spin.setObjectName("colorToleranceSpin")
        self.color_tolerance_spin.setRange(0.001, 1.0)
        self.color_tolerance_spin.setDecimals(3)
        self.color_tolerance_spin.setSingleStep(0.01)
        self.color_tolerance_spin.setToolTip("Adjust how much color variation the marker detector accepts.")
        self.color_tolerance_spin.valueChanged.connect(self._color_tolerance_changed)
        marker_form.addRow("Tolerance", self.color_tolerance_spin)
        self.color_max_candidates_spin.setObjectName("colorMaxCandidatesSpin")
        self.color_max_candidates_spin.setRange(1, 12)
        self.color_max_candidates_spin.setToolTip(
            "Keep separate color regions so the motion model can choose the most consistent target."
        )
        self.color_max_candidates_spin.setAccessibleName("Maximum marker candidates")
        self.color_max_candidates_spin.valueChanged.connect(self._color_candidate_settings_changed)
        marker_form.addRow("Max candidates", self.color_max_candidates_spin)
        self.color_min_area_spin.setObjectName("colorMinAreaSpin")
        self.color_min_area_spin.setRange(1, 100000)
        self.color_min_area_spin.setSuffix(" px")
        self.color_min_area_spin.setToolTip("Ignore color regions smaller than this pixel area.")
        self.color_min_area_spin.setAccessibleName("Minimum marker region area")
        self.color_min_area_spin.valueChanged.connect(self._color_candidate_settings_changed)
        marker_form.addRow("Minimum area", self.color_min_area_spin)
        layout.addWidget(self.marker_controls_widget)

        layout.addWidget(self._section_label("CURRENT MODULES"))
        self.module_summary_list.setObjectName("moduleSummaryList")
        layout.addWidget(self.module_summary_list, 1)
        self.tracking_tab = self._add_sidebar_page(tab, "Tracking", "trackingTab")

    def _build_review_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.review_summary_label.setObjectName("reviewSummaryLabel")
        self.review_summary_label.setWordWrap(True)
        layout.addWidget(self.review_summary_label)
        self.review_selection_label.setObjectName("reviewSelectionLabel")
        self.review_selection_label.setProperty("reviewTone", "empty")
        self.review_selection_label.setAccessibleName("Selected tracking result")
        self.review_selection_detail_label.setObjectName("reviewSelectionDetailLabel")
        self.review_selection_detail_label.setProperty("reviewTone", "empty")
        self.review_selection_detail_label.setWordWrap(True)
        self.review_selection_detail_label.setAccessibleName("Selected result state values")
        layout.addWidget(self.review_selection_label)
        layout.addWidget(self.review_selection_detail_label)

        action_grid = QGridLayout()
        self.correct_point_button.setObjectName("correctPointButton")
        self.mark_lost_button.setObjectName("markLostButton")
        self.undo_review_edit_button.setObjectName("undoReviewEditButton")
        self.rerun_after_button.setObjectName("rerunAfterButton")
        self.jump_to_result_button.setObjectName("jumpToResultButton")
        self.correct_point_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        self.mark_lost_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        self.undo_review_edit_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.rerun_after_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.jump_to_result_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.correct_point_button.setToolTip("Replace the selected result with a point clicked in the preview.")
        self.mark_lost_button.setToolTip("Mark the selected frame as lost and exclude it from trusted review.")
        self.undo_review_edit_button.setToolTip("Undo the most recent point correction or Mark Lost action.")
        self.rerun_after_button.setToolTip(
            "Review the affected later Results/Edits, then rerun tracking after the selected result."
        )
        self.jump_to_result_button.setToolTip("Move the preview to the selected result frame.")
        self.show_response_checkbox.setToolTip(
            "Show an image-space heatmap, or use the retained angular profile for polar detectors without "
            "recomputing the source frame."
        )
        self.show_observation_checkbox.setToolTip("Show the raw observation point.")
        self.show_measurement_checkbox.setToolTip("Show measured trajectory points before filtering.")
        self.show_candidates_checkbox.setToolTip("Show candidate points considered by the optimizer.")
        self.show_prediction_checkbox.setToolTip("Show the motion model prediction.")
        self.show_response_checkbox.stateChanged.connect(self._response_overlay_changed)
        self.show_observation_checkbox.setChecked(True)
        self.show_observation_checkbox.stateChanged.connect(lambda _state: self._render_preview())
        self.show_measurement_checkbox.setChecked(False)
        self.show_measurement_checkbox.stateChanged.connect(lambda _state: self._render_preview())
        self.show_candidates_checkbox.setChecked(True)
        self.show_candidates_checkbox.stateChanged.connect(lambda _state: self._render_preview())
        self.show_prediction_checkbox.setChecked(True)
        self.show_prediction_checkbox.stateChanged.connect(lambda _state: self._render_preview())
        action_grid.addWidget(self.correct_point_button, 0, 0)
        action_grid.addWidget(self.mark_lost_button, 0, 1)
        action_grid.addWidget(self.undo_review_edit_button, 1, 0)
        action_grid.addWidget(self.rerun_after_button, 1, 1)
        action_grid.addWidget(self.jump_to_result_button, 2, 0, 1, 2)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        layout.addLayout(action_grid)
        overlay_grid = QGridLayout()
        overlay_grid.addWidget(self._section_label("OVERLAYS"), 0, 0, 1, 2)
        overlay_grid.addWidget(self.show_response_checkbox, 1, 0)
        overlay_grid.addWidget(self.show_observation_checkbox, 1, 1)
        overlay_grid.addWidget(self.show_measurement_checkbox, 2, 0)
        overlay_grid.addWidget(self.show_candidates_checkbox, 2, 1)
        overlay_grid.addWidget(self.show_prediction_checkbox, 3, 0)
        overlay_grid.setColumnStretch(0, 1)
        overlay_grid.setColumnStretch(1, 1)
        layout.addLayout(overlay_grid)
        self.candidate_summary_label.setObjectName("candidateSummaryLabel")
        self.candidate_summary_label.setToolTip(
            "Candidate count for the selected frame and the score chosen by the motion model."
        )
        self.candidate_summary_label.setAccessibleName("Current frame candidate summary")
        layout.addWidget(self.candidate_summary_label)
        self.response_status_label.setObjectName("responseStatusLabel")
        self.response_status_label.setProperty("responseState", "off")
        self.response_status_label.setAccessibleName("Current frame response evidence status")
        self.response_status_label.setToolTip(
            "Turn on Response to inspect a retained angular profile or an image-space heatmap."
        )
        layout.addWidget(self.response_status_label)

        self.results_table.setObjectName("resultsTable")
        self.results_table.setAccessibleName("Tracking results")
        self.results_table.setAccessibleDescription(
            "Complete tracking result table. Rows are formatted as they become visible; "
            "all results remain selectable."
        )
        self.results_table.setModel(self.results_model)
        self.results_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._result_selection_changed()
        )
        layout.addWidget(self.results_table, 1)
        self.review_diagnostics_panel.frameActivated.connect(self._jump_to_diagnostic_frame)
        layout.addWidget(self.review_diagnostics_panel)
        layout.addWidget(self._section_label("HISTORY"))
        self.review_history_tabs.setObjectName("reviewHistoryTabs")
        self.review_history_tabs.setMaximumHeight(220)
        self.review_history_tabs.setAccessibleName("Review history")
        self.review_history_tabs.setAccessibleDescription(
            "Switch between tracking run history and manual edit history."
        )
        self.review_history_tabs.tabBar().setAccessibleName("Review history tabs")
        self.run_history_panel.compareRequested.connect(self._show_run_history_comparison)
        self.run_history_panel.exportRequested.connect(self._export_visible_run_history)
        self.edit_history_panel.jumpRequested.connect(self._jump_to_edit_frame)
        self.edit_history_panel.exportRequested.connect(self._export_visible_edit_history)
        self.review_history_tabs.addTab(self.run_history_panel, "Runs")
        self.review_history_tabs.addTab(self.edit_history_panel, "Edits")
        layout.addWidget(self.review_history_tabs)
        layout.addWidget(self._section_label("PHYSICS INSPECTOR"))
        layout.addWidget(self.physics_inspector)
        self.review_tab = self._add_sidebar_page(tab, "Review", "reviewTab")

    def _build_processing_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.analysis_source_combo.setObjectName("analysisSourceCombo")
        self.analysis_source_combo.setToolTip("Choose a tracking or WAV signal to process.")
        self.analysis_source_combo.currentIndexChanged.connect(lambda _index: self._analysis_source_changed())
        self.refresh_analysis_sources_button.setObjectName("refreshAnalysisSourcesButton")
        self.refresh_analysis_sources_button.setToolTip("Refresh available tracking and WAV signal sources.")
        self.refresh_analysis_sources_button.setAccessibleName("Refresh signal sources")
        self.refresh_analysis_sources_button.clicked.connect(
            lambda: self._refresh_analysis_sources(force=True)
        )
        source_row = QHBoxLayout()
        source_row.addWidget(self.analysis_source_combo, 1)
        source_row.addWidget(self.refresh_analysis_sources_button)
        layout.addWidget(self._section_label("DATA SOURCE"))
        layout.addLayout(source_row)
        self.analysis_source_detail_label.setObjectName("analysisSourceDetailLabel")
        self.analysis_source_detail_label.setWordWrap(True)
        self.analysis_source_detail_label.setAccessibleName("Selected signal source details")
        layout.addWidget(self.analysis_source_detail_label)

        self.analysis_method_combo.setObjectName("analysisMethodCombo")
        self.analysis_method_combo.setToolTip("Choose FFT for a spectrum or STFT for a time-frequency view.")
        self.analysis_method_combo.addItems(["FFT", "STFT"])
        self.analysis_method_combo.currentTextChanged.connect(self._analysis_method_changed)
        self.analysis_method_combo.currentTextChanged.connect(lambda _text: self._mark_analysis_dirty())
        self.analysis_detrend_combo.addItems(["none", "mean", "linear"])
        self.analysis_detrend_combo.setCurrentText("mean")
        self.analysis_detrend_combo.currentTextChanged.connect(lambda _text: self._mark_analysis_dirty())
        self.analysis_window_combo.addItems(["boxcar", "hann", "hamming", "blackman"])
        self.analysis_window_combo.setCurrentText("hann")
        self.analysis_window_combo.currentTextChanged.connect(lambda _text: self._mark_analysis_dirty())

        self.analysis_sample_rate_spin.setRange(0.0, 1_000_000.0)
        self.analysis_sample_rate_spin.setToolTip("Override the source sample rate, or leave at infer.")
        self.analysis_sample_rate_spin.setDecimals(3)
        self.analysis_sample_rate_spin.setSuffix(" Hz")
        self.analysis_sample_rate_spin.setSpecialValueText("infer")
        self.analysis_sample_rate_spin.valueChanged.connect(lambda _value: self._mark_analysis_dirty())
        self.analysis_freq_min_spin.setRange(0.0, 1_000_000.0)
        self.analysis_freq_min_spin.setToolTip("Optional lower frequency bound for exported and displayed results.")
        self.analysis_freq_min_spin.setDecimals(3)
        self.analysis_freq_min_spin.setSuffix(" Hz")
        self.analysis_freq_min_spin.setSpecialValueText("auto")
        self.analysis_freq_min_spin.valueChanged.connect(lambda _value: self._mark_analysis_dirty())
        self.analysis_freq_max_spin.setRange(0.0, 1_000_000.0)
        self.analysis_freq_max_spin.setToolTip("Optional upper frequency bound for exported and displayed results.")
        self.analysis_freq_max_spin.setDecimals(3)
        self.analysis_freq_max_spin.setSuffix(" Hz")
        self.analysis_freq_max_spin.setSpecialValueText("auto")
        self.analysis_freq_max_spin.valueChanged.connect(lambda _value: self._mark_analysis_dirty())
        self.analysis_stft_window_spin.setRange(2, 1_000_000)
        self.analysis_stft_window_spin.setValue(256)
        self.analysis_stft_window_spin.valueChanged.connect(lambda _value: self._mark_analysis_dirty())
        self.analysis_stft_overlap_spin.setRange(0.0, 0.99)
        self.analysis_stft_overlap_spin.setSingleStep(0.05)
        self.analysis_stft_overlap_spin.setDecimals(2)
        self.analysis_stft_overlap_spin.setValue(0.75)
        self.analysis_stft_overlap_spin.valueChanged.connect(lambda _value: self._mark_analysis_dirty())

        form = QFormLayout()
        self.analysis_parameters_form = form
        form.addRow("Method", self.analysis_method_combo)
        form.addRow("Detrend", self.analysis_detrend_combo)
        form.addRow("Window", self.analysis_window_combo)
        form.addRow("Sample rate", self.analysis_sample_rate_spin)
        form.addRow("Frequency min", self.analysis_freq_min_spin)
        form.addRow("Frequency max", self.analysis_freq_max_spin)
        form.addRow("STFT window", self.analysis_stft_window_spin)
        form.addRow("STFT overlap", self.analysis_stft_overlap_spin)
        layout.addLayout(form)

        self.run_analysis_button.setObjectName("runAnalysisButton")
        self.run_analysis_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_analysis_button.setToolTip("Run FFT or STFT on the selected signal source.")
        self.analysis_status_label.setObjectName("analysisStatusLabel")
        self.analysis_status_label.setAccessibleName("Signal processing status")
        action_row = QHBoxLayout()
        action_row.addWidget(self.run_analysis_button, 1)
        action_row.addWidget(self.analysis_status_label)
        export_row = QHBoxLayout()
        self.analysis_export_csv_button.setToolTip("Export the latest processing result as CSV.")
        self.analysis_export_npz_button.setToolTip("Export the latest processing result as NPZ.")
        self._set_analysis_export_enabled(False)
        export_row.addWidget(self.analysis_export_csv_button)
        export_row.addWidget(self.analysis_export_npz_button)
        layout.addLayout(action_row)
        layout.addLayout(export_row)

        self.analysis_result_view.setObjectName("analysisResultView")
        self.analysis_result_view.setReadOnly(True)
        self.analysis_result_view.setPlainText("Choose a signal source, then run FFT or STFT.")
        layout.addWidget(self.analysis_result_view, 1)
        self._add_sidebar_page(tab, "Signal", "signalTab")
        self._analysis_method_changed(self.analysis_method_combo.currentText())
        self._set_analysis_status("No source", "empty", "Run tracking or add a WAV file first.")

    def _build_calibration_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.curve_half_width_spin.setRange(1.0, 10_000.0)
        self.curve_half_width_spin.setDecimals(1)
        self.curve_half_width_spin.setSuffix(" px")
        self.curve_half_width_spin.setValue(24.0)
        self.curve_half_width_spin.setToolTip("Half-width used the next time a curve band is drawn.")
        self.curve_half_width_spin.setAccessibleName("New curve band half-width")
        form.addRow("ROI", self.roi_status_label)
        form.addRow("Scale", self.scale_status_label)
        layout.addLayout(form)

        self.roi_geometry_editor.configApplied.connect(self._roi_geometry_applied)
        self.roi_geometry_editor.draftChanged.connect(self._roi_geometry_draft_changed)
        self.roi_geometry_editor.nodeSelectionChanged.connect(self._roi_node_selection_changed)
        layout.addWidget(self._section_label("ROI GEOMETRY"))
        layout.addWidget(self.roi_geometry_editor)

        roi_grid = QGridLayout()
        roi_button = QPushButton("Rectangle")
        circle_button = QPushButton("Circle")
        annulus_button = QPushButton("Annulus")
        polygon_button = QPushButton("Polygon")
        curve_button = QPushButton("Curve Band")
        roi_button.setToolTip("Draw a rectangular ROI on the current video frame.")
        circle_button.setToolTip("Draw a circular ROI on the current video frame.")
        annulus_button.setToolTip("Draw an annular ROI for circular fronts or rings.")
        polygon_button.setToolTip("Draw a polygon ROI; finish it with Finish Drawing.")
        curve_button.setToolTip("Draw a curve band ROI for path-following motion.")
        self.reset_roi_button.setToolTip("Restore the ROI from the selected preset.")
        roi_button.clicked.connect(self._start_roi_selection)
        circle_button.clicked.connect(self._start_circular_roi_selection)
        annulus_button.clicked.connect(self._start_annular_roi_selection)
        polygon_button.clicked.connect(self._start_polygon_roi_selection)
        curve_button.clicked.connect(self._start_curve_band_roi_selection)
        self.reset_roi_button.clicked.connect(self._reset_roi_to_preset)
        roi_grid.addWidget(roi_button, 0, 0)
        roi_grid.addWidget(circle_button, 0, 1)
        roi_grid.addWidget(annulus_button, 1, 0)
        roi_grid.addWidget(polygon_button, 1, 1)
        roi_grid.addWidget(curve_button, 2, 0)
        roi_grid.addWidget(self.reset_roi_button, 2, 1)
        roi_grid.setColumnStretch(0, 1)
        roi_grid.setColumnStretch(1, 1)
        polygon_action_row = QHBoxLayout()
        self.finish_roi_drawing_button.setToolTip("Complete the active polygon or curve-band drawing.")
        self.cancel_roi_drawing_button.setToolTip("Cancel the active ROI or calibration drawing.")
        self.finish_roi_drawing_button.setAccessibleName("Finish ROI drawing")
        self.cancel_roi_drawing_button.setAccessibleName("Cancel preview drawing")
        self.finish_roi_drawing_button.clicked.connect(self._finish_roi_drawing_selection)
        self.cancel_roi_drawing_button.clicked.connect(self._cancel_preview_selection)
        self.finish_roi_drawing_button.setEnabled(False)
        self.cancel_roi_drawing_button.setEnabled(False)
        polygon_action_row.addWidget(self.finish_roi_drawing_button)
        polygon_action_row.addWidget(self.cancel_roi_drawing_button)
        self.calibration_editor.calibrationApplied.connect(self._calibration_editor_applied)
        self.calibration_editor.draftChanged.connect(self._calibration_draft_changed)
        self.mark_calibration_button.setToolTip("Draw a two-point calibration rod on the video frame.")
        self.mark_calibration_button.setAccessibleName("Mark calibration rod in preview")
        self.reset_calibration_button.setToolTip("Clear the current calibration rod and return to pixel units.")
        self.mark_calibration_button.clicked.connect(self._start_calibration_selection)
        self.reset_calibration_button.clicked.connect(self._reset_calibration)
        calibration_action_row = QHBoxLayout()
        calibration_action_row.addWidget(self.mark_calibration_button)
        calibration_action_row.addWidget(self.reset_calibration_button)
        layout.addWidget(self._section_label("ROI TOOLS"))
        layout.addLayout(roi_grid)
        curve_width_form = QFormLayout()
        curve_width_form.addRow("New curve half-width", self.curve_half_width_spin)
        layout.addLayout(curve_width_form)
        layout.addLayout(polygon_action_row)
        layout.addWidget(self._section_label("CALIBRATION"))
        layout.addWidget(self.calibration_editor)
        layout.addLayout(calibration_action_row)
        layout.addStretch(1)
        self.calibration_tab = self._add_sidebar_page(tab, "Calib", "calibrationTab")

    def _build_workflow_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(self._section_label("TRACKING FLOW"))
        self.workflow_list.setObjectName("workflowList")
        layout.addWidget(self.workflow_list, 1)
        self._add_sidebar_page(tab, "Flow", "workflowTab")

    def _build_advanced_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        header = QHBoxLayout()
        header.addWidget(self._section_label("PIPELINE JSON"))
        header.addWidget(self.json_status_label, 1)
        self.validate_json_button.setObjectName("validateJsonButton")
        self.apply_json_button.setObjectName("applyJsonButton")
        self.reset_json_button.setObjectName("resetJsonButton")
        self.validate_json_button.setToolTip("Validate the pipeline JSON without changing the current task.")
        self.apply_json_button.setToolTip("Apply the edited pipeline JSON to the current task.")
        self.reset_json_button.setToolTip("Reload the JSON editor from the current pipeline.")
        self.validate_json_button.clicked.connect(self._validate_advanced_config)
        self.apply_json_button.clicked.connect(self._apply_advanced_config)
        self.reset_json_button.clicked.connect(self._sync_advanced_config_view)
        layout.addLayout(header)
        actions = QGridLayout()
        actions.addWidget(self.validate_json_button, 0, 0)
        actions.addWidget(self.apply_json_button, 0, 1)
        actions.addWidget(self.reset_json_button, 0, 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        actions.setColumnStretch(2, 1)
        layout.addLayout(actions)
        self.json_status_label.setObjectName("jsonStatusLabel")
        self.json_status_label.setAccessibleName("Pipeline JSON status")
        self.json_validation_message.setObjectName("jsonValidationMessage")
        self.json_validation_message.setAccessibleName("Pipeline JSON validation error")
        self.json_validation_message.setWordWrap(True)
        self.json_validation_message.hide()
        layout.addWidget(self.json_validation_message)
        self.advanced_config_view.setObjectName("advancedConfigView")
        self.advanced_config_view.setReadOnly(False)
        self.advanced_config_view.textChanged.connect(self._advanced_config_text_changed)
        layout.addWidget(self.advanced_config_view, 1)
        self._add_sidebar_page(tab, "JSON", "advancedTab")

    def _load_presets(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for key, descriptor in self.registry.items():
            self.preset_combo.addItem(descriptor.title, key)
        self.preset_combo.blockSignals(False)

    def _new_task(
        self,
        media_path: str | None,
        pipeline_key: str,
        *,
        media_info: MediaInfo | None = None,
    ) -> DesktopTask:
        return self.project_controller.new_task(
            media_path,
            pipeline_key,
            media_info=media_info,
        )

    def _add_media(self) -> None:
        if self._media_probe_thread is not None:
            self._cancel_media_probe()
            return
        if not self._background_tasks.idle:
            self.statusBar().showMessage(
                "Finish or cancel background processing before adding media.",
                6000,
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add media",
            "",
            "Media files (*.mp4 *.mov *.avi *.mkv *.wav);;Video files (*.mp4 *.mov *.avi *.mkv);;WAV audio (*.wav);;All files (*)",
        )
        if not paths:
            return
        self._start_media_probe(paths)

    def _start_media_probe(
        self,
        paths: list[str] | tuple[str, ...],
    ) -> bool:
        selected_paths = tuple(str(path) for path in paths if str(path))
        if not selected_paths or self._media_probe_thread is not None:
            return False
        accepted = self._media_import_coordinator.start(
            MediaImportRequest(
                paths=selected_paths,
                pipeline_key=self.current_task.pipeline_key,
                media_probe=self.project_controller.media_probe,
            )
        )
        if not accepted:
            return False
        self._set_media_probe_busy(
            True,
            total=len(selected_paths),
            operation="add",
        )
        return True

    def _media_probe_progressed(self, completed: int, total: int, filename: str) -> None:
        job = self._media_probe_job
        if job is None or not self._background_tasks.is_current(job.token):
            return
        detail = f"Inspecting media {int(completed)}/{int(total)} · {filename}"
        self.media_probe_status_label.setText(detail)
        self.media_probe_status_label.setAccessibleDescription(detail)
        self.statusBar().showMessage(detail)

    def _media_import_completed(
        self,
        job: MediaProbeJob,
        validated_results: tuple[tuple[str, MediaInfo], ...],
    ) -> None:
        if self._background_tasks.closing:
            return
        self._set_media_probe_busy(False)
        if not self.tasks and not self._confirm_editor_context_transition(
            "adding media to this project"
        ):
            self.statusBar().showMessage(
                "Add media canceled. Current editor work is still available.",
                6000,
            )
            return
        self._discard_removed_task_undo()
        self._explicit_empty_project = False
        if not self.tasks:
            self.task_list.blockSignals(True)
            self.task_list.clear()
            self.task_list.blockSignals(False)
        unavailable_count = 0
        for path, media_info in validated_results:
            unavailable_count += int(not media_info.available)
            task = self._new_task(
                str(path),
                job.pipeline_key,
                media_info=media_info,
            )
            self.tasks.append(task)
            item = QListWidgetItem(task.title())
            item.setData(Qt.ItemDataRole.UserRole, len(self.tasks) - 1)
            item.setToolTip(str(path))
            self.task_list.addItem(item)
        if self.task_list.currentRow() < 0:
            self.task_list.setCurrentRow(0)
        else:
            self._render_task_actions(self.current_task)
        self._mark_project_changed()
        result_count = len(validated_results)
        summary = f"Added {result_count} media task{'s' if result_count != 1 else ''} to this project."
        if unavailable_count:
            summary += f" {unavailable_count} need source attention."
        self.statusBar().showMessage(summary, 8000)

    def _media_import_failed(self, _job: MediaProbeJob, message: str) -> None:
        if self._background_tasks.closing:
            return
        self._set_media_probe_busy(False)
        QMessageBox.warning(
            self,
            "Add media",
            f"Could not inspect the selected media:\n{message}",
        )

    def _media_import_canceled(self, _job: MediaProbeJob) -> None:
        if self._background_tasks.closing:
            return
        self._set_media_probe_busy(False)
        self.statusBar().showMessage(
            "Media import canceled. No selected files were added.",
            6000,
        )

    def _media_import_idle(self) -> None:
        self._schedule_close_if_workers_stopped()

    def _cancel_media_probe(self) -> None:
        worker = self._media_probe_worker
        job = self._media_probe_job
        if worker is None or job is None:
            return
        self._media_import_coordinator.cancel("user")
        self._update_action("media.add", enabled=False, text="Cancelling…")
        self._set_action_enabled("project.open", False)
        self.media_probe_status_label.setText("Cancelling media import…")
        self.media_probe_status_label.setAccessibleDescription(
            "Cancel requested. Waiting for the current media file inspection to finish safely."
        )

    def _set_media_probe_busy(
        self,
        busy: bool,
        *,
        total: int = 0,
        operation: str = "add",
    ) -> None:
        self._set_action_enabled("project.open", not busy)
        self._set_action_enabled("project.save", not busy)
        self._set_action_enabled("media.add", not busy)
        project_open_busy = bool(busy and operation == "open")
        self.task_list.setEnabled(not project_open_busy)
        self.task_actions_panel.setEnabled(not project_open_busy)
        self.media_relink_panel.setEnabled(not project_open_busy)
        self.physics_workspace.setEnabled(not project_open_busy)
        self.action_registry.set_actions_blocked(
            _PROJECT_OPEN_BLOCKED_ACTIONS,
            project_open_busy,
        )
        for tab_index in range(1, self.sidebar_tabs.count()):
            self.sidebar_tabs.setTabEnabled(tab_index, not project_open_busy)
        if busy:
            if operation == "open":
                detail = "Opening project · reading project file…"
                self._update_action(
                    "project.open",
                    enabled=True,
                    text="Cancel Open",
                    icon=self.style().standardIcon(
                        QStyle.StandardPixmap.SP_DialogCancelButton
                    ),
                    tool_tip=(
                        "Cancel opening this project after the current media inspection finishes."
                    ),
                )
                self.open_project_button.setAccessibleName("Cancel project open")
            else:
                detail = f"Inspecting 0/{max(0, int(total))} media files…"
                self._update_action(
                    "media.add",
                    enabled=True,
                    text="Cancel Import",
                    tool_tip=(
                        "Cancel this media import after the current file inspection finishes."
                    ),
                )
                self.add_media_button.setAccessibleName("Cancel media import")
            self.media_probe_status_label.setText(detail)
            accessible_detail = (
                "Target project media files are being inspected in the background. The current project "
                "remains visible, but editing is paused until the open completes or is canceled."
                if operation == "open"
                else "Media files are being inspected in the background. The interface remains available."
            )
            self.media_probe_status_label.setAccessibleDescription(accessible_detail)
            self.media_probe_status_label.show()
            self.statusBar().showMessage(detail)
            return
        self._update_action(
            "media.add",
            text="Add media",
            tool_tip="Add video or WAV files to the current project.",
        )
        self.add_media_button.setAccessibleName("Add media")
        self._update_action(
            "project.open",
            text="Open Project",
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            tool_tip="Open a saved Neo-Tracker project.",
        )
        self.open_project_button.setAccessibleName("Open project")
        self.media_probe_status_label.hide()

    def _start_project_open(self, path: str | Path) -> bool:
        project_path = Path(path)
        if self._project_open_thread is not None:
            return False
        accepted = self._project_io_coordinator.start_open(
            ProjectOpenRequest(
                path=project_path,
                controller=self.project_controller,
                project_loader=self.project_loader,
            )
        )
        if not accepted:
            return False
        self._set_media_probe_busy(True, operation="open")
        return True

    def _project_open_progressed(
        self,
        phase: str,
        completed: int,
        total: int,
        filename: str,
    ) -> None:
        job = self._project_open_job
        if job is None or not self._background_tasks.is_current(job.token):
            return
        if phase == "loading":
            detail = f"Opening project · reading {filename or job.path.name}…"
        elif phase == "validating":
            detail = f"Opening project · validating {filename or job.path.name} in isolation…"
        elif phase == "decoding":
            detail = "Opening project · decoding validated records…"
        elif phase == "materializing":
            detail = "Opening project · materializing validated results…"
        elif phase == "indexing-review":
            detail = "Opening project · preparing review index…"
        elif phase == "indexing-analysis":
            detail = "Opening project · preparing signal index…"
        elif phase == "fingerprinting":
            detail = "Opening project · verifying the saved baseline…"
        elif phase == "inspecting":
            detail = f"Opening project · inspecting {int(completed)}/{int(total)} media files"
            if filename:
                detail += f" · {filename}"
        else:
            detail = f"Opening project · preparing {int(completed)}/{int(total)} tasks…"
        self.media_probe_status_label.setText(detail)
        self.media_probe_status_label.setAccessibleDescription(
            detail
            + " The current project remains visible, but editing is paused until the open completes or is canceled."
        )
        self.statusBar().showMessage(detail)

    def _project_io_open_prepared(
        self,
        job: ProjectOpenJob,
        payload: PreparedProjectOpen,
    ) -> None:
        defer_heavy_views = bool(
            payload.tasks
            and len(payload.tasks[0].pipeline.results)
            >= _PROJECT_OPEN_DEFERRED_RESULTS_THRESHOLD
        )
        detail = "Opening project · applying the prepared workspace…"
        self.media_probe_status_label.setText(detail)
        self.media_probe_status_label.setAccessibleDescription(
            detail
            + " The validated project is replacing the current workspace; editing remains paused until this commit finishes."
        )
        self.statusBar().showMessage(detail)
        apply_started = monotonic()
        try:
            self._apply_loaded_project(
                payload.project,
                payload.path,
                prepared_tasks=payload.tasks,
                clean_fingerprint=payload.fingerprint,
                defer_heavy_views=defer_heavy_views,
                prepared_diagnostics=payload.review_diagnostics,
                prepared_analysis_sources=payload.analysis_sources,
            )
        except Exception as exc:
            job.failure_detail = str(exc)
            job.completed = False
        finally:
            self._last_project_open_apply_ms = (monotonic() - apply_started) * 1000.0
        self._finish_project_open_ui(job)

    def _project_io_open_failed(self, job: ProjectOpenJob, _message: str) -> None:
        self._finish_project_open_ui(job)

    def _project_io_open_canceled(self, job: ProjectOpenJob) -> None:
        self._finish_project_open_ui(job)

    def _restore_task_view_after_aborted_transition(self) -> None:
        if self._unapplied_draft_names():
            self._render_tracking_status(self.current_task)
            return
        self._render_task(refresh_project_state=False)

    def _finish_project_open_ui(self, job: ProjectOpenJob) -> None:
        if self._background_tasks.closing:
            return
        self._set_media_probe_busy(False)
        if not job.completed:
            self._restore_task_view_after_aborted_transition()
        if job.failure_detail:
            QMessageBox.warning(
                self,
                "Open project",
                f"Could not open project:\n{job.failure_detail}",
            )
        elif job.cancelled:
            self.statusBar().showMessage(
                "Project open canceled. The current project is unchanged.",
                6000,
            )

    def _project_io_idle(self) -> None:
        self._schedule_close_if_workers_stopped()

    def _cancel_project_open(self) -> None:
        worker = self._project_open_worker
        job = self._project_open_job
        if worker is None or job is None:
            return
        self._project_io_coordinator.cancel_open("user")
        self._update_action("project.open", enabled=False, text="Cancelling…")
        self._set_action_enabled("media.add", False)
        self.media_probe_status_label.setText("Cancelling project open…")
        self.media_probe_status_label.setAccessibleDescription(
            "Cancel requested. Waiting for the current project-read or media-inspection step to finish safely."
        )

    def _choose_media_relink(self) -> None:
        task = self.current_task
        if task.media_path is None:
            return
        current_parent = Path(task.media_path).expanduser().parent
        initial_directory = str(current_parent) if current_parent.exists() else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose replacement media",
            initial_directory,
            "Media files (*.mp4 *.mov *.avi *.mkv *.wav);;Video files (*.mp4 *.mov *.avi *.mkv);;WAV audio (*.wav);;All files (*)",
        )
        if path:
            self._stage_media_relink(path)

    def _stage_media_relink(
        self,
        media_path: str,
        media_info: MediaInfo | None = None,
        *,
        assessment: MediaRelinkAssessment | None = None,
    ) -> MediaRelinkAssessment:
        task = self.current_task
        candidate_info = media_info if media_info is not None else probe_media_for_ui(media_path)
        if assessment is None:
            assessment = self.project_controller.assess_media_relink(
                task.saved_media_info if task.media_identity_requires_review else task.media_info,
                candidate_info,
                has_result_state=self._task_has_tracking_result_state(task),
            )
        self._media_relink_task = task
        self._media_relink_candidate_path = str(media_path)
        self._media_relink_candidate_info = candidate_info
        self._media_relink_assessment = assessment
        self.media_relink_panel.set_candidate(str(media_path), candidate_info, assessment)
        self._refresh_draft_state()
        return assessment

    def _apply_media_relink(self) -> bool:
        task = self.current_task
        path = self._media_relink_candidate_path
        info = self._media_relink_candidate_info
        assessment = self._media_relink_assessment
        if self._media_relink_task is not task or path is None or info is None or assessment is None:
            return False
        if not assessment.can_apply:
            return False
        preserved_drafts = {"Media replacement"}
        if not assessment.clear_results:
            preserved_drafts.add("Physics fit")
        replaced_drafts = tuple(
            name for name in self._unapplied_draft_names() if name not in preserved_drafts
        )
        if not self._confirm_draft_replacement("applying media replacement", replaced_drafts):
            return False

        self._stop_playback()
        self._invalidate_review_responses(task)
        self._clear_analysis_result("Media source changed. Run processing again.", state="dirty")
        self.project_controller.relink_media(
            task,
            path,
            info,
            clear_results=assessment.clear_results,
        )
        if assessment.clear_results:
            self._reset_physics_context()
        current_item = self.task_list.currentItem()
        if current_item is not None and current_item.data(Qt.ItemDataRole.UserRole) is not None:
            current_item.setText(task.title())
            current_item.setToolTip(task.media_path or "")

        results_preserved = not assessment.clear_results
        self._cancel_media_relink(render=False)
        self.preview_label.cancel_selection()
        self._render_task(refresh_project_state=False)
        self._mark_project_changed()
        self.media_relink_panel.show_applied(info, results_preserved=results_preserved)
        if results_preserved:
            message = f"Relinked {Path(path).name}; results preserved. Save Project to persist the new path."
        else:
            message = f"Relinked {Path(path).name}; results cleared. Save Project to persist the new path."
        self.statusBar().showMessage(message, 8000)
        return True

    def _cancel_media_relink(self, *, render: bool = True) -> None:
        pending_task = self._media_relink_task
        if pending_task is not None:
            pending_task.pending_media_relink = None
        self._media_relink_task = None
        self._media_relink_candidate_path = None
        self._media_relink_candidate_info = None
        self._media_relink_assessment = None
        if render:
            self._render_media_relink(self.current_task)
        self._refresh_draft_state()

    def _render_media_relink(self, task: DesktopTask) -> None:
        visible = task.media_path is not None
        self.media_relink_section_label.setVisible(visible)
        self.media_relink_panel.setVisible(visible)
        if not visible:
            self.media_relink_panel.set_current(None, None)
            return
        if self._media_relink_task is not task and task.pending_media_relink is not None:
            candidate_info, assessment = task.pending_media_relink
            self._media_relink_task = task
            self._media_relink_candidate_path = task.media_path
            self._media_relink_candidate_info = candidate_info
            self._media_relink_assessment = assessment
        if (
            self._media_relink_task is task
            and self._media_relink_candidate_path is not None
            and self._media_relink_candidate_info is not None
            and self._media_relink_assessment is not None
        ):
            self.media_relink_panel.set_candidate(
                self._media_relink_candidate_path,
                self._media_relink_candidate_info,
                self._media_relink_assessment,
            )
            return
        if task.media_identity_requires_review:
            self.media_relink_panel.show_source_review_required(task.media_info)
            return
        self.media_relink_panel.set_current(task.media_path, task.media_info)

    def _open_project(self) -> None:
        if self._project_open_thread is not None:
            self._cancel_project_open()
            return
        active_kinds = set(self._background_tasks.active_kinds)
        if active_kinds.intersection(_PROJECT_OPEN_BLOCKING_KINDS):
            self.statusBar().showMessage(
                "Wait for background processing or project saving to finish before opening another project.",
                6000,
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open project",
            "",
            "Neo-Tracker projects (*.ntproj *.json);;All files (*)",
        )
        if not path:
            return
        if not self._confirm_project_transition("opening another project"):
            self.statusBar().showMessage("Open canceled. Current project and editor work are still available.", 6000)
            return
        if "review-response" in active_kinds:
            self._cancel_review_response_requests(clear_pending=True)
        if not self._start_project_open(path):
            QMessageBox.warning(
                self,
                "Open project",
                "Could not start project loading.",
            )

    def _save_project(self) -> bool:
        active_kinds = set(self._background_tasks.active_kinds)
        if active_kinds.intersection({"tracking", "media-probe", "project-open", "project-save"}):
            self.statusBar().showMessage(
                "Finish tracking, media import, or the current save before saving the project.",
                6000,
            )
            return False
        path = self.project_path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Save project",
                "",
                "Neo-Tracker projects (*.ntproj);;JSON files (*.json);;All files (*)",
            )
            if not selected:
                return False
            path = Path(selected)
        try:
            # Snapshot list membership and all mutable task metadata on the GUI
            # thread. TrackerResult objects are copy-on-write while this worker is
            # active, so the large result payload can be shared without a costly
            # GUI-thread deep copy.
            project = self._project_from_window(path)
        except Exception as exc:
            QMessageBox.warning(self, "Save project", f"Could not save project:\n{exc}")
            return False
        accepted = self._project_io_coordinator.start_save(
            ProjectSaveRequest(
                project=project,
                path=path,
                content_revision=self._project_content_revision,
                project_saver=self.project_saver,
            )
        )
        if not accepted:
            return False
        self._set_project_save_busy(True)
        return True

    def _set_project_save_busy(self, busy: bool) -> None:
        self._set_action_enabled("project.save", not busy)
        self._set_action_enabled("project.open", not busy)
        if busy:
            self._update_action(
                "project.save",
                text="Saving…",
                tool_tip="Saving a stable project snapshot in the background.",
            )
            self.save_project_button.setAccessibleDescription(
                "A stable project snapshot is being saved. You can continue editing."
            )
            self.statusBar().showMessage("Saving project… You can continue editing.")
        else:
            self._apply_project_state(self._project_dirty)

    def _project_io_save_completed(
        self,
        job: ProjectSaveJob,
        payload: CompletedProjectSave,
    ) -> None:
        if self._background_tasks.closing:
            return
        self.project_path = payload.path
        if self._project_content_revision == job.content_revision:
            self._set_project_clean_fingerprint(payload.fingerprint)
        else:
            # The on-disk snapshot is now the saved baseline, but newer UI edits
            # must remain visibly dirty. Avoid re-fingerprinting the large live
            # project on the GUI thread.
            self._saved_project_fingerprint = payload.fingerprint
            self._current_project_fingerprint = None
            self._apply_project_state(True)
        self._set_project_save_busy(False)
        if self._project_dirty:
            self.statusBar().showMessage(
                f"Saved snapshot: {job.path}. Newer edits still need saving.",
                8000,
            )
        else:
            self.statusBar().showMessage(f"Saved project: {job.path}", 6000)

    def _project_io_save_failed(self, _job: ProjectSaveJob, message: str) -> None:
        if self._background_tasks.closing:
            return
        self._set_project_save_busy(False)
        QMessageBox.warning(
            self,
            "Save project",
            f"Could not save project:\n{message}",
        )

    def _project_transition_dialog(self, action: str) -> QMessageBox:
        project_name = self.project_path.name if self.project_path is not None else "this project"
        return build_unsaved_changes_dialog(
            self,
            project_name=project_name,
            action=action,
        )

    def _ask_unsaved_changes(self, action: str) -> QMessageBox.StandardButton:
        dialog = self._project_transition_dialog(action)
        return QMessageBox.StandardButton(dialog.exec())

    def _unapplied_drafts_dialog(self, action: str, draft_names: tuple[str, ...]) -> QMessageBox:
        return build_unapplied_drafts_dialog(
            self,
            action=action,
            draft_names=draft_names,
        )

    def _ask_unapplied_drafts(self, action: str, draft_names: tuple[str, ...]) -> bool:
        dialog = self._unapplied_drafts_dialog(action, draft_names)
        dialog.exec()
        clicked = dialog.clickedButton()
        return clicked is not None and clicked.objectName() == "discardDraftsButton"

    def _result_replacement_dialog(self, task: DesktopTask) -> QMessageBox:
        return build_result_replacement_dialog(
            self,
            task_name=task.title(),
            result_count=len(task.pipeline.results),
            edit_count=len(task.edit_history),
        )

    def _ask_result_replacement(self, task: DesktopTask) -> bool:
        dialog = self._result_replacement_dialog(task)
        dialog.exec()
        clicked = dialog.clickedButton()
        return clicked is not None and clicked.objectName() == "confirmResultReplacementButton"

    def _rerun_replacement_dialog(
        self,
        task: DesktopTask,
        *,
        anchor_frame: int,
        start_frame: int,
        result_count: int,
    ) -> QMessageBox:
        return build_rerun_replacement_dialog(
            self,
            task_name=task.title(),
            anchor_frame=anchor_frame,
            start_frame=start_frame,
            result_count=result_count,
            affected_edit_count=self.review_controller.active_manual_edit_count_from_frame(
                task.edit_history,
                start_frame,
            ),
        )

    def _ask_rerun_replacement(
        self,
        task: DesktopTask,
        *,
        anchor_frame: int,
        start_frame: int,
        result_count: int,
    ) -> bool:
        dialog = self._rerun_replacement_dialog(
            task,
            anchor_frame=anchor_frame,
            start_frame=start_frame,
            result_count=result_count,
        )
        dialog.exec()
        clicked = dialog.clickedButton()
        return clicked is not None and clicked.objectName() == "confirmRerunReplacementButton"

    def _unapplied_draft_names(self) -> tuple[str, ...]:
        names: list[str] = []
        if self.roi_geometry_editor.is_dirty():
            names.append("ROI geometry")
        if self.calibration_editor.is_dirty():
            names.append("Calibration")
        if self._advanced_config_dirty:
            names.append("Pipeline JSON")
        if self.fit_panel.is_dirty():
            names.append("Physics fit")
        if self._media_relink_task is self.current_task and self._media_relink_candidate_path is not None:
            names.append("Media replacement")

        selection_name = self._active_preview_selection_draft_name()
        if selection_name is not None and selection_name not in names:
            names.append(selection_name)
        return tuple(names)

    def _refresh_draft_state(self) -> tuple[str, ...]:
        draft_names = self._unapplied_draft_names()
        if len(draft_names) == 1:
            self.global_draft_label.setText(f"Draft: {draft_names[0]}")
        else:
            self.global_draft_label.setText(f"Drafts: {len(draft_names)}")
        detail = (
            "Unapplied editor work: "
            + ", ".join(draft_names)
            + ". Finish, apply, or cancel it before opening another project or closing Neo-Tracker."
            if draft_names
            else "No unapplied editor work."
        )
        self.global_draft_label.setToolTip(detail)
        self.global_draft_label.setAccessibleDescription(detail)
        self.global_draft_label.setVisible(bool(draft_names))
        return draft_names

    def _block_tracking_for_unapplied_drafts(self, action: str) -> bool:
        draft_names = self._refresh_draft_state()
        if not draft_names:
            return False
        self.statusBar().showMessage(
            f"{action} not started. Finish, apply, or cancel drafts first: "
            + ", ".join(draft_names)
            + ".",
            8000,
        )
        return True

    def _confirm_editor_context_transition(self, action: str) -> bool:
        draft_names = self._refresh_draft_state()
        if not draft_names:
            return True
        if not self._ask_unapplied_drafts(action, draft_names):
            return False
        self._discard_unapplied_drafts()
        return True

    def _discard_unapplied_drafts(self, *, show_status: bool = True) -> None:
        self.preview_label.cancel_selection()
        self._cancel_media_relink(render=False)
        self._render_calibration(self.current_task)
        self.preview_label.set_calibration_line(self._calibration_line_for_task(self.current_task))
        self._sync_advanced_config_view()
        self.fit_panel.revert_draft()
        self._render_media_relink(self.current_task)
        self._sync_roi_node_editing()
        self._refresh_draft_state()
        if show_status:
            self.statusBar().showMessage("Unapplied editor work discarded.", 4000)

    def _confirm_project_transition(self, action: str) -> bool:
        self._refresh_project_state()
        draft_names = self._refresh_draft_state()
        if draft_names and not self._ask_unapplied_drafts(action, draft_names):
            return False

        if self._project_dirty:
            decision = self._ask_unsaved_changes(action)
            if decision == QMessageBox.StandardButton.Save:
                if not self._save_project():
                    return False
                if self._project_save_thread is not None:
                    self.statusBar().showMessage(
                        "Project save started. Retry the transition after saving finishes.",
                        6000,
                    )
                    return False
            elif decision != QMessageBox.StandardButton.Discard:
                return False

        return True

    def _project_from_window(self, path: Path | None = None) -> NeoTrackerProject:
        if self.tasks:
            source_tasks = list(self.tasks)
        elif self._explicit_empty_project:
            source_tasks = []
        else:
            source_tasks = [self.current_task]
        snapshots = [self._snapshot_from_task(task) for task in source_tasks]
        name = path.stem if path else (self.project_path.stem if self.project_path else "Untitled experiment")
        return NeoTrackerProject(
            name=name,
            pipelines=[
                dict(entry) if isinstance(entry, dict) else entry
                for entry in self._project_pipeline_library
            ],
            tasks=snapshots,
            notes=self._project_notes,
        )

    def _snapshot_from_task(self, task: DesktopTask) -> ProjectTaskSnapshot:
        return self.project_controller.snapshot_from_task(task)

    @staticmethod
    def _media_info_to_dict(info: MediaInfo | None) -> dict[str, object] | None:
        return ProjectTaskController.media_info_to_dict(info)

    @classmethod
    def _media_info_from_snapshot(
        cls,
        snapshot_info: dict[str, object] | None,
        live_info: MediaInfo | None,
    ) -> MediaInfo | None:
        return ProjectTaskController.media_info_from_snapshot(snapshot_info, live_info)

    @staticmethod
    def _calibration_rod_to_dict(rod: CalibrationRod | None) -> dict[str, object] | None:
        return ProjectTaskController.calibration_rod_to_dict(rod)

    def _load_project(self, path: Path) -> None:
        project = NeoTrackerProject.load(path)
        self._apply_loaded_project(project, path)

    def _apply_loaded_project(
        self,
        project: NeoTrackerProject,
        path: Path,
        *,
        media_info_by_path: dict[str, MediaInfo] | None = None,
        prepared_tasks: tuple[DesktopTask, ...] | None = None,
        clean_fingerprint: str | None = None,
        defer_heavy_views: bool = False,
        prepared_diagnostics: PreparedReviewDiagnostics | None = None,
        prepared_analysis_sources: tuple[AnalysisSource, ...] = (),
    ) -> None:
        previous_analysis_source = self._current_analysis_source()
        self._apply_project(
            project,
            set_clean=False,
            media_info_by_path=media_info_by_path,
            prepared_tasks=prepared_tasks,
            defer_heavy_views=defer_heavy_views,
        )
        self.project_path = path
        if clean_fingerprint is None:
            self._set_project_clean()
        else:
            self._set_project_clean_fingerprint(clean_fingerprint)
        if defer_heavy_views:
            self._schedule_project_open_heavy_views(
                previous_analysis_source,
                prepared_diagnostics=prepared_diagnostics,
                prepared_analysis_sources=prepared_analysis_sources,
            )
        self.statusBar().showMessage(f"Opened project: {path}", 6000)

    def _apply_project(
        self,
        project: NeoTrackerProject,
        *,
        set_clean: bool = True,
        media_info_by_path: dict[str, MediaInfo] | None = None,
        prepared_tasks: tuple[DesktopTask, ...] | None = None,
        defer_heavy_views: bool = False,
    ) -> None:
        self._invalidate_project_open_deferred_views()
        self._review_undo = None
        snapshots = project.tasks or [
            ProjectTaskSnapshot(media_path=media_path, pipeline_key=self.default_pipeline_key)
            for media_path in project.media_paths
        ]
        loaded_tasks: list[DesktopTask] = list(prepared_tasks or ())
        if prepared_tasks is None:
            try:
                for snapshot in snapshots:
                    live_media_info = None
                    if media_info_by_path is not None and snapshot.media_path:
                        media_path = str(snapshot.media_path)
                        if media_path not in media_info_by_path:
                            raise ValueError(f"Missing inspected media result for {media_path}")
                        live_media_info = media_info_by_path[media_path]
                    loaded_tasks.append(
                        self._task_from_snapshot(
                            snapshot,
                            live_media_info=live_media_info,
                        )
                    )
            except Exception:
                for task in loaded_tasks:
                    task.close_reader()
                raise

        self._discard_removed_task_undo()
        self._discard_unapplied_drafts(show_status=False)
        self._invalidate_review_responses()
        self._stop_playback()
        retired_tasks = list(self.tasks)
        self.scratch_task.close_reader()
        for task in retired_tasks:
            task.close_reader()
        self.task_list.blockSignals(True)
        try:
            self.task_list.clear()
            self.tasks = loaded_tasks
            self._retire_replaced_project_tasks(retired_tasks)
            self._project_pipeline_library = [
                dict(entry) if isinstance(entry, dict) else entry
                for entry in project.pipelines
            ]
            self._project_notes = str(project.notes)
            self._explicit_empty_project = not bool(self.tasks)
            for index, task in enumerate(self.tasks):
                item = QListWidgetItem(task.title())
                item.setData(Qt.ItemDataRole.UserRole, index)
                if task.media_path:
                    item.setToolTip(task.media_path)
                self.task_list.addItem(item)
        finally:
            self.task_list.blockSignals(False)
        self._project_open_apply_defer_heavy_views = bool(defer_heavy_views)
        try:
            if self.tasks:
                self.task_list.setCurrentRow(0)
            else:
                self._show_empty_task_list_placeholder()
                self.scratch_task = self._new_task(None, self.default_pipeline_key)
                self.current_task = self.scratch_task
                self._reset_physics_context()
                self._render_task(refresh_project_state=False)
        finally:
            self._project_open_apply_defer_heavy_views = False
        if set_clean:
            self._set_project_clean()

    def _invalidate_project_open_deferred_views(self) -> None:
        self._project_open_deferred_generation += 1
        self._project_open_diagnostics_timer.stop()
        self._project_open_analysis_timer.stop()
        self._project_open_deferred_task = None
        self._project_open_deferred_results = None
        self._project_open_deferred_state_units = {}
        self._project_open_deferred_selected_frame = None
        self._project_open_deferred_analysis_source = None
        self._project_open_prepared_diagnostics = None
        self._project_open_prepared_analysis_sources = ()
        self._project_open_diagnostics_pending = False
        self._project_open_analysis_pending = False

    def _retire_replaced_project_tasks(self, tasks: list[DesktopTask]) -> None:
        """Release large result graphs one task at a time between GUI commits."""

        retired = [
            task
            for task in tasks
            if not any(task is current for current in self.tasks)
        ]
        if not retired:
            return
        if sum(len(task.pipeline.results) for task in retired) < _PROJECT_OPEN_DEFERRED_RESULTS_THRESHOLD:
            retired.clear()
            return
        self._retired_project_tasks.extend(retired)
        if not self._retired_project_tasks_timer.isActive():
            self._retired_project_tasks_timer.start(120)

    def _release_retired_project_task(self) -> None:
        if not self._retired_project_tasks:
            return
        task = self._retired_project_tasks.pop(0)
        task.close_reader()
        del task
        if self._retired_project_tasks:
            self._retired_project_tasks_timer.start(60)

    def _schedule_project_open_heavy_views(
        self,
        previous_analysis_source: AnalysisSource,
        *,
        prepared_diagnostics: PreparedReviewDiagnostics | None,
        prepared_analysis_sources: tuple[AnalysisSource, ...],
    ) -> None:
        task = self.current_task
        results = task.pipeline.results
        if len(results) < _PROJECT_OPEN_DEFERRED_RESULTS_THRESHOLD:
            return
        selected_index = self._selected_result_index()
        selected_frame = (
            results[selected_index].frame_index
            if selected_index is not None and 0 <= selected_index < len(results)
            else None
        )
        self._project_open_deferred_task = task
        self._project_open_deferred_results = results
        self._project_open_deferred_state_units = dict(task.pipeline.state_model.units())
        self._project_open_deferred_selected_frame = selected_frame
        self._project_open_deferred_analysis_source = previous_analysis_source
        self._project_open_prepared_diagnostics = prepared_diagnostics
        self._project_open_prepared_analysis_sources = prepared_analysis_sources
        self._project_open_diagnostics_pending = True
        self._project_open_analysis_pending = True
        # Keep the commit-to-window step short, then separate the two O(results)
        # views so neither operation combines into one long GUI heartbeat gap.
        self._project_open_diagnostics_timer.start(40)
        self._project_open_analysis_timer.start(170)

    def _project_open_deferred_context_is_current(self) -> bool:
        task = self._project_open_deferred_task
        results = self._project_open_deferred_results
        return bool(
            task is not None
            and results is not None
            and task is self.current_task
            and task.pipeline.results is results
            and not self._background_tasks.closing
        )

    def _clear_project_open_deferred_context_if_done(self) -> None:
        if self._project_open_diagnostics_pending or self._project_open_analysis_pending:
            return
        self._project_open_deferred_task = None
        self._project_open_deferred_results = None
        self._project_open_deferred_state_units = {}
        self._project_open_deferred_selected_frame = None
        self._project_open_deferred_analysis_source = None
        self._project_open_prepared_diagnostics = None
        self._project_open_prepared_analysis_sources = ()

    def _hydrate_project_open_diagnostics(self) -> None:
        if not self._project_open_diagnostics_pending:
            return
        self._project_open_diagnostics_timer.stop()
        self._project_open_diagnostics_pending = False
        if self._project_open_deferred_context_is_current():
            results = self._project_open_deferred_results or []
            selected_index = self._selected_result_index()
            selected_frame = (
                results[selected_index].frame_index
                if selected_index is not None and 0 <= selected_index < len(results)
                else self._project_open_deferred_selected_frame
            )
            prepared = self._project_open_prepared_diagnostics
            prepared_matches = bool(
                prepared is not None
                and len(prepared.results) == len(results)
                and (
                    not results
                    or (
                        prepared.results[0] is results[0]
                        and prepared.results[-1] is results[-1]
                    )
                )
            )
            if prepared_matches and prepared is not None:
                self.review_diagnostics_panel.set_prepared_results(
                    prepared,
                    self._project_open_deferred_state_units,
                    selected_frame,
                )
            else:
                self.review_diagnostics_panel.set_results(
                    results,
                    self._project_open_deferred_state_units,
                    selected_frame,
                )
        self._clear_project_open_deferred_context_if_done()

    def _hydrate_project_open_analysis_sources(self) -> None:
        if not self._project_open_analysis_pending:
            return
        self._project_open_analysis_timer.stop()
        self._project_open_analysis_pending = False
        if self._project_open_deferred_context_is_current():
            self._refresh_analysis_sources(
                previous_source=self._project_open_deferred_analysis_source,
                prepared_sources=self._project_open_prepared_analysis_sources,
            )
        self._clear_project_open_deferred_context_if_done()

    def _task_from_snapshot(
        self,
        snapshot: ProjectTaskSnapshot,
        *,
        live_media_info: MediaInfo | None = None,
    ) -> DesktopTask:
        return self.project_controller.task_from_snapshot(
            snapshot,
            live_media_info=live_media_info,
        )

    @staticmethod
    def _apply_roi_config_to_task(task: DesktopTask, roi: dict[str, object]) -> bool:
        return ProjectTaskController.apply_roi_config_to_task(task, roi)

    @staticmethod
    def _calibration_rod_from_dict(data: dict[str, object] | None) -> CalibrationRod | None:
        return ProjectTaskController.calibration_rod_from_dict(data)

    @staticmethod
    def _apply_calibration_rod_to_task(task: DesktopTask, rod: CalibrationRod) -> bool:
        return ProjectTaskController.apply_calibration_rod_to_task(task, rod)

    @staticmethod
    def _state_model_with_calibration_unit(state_model: object, coordinate_model: object, unit: str) -> object:
        return ProjectTaskController.state_model_with_calibration_unit(state_model, coordinate_model, unit)

    @staticmethod
    def _state_model_with_pixel_unit(state_model: object) -> object:
        return ProjectTaskController.state_model_with_pixel_unit(state_model)

    @staticmethod
    def _single_state_key(state_model: object) -> str | None:
        return ProjectTaskController.single_state_key(state_model)

    @staticmethod
    def _state_length_unit_per_pixel(coordinate_model: object, state_model: object) -> float | None:
        return ProjectTaskController.state_length_unit_per_pixel(coordinate_model, state_model)

    @staticmethod
    def _retarget_keyed_model(model: object, old_key: str, new_key: str) -> object:
        return ProjectTaskController.retarget_keyed_model(model, old_key, new_key)

    @staticmethod
    def _scale_motion_model_units(model: object, key: str, scale: float) -> object:
        return ProjectTaskController.scale_motion_model_units(model, key, scale)

    def _render_project_label(self, *, use_cached: bool = False) -> None:
        self._refresh_project_state(use_cached=use_cached)

    def _project_content_fingerprint(self) -> str:
        return project_content_fingerprint(self._project_from_window(self.project_path))

    def _set_project_clean(self) -> None:
        current_fingerprint = self._project_content_fingerprint()
        self._set_project_clean_fingerprint(current_fingerprint)

    def _set_project_clean_fingerprint(self, current_fingerprint: str) -> None:
        self._saved_project_fingerprint = current_fingerprint
        self._current_project_fingerprint = current_fingerprint
        self._apply_project_state(False)

    def _mark_project_changed(self) -> None:
        """Record a known mutation without serializing the project on the GUI thread."""

        self._project_content_revision += 1
        self._current_project_fingerprint = None
        self._apply_project_state(True)

    def _refresh_project_state(self, *, use_cached: bool = False) -> None:
        current_fingerprint = self._current_project_fingerprint if use_cached else None
        if current_fingerprint is None and (not use_cached or self._saved_project_fingerprint is not None):
            previous_fingerprint = self._current_project_fingerprint
            current_fingerprint = self._project_content_fingerprint()
            self._current_project_fingerprint = current_fingerprint
            if previous_fingerprint is not None and current_fingerprint != previous_fingerprint:
                self._project_content_revision += 1
        dirty = bool(
            self._saved_project_fingerprint is not None
            and current_fingerprint != self._saved_project_fingerprint
        )
        self._apply_project_state(dirty)

    def _apply_project_state(self, dirty: bool) -> None:
        previous_dirty = self._project_dirty
        self._project_dirty = bool(dirty)
        self.project_status_panel.set_state(self.project_path, dirty=self._project_dirty)
        self.global_project_dirty_label.setVisible(self._project_dirty)
        self.save_project_button.setProperty("projectDirty", self._project_dirty)
        if self._project_save_thread is not None:
            action_text = "Saving…"
        elif self._project_dirty and self.project_path is not None:
            action_text = "Save Changes"
        elif self._project_dirty:
            action_text = "Save Project…"
        else:
            action_text = "Save Project"
        if self._project_save_thread is not None:
            detail = "Saving a stable project snapshot in the background. You can continue editing."
        else:
            detail = (
                "Save project changes now."
                if self._project_dirty
                else "Save media paths, settings, calibration, and results."
            )
        self._update_action("project.save", text=action_text, tool_tip=detail)
        self.save_project_button.setAccessibleDescription(detail)
        if self._project_dirty != previous_dirty:
            self.save_project_button.style().unpolish(self.save_project_button)
            self.save_project_button.style().polish(self.save_project_button)

    def _show_empty_task_list_placeholder(self) -> None:
        self.task_list.blockSignals(True)
        self.task_list.clear()
        item = QListWidgetItem("No media tasks yet")
        item.setData(Qt.ItemDataRole.UserRole, None)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        self.task_list.addItem(item)
        self.task_list.blockSignals(False)

    def _rebuild_task_list(self, selected_row: int = 0) -> None:
        self.task_list.blockSignals(True)
        try:
            self.task_list.clear()
            for index, task in enumerate(self.tasks):
                item = QListWidgetItem(task.title())
                item.setData(Qt.ItemDataRole.UserRole, index)
                if task.media_path:
                    item.setToolTip(task.media_path)
                self.task_list.addItem(item)
        finally:
            self.task_list.blockSignals(False)

        if self.tasks:
            self.task_list.setCurrentRow(max(0, min(int(selected_row), len(self.tasks) - 1)))
            return
        self._show_empty_task_list_placeholder()
        self.scratch_task.close_reader()
        self.scratch_task = self._new_task(None, self.default_pipeline_key)
        self.current_task = self.scratch_task
        self._reset_physics_context()
        self._render_task(refresh_project_state=False)

    def _discard_removed_task_undo(self) -> None:
        self._last_removed_task = None
        self.task_actions_panel.clear_undo()

    def _remove_current_task(self) -> bool:
        if not self._background_tasks.idle:
            self.statusBar().showMessage("Wait for background processing to finish before removing a task.", 6000)
            return False
        current_item = self.task_list.currentItem()
        task_index = current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        if task_index is None:
            return False
        index = int(task_index)
        if not (0 <= index < len(self.tasks)) or self.tasks[index] is not self.current_task:
            self._render_task(refresh_project_state=False)
            return False

        task = self.current_task
        if not self._confirm_editor_context_transition(f"removing {task.title()}"):
            self.statusBar().showMessage(
                f"Task removal canceled. Drafts for {task.title()} are still available.",
                6000,
            )
            return False
        self._discard_removed_task_undo()
        previous_dirty = self._project_dirty
        previous_saved_fingerprint = self._saved_project_fingerprint
        previous_current_fingerprint = self._current_project_fingerprint
        self._stop_playback()
        self._cancel_media_relink(render=False)
        self._invalidate_review_responses(task)
        self._clear_analysis_result("Task removed. Select another task or add media.", state="empty")
        task.close_reader()
        self.tasks.pop(index)
        self._explicit_empty_project = not bool(self.tasks)
        self._rebuild_task_list(index)
        self._mark_project_changed()
        self._last_removed_task = (
            task,
            index,
            previous_dirty,
            previous_saved_fingerprint,
            previous_current_fingerprint,
            self._project_content_revision,
        )
        self.task_actions_panel.show_removed(task.title())
        self.statusBar().showMessage(
            f"Removed {task.title()} from this project; media remains on disk. Save Project to persist or undo now.",
            10000,
        )
        return True

    def _undo_removed_task(self) -> bool:
        if not self._background_tasks.idle:
            self.statusBar().showMessage("Wait for background processing to finish before restoring a task.", 6000)
            return False
        removed = self._last_removed_task
        if removed is None:
            return False
        (
            task,
            previous_index,
            previous_dirty,
            previous_saved_fingerprint,
            previous_current_fingerprint,
            removal_revision,
        ) = removed
        if any(existing is task for existing in self.tasks):
            self._discard_removed_task_undo()
            return False
        if not self._confirm_editor_context_transition(f"restoring {task.title()}"):
            self.statusBar().showMessage(
                f"Restore canceled. Drafts for {self.current_task.title()} are still available.",
                6000,
            )
            return False

        if not self.tasks:
            self.scratch_task.close_reader()
        index = max(0, min(int(previous_index), len(self.tasks)))
        self.tasks.insert(index, task)
        self._last_removed_task = None
        self._explicit_empty_project = False
        self.task_actions_panel.clear_undo()
        self._rebuild_task_list(index)
        if (
            self._project_content_revision == removal_revision
            and self._saved_project_fingerprint == previous_saved_fingerprint
        ):
            self._project_content_revision += 1
            self._saved_project_fingerprint = previous_saved_fingerprint
            self._current_project_fingerprint = previous_current_fingerprint
            self._apply_project_state(previous_dirty)
        else:
            self._mark_project_changed()
        persistence_detail = (
            "Save Project to persist."
            if self._project_dirty
            else "The saved project content is unchanged."
        )
        self.statusBar().showMessage(
            f"Restored {task.title()} with its results and history. {persistence_detail}",
            8000,
        )
        return True

    def _set_current_task_item_silently(self, item: QListWidgetItem | None) -> None:
        self.task_list.blockSignals(True)
        try:
            if item is None:
                self.task_list.setCurrentRow(-1)
            else:
                self.task_list.setCurrentItem(item)
        finally:
            self.task_list.blockSignals(False)

    def _task_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is previous:
            return
        if _TASK_SWITCH_BLOCKING_KINDS.intersection(self._background_tasks.active_kinds):
            self._set_current_task_item_silently(previous)
            self.statusBar().showMessage(
                "Wait for current task processing to finish before switching tasks.",
                6000,
            )
            return
        previous_title = self.current_task.title()
        draft_names = self._refresh_draft_state()
        if draft_names:
            self._set_current_task_item_silently(previous)
            target_title = current.text() if current is not None else "another task"
            if not self._ask_unapplied_drafts(f"switching to {target_title}", draft_names):
                self.statusBar().showMessage(
                    f"Task switch canceled. Drafts for {previous_title} are still available.",
                    6000,
                )
                return
            self._discard_unapplied_drafts()
            self._set_current_task_item_silently(current)

        self._stop_playback()
        self._cancel_review_response_requests(owner=self.current_task, clear_pending=True)
        self._cancel_media_relink(render=False)
        task_index = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if task_index is None:
            self.current_task = self.scratch_task
        else:
            index = int(task_index)
            self.current_task = self.tasks[index]
        self._review_undo = None
        self._reset_physics_context()
        self._render_task(refresh_project_state=False)
        if draft_names:
            self.statusBar().showMessage(
                f"Switched to {self.current_task.title()}; discarded drafts from {previous_title}.",
                6000,
            )

    def _preset_changed(self, index: int) -> None:
        if index < 0:
            return
        key = str(self.preset_combo.itemData(index))
        previous_key = self.current_task.pipeline_key
        if key == previous_key:
            return
        descriptor = self.registry[key]
        draft_names = self._refresh_draft_state()
        previous_index = self.preset_combo.findData(previous_key)
        with QSignalBlocker(self.preset_combo):
            self.preset_combo.setCurrentIndex(previous_index)
        if draft_names and not self._ask_unapplied_drafts(
            f"changing the preset to {descriptor.title}", draft_names
        ):
            self.statusBar().showMessage("Preset change canceled. Current editor work is still available.", 6000)
            return
        if not self._confirm_config_result_replacement(self.current_task):
            return
        if draft_names:
            self._discard_unapplied_drafts()
        with QSignalBlocker(self.preset_combo):
            self.preset_combo.setCurrentIndex(index)
        calibration_rod = self.current_task.calibration_rod
        self._invalidate_review_responses(self.current_task)
        self.current_task.pipeline_key = key
        self.current_task.pipeline = self.registry[key].factory()
        if calibration_rod is None or not self._apply_calibration_rod_to_task(
            self.current_task,
            calibration_rod,
        ):
            self.current_task.calibration_rod = CalibrationRod()
        self.current_task.mark_results_changed()
        self.current_task.tracking_outcome = ""
        self.current_task.tracking_note = ""
        self.current_task.roi = None
        self.current_task.edit_history.clear()
        self._review_undo = None
        self._sync_review_undo_action()
        self._reset_physics_context()
        self._clear_analysis_result()
        self._render_task(sync_combo=False, refresh_project_state=False)
        self._mark_project_changed()
        if draft_names:
            self.statusBar().showMessage(
                f"Preset changed to {descriptor.title}; previous editor drafts were discarded.", 6000
            )

    def _preview_frame_changed(
        self,
        frame_index: int,
        *,
        sync_review_selection: bool = True,
    ) -> bool:
        target_frame = self._clamped_preview_frame(self.current_task, frame_index)
        if (
            target_frame != int(self.current_task.preview_frame_index)
            and not self._finish_preview_selection_before(f"moving to frame {target_frame}")
        ):
            self._stop_playback()
            self._sync_preview_position_controls(self.current_task)
            self._sync_review_selection_to_frame()
            if self._applying_selection_revision is not None:
                current_frame = int(self.current_task.preview_frame_index)
                source_revision = self.selection_session.state.source_revision
                QTimer.singleShot(
                    0,
                    lambda: self.selection_session.select_frame(
                        current_frame,
                        origin=SelectionOrigin.VIDEO,
                        expected_source_revision=source_revision,
                    ),
                )
            return False
        self.current_task.preview_frame_index = target_frame
        self._sync_preview_position_controls(self.current_task)
        if sync_review_selection:
            self._sync_review_selection_to_frame()
        self._render_preview()
        if (
            self._applying_selection_revision is None
            and self._physics_series_owner_token == id(self.current_task)
        ):
            state = self.selection_session.state
            self.selection_session.select_frame(
                int(self.current_task.preview_frame_index),
                origin=SelectionOrigin.VIDEO,
                expected_source_revision=state.source_revision,
            )
        return True

    def _render_task(self, sync_combo: bool = True, *, refresh_project_state: bool = True) -> None:
        task = self.current_task
        defer_heavy_views = self._project_open_apply_defer_heavy_views
        if not defer_heavy_views and self._project_open_deferred_task is not None:
            self._invalidate_project_open_deferred_views()
        descriptor = self.registry[task.pipeline_key]
        if sync_combo:
            combo_index = self.preset_combo.findData(task.pipeline_key)
            if combo_index >= 0 and combo_index != self.preset_combo.currentIndex():
                self.preset_combo.blockSignals(True)
                self.preset_combo.setCurrentIndex(combo_index)
                self.preset_combo.blockSignals(False)

        self._render_media_info(task)
        self._render_task_actions(task)
        self._render_project_label(use_cached=not refresh_project_state)
        self._render_preset(descriptor, task.pipeline)
        self._render_calibration(task)
        self._render_tracking_status(task)
        self._render_results(task, defer_diagnostics=defer_heavy_views)
        self._render_run_history(task)
        self._render_edit_history(task)
        if defer_heavy_views:
            self.analysis_controller.invalidate_source_cache()
            self.analysis_controller.clear()
            self.analysis_source_combo.blockSignals(True)
            self.analysis_source_combo.clear()
            self.analysis_source_combo.addItem(
                "Preparing project signals…",
                AnalysisSource(kind="none", label="Preparing project signals…").to_data(),
            )
            self.analysis_source_combo.blockSignals(False)
            self.analysis_source_detail_label.setText(
                f"Preparing signal sources for {len(task.pipeline.results):,} tracking results."
            )
            self._set_analysis_export_enabled(False)
            self.analysis_result_view.setPlainText(
                "Project signal sources are being indexed."
            )
            self._set_analysis_status(
                "Preparing",
                "ready",
                "Project signal sources are prepared and will be attached before this tab is shown.",
            )
        else:
            self._refresh_analysis_sources()
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self._render_preview()

    def _render_task_actions(self, task: DesktopTask) -> None:
        can_remove = any(existing is task for existing in self.tasks)
        self.task_actions_panel.setVisible(can_remove or self._last_removed_task is not None)
        self.task_actions_panel.set_current(
            task.title(),
            result_count=len(task.pipeline.results),
            edit_count=len(task.edit_history),
            run_count=len(task.run_history),
            can_remove=can_remove,
        )

    def _sync_advanced_config_view(self) -> None:
        self._syncing_advanced_config = True
        try:
            self.advanced_config_view.setPlainText(json.dumps(self.current_task.pipeline.to_config(), indent=2))
        finally:
            self._syncing_advanced_config = False
        self._advanced_config_dirty = False
        self._set_json_status("Synced", "synced", "Pipeline JSON matches the current task.")
        self._refresh_draft_state()

    def _advanced_config_text_changed(self) -> None:
        if not self._syncing_advanced_config:
            self._advanced_config_dirty = True
            self._set_json_status("Edited", "edited", "Pipeline JSON has unapplied changes.")
            self._refresh_draft_state()

    def _set_json_status(self, text: str, state: str, detail: str = "") -> None:
        self.json_status_label.setText(text)
        self.json_status_label.setProperty("jsonState", state)
        self.json_status_label.setToolTip(detail)
        self.json_status_label.setAccessibleDescription(detail)
        self.json_status_label.style().unpolish(self.json_status_label)
        self.json_status_label.style().polish(self.json_status_label)
        show_error = state == "invalid" and bool(detail)
        self.json_validation_message.setText(detail if show_error else "")
        self.json_validation_message.setToolTip(detail if show_error else "")
        self.json_validation_message.setAccessibleDescription(detail if show_error else "")
        self.json_validation_message.setVisible(show_error)

    def _validate_advanced_config(self) -> bool:
        try:
            config = self._advanced_config_from_text()
            self._strict_pipeline_from_config(config)
        except Exception as exc:
            self._set_json_status("Invalid", "invalid", str(exc))
            return False
        self._set_json_status("JSON valid", "valid", "Pipeline JSON is valid and can be applied.")
        return True

    def _apply_advanced_config(self) -> bool:
        task = self.current_task
        try:
            config = self._advanced_config_from_text()
            pipeline_key, pipeline = self._strict_pipeline_from_config(config)
        except Exception as exc:
            self._set_json_status("Invalid", "invalid", str(exc))
            QMessageBox.warning(self, "Pipeline JSON", f"Could not apply pipeline JSON:\n{exc}")
            return False

        if pipeline_key == task.pipeline_key and config == task.pipeline.to_config():
            self._sync_advanced_config_view()
            self.statusBar().showMessage("Pipeline JSON is unchanged.", 4000)
            return True
        if not self._confirm_draft_replacement(
            "applying Pipeline JSON", ("ROI geometry", "Calibration", "Physics fit")
        ):
            return False
        if not self._confirm_config_result_replacement(task):
            return False
        self._invalidate_review_responses(task)
        previous_coordinate_config = task.pipeline.coordinate_model.to_config()
        preserve_rod = config.get("coordinate_model") == previous_coordinate_config
        preset_roi = self.registry[pipeline_key].factory().roi.to_config()
        task.pipeline_key = pipeline_key
        task.pipeline = pipeline
        task.tracking_outcome = ""
        task.tracking_note = ""
        task.roi = None if pipeline.roi.to_config() == preset_roi else pipeline.roi.to_config()
        if not preserve_rod:
            task.calibration_rod = CalibrationRod()
            self.preview_label.set_calibration_line(None)
        task.pipeline.results.clear()
        task.mark_results_changed()
        task.edit_history.clear()
        self._reset_physics_context()
        self._clear_analysis_result()
        self._render_task(refresh_project_state=False)
        self._mark_project_changed()
        self._set_json_status("JSON applied", "valid", "Pipeline JSON was applied to the current task.")
        self.statusBar().showMessage("Pipeline JSON applied. Run tracking again.", 6000)
        return True

    def _advanced_config_from_text(self) -> dict[str, object]:
        try:
            config = json.loads(self.advanced_config_view.toPlainText())
        except json.JSONDecodeError as exc:
            raise ValueError(f"syntax error at line {exc.lineno}, column {exc.colno}") from exc
        if not isinstance(config, dict):
            raise ValueError("top-level value must be an object")
        return config

    def _strict_pipeline_from_config(self, config: dict[str, object]) -> tuple[str, TrackingPipeline]:
        roi_error = validate_roi_config(config.get("roi"))
        if roi_error is not None:
            raise ValueError(f"$.roi is invalid: {roi_error}")
        pipeline_key = self._pipeline_key_from_config(config)
        pipeline = self.registry[pipeline_key].factory()
        apply_pipeline_config(pipeline, config)
        applied = pipeline.to_config()
        config_diff = first_config_diff(config, applied)
        if config_diff is not None:
            path, expected, actual = config_diff
            raise ValueError(
                f"{path} did not apply exactly: expected {short_config_value(expected)}, "
                f"got {short_config_value(actual)}"
            )
        return pipeline_key, pipeline

    def _pipeline_key_from_config(self, config: dict[str, object]) -> str:
        metadata = config.get("metadata")
        if isinstance(metadata, dict) and metadata.get("preset") is not None:
            pipeline_key = str(metadata["preset"])
            if pipeline_key not in self.registry:
                raise ValueError(f"unknown preset {pipeline_key!r}")
            return pipeline_key
        return self.current_task.pipeline_key

    def _render_media_info(self, task: DesktopTask) -> None:
        backend_text = "OpenCV available" if has_media_backend() else "OpenCV not installed"
        self.backend_label.setText(backend_text)
        self.preview_title_label.setText(task.title())
        if task.media_path is None:
            self._render_media_relink(task)
            self.media_fps_title_label.setText("Source FPS")
            self.media_frames_title_label.setText("Frames")
            self.media_resolution_title_label.setText("Resolution")
            self.media_path_label.setText("No media selected")
            self.media_path_label.setToolTip("")
            self.media_path_label.setAccessibleDescription("No media selected")
            self.media_fps_label.setText("-")
            self.media_frames_label.setText("-")
            self.media_resolution_label.setText("-")
            self.media_duration_label.setText("-")
            self.preview_frame_spin.blockSignals(True)
            self.preview_frame_spin.setRange(0, 0)
            self.preview_frame_spin.setValue(0)
            self.preview_frame_spin.blockSignals(False)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setRange(0, 0)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self._set_playback_enabled(False)
            return

        if task.media_info is None:
            task.media_info = probe_media_for_ui(task.media_path)
        info = task.media_info
        self._render_media_relink(task)
        if info.kind == "audio":
            self.backend_label.setText("WAV audio")
            self.media_fps_title_label.setText("Sample rate")
            self.media_frames_title_label.setText("Samples")
            self.media_resolution_title_label.setText("Channels")
        else:
            self.media_fps_title_label.setText("Source FPS")
            self.media_frames_title_label.setText("Frames")
            self.media_resolution_title_label.setText("Resolution")
        self.media_path_label.setText(self._compact_media_path(task.media_path))
        self.media_path_label.setToolTip(task.media_path)
        self.media_path_label.setAccessibleDescription(task.media_path)
        if info.kind == "audio":
            self.media_fps_label.setText(f"{info.sample_rate_hz:.6g} Hz" if info.sample_rate_hz > 0 else "-")
            self.media_frames_label.setText(str(info.frame_count) if info.frame_count > 0 else "-")
            self.media_resolution_label.setText(str(info.channels) if info.channels > 0 else "-")
        else:
            self.media_fps_label.setText(f"{info.fps:.3f}" if info.fps > 0 else "-")
            self.media_frames_label.setText(str(info.frame_count) if info.frame_count > 0 else "-")
            self.media_resolution_label.setText(f"{info.width} x {info.height}" if info.width > 0 and info.height > 0 else "-")
        self.media_duration_label.setText(f"{info.duration_s:.3f} s" if info.duration_s > 0 else "-")
        if not info.available:
            self.preview_frame_spin.blockSignals(True)
            self.preview_frame_spin.setRange(0, 0)
            self.preview_frame_spin.setValue(0)
            self.preview_frame_spin.blockSignals(False)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setRange(0, 0)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self._set_playback_enabled(False)
            return
        if info.kind == "audio":
            task.preview_frame_index = 0
            self.preview_frame_spin.blockSignals(True)
            self.preview_frame_spin.setRange(0, 0)
            self.preview_frame_spin.setValue(0)
            self.preview_frame_spin.blockSignals(False)
            self.frame_slider.blockSignals(True)
            self.frame_slider.setRange(0, 0)
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
        else:
            task.preview_frame_index = self._clamped_preview_frame(task, task.preview_frame_index)
            self._sync_preview_position_controls(task)
        self._set_playback_enabled(info.kind == "video" and info.frame_count > 0)

    def _render_preset(self, descriptor: PresetDescriptor, pipeline: TrackingPipeline) -> None:
        description = descriptor.description
        default_observation = descriptor.factory().observation_model.to_config().get("type")
        active_observation = pipeline.observation_model.to_config().get("type")
        if active_observation != default_observation:
            observation_label = {
                "template": "Template matching",
                "color_blob": "Color marker",
                "brightness_peak": "Brightness peak",
                "edge_front": "Edge / interface front",
                "annular_radial_front": "Annular radial front",
            }.get(str(active_observation), str(active_observation).replace("_", " ").title())
            description += (
                f"\n\nCustom observation active · {observation_label}. "
                "The preset selector above identifies the base pipeline."
            )
        self.preset_description.setPlainText(description)
        self._render_observation_backend(pipeline)
        self._render_marker_controls(pipeline)
        self.module_summary_list.clear()
        for step in self._pipeline_steps(pipeline):
            item = QListWidgetItem(f"{step.title}: {step.module}")
            item.setToolTip(step.purpose)
            self.module_summary_list.addItem(item)

    def _render_observation_backend(self, pipeline: TrackingPipeline) -> None:
        backend = observation_backend_info(pipeline.observation_model)
        self.tracking_backend_label.setText(backend.label)
        self.tracking_backend_label.setToolTip(backend.detail)
        self.tracking_backend_label.setAccessibleDescription(backend.detail)
        if self.tracking_backend_label.property("backendState") == backend.state:
            return
        self.tracking_backend_label.setProperty("backendState", backend.state)
        self.tracking_backend_label.style().unpolish(self.tracking_backend_label)
        self.tracking_backend_label.style().polish(self.tracking_backend_label)

    def _render_marker_controls(self, pipeline: TrackingPipeline) -> None:
        observation = pipeline.observation_model
        is_color_marker = isinstance(observation, ColorBlobObservation)
        self.marker_controls_widget.setVisible(is_color_marker)
        self.sample_marker_button.setEnabled(is_color_marker)
        self.color_tolerance_spin.setEnabled(is_color_marker)
        self.color_max_candidates_spin.setEnabled(is_color_marker)
        self.color_min_area_spin.setEnabled(is_color_marker)
        self.color_tolerance_spin.blockSignals(True)
        self.color_max_candidates_spin.blockSignals(True)
        self.color_min_area_spin.blockSignals(True)
        if is_color_marker:
            display_tolerance = self._display_color_tolerance(observation.tolerance)
            self.color_tolerance_spin.setValue(display_tolerance)
            self.color_max_candidates_spin.setValue(max(1, int(observation.max_candidates)))
            self.color_min_area_spin.setValue(max(1, int(observation.min_component_area)))
            self.marker_sample_label.setText(self._format_marker_sample(observation.sample_rgb))
            self._set_marker_swatch(observation.sample_rgb)
        else:
            self.color_tolerance_spin.setValue(0.2)
            self.color_max_candidates_spin.setValue(4)
            self.color_min_area_spin.setValue(1)
            self.marker_sample_label.setText("Not used by this preset")
            self._set_marker_swatch(None)
        self.color_tolerance_spin.blockSignals(False)
        self.color_max_candidates_spin.blockSignals(False)
        self.color_min_area_spin.blockSignals(False)

    @staticmethod
    def _display_color_tolerance(tolerance: float) -> float:
        value = float(tolerance)
        if value > 1.0:
            value = value / 255.0
        return max(0.001, min(1.0, value))

    @classmethod
    def _format_marker_sample(cls, sample_rgb: tuple[float, float, float]) -> str:
        r, g, b = cls._rgb8(sample_rgb)
        return f"RGB {r}, {g}, {b}"

    def _set_marker_swatch(self, sample_rgb: tuple[float, float, float] | None) -> None:
        if sample_rgb is None:
            self.marker_swatch_label.setStyleSheet(
                "QLabel#markerSampleSwatch { background: #edf1f6; border: 1px solid #cfd8e3; border-radius: 4px; }"
            )
            return
        r, g, b = self._rgb8(sample_rgb)
        self.marker_swatch_label.setStyleSheet(
            "QLabel#markerSampleSwatch { "
            f"background: rgb({r}, {g}, {b}); "
            "border: 1px solid #7b8794; border-radius: 4px; "
            "}"
        )

    @staticmethod
    def _rgb8(sample_rgb: tuple[float, float, float]) -> tuple[int, int, int]:
        sample = np.asarray(sample_rgb[:3], dtype=float)
        if sample.size != 3:
            sample = np.zeros(3, dtype=float)
        if np.nanmax(sample) <= 1.0:
            sample = sample * 255.0
        sample = np.nan_to_num(sample, nan=0.0, posinf=255.0, neginf=0.0)
        return tuple(int(round(float(value))) for value in np.clip(sample, 0.0, 255.0))  # type: ignore[return-value]

    def _render_calibration(self, task: DesktopTask) -> None:
        config = self._roi_config_for_task(task)
        roi_type = task.pipeline.roi.to_config().get("type", "unknown")
        self.roi_status_label.setText(
            f"{roi_type} from preset" if task.roi is None else self._format_roi_summary(task.roi)
        )
        self.roi_geometry_editor.set_config(config)
        self._render_curve_half_width(task)
        rod = task.calibration_rod or CalibrationRod()
        calibration_supported = self.project_controller.supports_calibration_rod(
            task.pipeline.coordinate_model
        )
        calibration_tip = (
            "Draw a two-point calibration rod on the video frame."
            if calibration_supported
            else "This pipeline's coordinate model does not accept a two-point length calibration."
        )
        self.calibration_editor.setEnabled(calibration_supported)
        self.calibration_editor.setToolTip("" if calibration_supported else calibration_tip)
        self.calibration_editor.setAccessibleDescription(
            "" if calibration_supported else calibration_tip
        )
        self.mark_calibration_button.setEnabled(calibration_supported)
        self.mark_calibration_button.setToolTip(calibration_tip)
        self.mark_calibration_button.setAccessibleDescription(calibration_tip)
        has_axis = isinstance(
            task.pipeline.coordinate_model,
            (ImageCoordinate, LinearWorldCoordinate),
        )
        self.calibration_editor.set_axis_direction_available(has_axis)
        self.preview_label.set_calibration_axis(has_axis, rod.y_positive)
        self.calibration_editor.set_calibration(self.project_controller.calibration_rod_to_dict(rod))
        self.reset_calibration_button.setEnabled(
            calibration_supported
            and rod.start_px is not None
            and rod.end_px is not None
            and rod.real_length is not None
        )
        scale = rod.unit_per_pixel() if calibration_supported else None
        if scale is not None:
            text = f"1 px = {scale:.6g} {rod.unit}"
            if has_axis:
                y_side = "left of +X" if rod.y_positive == "up" else "right of +X"
                text += f" · +Y {y_side}"
            self.scale_status_label.setText(text)
        else:
            unit = task.pipeline.state_model.units()
            self.scale_status_label.setText(self._format_state_unit_summary(unit))

    def _render_curve_half_width(self, task: DesktopTask) -> None:
        value = 24.0
        config = self._roi_config_for_task(task)
        if isinstance(config, dict) and config.get("type") == "curve_band":
            try:
                value = max(1.0, float(config["half_width"]))
            except Exception:
                value = 24.0
        self.curve_half_width_spin.blockSignals(True)
        self.curve_half_width_spin.setValue(value)
        self.curve_half_width_spin.blockSignals(False)

    @staticmethod
    def _format_roi_summary(roi: dict[str, object]) -> str:
        try:
            roi_type = roi.get("type")
            if roi_type == "rectangle":
                return (
                    f"Rectangle · {float(roi['width']):.1f} × {float(roi['height']):.1f} px · "
                    f"origin ({float(roi['x']):.1f}, {float(roi['y']):.1f}) px"
                )
            if roi_type == "circle":
                center = roi["center"]  # type: ignore[assignment]
                return (
                    f"Circle · {float(roi['radius']):.1f} px radius · "
                    f"center ({float(center[0]):.1f}, {float(center[1]):.1f}) px"  # type: ignore[index]
                )
            if roi_type == "annulus":
                center = roi["center"]  # type: ignore[assignment]
                return (
                    f"Annulus · {float(roi['inner_radius']):.1f}–"
                    f"{float(roi['outer_radius']):.1f} px radii · "
                    f"center ({float(center[0]):.1f}, {float(center[1]):.1f}) px"  # type: ignore[index]
                )
            if roi_type == "polygon":
                points = roi["points"]  # type: ignore[assignment]
                return f"Polygon · {len(points)} nodes"
            if roi_type == "curve_band":
                polyline = roi["polyline"]  # type: ignore[assignment]
                return (
                    f"Curve band · {len(polyline)} nodes · "
                    f"{float(roi['half_width']):.1f} px half-width"
                )
        except Exception:
            return str(roi)
        return str(roi)

    @staticmethod
    def _format_state_unit_summary(units: dict[str, str]) -> str:
        if not units:
            return "Pixel units"
        if "s" in units:
            return f"Path distance · {units['s']}"
        if "theta" in units:
            return f"Angle · {units['theta']}"
        if "area" in units:
            return f"Area · {units['area'].replace('^2', '²')}"
        if "x_px" in units and "y_px" in units:
            if units["x_px"] == units["y_px"]:
                return f"Position · {units['x_px']}"
        if "target_count" in units:
            return f"Targets · {units['target_count']}"
        labels = {
            "x_px": "Horizontal position",
            "y_px": "Vertical position",
            "x_world": "Horizontal position",
            "y_world": "Vertical position",
        }
        return ", ".join(
            f"{labels.get(key, key.replace('_', ' ').title())} · {value.replace('^2', '²')}"
            for key, value in units.items()
        )

    def _render_tracking_status(self, task: DesktopTask) -> None:
        has_results = bool(task.pipeline.results)
        analysis_busy = self._analysis_thread is not None
        status_labels = {
            "partial": "Partial",
            "canceled": "Canceled",
            "failed": "Failed",
        }
        display_outcome = task.tracking_outcome or ("complete" if has_results else "neutral")
        self._set_tracking_status_outcome(display_outcome)
        tracking_note = task.tracking_note.strip()
        self.tracking_status_label.setToolTip(tracking_note)
        self.tracking_summary_label.setToolTip(tracking_note)
        self.tracking_status_label.setAccessibleDescription(tracking_note)
        if task.media_path is None:
            if has_results:
                self.tracking_status_label.setText(status_labels.get(task.tracking_outcome, "Tracked"))
                summary = self._tracking_summary_text(task.pipeline.results)
                if task.tracking_outcome == "partial":
                    summary += " · source ended early"
                self.tracking_summary_label.setText(summary + " · media unavailable")
            else:
                self.tracking_status_label.setText("No media")
                self.tracking_summary_label.setText("Results: none")
            self._set_action_enabled("tracking.run", False)
            self._set_action_enabled("tracking.export_csv", has_results)
            self._set_action_enabled("tracking.export_report", has_results)
            self._set_action_enabled("review.correct", False)
            self._set_action_enabled(
                "review.mark_lost", bool(has_results and not analysis_busy)
            )
            self._sync_review_undo_action()
            self._set_action_enabled("review.rerun", False)
            self._set_action_enabled("review.jump", has_results)
            return
        info = task.media_info or probe_media_for_ui(task.media_path)
        task.media_info = info
        is_audio = info.kind == "audio"
        can_track = bool(info.available and info.kind == "video" and info.frame_count > 0)
        self._set_action_enabled("tracking.run", bool(can_track and not analysis_busy))
        self._set_action_enabled("tracking.export_csv", has_results)
        self._set_action_enabled("tracking.export_report", True)
        self._set_action_enabled(
            "review.correct",
            bool(has_results and self.preview_label.has_frame() and not analysis_busy)
        )
        self._set_action_enabled(
            "review.mark_lost", bool(has_results and not analysis_busy)
        )
        self._sync_review_undo_action()
        self._set_action_enabled(
            "review.rerun", bool(has_results and can_track and not analysis_busy)
        )
        self._set_action_enabled("review.jump", has_results)
        if task.media_identity_requires_review:
            detail = (
                "The file at the saved media path does not match the project snapshot. "
                "Review the staged source in Media before previewing, editing, rerunning, or exporting results."
            )
            self._set_tracking_status_outcome("partial")
            self.tracking_status_label.setText("Source changed")
            self.tracking_status_label.setToolTip(detail)
            self.tracking_status_label.setAccessibleDescription(detail)
            result_text = self._tracking_summary_text(task.pipeline.results)
            self.tracking_summary_label.setText(f"{result_text} · source review required")
            self.tracking_summary_label.setToolTip(detail)
            self._set_action_enabled("tracking.run", False)
            self._set_action_enabled("tracking.export_csv", False)
            self._set_action_enabled("tracking.export_report", False)
            self._set_action_enabled("review.correct", False)
            self._set_action_enabled("review.mark_lost", False)
            self._set_action_enabled("review.undo", False)
            self._set_action_enabled("review.rerun", False)
            self._set_action_enabled("review.jump", False)
            return
        if is_audio:
            if not info.available:
                self.tracking_status_label.setText("Audio unavailable")
                self.tracking_summary_label.setText("Saved audio metadata only; reconnect the WAV file for processing.")
                return
            self.tracking_status_label.setText("Audio ready")
            self.tracking_summary_label.setText("Audio source available")
            return
        if has_results:
            self.tracking_status_label.setText(status_labels.get(task.tracking_outcome, "Tracked"))
            summary = self._tracking_summary_text(task.pipeline.results)
            if task.tracking_outcome == "partial":
                summary += " · source ended early"
            if not can_track:
                summary += " · media unavailable"
        elif task.tracking_outcome in status_labels:
            self.tracking_status_label.setText(status_labels[task.tracking_outcome])
            summary = self._tracking_summary_text(task.pipeline.results)
            if task.tracking_outcome == "partial":
                summary += " · source ended early"
        elif not can_track:
            self.tracking_status_label.setText("Preview unavailable")
            if not info.available and self._media_info_has_saved_metrics(info):
                summary = "Saved video metadata only; reconnect the media file to preview or track."
            else:
                summary = self._tracking_summary_text(task.pipeline.results)
        else:
            self.tracking_status_label.setText("Ready")
            summary = self._tracking_summary_text(task.pipeline.results)
        self.tracking_summary_label.setText(summary)

    def _set_tracking_status_outcome(self, outcome: str) -> None:
        self.tracking_status_label.setProperty("trackingOutcome", str(outcome))
        self.tracking_status_label.style().unpolish(self.tracking_status_label)
        self.tracking_status_label.style().polish(self.tracking_status_label)

    @staticmethod
    def _media_info_has_saved_metrics(info: MediaInfo) -> bool:
        return any(
            value > 0
            for value in (
                info.fps,
                float(info.frame_count),
                float(info.width),
                float(info.height),
                info.duration_s,
                info.sample_rate_hz,
                float(info.channels),
            )
        )

    @staticmethod
    def _compact_media_path(media_path: str, max_chars: int = 58) -> str:
        if len(media_path) <= max_chars:
            return media_path
        path = Path(media_path)
        compact = f"…/{path.parent.name}/{path.name}"
        if len(compact) <= max_chars:
            return compact
        keep = max(12, max_chars - len(path.name) - 3)
        return f"…/{path.parent.name[-keep:]}/{path.name}"

    def _render_results(
        self,
        task: DesktopTask,
        *,
        prefer_preview_frame: bool = False,
        defer_diagnostics: bool = False,
    ) -> None:
        results = task.pipeline.results
        state_units = task.pipeline.state_model.units()
        task_token = id(task)
        previous_selection = (
            self._selected_result_index()
            if (
                not prefer_preview_frame
                and task is self.current_task
                and self._review_render_task_token == task_token
            )
            else None
        )
        preferred_index = self.review_controller.preferred_result_index(
            results,
            previous_selection,
            task.preview_frame_index,
        )
        selection_model = self.results_table.selectionModel()
        selection_model.blockSignals(True)
        self.results_model.set_results(results, state_units, task.tracking_note)
        self._fit_results_table_columns(list(self.results_model.state_headers))
        if preferred_index is not None:
            self.results_table.selectRow(preferred_index)
            self.results_table.scrollTo(
                self.results_model.index(preferred_index, 0),
                QTableView.ScrollHint.EnsureVisible,
            )
            QTimer.singleShot(
                0,
                lambda row=preferred_index: self._scroll_selected_result_row_into_view(row),
            )
        selection_model.blockSignals(False)
        self._review_render_task_token = task_token
        self.review_summary_label.setText(self.results_model.summary)
        self.review_summary_label.setToolTip(self.results_model.summary_tooltip)
        selected_frame = (
            results[preferred_index].frame_index
            if preferred_index is not None and 0 <= preferred_index < len(results)
            else None
        )
        if defer_diagnostics:
            self.review_diagnostics_panel.set_results([], state_units, None)
            detail = f"Indexing diagnostics for {len(results):,} tracking results."
            self.review_diagnostics_panel.status_label.setText("Preparing diagnostics…")
            self.review_diagnostics_panel.status_label.setToolTip(detail)
            self.review_diagnostics_panel.status_label.setAccessibleDescription(detail)
        else:
            self.review_diagnostics_panel.set_results(results, state_units, selected_frame)
        self._render_review_selection(preferred_index)

    def _refresh_edited_result(
        self,
        task: DesktopTask,
        result_index: int,
        previous_result: TrackerResult,
    ) -> None:
        """Refresh one copy-on-write review edit without resetting 100k-row views."""

        index = int(result_index)
        result = task.pipeline.results[index]
        if task is not self.current_task or self._review_render_task_token != id(task):
            self._render_results(task)
            return
        if not self.results_model.refresh_result(index, result):
            self._render_results(task)
            return

        diagnostics_deferred = bool(
            self._project_open_diagnostics_pending
            and self._project_open_deferred_task is task
        )
        if not diagnostics_deferred and not self.review_diagnostics_panel.refresh_result(index, result):
            self.review_diagnostics_panel.set_results(
                task.pipeline.results,
                task.pipeline.state_model.units(),
                result.frame_index,
            )
        self.review_summary_label.setText(self.results_model.summary)
        self.review_summary_label.setToolTip(self.results_model.summary_tooltip)
        self._render_review_selection(index)
        self._render_tracking_summary_after_result_edit(task)

    def _render_tracking_summary_after_result_edit(self, task: DesktopTask) -> None:
        summary = self.results_model.tracking_summary
        if task.tracking_outcome == "partial":
            summary += " · source ended early"
        if task.media_identity_requires_review:
            summary += " · source review required"
        elif task.media_path is None:
            summary += " · media unavailable"
        elif task.media_info is not None and (
            not task.media_info.available or task.media_info.kind != "video"
        ):
            summary += " · media unavailable"
        self.tracking_summary_label.setText(summary)

    def _render_review_selection(self, result_index: int | None = None) -> None:
        if result_index is None:
            result_index = self._selected_result_index()
        selection = self.review_controller.selection(
            self.current_task.pipeline.results,
            result_index,
            self.current_task.pipeline.state_model.units(),
        )
        if selection is None:
            self.review_selection_label.setText("No result selected")
            self.review_selection_detail_label.setText("Select a result row or move to a tracked frame.")
            tone = "empty"
            detail = "No tracking result is selected."
        else:
            self.review_selection_label.setText(selection.text)
            self.review_selection_detail_label.setText(selection.detail)
            tone = selection.tone
            detail = f"{selection.text}\n{selection.detail}"
        for label in (self.review_selection_label, self.review_selection_detail_label):
            label.setToolTip(detail)
            label.setAccessibleDescription(detail)
            if label.property("reviewTone") != tone:
                label.setProperty("reviewTone", tone)
                label.style().unpolish(label)
                label.style().polish(label)
        results = self.current_task.pipeline.results
        selected_frame = (
            results[result_index].frame_index
            if result_index is not None and 0 <= result_index < len(results)
            else None
        )
        self.review_diagnostics_panel.set_selected_frame(selected_frame)

    def _render_edit_history(self, task: DesktopTask) -> None:
        self.review_history_tabs.setTabText(1, f"Edits ({len(task.edit_history)})")
        self.edit_history_panel.set_records(task.edit_history)

    def _render_run_history(self, task: DesktopTask) -> None:
        self.review_history_tabs.setTabText(0, f"Runs ({len(task.run_history)})")
        self.run_history_panel.set_records(task.run_history)

    def _show_run_history_comparison(self, selections_object: object) -> None:
        if not isinstance(selections_object, (list, tuple)):
            return
        selections = tuple(
            selection for selection in selections_object if isinstance(selection, RunHistorySelection)
        )
        if len(selections) != 2:
            return
        dialog = RunHistoryComparisonDialog((selections[0], selections[1]), self)
        dialog.exec()

    def _export_visible_run_history(self, selections_object: object) -> None:
        if not isinstance(selections_object, (list, tuple)):
            return
        selections = [
            selection for selection in selections_object if isinstance(selection, RunHistorySelection)
        ]
        if not selections:
            return
        task = self.current_task
        stem = Path(task.media_path).stem if task.media_path else "tracking"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export tracking run history",
            f"{stem}-run-history.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        write_tracking_run_csv(
            path,
            [selection.record for selection in selections],
            sequence_numbers=[selection.sequence for selection in selections],
        )
        self.statusBar().showMessage(f"Exported {len(selections)} tracking runs: {path}", 6000)

    def _jump_to_edit_frame(self, frame_index: int) -> None:
        frame_index = self._clamped_preview_frame(self.current_task, int(frame_index))
        self._stop_playback()
        if not self._preview_frame_changed(frame_index):
            return
        if self.review_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.review_tab)
        self.statusBar().showMessage(f"Jumped to edit frame {frame_index}.", 4000)

    def _jump_to_diagnostic_frame(self, frame_index: int) -> None:
        frame_index = self._clamped_preview_frame(self.current_task, int(frame_index))
        self._stop_playback()
        if not self._preview_frame_changed(frame_index):
            return
        if self.review_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.review_tab)
        self.statusBar().showMessage(f"Selected diagnostic frame {frame_index}.", 4000)

    def _export_visible_edit_history(self, selections_object: object) -> None:
        if not isinstance(selections_object, (list, tuple)):
            return
        selections = [
            selection for selection in selections_object if isinstance(selection, EditHistorySelection)
        ]
        if not selections:
            return
        task = self.current_task
        stem = Path(task.media_path).stem if task.media_path else "tracking"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export manual edit history",
            f"{stem}-edit-history.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        write_edit_history_csv(
            path,
            [selection.entry for selection in selections],
            sequence_numbers=[selection.sequence for selection in selections],
        )
        self.statusBar().showMessage(f"Exported {len(selections)} manual edits: {path}", 6000)

    def _record_edit_event(
        self,
        task: DesktopTask,
        event_type: str,
        frame_index: int | None = None,
        **details: object,
    ) -> None:
        self.review_controller.append_event(task.edit_history, event_type, frame_index, **details)
        self._render_edit_history(task)
        self.edit_history_panel.show_all()
        self.edit_history_panel.select_sequence(len(task.edit_history))
        self.review_history_tabs.setCurrentIndex(1)
        self._mark_project_changed()

    def _record_review_edit(self, task: DesktopTask, edit: ReviewEdit) -> None:
        self.review_controller.append_edit(task.edit_history, edit)
        self._render_edit_history(task)
        self.edit_history_panel.show_all()
        self.edit_history_panel.select_sequence(len(task.edit_history))
        self.review_history_tabs.setCurrentIndex(1)
        self._mark_project_changed()

    def _render_workflow(self, pipeline: TrackingPipeline) -> None:
        self.workflow_list.clear()
        for index, step in enumerate(self._pipeline_steps(pipeline), start=1):
            item = QListWidgetItem(f"{index}. {step.title} -> {step.module}")
            item.setToolTip(step.purpose)
            self.workflow_list.addItem(item)

    def _response_overlay_changed(self, _state: int) -> None:
        self._response_mode_routing_requested = self.show_response_checkbox.isChecked()
        self._render_preview()

    def _render_preview(self) -> None:
        task = self.current_task
        if self.show_response_checkbox.isChecked():
            self._set_response_status(
                "Response: unavailable",
                "unavailable",
                "Select a tracked video frame to inspect its response heatmap.",
            )
        else:
            self._cancel_review_response_requests(clear_pending=True)
            self._set_response_status(
                "Response overlay: off",
                "off",
                "Turn on Response to inspect retained angular evidence or an observation heatmap.",
            )
        if self.show_response_checkbox.isChecked() and task.pipeline.results:
            result_index = self._current_result_index()
            retained_result = (
                task.pipeline.results[result_index]
                if result_index is not None and 0 <= result_index < len(task.pipeline.results)
                else None
            )
            if retained_result is not None and angular_response(retained_result) is not None:
                # Retained 1D detector evidence does not depend on media availability.
                # Route it before any source-frame decode or relink early return.
                self._tracking_overlay_for_task(task, None)
        if task.media_path is None:
            self._preview_coordinator.invalidate()
            self._cancel_review_response_requests(clear_pending=True)
            self.candidate_summary_label.setText("Candidates: none")
            self.preview_label.clear_message("No media loaded\nAdd a video or WAV file from the Media tab.")
            self._sync_preview_dependent_actions(task)
            return
        info = task.media_info or probe_media_for_ui(task.media_path)
        task.media_info = info
        if not info.available:
            self._preview_coordinator.invalidate()
            self._cancel_review_response_requests(clear_pending=True)
            self.candidate_summary_label.setText("Candidates: unavailable")
            detail = f"{task.title()}\nPreview unavailable\n{info.error}"
            if task.media_identity_requires_review:
                visible_reason = "Media differs from saved project snapshot"
                recovery = "Review the staged source in Media before using preview or results."
            elif "does not exist" in info.error:
                visible_reason = "Media file does not exist"
                recovery = "Use Media > Relink Media to restore this source."
            elif "OpenCV" in info.error:
                visible_reason = "OpenCV media backend unavailable"
                recovery = "Use Media > Relink Media to restore this source."
            else:
                visible_reason = "Media source could not be opened"
                recovery = "Use Media > Relink Media to restore this source."
            self.preview_label.clear_message(
                f"{task.title()}\nPreview unavailable · {visible_reason}\n{recovery}",
                detail,
            )
            self._sync_preview_dependent_actions(task)
            return
        if info.kind == "audio":
            self._preview_coordinator.invalidate()
            self._cancel_review_response_requests(clear_pending=True)
            self.candidate_summary_label.setText("Candidates: not used for audio")
            self.preview_label.clear_message(f"{task.title()}\nAudio file loaded\nReady for signal processing.")
            self._render_tracking_status(task)
            return
        display_frame: np.ndarray | None = None
        if self._has_injected_preview_reader(task):
            # Explicit test/plugin injection keeps its documented in-process
            # semantics. Production preview decoding never takes this branch.
            self._cancel_preview_decode(clear_pending=True)
            try:
                reader = self._reader_for_task(task)
                read_for_display = getattr(reader, "read_frame_for_display", None)
                if callable(read_for_display):
                    display_frame = read_for_display(task.preview_frame_index)
                    frame = display_frame[:, :, ::-1]
                else:
                    frame = reader.read_frame(task.preview_frame_index)
            except Exception as exc:
                self._show_preview_decode_failure(task, int(task.preview_frame_index), str(exc))
                return
        else:
            request = self._preview_decode_request(task, info)
            cached = self._preview_decode_cache
            if cached is None or not same_preview_request(cached.request, request):
                self._queue_preview_decode(task, request)
                self.preview_label.clear_message(
                    f"{task.title()}\nLoading frame {request.frame_index}…",
                    (
                        f"Frame {request.frame_index} is being decoded in an isolated helper process. "
                        "A newer preview request will supersede it."
                    ),
                )
                self._sync_preview_dependent_actions(task)
                return
            display_frame = cached.bgr_frame
            frame = display_frame[:, :, ::-1]
        if display_frame is None:
            self.preview_label.set_frame(frame)
        else:
            self.preview_label.set_bgr_frame(display_frame)
        self.preview_label.set_roi_config(dict(task.roi) if isinstance(task.roi, dict) else None)
        self.preview_label.set_calibration_line(self._calibration_line_for_task(task))
        (
            trajectory_points,
            current_point,
            observation_point,
            response_map,
            candidate_points,
            prediction_point,
            measurement_points,
        ) = self._tracking_overlay_for_task(task, frame)
        self.preview_label.set_tracking_overlay(
            trajectory_points,
            current_point,
            observation_point,
            response_map,
            candidate_points,
            prediction_point,
            measurement_points,
        )
        self._render_candidate_summary(task)
        self._sync_preview_dependent_actions(task)

    @staticmethod
    def _preview_decode_request(task: DesktopTask, info: MediaInfo) -> PreviewDecodeRequest:
        return PreviewDecodeRequest(
            owner_token=id(task),
            media_path=str(task.media_path or ""),
            frame_index=max(0, int(task.preview_frame_index)),
            expected_width=max(0, int(info.width)),
            expected_height=max(0, int(info.height)),
            expected_identity=info.source_identity,
        )

    def _has_injected_preview_reader(self, task: DesktopTask) -> bool:
        """Return true only for the explicit legacy/test reader extension path."""

        return bool("_reader_for_task" in self.__dict__ or task.media_reader is not None)

    def _queue_preview_decode(
        self,
        task: DesktopTask,
        request: PreviewDecodeRequest,
    ) -> None:
        self._preview_coordinator.start(PreviewRequest(task, request))

    def _start_preview_decode(
        self,
        task: DesktopTask,
        request: PreviewDecodeRequest,
    ) -> None:
        self._preview_coordinator.start(PreviewRequest(task, request))

    def _preview_decoder_session_for(
        self,
        request: PreviewDecodeRequest,
    ) -> PreviewDecoderSession:
        return self._preview_coordinator.session_for(request)

    def _discard_preview_decoder_session(self) -> None:
        self._preview_coordinator.discard_session()

    def _cancel_preview_decode(self, *, clear_pending: bool) -> None:
        self._preview_coordinator.cancel(clear_pending=clear_pending)

    def _preview_decode_completed(self, result_object: object) -> None:
        self._preview_coordinator.handle_completed(result_object)

    def _preview_decode_failed(self, request_object: object, message: str) -> None:
        self._preview_coordinator.handle_failed(request_object, message)

    def _preview_decode_canceled(self, request_object: object) -> None:
        self._preview_coordinator.handle_canceled(request_object)

    def _preview_decode_thread_finished(self) -> None:
        if self._preview_coordinator.thread is None:
            self._preview_coordinator.handle_thread_finished()

    def _preview_coordinator_completed(self, task_object: object, result_object: object) -> None:
        if (
            not isinstance(task_object, DesktopTask)
            or not isinstance(result_object, PreviewDecodeResult)
            or not self._preview_decode_request_is_current(task_object, result_object.request)
        ):
            self._preview_coordinator.clear_cache()
            self._preview_coordinator.discard_session()
            return
        self._render_preview()

    def _preview_coordinator_failed(
        self,
        task_object: object,
        request_object: object,
        message: str,
    ) -> None:
        if (
            isinstance(task_object, DesktopTask)
            and isinstance(request_object, PreviewDecodeRequest)
            and self._preview_decode_request_is_current(task_object, request_object)
        ):
            self._show_preview_decode_failure(
                task_object,
                request_object.frame_index,
                message,
            )

    def _preview_coordinator_idle(self) -> None:
        if self._background_tasks.closing:
            self._schedule_close_if_workers_stopped()

    def _preview_decode_request_is_current(
        self,
        task: DesktopTask,
        request: PreviewDecodeRequest,
    ) -> bool:
        if task is not self.current_task or task.media_path is None or task.media_info is None:
            return False
        return same_preview_request(request, self._preview_decode_request(task, task.media_info))

    def _show_preview_decode_failure(
        self,
        task: DesktopTask,
        frame_index: int,
        message: str,
    ) -> None:
        if task is not self.current_task:
            return
        self._cancel_review_response_requests(clear_pending=True)
        detail = f"Could not decode frame {frame_index} from {task.media_path}: {message}"
        self.preview_label.clear_message(
            f"{task.title()}\nFrame {frame_index} could not be decoded\n"
            "Choose another frame or relink the media.",
            detail,
        )
        self._sync_preview_dependent_actions(task)
        if self.play_timer.isActive() or self.playback_clock.active:
            self._stop_playback(failure_detail=detail)

    def _sync_preview_dependent_actions(self, task: DesktopTask) -> None:
        if task is not self.current_task:
            return
        self._set_action_enabled(
            "review.correct",
            bool(task.pipeline.results and self.preview_label.has_frame()),
        )

    def _set_response_status(self, text: str, state: str, detail: str) -> None:
        self.response_status_label.setText(text)
        self.response_status_label.setToolTip(detail)
        self.response_status_label.setAccessibleDescription(detail)
        if self.response_status_label.property("responseState") == state:
            return
        self.response_status_label.setProperty("responseState", state)
        self.response_status_label.style().unpolish(self.response_status_label)
        self.response_status_label.style().polish(self.response_status_label)

    def _render_review_response_status(self, response: ReviewResponse, *, pre_edit_evidence: bool = False) -> None:
        labels = {
            "stored": "Response: stored with tracking",
            "cached": "Response: review cache",
            "recomputed": "Response: recomputed for this frame",
            "unavailable": "Response: unavailable",
        }
        text = labels[response.source]
        detail = response.detail
        if pre_edit_evidence:
            text += " · pre-edit evidence"
            detail += " The selected result was manually edited; this map remains the original detector evidence."
        self._set_response_status(text, response.source, detail)

    def _response_map_for_review(
        self,
        task: DesktopTask,
        pipeline: TrackingPipeline,
        result: TrackerResult,
        frame: np.ndarray,
    ) -> np.ndarray | None:
        available = self._review_responses.lookup(task, result)
        if available is not None:
            self._cancel_review_response_requests(clear_pending=True)
            self._render_review_response_status(
                available,
                pre_edit_evidence=result.status.startswith("manual"),
            )
            return available.response_map

        request = self._review_responses.prepare_request(task, pipeline, result, frame)
        self._queue_review_response(request)
        evidence_suffix = " · pre-edit evidence" if result.status.startswith("manual") else ""
        self._set_response_status(
            f"Response: loading…{evidence_suffix}",
            "loading",
            "Recomputing this frame in the background. The preview remains interactive.",
        )
        return None

    @staticmethod
    def _same_review_response_request(
        first: ReviewResponseRequest,
        second: ReviewResponseRequest,
    ) -> bool:
        return ReviewResponseCoordinator.same_request(first, second)

    def _queue_review_response(self, request: ReviewResponseRequest) -> None:
        self._review_response_coordinator.queue(request)

    def _cancel_review_response_requests(
        self,
        *,
        owner: object | None = None,
        first_frame: int | None = None,
        clear_pending: bool,
    ) -> None:
        self._review_response_coordinator.cancel_requests(
            owner=owner,
            first_frame=first_frame,
            clear_pending=clear_pending,
        )

    def _invalidate_review_responses(
        self,
        owner: object | None = None,
        first_frame: int | None = None,
    ) -> None:
        self._review_response_coordinator.invalidate(owner, first_frame)

    def _review_response_completed(
        self,
        request: ReviewResponseRequest,
        _response: ReviewResponse,
    ) -> None:
        if self._review_response_request_is_current(request):
            self._render_preview()

    def _review_response_failed(
        self,
        request: ReviewResponseRequest,
        message: str,
    ) -> None:
        if self._review_response_request_is_current(request):
            self._set_response_status("Response: unavailable", "unavailable", message)

    def _review_response_idle(self) -> None:
        if self._background_tasks.closing:
            self._schedule_close_if_workers_stopped()

    def _review_response_request_is_valid(self, request: ReviewResponseRequest) -> bool:
        return self._review_response_coordinator.request_is_valid(request)

    def _review_response_request_is_current(self, request: ReviewResponseRequest) -> bool:
        return bool(
            self.show_response_checkbox.isChecked()
            and request.owner is self.current_task
            and self.current_task.preview_frame_index == request.result.frame_index
            and self._review_response_request_is_valid(request)
        )

    def _render_candidate_summary(self, task: DesktopTask) -> None:
        current_frame = int(task.preview_frame_index)
        result = self.review_controller.result_for_frame(task.pipeline.results, current_frame)
        if result is None:
            self.candidate_summary_label.setText("Candidates: no result on this frame")
            return
        candidates = result.debug.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            self.candidate_summary_label.setText("Candidates: none detected")
            return
        selected = next(
            (candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("selected")),
            None,
        )
        score = None
        if isinstance(selected, dict):
            try:
                score = float(selected.get("combined_score", selected.get("score", 0.0)))
            except (TypeError, ValueError):
                score = None
        selected_text = f" · selected score {score:.2f}" if score is not None else ""
        if result.status.startswith("manual"):
            selected_text += " · pre-edit detector evidence"
        self.candidate_summary_label.setText(
            f"Candidates on frame {current_frame}: {len(candidates)}{selected_text}"
        )

    def _reader_for_task(self, task: DesktopTask) -> MediaReader:
        if task.media_path is None:
            raise RuntimeError("No media file is selected.")
        if task.media_reader is None:
            task.media_reader = MediaReader(task.media_path)
            source_identity = task.media_info.source_identity if task.media_info is not None else None
            task.media_info = replace(task.media_reader.info, source_identity=source_identity)
        return task.media_reader

    @staticmethod
    def _tracking_reader_for_path(media_path: str) -> MediaReader:
        """Create a worker-owned reader that is never exposed through DesktopTask."""

        return MediaReader(media_path)

    def _sync_preview_position_controls(self, task: DesktopTask) -> None:
        info = task.media_info
        max_frame = max(0, (info.frame_count - 1) if info and info.available and info.kind == "video" else 0)
        task.preview_frame_index = max(0, min(int(task.preview_frame_index), max_frame))
        self.preview_frame_spin.blockSignals(True)
        self.preview_frame_spin.setRange(0, max_frame)
        self.preview_frame_spin.setValue(task.preview_frame_index)
        self.preview_frame_spin.blockSignals(False)
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max_frame)
        self.frame_slider.setValue(task.preview_frame_index)
        self.frame_slider.blockSignals(False)

    def _clamped_preview_frame(self, task: DesktopTask, frame_index: int) -> int:
        info = task.media_info
        max_frame = max(0, (info.frame_count - 1) if info and info.available else 0)
        return max(0, min(int(frame_index), max_frame))

    def _set_playback_enabled(self, enabled: bool) -> None:
        self._set_action_enabled("playback.toggle", enabled)
        self._set_action_enabled("playback.previous", enabled)
        self._set_action_enabled("playback.next", enabled)
        self.frame_slider.setEnabled(enabled)
        self.preview_frame_spin.setEnabled(enabled)
        if not enabled:
            self._stop_playback()

    def _toggle_playback(self) -> None:
        if self._playback_coordinator.active:
            self._stop_playback()
            return
        task = self.current_task
        if not task.media_info or not task.media_info.available:
            return
        if self._playback_coordinator.closing:
            return
        info = task.media_info
        if not self._finish_preview_selection_before("starting preview playback"):
            return
        if not self._playback_coordinator.start(
            current_frame=task.preview_frame_index,
            frame_count=info.frame_count,
            fps=info.fps,
        ):
            return
        self._update_action(
            "playback.toggle",
            text="Pause",
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause),
        )
        self.play_button.setAccessibleName("Pause video preview")
        self._set_playback_status(
            f"Playing · Source {self._format_source_fps(self._playback_coordinator.fps)} fps",
            state="smooth",
            skipped_total=0,
        )

    def _stop_playback(self, *, reached_end: bool = False, failure_detail: str = "") -> None:
        was_active = self._playback_coordinator.active
        skipped_total = self._playback_coordinator.skipped_total
        self._playback_coordinator.stop()
        self._update_action(
            "playback.toggle",
            text="Play",
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
        )
        self.play_button.setAccessibleName("Play video preview")
        if failure_detail:
            self._set_playback_error(int(self.current_task.preview_frame_index), failure_detail, skipped_total)
            return
        self.playback_status_label.hide()
        if not was_active:
            return
        frame_index = int(self.current_task.preview_frame_index)
        if reached_end:
            message = f"Playback complete at frame {frame_index}."
        else:
            message = f"Playback paused at frame {frame_index}."
        if skipped_total:
            message += (
                f" Skipped {skipped_total} preview frame{'s' if skipped_total != 1 else ''} "
                "to stay aligned with source time; tracking data was unchanged."
            )
        self.statusBar().showMessage(message, 6000)

    def _advance_playback(self) -> None:
        task = self.current_task
        info = task.media_info
        if not info or not info.available:
            self._stop_playback()
            return
        if not self._has_injected_preview_reader(task) and self._preview_coordinator.busy:
            # Do not continuously supersede a bounded decoder request at the
            # source frame rate. Advance again after the isolated frame lands.
            return
        tick = self._playback_coordinator.tick(task.preview_frame_index)
        if tick.frame_index <= int(task.preview_frame_index):
            if tick.reached_end:
                self._stop_playback(reached_end=True)
            return
        if not self._preview_frame_changed(tick.frame_index):
            return
        if not self._playback_coordinator.active:
            return
        self._set_playback_status(
            f"Playing · Source {self._format_source_fps(self._playback_coordinator.fps)} fps"
            + (f" · Preview skips {tick.skipped_total}" if tick.skipped_total else ""),
            state="catchup" if tick.skipped_total else "smooth",
            skipped_total=tick.skipped_total,
        )
        if tick.reached_end:
            self._stop_playback(reached_end=True)

    def _preview_frame_selected_by_user(self, frame_index: int) -> None:
        self._stop_playback()
        self._preview_frame_changed(frame_index)

    def _set_playback_status(self, text: str, *, state: str, skipped_total: int) -> None:
        detail = (
            f"Video preview is playing at the source rate of {self._format_source_fps(self._playback_coordinator.fps)} "
            f"frames per second. {skipped_total} preview display frame"
            f"{'s have' if skipped_total != 1 else ' has'} been skipped to stay aligned with source time. "
            "Tracking results and source data are unchanged."
        )
        self._show_playback_status(text, detail, state)

    def _set_playback_error(self, frame_index: int, failure_detail: str, skipped_total: int) -> None:
        skip_detail = (
            f" {skipped_total} preview display frame{'s were' if skipped_total != 1 else ' was'} "
            "skipped before the failure."
            if skipped_total
            else ""
        )
        detail = (
            f"Preview playback stopped at frame {frame_index} because it could not be decoded. "
            f"{failure_detail}.{skip_detail} Tracking results and source data are unchanged. "
            "Choose another frame or relink the media."
        )
        self._show_playback_status(
            f"Playback stopped · Frame {frame_index} unreadable",
            detail,
            "error",
        )
        self.statusBar().showMessage(
            f"Playback stopped at frame {frame_index}. This frame could not be decoded; "
            "choose another frame or relink media. Tracking data is unchanged.",
            10000,
        )

    def _show_playback_status(self, text: str, detail: str, state: str) -> None:
        self.playback_status_label.setText(text)
        self.playback_status_label.setToolTip(detail)
        self.playback_status_label.setAccessibleDescription(detail)
        if self.playback_status_label.property("playbackState") != state:
            self.playback_status_label.setProperty("playbackState", state)
            self.playback_status_label.style().unpolish(self.playback_status_label)
            self.playback_status_label.style().polish(self.playback_status_label)
        self.playback_status_label.show()

    @staticmethod
    def _format_source_fps(fps: float) -> str:
        value = float(fps)
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _step_preview_frame(self, delta: int) -> None:
        self._stop_playback()
        self._preview_frame_changed(int(self.current_task.preview_frame_index) + int(delta))

    @staticmethod
    def _playback_interval_ms(task: DesktopTask) -> int:
        fps = task.media_info.fps if task.media_info and task.media_info.fps > 0 else 30.0
        return PlaybackCoordinator.timer_interval_ms(fps)

    def _preview_selection_mode_changed(self, mode: object) -> None:
        selection_mode = str(mode) if mode is not None else ""
        self.finish_roi_drawing_button.setEnabled(selection_mode in {"roi_polygon", "roi_curve_band"})
        self.cancel_roi_drawing_button.setEnabled(bool(selection_mode))
        drawing_labels = {
            "roi_rectangle": "Rectangle",
            "roi_circle": "Circle",
            "roi_annulus": "Annulus",
            "roi_polygon": "Polygon",
            "roi_curve_band": "Curve Band",
        }
        self.roi_geometry_editor.set_drawing_active(drawing_labels.get(selection_mode))
        self.calibration_editor.set_drawing_active(selection_mode == "calibration")
        self._sync_roi_node_editing()
        self._refresh_draft_state()

    def _sync_roi_node_editing(self) -> None:
        editing_active = bool(
            self.calibration_tab is not None
            and self.sidebar_tabs.currentWidget() is self.calibration_tab
            and self.preview_label.selection_mode() is None
            and self.sidebar_tabs.isEnabled()
            and self._tracking_thread is None
        )
        config = self.roi_geometry_editor.current_config() if editing_active else None
        if isinstance(config, dict) and config.get("type") in {"polygon", "curve_band"}:
            self.preview_label.set_editable_roi_config(
                config,
                self.roi_geometry_editor.selected_node_index(),
                self._roi_config_for_task(self.current_task),
            )
            return
        self.preview_label.set_editable_roi_config(None)

    def _roi_geometry_draft_changed(self, config: object) -> None:
        self._refresh_draft_state()
        if self.calibration_tab is None or self.sidebar_tabs.currentWidget() is not self.calibration_tab:
            return
        if self.preview_label.selection_mode() is not None:
            return
        self.preview_label.set_editable_roi_config(
            config if isinstance(config, dict) else None,
            self.roi_geometry_editor.selected_node_index(),
            self._roi_config_for_task(self.current_task),
        )

    def _roi_node_selection_changed(self, index: object) -> None:
        if self.calibration_tab is None or self.sidebar_tabs.currentWidget() is not self.calibration_tab:
            return
        try:
            selected = int(index) if index is not None else None
        except (TypeError, ValueError, OverflowError):
            selected = None
        self.preview_label.set_selected_roi_node(selected)

    def _preview_roi_node_selected(self, index: object) -> None:
        try:
            selected = int(index) if index is not None else None
        except (TypeError, ValueError, OverflowError):
            selected = None
        self.roi_geometry_editor.select_node(selected)

    def _preview_roi_node_moved(self, payload: object) -> None:
        try:
            index = int(payload[0])  # type: ignore[index]
            point = payload[1]  # type: ignore[index]
        except (TypeError, ValueError, IndexError, OverflowError):
            return
        self.roi_geometry_editor.move_node(index, point)

    def _finish_roi_drawing_selection(self) -> None:
        if self.preview_label.finish_polygon_roi_selection() or self.preview_label.finish_curve_band_roi_selection():
            self.statusBar().showMessage("ROI updated. Run tracking again.", 6000)
            return
        QMessageBox.information(
            self,
            "ROI selection",
            "Add at least three polygon points or two curve points in the preview before finishing.",
        )

    def _cancel_preview_selection(self) -> None:
        self.preview_label.cancel_selection()
        self.statusBar().showMessage("Preview drawing cancelled.", 3000)

    def _color_sample_selected(self, point: object) -> None:
        observation = self._color_blob_observation()
        if observation is None:
            return
        try:
            point_px = (float(point[0]), float(point[1]))  # type: ignore[index]
        except Exception:
            return
        task = self.current_task
        if self._has_injected_preview_reader(task):
            try:
                frame = self._reader_for_task(task).read_frame(task.preview_frame_index)
            except Exception as exc:
                QMessageBox.warning(self, "Marker color", f"Could not read the current frame:\n{exc}")
                return
        else:
            cached = self._preview_decode_cache
            info = task.media_info
            if (
                cached is None
                or info is None
                or not same_preview_request(cached.request, self._preview_decode_request(task, info))
            ):
                QMessageBox.information(
                    self,
                    "Marker color",
                    "Wait for the current isolated preview frame to finish loading, then sample again.",
                )
                return
            frame = cached.bgr_frame[:, :, ::-1]
        if frame.ndim != 3 or frame.shape[2] < 3:
            QMessageBox.warning(self, "Marker color", "Marker color sampling requires an RGB video frame.")
            return
        height, width = frame.shape[:2]
        x = max(0, min(width - 1, int(round(point_px[0]))))
        y = max(0, min(height - 1, int(round(point_px[1]))))
        rgb = tuple(float(value) for value in frame[y, x, :3])
        if observation.sample_rgb == rgb:
            return
        if not self._confirm_configuration_change(task, "sampling a new marker color"):
            return
        observation.sample_rgb = rgb  # type: ignore[assignment]
        task.pipeline.metadata["marker_sample_rgb"] = [int(value) for value in self._rgb8(rgb)]
        self._render_marker_controls(task.pipeline)
        self._clear_tracking_results("Marker color updated. Run tracking again.")
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self.statusBar().showMessage(
            f"Sampled marker color at frame {task.preview_frame_index}: {self._format_marker_sample(rgb)}.",
            6000,
        )

    def _color_tolerance_changed(self, value: float) -> None:
        observation = self._color_blob_observation()
        if observation is None:
            return
        if float(value) == observation.tolerance:
            self._render_marker_controls(self.current_task.pipeline)
            return
        if not self._confirm_configuration_change(self.current_task, "changing the marker tolerance"):
            self._render_marker_controls(self.current_task.pipeline)
            return
        observation.tolerance = float(value)
        self.current_task.pipeline.metadata["marker_tolerance"] = float(value)
        self._render_marker_controls(self.current_task.pipeline)
        self._clear_tracking_results("Marker tolerance updated. Run tracking again.")
        self._render_workflow(self.current_task.pipeline)
        self._sync_advanced_config_view()
        self.statusBar().showMessage(f"Marker tolerance set to {float(value):.3f}.", 6000)

    def _color_candidate_settings_changed(self, _value: int) -> None:
        observation = self._color_blob_observation()
        if observation is None:
            return
        new_max = int(self.color_max_candidates_spin.value())
        new_min = int(self.color_min_area_spin.value())
        if new_max == observation.max_candidates and new_min == observation.min_component_area:
            return
        if not self._confirm_configuration_change(self.current_task, "changing marker candidate settings"):
            self._render_marker_controls(self.current_task.pipeline)
            return
        observation.max_candidates = new_max
        observation.min_component_area = new_min
        self.current_task.pipeline.metadata["marker_max_candidates"] = observation.max_candidates
        self.current_task.pipeline.metadata["marker_min_component_area"] = observation.min_component_area
        self._clear_tracking_results("Marker candidate settings updated. Run tracking again.")
        self._render_workflow(self.current_task.pipeline)
        self._sync_advanced_config_view()
        self.statusBar().showMessage(
            f"Marker detector keeps up to {observation.max_candidates} regions "
            f"with area ≥ {observation.min_component_area} px.",
            6000,
        )

    def _color_blob_observation(self) -> ColorBlobObservation | None:
        observation = self.current_task.pipeline.observation_model
        return observation if isinstance(observation, ColorBlobObservation) else None

    def _reset_roi_to_preset(self) -> None:
        task = self.current_task
        if task.roi is None:
            return
        if not self._confirm_configuration_change(
            task, "resetting the ROI to its preset", ("Pipeline JSON", "ROI geometry", "Calibration")
        ):
            return
        preset_pipeline = self.registry[task.pipeline_key].factory()
        task.roi = None
        task.pipeline.roi = preset_pipeline.roi
        coordinate_model = preset_pipeline.coordinate_model
        if isinstance(coordinate_model, (AnnularCoordinate, PathCoordinate)):
            rod = task.calibration_rod
            scale = rod.unit_per_pixel() if rod is not None else None
            if scale is not None:
                coordinate_model = replace(
                    coordinate_model,
                    unit_per_pixel=scale,
                    unit=rod.unit,
                )
        if isinstance(coordinate_model, (AnnularCoordinate, PathCoordinate, PolarCoordinate)):
            task.pipeline.coordinate_model = coordinate_model
        self.preview_label.set_roi_config(None)
        self._clear_tracking_results("ROI reset to preset. Run tracking again.")
        self._render_calibration(task)
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self._render_preview()
        self.statusBar().showMessage("ROI reset to the selected preset defaults.", 6000)

    def _roi_selected(self, rect: object) -> None:
        task = self.current_task
        roi_config = self._normalize_roi_selection(rect)
        if roi_config is None:
            return
        self._commit_roi_config(
            task,
            roi_config,
            clear_message="ROI updated. Run tracking again.",
            status_message="ROI drawing applied. Run tracking again.",
            replaced_drafts=("Pipeline JSON", "ROI geometry", "Calibration"),
        )

    def _roi_geometry_applied(self, config: object) -> None:
        if not isinstance(config, dict):
            self.roi_geometry_editor.show_error("ROI geometry must be an object.")
            return
        error = validate_roi_config(config)
        if error is not None:
            self.roi_geometry_editor.show_error(error)
            return
        result = self._commit_roi_config(
            self.current_task,
            config,
            clear_message="ROI geometry updated. Run tracking again.",
            status_message="ROI geometry applied. Run tracking again.",
        )
        if result is True:
            self.roi_geometry_editor.show_applied()
        elif result is False:
            self.roi_geometry_editor.show_error("The ROI geometry could not be applied.")

    def _commit_roi_config(
        self,
        task: DesktopTask,
        roi_config: dict[str, object],
        *,
        clear_message: str,
        status_message: str,
        replaced_drafts: tuple[str, ...] = ("Pipeline JSON", "Calibration"),
    ) -> bool | None:
        is_noop = self._roi_config_for_task(task) == roi_config
        if is_noop:
            return True
        if not self._confirm_configuration_change(task, "applying ROI geometry", replaced_drafts):
            return None
        if not self._apply_roi_config_to_task(task, roi_config):
            return False
        self._clear_tracking_results(clear_message)
        self.preview_label.set_roi_config(task.roi)
        self._render_calibration(task)
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self.statusBar().showMessage(status_message, 6000)
        return True

    @staticmethod
    def _normalize_roi_selection(selection: object) -> dict[str, object] | None:
        if isinstance(selection, tuple) and len(selection) == 4:
            x, y, width, height = (float(value) for value in selection)
            return {
                "type": "rectangle",
                "x": round(x, 3),
                "y": round(y, 3),
                "width": round(width, 3),
                "height": round(height, 3),
            }
        if not isinstance(selection, dict):
            return None
        roi_type = selection.get("type")
        try:
            if roi_type == "circle":
                center = selection["center"]  # type: ignore[assignment]
                return {
                    "type": "circle",
                    "center": [round(float(center[0]), 3), round(float(center[1]), 3)],  # type: ignore[index]
                    "radius": round(float(selection["radius"]), 3),
                }
            if roi_type == "annulus":
                center = selection["center"]  # type: ignore[assignment]
                inner_radius = float(selection["inner_radius"])
                outer_radius = float(selection["outer_radius"])
                if inner_radius <= 0 or outer_radius <= inner_radius:
                    return None
                return {
                    "type": "annulus",
                    "center": [round(float(center[0]), 3), round(float(center[1]), 3)],  # type: ignore[index]
                    "inner_radius": round(inner_radius, 3),
                    "outer_radius": round(outer_radius, 3),
                }
            if roi_type == "polygon":
                points_data = selection["points"]  # type: ignore[assignment]
                points = [
                    [round(float(point[0]), 3), round(float(point[1]), 3)]  # type: ignore[index]
                    for point in points_data
                ]
                if len(points) < 3:
                    return None
                return {"type": "polygon", "points": points}
            if roi_type == "curve_band":
                polyline_data = selection["polyline"]  # type: ignore[assignment]
                polyline = [
                    [round(float(point[0]), 3), round(float(point[1]), 3)]  # type: ignore[index]
                    for point in polyline_data
                ]
                half_width = float(selection["half_width"])
                if len(polyline) < 2 or half_width <= 0:
                    return None
                return {"type": "curve_band", "polyline": polyline, "half_width": round(half_width, 3)}
        except Exception:
            return None
        return None

    def _calibration_rod_selected(self, line: object) -> None:
        if not isinstance(line, tuple) or len(line) != 2:
            return
        start, end = line
        try:
            start_px = (float(start[0]), float(start[1]))  # type: ignore[index]
            end_px = (float(end[0]), float(end[1]))  # type: ignore[index]
        except Exception:
            return
        if not self.calibration_editor.set_line(start_px, end_px):
            return
        self.preview_label.set_calibration_line((start_px, end_px))
        self.statusBar().showMessage("Calibration rod marked. Enter its real length and unit, then apply.", 8000)

    def _calibration_draft_changed(self, config: object) -> None:
        line: tuple[tuple[float, float], tuple[float, float]] | None = None
        if isinstance(config, dict):
            try:
                start = config["start_px"]
                end = config["end_px"]
                line = (
                    (float(start[0]), float(start[1])),  # type: ignore[index]
                    (float(end[0]), float(end[1])),  # type: ignore[index]
                )
            except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                line = None
        self.preview_label.set_calibration_line(line)
        if isinstance(config, dict):
            self.preview_label.set_calibration_axis(
                isinstance(
                    self.current_task.pipeline.coordinate_model,
                    (ImageCoordinate, LinearWorldCoordinate),
                ),
                str(config.get("y_positive", "up")),
            )
        if self.calibration_editor.is_dirty():
            self.statusBar().showMessage("Calibration draft changed. Apply or revert before tracking.", 8000)
        else:
            self.statusBar().showMessage("Calibration draft reverted.", 4000)
        self._refresh_draft_state()

    def _calibration_editor_applied(self, config: object) -> None:
        rod = self._calibration_rod_from_dict(config if isinstance(config, dict) else None)
        if rod is None or rod.start_px is None or rod.end_px is None or rod.real_length is None:
            self.calibration_editor.show_error("Calibration values are incomplete or invalid.")
            return
        self._apply_calibration_rod(
            rod.start_px,
            rod.end_px,
            rod.real_length,
            rod.unit,
            rod.y_positive,
        )

    def _apply_calibration_rod(
        self,
        start_px: tuple[float, float],
        end_px: tuple[float, float],
        real_length: float,
        unit: str = "cm",
        y_positive: str = "up",
    ) -> bool:
        task = self.current_task
        rod = CalibrationRod(
            start_px=start_px,
            end_px=end_px,
            real_length=float(real_length),
            unit=str(unit).strip(),
            y_positive=str(y_positive),
        )
        if rod.pixel_length() <= 1e-12:
            QMessageBox.warning(self, "Calibration rod", "Calibration rod endpoints must be distinct.")
            return False
        if rod.unit_per_pixel() is None or not rod.unit or rod.y_positive not in {"up", "down"}:
            QMessageBox.warning(
                self,
                "Calibration rod",
                "Calibration length, unit, and axis direction must be valid.",
            )
            return False
        if self._calibration_rod_to_dict(task.calibration_rod) == self._calibration_rod_to_dict(rod):
            return True
        if not self._confirm_configuration_change(
            task, "applying calibration", ("Pipeline JSON", "ROI geometry")
        ):
            return False
        if not self._apply_calibration_rod_to_task(task, rod):
            self.calibration_editor.show_error("Calibration could not be applied to this pipeline.")
            return False
        self.preview_label.set_calibration_line((start_px, end_px))
        self._clear_tracking_results("Calibration updated. Run tracking again.")
        self._render_calibration(task)
        self.calibration_editor.show_applied()
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self.statusBar().showMessage("Calibration applied. Run tracking again.", 6000)
        return True

    def _reset_calibration(self) -> None:
        task = self.current_task
        if self._calibration_rod_to_dict(task.calibration_rod) is None:
            return
        if not self._confirm_configuration_change(
            task, "resetting calibration", ("Pipeline JSON", "ROI geometry", "Calibration")
        ):
            return
        old_coordinate_model = task.pipeline.coordinate_model
        old_state_model = task.pipeline.state_model
        old_state_key = self._single_state_key(old_state_model)
        old_unit_scale = self._state_length_unit_per_pixel(old_coordinate_model, old_state_model)
        task.calibration_rod = CalibrationRod()
        if isinstance(task.pipeline.coordinate_model, LinearWorldCoordinate):
            task.pipeline.coordinate_model = self.registry[task.pipeline_key].factory().coordinate_model
        elif isinstance(task.pipeline.coordinate_model, AnnularCoordinate):
            old = task.pipeline.coordinate_model
            task.pipeline.coordinate_model = AnnularCoordinate(
                center_px=old.center_px,
                theta_zero_px=old.theta_zero_px,
                direction=old.direction,
                inner_radius=old.inner_radius,
                outer_radius=old.outer_radius,
                unit_per_pixel=1.0,
                unit="px",
            )
        elif isinstance(task.pipeline.coordinate_model, PathCoordinate):
            old = task.pipeline.coordinate_model
            task.pipeline.coordinate_model = PathCoordinate(
                polyline=old.polyline,
                unit_per_pixel=1.0,
                unit="px",
            )
        new_state_model = self._state_model_with_pixel_unit(old_state_model)
        new_state_key = self._single_state_key(new_state_model)
        task.pipeline.state_model = new_state_model
        if old_state_key and new_state_key:
            task.pipeline.motion_model = self._retarget_keyed_model(
                task.pipeline.motion_model,
                old_state_key,
                new_state_key,
            )
            task.pipeline.tracker_filter = self._retarget_keyed_model(
                task.pipeline.tracker_filter,
                old_state_key,
                new_state_key,
            )
            new_unit_scale = self._state_length_unit_per_pixel(task.pipeline.coordinate_model, new_state_model)
            if old_unit_scale and new_unit_scale:
                task.pipeline.motion_model = self._scale_motion_model_units(
                    task.pipeline.motion_model,
                    new_state_key,
                    new_unit_scale / old_unit_scale,
                )
        self.preview_label.set_calibration_line(None)
        self._clear_tracking_results("Calibration reset. Run tracking again.")
        self._render_calibration(task)
        self._render_workflow(task.pipeline)
        self._sync_advanced_config_view()
        self._render_preview()
        self.statusBar().showMessage("Calibration reset to the selected preset defaults.", 6000)

    @staticmethod
    def _roi_config_for_task(task: DesktopTask) -> dict[str, object] | None:
        if isinstance(task.roi, dict):
            return dict(task.roi)
        return task.pipeline.roi.to_config()

    @staticmethod
    def _calibration_line_for_task(task: DesktopTask) -> tuple[tuple[float, float], tuple[float, float]] | None:
        rod = task.calibration_rod
        if rod is None or rod.start_px is None or rod.end_px is None:
            return None
        return rod.start_px, rod.end_px

    @staticmethod
    def _task_has_tracking_result_state(task: DesktopTask) -> bool:
        return bool(
            task.pipeline.results
            or task.edit_history
            or task.tracking_outcome
            or task.tracking_note
        )

    def _fresh_tracking_media(self, task: DesktopTask, *, action: str) -> MediaInfo | None:
        """Probe and verify the current path before either full or tail tracking."""

        if task.media_path is None:
            return None
        previous_info = task.saved_media_info or task.media_info
        info = self.project_controller.media_probe(task.media_path)
        has_result_state = self._task_has_tracking_result_state(task)
        assessment = self.project_controller.assess_media_relink(
            previous_info,
            info,
            has_result_state=has_result_state,
        )
        requires_review = assessment.requires_review
        if requires_review:
            self._stage_media_relink(task.media_path, info, assessment=assessment)
            if previous_info is not None:
                task.saved_media_info = previous_info
                task.media_info = replace(
                    previous_info,
                    available=False,
                    error=(
                        "Media at this path changed or could not be verified after it was loaded. "
                        "Review and apply the staged source before tracking."
                    ),
                )
            else:
                task.media_info = replace(
                    info,
                    available=False,
                    error="Review and apply the staged source before tracking.",
                )
            task.pending_media_relink = (info, assessment)
            task.media_identity_requires_review = True
            self._render_task(refresh_project_state=False)
            if self.media_tab is not None:
                self.sidebar_tabs.setCurrentWidget(self.media_tab)
            self.statusBar().showMessage(
                f"{action} paused: the media source changed or could not be verified. "
                "Review the staged source in Media.",
                8000,
            )
            return None
        task.media_info = info
        if info.available:
            task.saved_media_info = info
        return info

    def _run_tracking(self) -> None:
        if self._background_tasks.closing:
            return
        if {"media-probe", "project-open", "project-save"}.intersection(
            self._background_tasks.active_kinds
        ):
            self.statusBar().showMessage(
                "Finish project/media loading or project saving before starting tracking.",
                6000,
            )
            return
        if self._analysis_thread is not None:
            self.statusBar().showMessage(
                "Cancel or finish Signal processing before replacing tracking results.",
                6000,
            )
            return
        if self._tracking_worker is not None:
            self._cancel_tracking()
            return
        task = self.current_task
        if task.media_path is None:
            QMessageBox.information(self, "Run tracking", "Add a video file before running tracking.")
            return
        if self._block_tracking_for_unapplied_drafts("Full tracking"):
            return
        info = self._fresh_tracking_media(task, action="Tracking")
        if info is None:
            return
        if not info.available or info.frame_count <= 0:
            QMessageBox.information(self, "Run tracking", info.error or "The selected media cannot be tracked.")
            return
        if info.kind != "video":
            QMessageBox.information(self, "Run tracking", "Video tracking requires a video file.")
            return
        if (task.pipeline.results or task.edit_history) and not self._ask_result_replacement(task):
            self.statusBar().showMessage(
                "Full tracking canceled. Current Results/Edits are unchanged.",
                6000,
            )
            return

        self._stop_playback()
        fps = info.fps if info.fps > 0 else 30.0
        self._start_tracking_job(task, info.frame_count, fps, mode="full")

    def _rerun_after_current_result(self) -> None:
        if self._background_tasks.closing:
            return
        if self._analysis_thread is not None:
            self.statusBar().showMessage(
                "Cancel or finish Signal processing before rerunning tracking.",
                6000,
            )
            return
        if self._tracking_worker is not None:
            return
        task = self.current_task
        if not task.pipeline.results:
            QMessageBox.information(self, "Rerun after", "Run tracking before rerunning a segment.")
            return
        index = self._current_result_index()
        if index is None:
            QMessageBox.information(self, "Rerun after", "Select a result row or move to a tracked frame first.")
            return
        if self._block_tracking_for_unapplied_drafts("Rerun"):
            return
        info = self._fresh_tracking_media(task, action="Rerun")
        if task.media_path is None or info is None or not info.available or info.frame_count <= 0:
            if info is not None:
                QMessageBox.information(
                    self,
                    "Rerun after",
                    info.error or "Add a readable video file before rerunning a segment.",
                )
            return
        if info.kind != "video":
            QMessageBox.information(self, "Rerun after", "Video tracking requires a video file.")
            return

        anchor = task.pipeline.results[index]
        start_frame = int(anchor.frame_index) + 1
        max_frame = int(info.frame_count) - 1
        if start_frame > max_frame:
            QMessageBox.information(self, "Rerun after", "There are no later frames after the selected result.")
            return

        prefix = list(task.pipeline.results[: index + 1])
        replaced_result_count = len(task.pipeline.results) - len(prefix)
        if not self._ask_rerun_replacement(
            task,
            anchor_frame=int(anchor.frame_index),
            start_frame=start_frame,
            result_count=replaced_result_count,
        ):
            self.statusBar().showMessage(
                "Rerun canceled. Current later Results/Edits are unchanged.",
                6000,
            )
            return

        self._stop_playback()
        fps = info.fps if info.fps > 0 else 30.0
        self._start_tracking_job(
            task,
            info.frame_count,
            fps,
            mode="rerun",
            start_frame=start_frame,
            prefix=prefix,
            anchor_frame=anchor.frame_index,
        )

    def _start_tracking_job(
        self,
        task: DesktopTask,
        frame_count: int,
        fps: float,
        *,
        mode: str,
        start_frame: int = 0,
        prefix: list[TrackerResult] | None = None,
        anchor_frame: int | None = None,
    ) -> None:
        if not self._tracking_coordinator.can_start:
            return
        self._review_undo = None
        self._sync_review_undo_action()
        previous_analysis_run = self.analysis_controller.current_run
        if self.analysis_controller.has_result or self._analysis_thread is not None:
            self._clear_analysis_result(
                "Tracking data is being regenerated. Run processing again when it finishes.",
                state="dirty",
            )
        self._invalidate_review_responses(task, start_frame if mode == "rerun" else None)
        self._reset_physics_context(schedule_build=False)
        task.close_reader()
        source_path = str(task.media_path)
        tracking_reader_overridden = (
            self._tracking_reader_for_path is not NeoTrackerWindow._tracking_reader_for_path
        )
        self._tracking_coordinator.start(
            TrackingRequest(
                task=task,
                frame_count=frame_count,
                fps=fps,
                mode=mode,
                start_frame=start_frame,
                prefix=tuple(prefix or ()),
                anchor_frame=anchor_frame,
                previous_analysis_run=previous_analysis_run,
                reader_factory=partial(self._tracking_reader_for_path, source_path),
                process_isolation=not tracking_reader_overridden,
                isolated_reader_factory=(
                    None if tracking_reader_overridden else partial(MediaReader, source_path)
                ),
            )
        )

    def _tracking_started(self, job: TrackingJob) -> None:
        self._set_tracking_busy(True, job.mode)

    def _tracking_coordinator_state_changed(self, state: str) -> None:
        if state == "finishing":
            self._set_tracking_finishing()

    def _tracking_idle(self) -> None:
        self._schedule_close_if_workers_stopped()

    def _set_tracking_busy(self, busy: bool, mode: str = "full") -> None:
        if busy and self.tracking_tab is not None:
            if self.sidebar_tabs.currentWidget() is not self.tracking_tab:
                self._tracking_previous_sidebar_tab = self.sidebar_tabs.currentWidget()
            self.sidebar_tabs.setCurrentWidget(self.tracking_tab)
        self.sidebar_tabs.setEnabled(not busy)
        self._set_action_enabled(
            "tracking.export_csv",
            False if busy else bool(self.current_task.pipeline.results),
        )
        self._set_action_enabled(
            "tracking.export_report",
            bool(not busy and self.current_task.media_path is not None),
        )
        self._set_action_enabled(
            "review.rerun",
            False if busy else bool(self.current_task.pipeline.results),
        )
        if busy:
            self._set_action_enabled("review.undo", False)
        else:
            self._sync_review_undo_action()
        self._set_playback_enabled(False if busy else self._current_task_can_play())
        self.run_tracking_button.setProperty("trackingBusy", busy)
        self.run_tracking_button.style().unpolish(self.run_tracking_button)
        self.run_tracking_button.style().polish(self.run_tracking_button)
        self._sync_roi_node_editing()
        if busy:
            verb = "Rerunning" if mode == "rerun" else "Tracking"
            preview_frame = int(self.current_task.preview_frame_index)
            self._show_playback_status(
                f"Preview paused · Frame {preview_frame}",
                (
                    f"{verb} is processing source frames in the background. The Preview remains "
                    f"on selected frame {preview_frame} until tracking finishes; this is not the "
                    "current processing frame. No extra preview decode is performed, so live "
                    "tracking throughput is not reduced."
                ),
                "tracking",
            )
            self._set_tracking_status_outcome("running")
            self.tracking_status_label.setText(f"{verb} 0%")
            self.tracking_summary_label.setText("Preparing frames…")
            self.tracking_performance_label.setText("Measuring input, compute, and review cache…")
            self.tracking_performance_label.setProperty("performanceState", "running")
            self.tracking_performance_label.style().unpolish(self.tracking_performance_label)
            self.tracking_performance_label.style().polish(self.tracking_performance_label)
            self.tracking_performance_label.setToolTip(
                "Live averages separate media input from tracking computation and show retained Review data."
            )
            self.tracking_performance_label.setAccessibleDescription(
                "Measuring average media input and tracking computation time per frame, plus retained Review data."
            )
            self.tracking_performance_title_label.show()
            self.tracking_performance_label.show()
            self.tracking_summary_label.setToolTip(
                "Waiting for the first frame before estimating throughput and remaining time."
            )
            self.tracking_summary_label.setAccessibleDescription(
                "Preparing video frames. Throughput and estimated remaining time are not available yet."
            )
            self._update_action(
                "tracking.run",
                enabled=True,
                text="Cancel",
                icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop),
                tool_tip=f"Cancel the active {verb.lower()} job.",
            )
            self.run_tracking_button.setAccessibleName(f"Cancel {verb.lower()}")
            return
        previous_tab = self._tracking_previous_sidebar_tab
        self._tracking_previous_sidebar_tab = None
        if (
            previous_tab is not None
            and self.sidebar_tabs.currentWidget() is self.tracking_tab
            and self.sidebar_tabs.indexOf(previous_tab) >= 0
        ):
            self.sidebar_tabs.setCurrentWidget(previous_tab)
        if self.playback_status_label.property("playbackState") == "tracking":
            self.playback_status_label.hide()
        self.tracking_performance_title_label.hide()
        self.tracking_performance_label.hide()
        self._update_action(
            "tracking.run",
            text="Run Tracking",
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            tool_tip="Run the selected video tracking pipeline in the background.",
        )
        self.run_tracking_button.setAccessibleName("Run video tracking")
        self._render_tracking_status(self.current_task)

    def _current_task_can_play(self) -> bool:
        info = self.current_task.media_info
        return bool(info and info.available and info.kind == "video" and info.frame_count > 0)

    def _tracking_progressed(
        self,
        job: TrackingJob,
        progress: TrackingProgress,
    ) -> None:
        completed = int(progress.completed)
        total = int(progress.total)
        span = max(1, int(total) - job.start_frame)
        processed = max(0, int(completed) - job.start_frame)
        percent = min(100, int(round(processed * 100 / span)))
        verb = "Rerunning" if job.mode == "rerun" else "Tracking"
        self.tracking_status_label.setText(f"{verb} {percent}%")
        rate = progress.throughput_fps
        eta = self._format_tracking_eta(progress.eta_s)
        self.tracking_summary_label.setText(
            f"Frame {completed}/{total} · Throughput {rate:.1f} fps · {eta} left"
        )
        cache_target = (
            f" against a {self._format_binary_bytes(progress.debug_history_max_bytes)} target"
            if progress.debug_history_max_bytes > 0
            else ""
        )
        detail = (
            f"{verb} processed {progress.processed_frames} frames at {rate:.1f} frames per second. "
            f"Average input {progress.input_ms_per_frame:.2f} ms/frame; "
            f"tracking compute {progress.processing_ms_per_frame:.2f} ms/frame; "
            f"Review cache retains {progress.retained_debug_frames} recent frame"
            f"{'s' if progress.retained_debug_frames != 1 else ''} using "
            f"{self._format_binary_bytes(progress.retained_debug_bytes)}{cache_target}; "
            "the newest response remains available even when it alone exceeds the target; "
            f"estimated time remaining {eta}."
        )
        pipeline_label = ""
        if progress.prefetch_frames > 0:
            detail += (
                f" Input and compute run as a bounded {progress.prefetch_frames}-frame pipeline; "
                "their stage averages can overlap and should not be added. "
                f"Measured stage overlap is {progress.stage_overlap_ms_per_frame:.2f} ms/frame."
            )
            pipeline_label = f"{progress.prefetch_frames}-frame pipeline"
        self.tracking_summary_label.setToolTip(detail)
        self.tracking_summary_label.setAccessibleDescription(detail)
        performance_lines = []
        if pipeline_label:
            performance_lines.append(pipeline_label)
        performance_lines.extend(
            (
                f"Input {progress.input_ms_per_frame:.2f} ms/f · "
                f"Compute {progress.processing_ms_per_frame:.2f} ms/f",
                f"Review cache {self._format_binary_bytes(progress.retained_debug_bytes)}",
            )
        )
        performance_text = "\n".join(performance_lines)
        self.tracking_performance_label.setText(performance_text)
        self.tracking_performance_label.setToolTip(detail)
        self.tracking_performance_label.setAccessibleDescription(detail)
        self.statusBar().showMessage(
            f"{verb} frame {completed} of {total} · Throughput {rate:.1f} fps · {eta} left…"
        )

    @staticmethod
    def _format_tracking_eta(seconds: float) -> str:
        remaining = max(0.0, float(seconds))
        if remaining < 1.0:
            return "<1s"
        rounded = int(round(remaining))
        if rounded < 60:
            return f"{rounded}s"
        minutes, seconds_part = divmod(rounded, 60)
        return f"{minutes}m {seconds_part:02d}s"

    @staticmethod
    def _format_binary_bytes(byte_count: int) -> str:
        size = max(0, int(byte_count))
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024.0:.1f} KiB"
        if size < 1024 * 1024 * 1024:
            return f"{size / (1024.0 * 1024.0):.1f} MiB"
        return f"{size / (1024.0 * 1024.0 * 1024.0):.2f} GiB"

    def _cancel_tracking(self) -> None:
        worker = self._tracking_worker
        if worker is None:
            return
        self._tracking_coordinator.cancel("user")
        self._update_action("tracking.run", enabled=False, text="Cancelling…")
        self.tracking_status_label.setText("Cancelling…")
        self.tracking_summary_label.setText("Finishing the current frame safely.")
        current_lines = self.tracking_performance_label.text().splitlines()
        metric_lines = [
            line
            for line in current_lines
            if line.startswith("Input ") or line.startswith("Review cache ")
        ]
        if metric_lines:
            performance_text = "\n".join(
                ("Cancellation requested · last sample", *metric_lines)
            )
            detail = (
                "Cancellation requested. Input, compute, and Review cache values are the last "
                "completed sample; no new source frame will start after the current input or "
                "compute step returns."
            )
        else:
            performance_text = (
                "Cancellation requested\n"
                "Waiting for the current input or compute step\n"
                "No completed performance sample yet"
            )
            detail = (
                "Cancellation requested before the first completed performance sample. "
                "Waiting for the current input or compute step to return safely."
            )
        self.tracking_performance_label.setText(performance_text)
        self.tracking_performance_label.setProperty("performanceState", "cancelling")
        self.tracking_performance_label.setToolTip(detail)
        self.tracking_performance_label.setAccessibleDescription(detail)
        self.tracking_performance_label.style().unpolish(self.tracking_performance_label)
        self.tracking_performance_label.style().polish(self.tracking_performance_label)
        self.tracking_summary_label.setToolTip(detail)
        self.tracking_summary_label.setAccessibleDescription(detail)
        if self._background_tasks.closing:
            self.statusBar().showMessage("Closing… Finishing background operations safely.")
        else:
            self.statusBar().showMessage("Cancellation requested · finishing the current frame safely…")

    def _tracking_terminal_ready(self, job: TrackingJob) -> None:
        if job.source_changed:
            self._quarantine_changed_tracking_source(job)
        if job.failed and not self._background_tasks.closing:
            title = "Rerun after" if job.mode == "rerun" else "Run tracking"
            QMessageBox.warning(self, title, f"Tracking failed:\n{job.completion_note}")
        self._finish_tracking_job(job)
        if not self._background_tasks.closing:
            self._set_tracking_busy(False)
            if job.source_changed and self.media_tab is not None:
                self.sidebar_tabs.setCurrentWidget(self.media_tab)
                recovery_button = (
                    self.media_relink_panel.apply_button
                    if self.media_relink_panel.apply_button.isEnabled()
                    else self.media_relink_panel.browse_button
                )
                recovery_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _quarantine_changed_tracking_source(self, job: TrackingJob) -> None:
        """Stage the current path for review without touching restored result state."""

        task = job.task
        baseline = task.saved_media_info or task.media_info
        try:
            candidate = self.project_controller.media_probe(job.source_path)
        except Exception as exc:
            candidate = MediaInfo(error=f"Could not inspect changed media source: {exc}")
        assessment = self.project_controller.assess_media_relink(
            baseline,
            candidate,
            has_result_state=bool(
                job.previous_results
                or job.previous_edit_history
                or job.previous_tracking_outcome
                or job.previous_tracking_note
            ),
        )
        task.close_reader()
        task.saved_media_info = baseline
        unavailable_source = baseline or candidate
        task.media_info = replace(
            unavailable_source,
            available=False,
            error=(
                "Media changed while tracking. Previous results were restored, but preview, "
                "editing, and export stay disabled until the staged source is reviewed."
            ),
        )
        task.pending_media_relink = (candidate, assessment)
        task.media_identity_requires_review = True
        if task is self.current_task:
            self._stage_media_relink(
                job.source_path,
                candidate,
                assessment=assessment,
            )

    def _finish_tracking_job(self, job: TrackingJob) -> None:
        task = job.task
        if not job.previous_result_state_restored and job.result_replacement_committed:
            task.mark_results_changed()
        if task is self.current_task:
            self._reset_physics_context()
        if job.previous_result_state_restored and job.previous_analysis_run is not None:
            self.analysis_controller.accept_run(job.previous_analysis_run)
        self._render_observation_backend(task.pipeline)
        if task.pipeline.results:
            last_frame = task.pipeline.results[-1].frame_index
            task.preview_frame_index = self._clamped_preview_frame(task, last_frame)
            self._sync_preview_position_controls(task)
        self._render_results(task, prefer_preview_frame=True)
        self.run_history_panel.show_all()
        self._render_run_history(task)
        self.run_history_panel.select_latest()
        self._render_edit_history(task)
        self.analysis_controller.invalidate_source_cache()
        self._refresh_analysis_sources()
        if job.previous_result_state_restored and job.previous_analysis_run is not None:
            self.analysis_result_view.setPlainText(job.previous_analysis_run.summary)
            self._set_analysis_export_enabled(True)
            label = f"{job.previous_analysis_run.kind.upper()} ready"
            detail = (
                f"{job.previous_analysis_run.source.label} remains valid because the previous "
                "tracking results were restored unchanged."
            )
            self._set_analysis_status(label, "complete", detail)
        self._render_preview()
        self._render_tracking_status(task)
        if job.source_changed:
            self._set_analysis_export_enabled(False)
            if job.previous_analysis_run is not None:
                self._set_analysis_status(
                    "Source review required",
                    "dirty",
                    "The previous analysis remains in memory, but export is disabled until the "
                    "staged media source is reviewed.",
                )

        if job.mode == "rerun" and not job.previous_result_state_restored:
            status = (
                "failed"
                if job.failed
                else ("canceled" if job.cancelled else ("early_end" if job.ended_early else "done"))
            )
            anchor_frame = int(job.anchor_frame if job.anchor_frame is not None else job.start_frame - 1)
            self._record_edit_event(
                task,
                "rerun_after",
                anchor_frame,
                start_frame=job.start_frame,
                end_frame=max(job.start_frame, job.completed - 1),
                status=status,
                retained_frames=len(job.prefix),
                result_count=len(task.pipeline.results),
                superseded_edit_count=job.superseded_edit_count,
            )
            if job.failed:
                message = f"Rerun failed; kept results through frame {anchor_frame}."
            elif job.cancelled:
                message = f"Rerun canceled after frame {job.completed - 1}."
            elif job.ended_early:
                message = f"Source ended early after frame {job.completed - 1}; partial rerun results were kept."
            else:
                message = f"Reran frames {job.start_frame}-{job.completed - 1}."
            if job.superseded_edit_count:
                edit_label = "edit" if job.superseded_edit_count == 1 else "edits"
                message += f" {job.superseded_edit_count} later manual {edit_label} marked historical."
        elif job.previous_result_state_restored:
            if job.source_changed:
                message = (
                    "Media source changed during tracking; new results were discarded and the "
                    "previous Results/Edits, outcome, and analysis were restored."
                )
            elif job.failed:
                message = "Tracking failed before the first new frame; previous Results/Edits were restored."
            elif job.cancelled:
                message = "Tracking canceled before the first new frame; previous Results/Edits were restored."
            else:
                message = "No new frame was processed; previous Results/Edits were restored."
        elif job.failed:
            message = f"Tracking failed after {job.completed} frames; partial results were kept."
        elif job.cancelled:
            message = f"Tracking canceled after {job.completed} frames."
        elif job.ended_early:
            message = f"Source ended early after {job.completed} frames; partial results were kept."
        else:
            message = f"Tracked {len(task.pipeline.results)} frames."
        if self._background_tasks.closing:
            self.statusBar().showMessage("Closing… Finishing background operations safely.")
        else:
            self.statusBar().showMessage(message, 6000)
        if task is self.current_task:
            self.review_history_tabs.setCurrentIndex(0)
        if (
            not self._background_tasks.closing
            and task.pipeline.results
            and not job.failed
            and self.review_tab is not None
        ):
            self.sidebar_tabs.setCurrentWidget(self.review_tab)
        self._mark_project_changed()

    def _set_tracking_finishing(self) -> None:
        closing = self._background_tasks.closing
        status_text = "Closing…" if closing else "Finishing…"
        summary = (
            "Finishing background operations safely."
            if closing
            else "Finalizing results and releasing the tracking worker."
        )
        detail = (
            "Neo-Tracker is closing after the tracking worker exits safely."
            if closing
            else "Tracking has stopped. Waiting for the background thread to exit safely before controls unlock."
        )
        self._set_tracking_status_outcome("running")
        self.tracking_status_label.setText(status_text)
        self.tracking_status_label.setToolTip(detail)
        self.tracking_status_label.setAccessibleDescription(detail)
        self.tracking_summary_label.setText(summary)
        self.tracking_summary_label.setToolTip(detail)
        self.tracking_summary_label.setAccessibleDescription(detail)
        self.tracking_performance_label.setText(
            "Closing · tracking stopped\nWaiting for the worker to exit safely"
            if closing
            else "Tracking stopped\nFinalizing results and releasing the worker"
        )
        self.tracking_performance_label.setProperty(
            "performanceState",
            "cancelling" if closing else "finishing",
        )
        self.tracking_performance_label.setToolTip(detail)
        self.tracking_performance_label.setAccessibleDescription(detail)
        self.tracking_performance_label.style().unpolish(self.tracking_performance_label)
        self.tracking_performance_label.style().polish(self.tracking_performance_label)
        self._update_action(
            "tracking.run",
            enabled=False,
            text="Finishing…",
            tool_tip=detail,
        )
        self.run_tracking_button.setAccessibleName(
            "Closing Neo-Tracker" if closing else "Finishing video tracking"
        )
        self.run_tracking_button.setAccessibleDescription(detail)

    def _export_tracking_csv(self) -> None:
        results = self.current_task.pipeline.results
        if not results:
            QMessageBox.information(self, "Export tracking", "Run tracking before exporting CSV.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export tracking CSV", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        write_tracking_csv(path, results, state_units=self.current_task.pipeline.state_model.units())
        self.statusBar().showMessage(f"Exported tracking CSV: {path}", 6000)

    def _export_report(self) -> None:
        task = self.current_task
        if task.media_path is None:
            QMessageBox.information(self, "Export report", "Add media before exporting a report.")
            return
        default_name = f"{Path(task.media_path).stem}-report.md"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export report",
            default_name,
            "Markdown files (*.md);;All files (*)",
        )
        if not path:
            return
        try:
            write_markdown_report(
                path=path,
                title=f"Neo-Tracker Report - {task.title()}",
                pipeline=task.pipeline,
                results=task.pipeline.results,
                media_path=task.media_path,
                media_info=task.media_info,
                roi=self._roi_config_for_task(task),
                calibration_rod=self._calibration_rod_to_dict(task.calibration_rod),
                edit_history=[dict(entry) for entry in task.edit_history],
                run_history=list(task.run_history),
                project_path=str(self.project_path) if self.project_path else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Export report", f"Could not export report:\n{exc}")
            return
        self.statusBar().showMessage(f"Exported report: {path}", 6000)

    def _jump_to_selected_result(self) -> None:
        self._result_selection_changed()

    def _result_selection_changed(self) -> None:
        index = self._selected_result_index()
        self._render_review_selection(index)
        if index is None:
            return
        results = self.current_task.pipeline.results
        if index < 0 or index >= len(results):
            return
        self._stop_playback()
        self._preview_frame_changed(
            results[index].frame_index,
            sync_review_selection=False,
        )

    def _sync_review_selection_to_frame(self) -> None:
        index = self.review_controller.preferred_result_index(
            self.current_task.pipeline.results,
            None,
            self.current_task.preview_frame_index,
        )
        selection_model = self.results_table.selectionModel()
        selection_model.blockSignals(True)
        if index is None:
            self.results_table.clearSelection()
        else:
            self.results_table.selectRow(index)
            self.results_table.scrollTo(
                self.results_model.index(index, 0),
                QTableView.ScrollHint.EnsureVisible,
            )
            QTimer.singleShot(
                0,
                lambda row=index: self._scroll_selected_result_row_into_view(row),
            )
        selection_model.blockSignals(False)
        self._render_review_selection(index)

    def _scroll_selected_result_row_into_view(self, row: int) -> None:
        """Finish scrolling after a hidden Review tab has received its layout."""

        if self._selected_result_index() != int(row):
            return
        if not 0 <= int(row) < self.results_model.rowCount():
            return
        self.results_table.scrollTo(
            self.results_model.index(int(row), 0),
            QTableView.ScrollHint.PositionAtCenter,
        )

    def _current_result_index(self) -> int | None:
        selected = self._selected_result_index()
        if selected is not None:
            return selected
        return self.review_controller.preferred_result_index(
            self.current_task.pipeline.results,
            None,
            int(self.current_task.preview_frame_index),
        )

    def _selected_result_index(self) -> int | None:
        selected_rows = self.results_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        data = selected_rows[0].data(Qt.ItemDataRole.UserRole)
        try:
            return int(data)
        except Exception:
            return None

    def _tracking_overlay_for_task(
        self,
        task: DesktopTask,
        frame: np.ndarray | None = None,
    ) -> tuple[
        list[tuple[float, float]],
        tuple[float, float] | None,
        tuple[float, float] | None,
        np.ndarray | None,
        list[tuple[float, float, float, bool]],
        tuple[float, float] | None,
        list[tuple[float, float]],
    ]:
        response_map: np.ndarray | None = None
        overlay = self.review_controller.overlay(
            task.pipeline,
            int(task.preview_frame_index),
            show_observation=self.show_observation_checkbox.isChecked(),
            show_measurement=self.show_measurement_checkbox.isChecked(),
            show_candidates=self.show_candidates_checkbox.isChecked(),
            show_prediction=self.show_prediction_checkbox.isChecked(),
        )
        if self.show_response_checkbox.isChecked() and overlay.selected_result is not None:
            retained_angular_response = angular_response(overlay.selected_result)
            if retained_angular_response is not None:
                self._cancel_review_response_requests(clear_pending=True)
                sample_count = int(retained_angular_response.size)
                pre_edit_evidence = overlay.selected_result.status.startswith("manual")
                evidence_text = " · pre-edit evidence" if pre_edit_evidence else ""
                evidence_detail = (
                    " The selected result was manually edited; this profile remains the original detector "
                    "evidence."
                    if pre_edit_evidence
                    else ""
                )
                route_to_angular = bool(
                    self._response_mode_routing_requested
                    or self.review_diagnostics_panel.mode == "confidence"
                )
                routed = bool(
                    route_to_angular
                    and self.review_diagnostics_panel.select_mode("angular_response")
                )
                self._response_mode_routing_requested = False
                if routed or self.review_diagnostics_panel.mode == "angular_response":
                    self._set_response_status(
                        f"Response: angular profile below · {sample_count:,} samples{evidence_text}",
                        "diagnostic",
                        f"The selected result already stores a 1D angular response with {sample_count:,} "
                        "samples. It is shown below; no source-frame recomputation or image-space heatmap "
                        f"is needed.{evidence_detail}",
                    )
                elif self.review_diagnostics_panel.mode_combo.findData("angular_response") >= 0:
                    self._set_response_status(
                        f"Response: angular profile available · {sample_count:,} samples{evidence_text}",
                        "diagnostic",
                        f"The selected result already stores a 1D angular response with {sample_count:,} "
                        "samples. Choose Angular response in the diagnostic plot to inspect it; no "
                        f"source-frame recomputation is needed.{evidence_detail}",
                    )
                else:
                    self._set_response_status(
                        f"Response: retained angular evidence · {sample_count:,} samples{evidence_text}",
                        "diagnostic",
                        f"The selected result stores a 1D angular response with {sample_count:,} samples, "
                        f"but this view cannot plot it as an image-space heatmap.{evidence_detail}",
                    )
            elif frame is None:
                self._cancel_review_response_requests(clear_pending=True)
                self._set_response_status(
                    "Response: unavailable",
                    "unavailable",
                    "The source frame is not available for response recomputation.",
                )
            else:
                response_map = self._response_map_for_review(
                    task,
                    task.pipeline,
                    overlay.selected_result,
                    frame,
                )
                if response_map is not None and (
                    response_map.ndim != 2 or tuple(response_map.shape) != tuple(frame.shape[:2])
                ):
                    response_shape = " × ".join(str(int(size)) for size in response_map.shape)
                    response_map = None
                    pre_edit_evidence = overlay.selected_result.status.startswith("manual")
                    evidence_text = " · pre-edit evidence" if pre_edit_evidence else ""
                    evidence_detail = (
                        " The selected result was manually edited; this response remains the original "
                        "detector evidence."
                        if pre_edit_evidence
                        else ""
                    )
                    route_to_angular = bool(
                        self._response_mode_routing_requested
                        or self.review_diagnostics_panel.mode == "confidence"
                    )
                    routed = bool(
                        route_to_angular
                        and self.review_diagnostics_panel.select_mode("angular_response")
                    )
                    self._response_mode_routing_requested = False
                    if routed or self.review_diagnostics_panel.mode == "angular_response":
                        self._set_response_status(
                            f"Response: polar diagnostic below{evidence_text}",
                            "diagnostic",
                            f"This detector returned a {response_shape} polar-space response rather than an "
                            "image-space heatmap. The selected frame's angular profile is shown below."
                            f"{evidence_detail}",
                        )
                    elif self.review_diagnostics_panel.mode_combo.findData("angular_response") >= 0:
                        self._set_response_status(
                            f"Response: angular diagnostic available{evidence_text}",
                            "diagnostic",
                            f"This detector returned a {response_shape} polar-space response rather than an "
                            "image-space heatmap. Choose Angular response in the diagnostic plot to inspect it."
                            f"{evidence_detail}",
                        )
                    else:
                        self._set_response_status(
                            f"Response: non-image diagnostic{evidence_text}",
                            "diagnostic",
                            f"This detector returned a {response_shape} response that cannot be overlaid "
                            f"directly on the video frame.{evidence_detail}",
                        )
                elif response_map is not None:
                    self._response_mode_routing_requested = False
        elif self.show_response_checkbox.isChecked():
            self._cancel_review_response_requests(clear_pending=True)
        return (
            list(overlay.trajectory),
            overlay.current_point,
            overlay.observation_point,
            response_map,
            list(overlay.candidate_points),
            overlay.prediction_point,
            list(overlay.measurements),
        )

    def _clear_tracking_results(self, message: str | None = None) -> None:
        self._review_undo = None
        self._invalidate_review_responses(self.current_task)
        had_result_state = self._task_has_tracking_result_state(self.current_task)
        self.current_task.tracking_outcome = ""
        self.current_task.tracking_note = ""
        if had_result_state:
            self.analysis_controller.invalidate_source_cache()
            self.current_task.pipeline.reset()
            self.current_task.mark_results_changed()
            self.current_task.edit_history.clear()
            self._render_edit_history(self.current_task)
            self._sync_review_undo_action()
            self._clear_analysis_result()
            self._reset_physics_context()
            if message:
                self.statusBar().showMessage(message, 6000)
        self._render_results(self.current_task)
        self._refresh_analysis_sources()
        self._render_tracking_status(self.current_task)
        if had_result_state:
            self._render_preview()
        self._mark_project_changed()

    def _fit_results_table_columns(self, state_headers: list[str]) -> None:
        # Reallocate the narrow sidebar budget toward numeric data without
        # forcing a horizontal scrollbar for the common two-state result table.
        widths = [60, 74]
        widths.extend(max(78, min(112, len(header) * 7 + 14)) for header in state_headers)
        widths.extend([60, 74])
        for column_index, width in enumerate(widths[: self.results_model.columnCount()]):
            self.results_table.setColumnWidth(column_index, width)

    @staticmethod
    def _tracking_summary_text(results: list[TrackerResult]) -> str:
        return ReviewController.tracking_summary_text(results)

    @property
    def analysis_result(self) -> FFTResult | STFTResult | None:
        return self.analysis_controller.result

    @property
    def analysis_result_kind(self) -> str | None:
        return self.analysis_controller.result_kind

    def _current_analysis_source(self) -> AnalysisSource:
        return AnalysisSource.from_data(
            self.analysis_source_combo.currentData(),
            self.analysis_source_combo.currentText(),
        )

    def _refresh_analysis_sources(
        self,
        *,
        force: bool = False,
        previous_source: AnalysisSource | None = None,
        prepared_sources: tuple[AnalysisSource, ...] | None = None,
    ) -> None:
        if previous_source is None:
            previous_source = self._current_analysis_source()
        sources = (
            list(prepared_sources)
            if prepared_sources is not None
            else self.analysis_controller.available_sources_cached(
                self.current_task.pipeline.results,
                self.current_task.pipeline.state_model.units(),
                self.current_task.media_path,
                self.current_task.media_info,
                force=force,
            )
        )
        self.analysis_source_combo.blockSignals(True)
        self.analysis_source_combo.clear()
        for source in sources:
            self.analysis_source_combo.addItem(source.label, source.to_data())

        selected_index = 0
        selection_preserved = False
        if previous_source.available:
            for index, source in enumerate(sources):
                if source.identity == previous_source.identity:
                    selected_index = index
                    selection_preserved = True
                    break
        self.analysis_source_combo.setCurrentIndex(selected_index)
        self.analysis_source_combo.blockSignals(False)

        source = self._current_analysis_source()
        invalidated = False
        job = self._analysis_job
        if job is not None and (
            job.task is not self.current_task or job.source.identity != source.identity
        ):
            self._cancel_analysis(
                "Task or signal data changed. Run processing again.",
                state="dirty" if source.available else "empty",
            )
            invalidated = True
        if self.analysis_controller.has_result and not self.analysis_controller.matches_context(
            source,
            id(self.current_task),
        ):
            self._clear_analysis_result(
                "Task or signal data changed. Run processing again.",
                state="dirty",
            )
            invalidated = True
        if not selection_preserved and previous_source.available:
            self._reset_analysis_sample_rate_override()
        inventory = self._render_analysis_source_detail()
        if force:
            self.statusBar().showMessage(f"Signal sources refreshed · {inventory}.", 6000)
        if self._analysis_thread is not None:
            return
        if not source.available:
            self._set_analysis_status(
                "No source",
                "empty",
                "Run tracking or add an available WAV file first.",
            )
        elif not invalidated and not self.analysis_controller.has_result:
            current_state = str(self.analysis_status_label.property("analysisState") or "")
            if current_state not in {"dirty", "failed"}:
                self._set_analysis_status("Ready", "ready", source.detail())

    def _analysis_source_changed(self) -> None:
        self._reset_analysis_sample_rate_override()
        source = self._current_analysis_source()
        if self._analysis_thread is not None:
            self._cancel_analysis(
                "Signal source changed. Run processing again.",
                state="dirty" if source.available else "empty",
            )
            self._render_analysis_source_detail()
            return
        if self.analysis_controller.has_result:
            self._clear_analysis_result("Signal source changed. Run processing again.", state="dirty")
        elif source.available:
            self._set_analysis_status("Ready", "ready", source.detail())
        else:
            self._set_analysis_status("No source", "empty", source.detail())
        self._render_analysis_source_detail()

    def _reset_analysis_sample_rate_override(self) -> None:
        self.analysis_sample_rate_spin.blockSignals(True)
        self.analysis_sample_rate_spin.setValue(0.0)
        self.analysis_sample_rate_spin.blockSignals(False)

    def _update_analysis_source_sample_rate(self) -> None:
        """Compatibility wrapper: source rate is shown as metadata, not forced into the override."""
        self._render_analysis_source_detail()

    def _render_analysis_source_detail(self) -> str:
        source = self._current_analysis_source()
        detail = source.detail()
        available_sources = [
            AnalysisSource.from_data(
                self.analysis_source_combo.itemData(index),
                self.analysis_source_combo.itemText(index),
            )
            for index in range(self.analysis_source_combo.count())
        ]
        source_count = sum(item.available for item in available_sources)
        tracking_sources = [item for item in available_sources if item.kind == "tracking"]
        audio_source_count = max(0, source_count - len(tracking_sources))
        if tracking_sources:
            result_count = max(item.total_sample_count for item in tracking_sources)
            noun = "signal" if len(tracking_sources) == 1 else "signals"
            inventory = (
                f"{len(tracking_sources)} tracking {noun} indexed from {result_count:,} results"
            )
            if audio_source_count > 0:
                audio_noun = "source" if audio_source_count == 1 else "sources"
                inventory += f" · {audio_source_count} audio {audio_noun}"
        else:
            inventory = (
                f"{source_count} signal source available"
                if source_count == 1
                else f"{source_count} signal sources available"
            )
        visible_detail = f"{detail}\n{inventory}" if source_count > 0 else detail
        self.analysis_source_detail_label.setText(visible_detail)
        quality_note = (
            "\nOmitted samples have a missing or non-finite time or signal value."
            if source.omitted_sample_count > 0
            else ""
        )
        described_detail = f"{visible_detail}{quality_note}"
        self.analysis_source_detail_label.setToolTip(described_detail)
        self.analysis_source_detail_label.setAccessibleDescription(described_detail)
        refresh_detail = f"Rescan tracking results and WAV metadata. {inventory.capitalize()}."
        self.refresh_analysis_sources_button.setToolTip(refresh_detail)
        self.refresh_analysis_sources_button.setAccessibleDescription(refresh_detail)
        if self._analysis_thread is None:
            self._set_action_enabled("analysis.run", source.available)
        else:
            self._set_action_enabled(
                "analysis.run",
                bool(self._analysis_job and not self._analysis_job.cancelled),
            )
        return inventory

    def _clear_analysis_result(self, message: str | None = None, state: str | None = None) -> None:
        if self._analysis_thread is not None:
            source = self._current_analysis_source()
            self._cancel_analysis(
                message or "Signal data changed. Run processing again.",
                state=state or ("dirty" if source.available else "empty"),
            )
            return
        self.analysis_controller.clear()
        self._set_analysis_export_enabled(False)
        source = self._current_analysis_source()
        if message is None:
            if source.available:
                message = "Choose analysis settings, then run FFT or STFT."
                state = state or "ready"
            else:
                message = "No signal source is available. Run tracking or add a WAV file first."
                state = state or "empty"
        self.analysis_result_view.setPlainText(message)
        resolved_state = state or "dirty"
        labels = {
            "empty": "No source",
            "ready": "Ready",
            "dirty": "Needs run",
            "failed": "Failed",
            "canceled": "Canceled",
        }
        self._set_analysis_status(labels.get(resolved_state, "Ready"), resolved_state, message)

    def _mark_analysis_dirty(self) -> None:
        state = str(self.analysis_status_label.property("analysisState") or "")
        if self._analysis_thread is not None:
            self._cancel_analysis("Analysis settings changed. Run processing again.", state="dirty")
            return
        if not self.analysis_controller.has_result and state != "failed":
            return
        self._clear_analysis_result("Analysis settings changed. Run processing again.", state="dirty")

    def _set_analysis_status(self, text: str, state: str, detail: str = "") -> None:
        self.analysis_status_label.setText(text)
        self.analysis_status_label.setProperty("analysisState", state)
        self.analysis_status_label.setToolTip(detail)
        self.analysis_status_label.setAccessibleDescription(detail)
        self.analysis_status_label.style().unpolish(self.analysis_status_label)
        self.analysis_status_label.style().polish(self.analysis_status_label)

    def _set_analysis_export_enabled(self, enabled: bool) -> None:
        self._set_action_enabled("analysis.export_csv", enabled)
        self._set_action_enabled("analysis.export_npz", enabled)

    def _analysis_method_changed(self, method: str) -> None:
        is_stft = method.upper() == "STFT"
        self.analysis_stft_window_spin.setEnabled(is_stft)
        self.analysis_stft_overlap_spin.setEnabled(is_stft)
        if self.analysis_parameters_form is not None:
            self.analysis_parameters_form.setRowVisible(self.analysis_stft_window_spin, is_stft)
            self.analysis_parameters_form.setRowVisible(self.analysis_stft_overlap_spin, is_stft)

    def _selected_analysis_series(self) -> SignalSeries:
        return self.analysis_controller.series_for_source(
            self._current_analysis_source(),
            self.current_task.pipeline.results,
            self.current_task.media_path,
        )

    @staticmethod
    def _tracking_unit_for_key(key: str, state_units: dict[str, str]) -> str:
        return AnalysisController.tracking_unit_for_key(key, state_units)

    def _analysis_config(self) -> AnalysisConfig:
        return self.analysis_controller.make_config(
            method=self.analysis_method_combo.currentText(),
            detrend=self.analysis_detrend_combo.currentText(),
            window=self.analysis_window_combo.currentText(),
            sample_rate_hz=self.analysis_sample_rate_spin.value(),
            frequency_min_hz=self.analysis_freq_min_spin.value(),
            frequency_max_hz=self.analysis_freq_max_spin.value(),
            stft_window_size=self.analysis_stft_window_spin.value(),
            stft_overlap_ratio=self.analysis_stft_overlap_spin.value(),
        )

    def _run_analysis(self) -> None:
        if self._background_tasks.closing:
            return
        if {"media-probe", "project-open"}.intersection(self._background_tasks.active_kinds):
            self.statusBar().showMessage(
                "Finish or cancel project/media loading before starting Signal processing.",
                6000,
            )
            return
        if self._analysis_thread is not None:
            self._cancel_analysis("Processing canceled by user.", state="canceled")
            return
        source = self._current_analysis_source()
        if not source.available:
            self._clear_analysis_result("No signal source is available yet.", state="empty")
            return
        task = self.current_task
        config = self._analysis_config()
        try:
            if source.kind == "tracking":
                # Freeze list membership on the GUI thread, then build numeric arrays
                # in the worker. Result-mutating actions are gated while this snapshot
                # is active, so tracking cannot change the objects being traversed.
                results_snapshot = tuple(task.pipeline.results)
                media_path = task.media_path
                series_loader = (
                    lambda cancel_requested,
                    source=source,
                    results_snapshot=results_snapshot,
                    media_path=media_path: AnalysisController.series_for_source(
                        source,
                        results_snapshot,
                        media_path,
                        cancel_requested=cancel_requested,
                    )
                )
            else:
                media_path = task.media_path
                series_loader = lambda cancel_requested, source=source, media_path=media_path: (
                    AnalysisController.series_for_source(
                        source,
                        [],
                        media_path,
                        cancel_requested=cancel_requested,
                    )
                )
        except Exception as exc:
            self._show_analysis_failure(str(exc))
            return

        self._analysis_coordinator.start(
            AnalysisRequest(
                owner=task,
                source=source,
                config=config,
                series_loader=series_loader,
            )
        )

    def _analysis_started(self, job: AnalysisJob) -> None:
        self.analysis_controller.clear()
        self._set_analysis_export_enabled(False)
        self._set_analysis_busy(True)

    def _set_analysis_busy(self, busy: bool) -> None:
        self._set_analysis_export_enabled(False if busy else self.analysis_controller.has_result)
        if busy:
            self._update_action(
                "analysis.run",
                enabled=True,
                text="Cancel",
                icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop),
                tool_tip="Cancel the active background signal-processing job.",
            )
            self.run_analysis_button.setAccessibleName("Cancel signal processing")
            self._set_action_enabled("tracking.run", False)
            self._set_action_enabled("review.correct", False)
            self._set_action_enabled("review.mark_lost", False)
            self._set_action_enabled("review.undo", False)
            self._set_action_enabled("review.rerun", False)
            job = self._analysis_job
            if job is not None:
                self._analysis_stage_changed(job, "loading")
            return
        source = self._current_analysis_source()
        self._update_action(
            "analysis.run",
            enabled=source.available,
            text="Run processing",
            icon=self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            tool_tip=(
                "Run FFT or STFT on the selected signal source in the background."
            ),
        )
        self.run_analysis_button.setAccessibleName("Run signal processing")
        self._render_tracking_status(self.current_task)

    def _analysis_stage_changed(self, job: AnalysisJob, stage: str) -> None:
        if (
            not self._background_tasks.is_current(job.token)
            or job.cancelled
        ):
            return
        method = job.config.method.upper()
        source_detail = job.source.detail()
        if stage == "loading":
            if job.source.kind == "audio":
                label = "Loading WAV…"
                detail = (
                    f"Stage 1 of 2 · Loading and decoding {job.source.label} ({source_detail}) in the background. "
                    "The window remains interactive."
                )
            else:
                sample_label = f"{job.source.sample_count:,} samples" if job.source.sample_count > 0 else "signal"
                label = f"Preparing {sample_label}…"
                detail = (
                    f"Stage 1 of 2 · Preparing a finite snapshot of {job.source.label} ({source_detail}) "
                    f"in the background before {method}. The window remains interactive."
                )
        else:
            label = f"{method} running…"
            detail = (
                f"Stage 2 of 2 · Running {method} on {job.source.label} ({source_detail}) in the background. "
                "The window remains interactive."
            )
        self.analysis_result_view.setPlainText(detail)
        self._set_analysis_status(label, "running", detail)

    def _cancel_analysis(self, message: str, *, state: str) -> None:
        if not self._analysis_coordinator.cancel(message, state=state):
            return
        self.analysis_controller.clear()
        self._set_analysis_export_enabled(False)
        self._update_action("analysis.run", enabled=False, text="Cancelling…")
        detail = f"{message} Finishing the active background operation safely."
        self.analysis_result_view.setPlainText(detail)
        self._set_analysis_status("Cancelling…", "running", detail)

    def _analysis_completed(self, job: AnalysisJob, run_object: AnalysisRun) -> None:
        source = self._current_analysis_source()
        context_matches = bool(
            job.task is self.current_task
            and source.identity == job.source.identity
            and run_object.owner_token == id(self.current_task)
            and run_object.config == self._analysis_config()
        )
        if not context_matches:
            self._analysis_coordinator.cancel(
                AnalysisCoordinator.STALE_MESSAGE,
                state="dirty" if source.available else "empty",
            )
            return
        self.analysis_controller.accept_run(run_object)
        self.analysis_result_view.setPlainText(run_object.summary)
        self._set_analysis_export_enabled(True)
        label = f"{run_object.kind.upper()} ready"
        detail = f"{run_object.source.label} processed successfully; CSV and NPZ exports are available."
        self._set_analysis_status(label, "complete", detail)
        self._set_analysis_finishing()

    def _analysis_failed(self, job: AnalysisJob, message: str) -> None:
        if job.cancelled:
            return
        self._show_analysis_failure(message)
        self._set_analysis_finishing()

    def _set_analysis_finishing(self) -> None:
        self._update_action(
            "analysis.run",
            enabled=False,
            text="Finishing…",
            tool_tip=(
                "Waiting for the background signal-processing thread to exit safely."
            ),
        )
        self.run_analysis_button.setAccessibleName("Finishing signal processing")

    def _show_analysis_failure(self, message: str) -> None:
        self.analysis_controller.clear()
        self._set_analysis_export_enabled(False)
        detail = f"Processing failed:\n{message}"
        self.analysis_result_view.setPlainText(detail)
        self._set_analysis_status("Failed", "failed", detail)

    def _analysis_thread_finished(self, job: AnalysisJob) -> None:
        if job.cancelled:
            self.analysis_controller.clear()
            self._set_analysis_export_enabled(False)
            labels = {
                "empty": "No source",
                "dirty": "Needs run",
                "canceled": "Canceled",
            }
            self.analysis_result_view.setPlainText(job.cancel_message)
            self._set_analysis_status(
                labels.get(job.cancel_state, "Canceled"),
                job.cancel_state,
                job.cancel_message,
            )
        if not self._background_tasks.closing:
            self._set_analysis_busy(False)

    def _analysis_idle(self) -> None:
        self._schedule_close_if_workers_stopped()

    def _export_analysis_csv(self) -> None:
        if not self.analysis_controller.has_result:
            QMessageBox.information(self, "Export analysis", "Run FFT or STFT before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export analysis CSV", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            self.analysis_controller.export_csv(path)
        except Exception as exc:
            QMessageBox.warning(self, "Export analysis", f"Could not export CSV:\n{exc}")
            return
        self.statusBar().showMessage(f"Exported analysis CSV: {path}", 6000)

    def _export_analysis_npz(self) -> None:
        if not self.analysis_controller.has_result:
            QMessageBox.information(self, "Export analysis", "Run FFT or STFT before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export analysis NPZ", "", "NumPy archives (*.npz);;All files (*)")
        if not path:
            return
        try:
            self.analysis_controller.export_npz(path)
        except Exception as exc:
            QMessageBox.warning(self, "Export analysis", f"Could not export NPZ:\n{exc}")
            return
        self.statusBar().showMessage(f"Exported analysis NPZ: {path}", 6000)

    @staticmethod
    def _format_fft_summary(series: SignalSeries, result: FFTResult) -> str:
        return AnalysisController.format_fft_summary(series, result)

    @staticmethod
    def _format_stft_summary(series: SignalSeries, result: STFTResult) -> str:
        return AnalysisController.format_stft_summary(series, result)
    def _schedule_close_if_workers_stopped(self) -> None:
        if self._background_tasks.ready_to_close:
            QTimer.singleShot(0, self.close)

    def _resume_after_cancelled_close(self) -> None:
        self._background_tasks.cancel_close()
        self._close_requested_by_user = False
        root = self.centralWidget()
        if root is not None:
            root.setEnabled(True)
        if self._tracking_thread is None:
            self._set_tracking_busy(False)
        if self._analysis_thread is None:
            self._set_analysis_busy(False)
        if not self.analysis_workspace_controller.busy:
            self.fit_panel.apply_state(self.analysis_workspace_controller.state)
        if self._media_probe_thread is None and self._project_open_thread is None:
            self._set_media_probe_busy(False)
        self._restore_task_view_after_aborted_transition()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._background_tasks.closing:
            self._close_requested_by_user = True
        should_confirm = self._close_requested_by_user
        if self._background_tasks.idle and should_confirm:
            if not self._confirm_project_transition("closing Neo-Tracker"):
                self._resume_after_cancelled_close()
                self.statusBar().showMessage(
                    "Close canceled. Current project and editor work are still available.",
                    6000,
                )
                event.ignore()
                return
        active_kinds = self._background_tasks.begin_close()
        if active_kinds:
            root = self.centralWidget()
            if root is not None:
                root.setEnabled(False)
            self.statusBar().showMessage("Closing… Finishing background operations safely.")
            if "tracking" in active_kinds:
                self._cancel_tracking()
            if "analysis" in active_kinds:
                self._cancel_analysis("Closing Neo-Tracker; signal processing was canceled.", state="canceled")
            if "kinematics-fit" in active_kinds:
                self.analysis_workspace_controller.cancel(
                    "Closing Neo-Tracker; the active fit was canceled."
                )
            if "kinematics-analysis" in active_kinds:
                self._kinematics_workspace_coordinator.cancel()
            if "media-probe" in active_kinds:
                self._cancel_media_probe()
            if "project-open" in active_kinds:
                self._cancel_project_open()
            if "review-response" in active_kinds:
                self._cancel_review_response_requests(clear_pending=True)
            if "preview-decode" in active_kinds and hasattr(self, "_cancel_preview_decode"):
                self._cancel_preview_decode(clear_pending=True)
            event.ignore()
            return
        self._close_requested_by_user = False
        self._stop_playback()
        self.scratch_task.close_reader()
        for task in self.tasks:
            task.close_reader()
        self._retired_project_tasks_timer.stop()
        for task in self._retired_project_tasks:
            task.close_reader()
        self._retired_project_tasks.clear()
        self._analysis_coordinator.close()
        self._kinematics_workspace_coordinator.close()
        self.analysis_workspace_controller.close()
        self._review_response_coordinator.close()
        self._preview_coordinator.close()
        self._playback_coordinator.close()
        if self._selection_unsubscribe is not None:
            self._selection_unsubscribe()
            self._selection_unsubscribe = None
        self._application_shell.close()
        super().closeEvent(event)

    @staticmethod
    def _pipeline_steps(pipeline: TrackingPipeline) -> list[PipelineStep]:
        return [
            PipelineStep("ROI", pipeline.roi.to_config()["type"], "Limits the valid image region before detection."),
            PipelineStep(
                "Mapping",
                pipeline.coordinate_model.to_config()["type"],
                "Maps pixels into the experiment coordinate space.",
            ),
            PipelineStep(
                "Observation",
                pipeline.observation_model.to_config()["type"],
                "Extracts candidate measurements from each frame.",
            ),
            PipelineStep("State", pipeline.state_model.to_config()["type"], "Defines the tracked physical quantity."),
            PipelineStep("Motion", pipeline.motion_model.to_config()["type"], "Applies continuity and physics priors."),
            PipelineStep("Filter", pipeline.tracker_filter.to_config()["type"], "Smooths or predicts the state estimate."),
            PipelineStep("Export", "csv/json/overlays", "Produces tables and visualization-ready overlays."),
        ]


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = NeoTrackerWindow(
        physics_export_directory_picker=lambda parent: QFileDialog.getExistingDirectory(
            parent,
            "Export physics analysis",
            "",
        )
    )
    window.show()
    return int(app.exec())
