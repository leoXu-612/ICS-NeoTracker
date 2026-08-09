from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from neo_tracker.ui.action_registry import ActionRegistry
from neo_tracker.ui.project_controller import DesktopTask

if TYPE_CHECKING:
    from PySide6.QtCore import QThread, QTimer

    from neo_tracker.application.job_state import (
        AnalysisJob,
        MediaProbeJob,
        PreviewDecodeJob,
        ProjectOpenJob,
        ProjectSaveJob,
        ReviewResponseJob,
        TrackingJob,
    )
    from neo_tracker.ui.isolated_media import PreviewDecoderSession
    from neo_tracker.ui.playback_controller import PlaybackClock
    from neo_tracker.ui.preview_decode_worker import PreviewDecodeRequest, PreviewDecodeResult
    from neo_tracker.ui.review_response import ReviewResponseRequest, ReviewResponseService


class CoordinatorCompatibilityMixin:
    """Read-only legacy/test views over Application-owned coordinator state."""

    @property
    def _close_when_workers_stop(self) -> bool:
        return self._background_tasks.closing

    @property
    def play_timer(self) -> QTimer:
        return self._playback_coordinator.timer

    @property
    def playback_clock(self) -> PlaybackClock:
        return self._playback_coordinator.clock

    @playback_clock.setter
    def playback_clock(self, value: PlaybackClock) -> None:
        self._playback_coordinator.clock = value

    @property
    def _media_probe_thread(self) -> QThread | None:
        return self._media_import_coordinator.thread

    @property
    def _media_probe_worker(self) -> object | None:
        return self._media_import_coordinator.worker

    @property
    def _media_probe_job(self) -> MediaProbeJob | None:
        return self._media_import_coordinator.job

    @property
    def _project_open_thread(self) -> QThread | None:
        return self._project_io_coordinator.open_thread

    @property
    def _project_open_worker(self) -> object | None:
        return self._project_io_coordinator.open_worker

    @property
    def _project_open_job(self) -> ProjectOpenJob | None:
        return self._project_io_coordinator.open_job

    @property
    def _project_save_thread(self) -> QThread | None:
        return self._project_io_coordinator.save_thread

    @property
    def _project_save_worker(self) -> object | None:
        return self._project_io_coordinator.save_worker

    @property
    def _project_save_job(self) -> ProjectSaveJob | None:
        return self._project_io_coordinator.save_job

    @property
    def _tracking_thread(self) -> QThread | None:
        return self._tracking_coordinator.thread

    @property
    def _tracking_worker(self) -> object | None:
        return self._tracking_coordinator.worker

    @property
    def _tracking_job(self) -> TrackingJob | None:
        return self._tracking_coordinator.job

    @property
    def _analysis_thread(self) -> QThread | None:
        return self._analysis_coordinator.thread

    @property
    def _analysis_worker(self) -> object | None:
        return self._analysis_coordinator.worker

    @property
    def _analysis_job(self) -> AnalysisJob | None:
        return self._analysis_coordinator.job

    @property
    def _review_response_thread(self) -> QThread | None:
        return self._review_response_coordinator.thread

    @property
    def _review_response_worker(self) -> object | None:
        return self._review_response_coordinator.worker

    @property
    def _review_response_job(self) -> ReviewResponseJob | None:
        return self._review_response_coordinator.job

    @property
    def _pending_review_response(self) -> ReviewResponseRequest | None:
        return self._review_response_coordinator.pending_request

    @property
    def _review_responses(self) -> ReviewResponseService:
        return self._review_response_coordinator.service

    @property
    def _preview_decode_thread(self) -> QThread | None:
        return self._preview_coordinator.thread

    @property
    def _preview_decode_worker(self) -> object | None:
        return self._preview_coordinator.worker

    @property
    def _preview_decode_job(self) -> PreviewDecodeJob | None:
        return self._preview_coordinator.job

    @_preview_decode_job.setter
    def _preview_decode_job(self, value: PreviewDecodeJob | None) -> None:
        self._preview_coordinator.replace_job_for_testing(value)

    @property
    def _pending_preview_decode(self) -> tuple[DesktopTask, PreviewDecodeRequest] | None:
        pending = self._preview_coordinator.pending_request
        if pending is None or not isinstance(pending.owner, DesktopTask):
            return None
        return pending.owner, pending.decode_request

    @property
    def _preview_decode_cache(self) -> PreviewDecodeResult | None:
        return self._preview_coordinator.cache

    @_preview_decode_cache.setter
    def _preview_decode_cache(self, value: PreviewDecodeResult | None) -> None:
        self._preview_coordinator.replace_cache_for_testing(value)

    @property
    def _preview_decoder_session(self) -> PreviewDecoderSession | None:
        return self._preview_coordinator.session

    @_preview_decoder_session.setter
    def _preview_decoder_session(self, value: PreviewDecoderSession | None) -> None:
        self._preview_coordinator.replace_session_for_testing(value)


@dataclass(frozen=True)
class ActionBinding:
    key: str
    button_attribute: str
    method_name: str
    menu: str
    argument: int | None = None


ACTION_BINDINGS = (
    ActionBinding("media.add", "add_media_button", "_add_media", "file"),
    ActionBinding("project.open", "open_project_button", "_open_project", "file"),
    ActionBinding("project.save", "save_project_button", "_save_project", "file"),
    ActionBinding("tracking.export_csv", "export_tracking_csv_button", "_export_tracking_csv", "file"),
    ActionBinding("tracking.export_report", "export_report_button", "_export_report", "file"),
    ActionBinding("analysis.export_csv", "analysis_export_csv_button", "_export_analysis_csv", "file"),
    ActionBinding("analysis.export_npz", "analysis_export_npz_button", "_export_analysis_npz", "file"),
    ActionBinding(
        "physics.export",
        "export_physics_analysis_button",
        "_export_physics_analysis",
        "file",
    ),
    ActionBinding("tracking.run", "run_tracking_button", "_run_tracking", "run"),
    ActionBinding("analysis.run", "run_analysis_button", "_run_analysis", "run"),
    ActionBinding("physics.velocity", "create_velocity_button", "_create_physics_velocity", "run"),
    ActionBinding(
        "physics.acceleration",
        "create_acceleration_button",
        "_create_physics_acceleration",
        "run",
    ),
    ActionBinding("physics.smooth", "smooth_series_button", "_smooth_physics_series", "run"),
    ActionBinding("physics.fit", "fit_model_button", "_show_physics_fit", "run"),
    ActionBinding("playback.previous", "previous_frame_button", "_step_preview_frame", "run", -1),
    ActionBinding("playback.toggle", "play_button", "_toggle_playback", "run"),
    ActionBinding("playback.next", "next_frame_button", "_step_preview_frame", "run", 1),
    ActionBinding("review.correct", "correct_point_button", "_start_manual_correction", "review"),
    ActionBinding("review.mark_lost", "mark_lost_button", "_mark_current_result_lost", "review"),
    ActionBinding("review.rerun", "rerun_after_button", "_rerun_after_current_result", "review"),
    ActionBinding("review.jump", "jump_to_result_button", "_jump_to_selected_result", "review"),
    ActionBinding("physics.residual", "show_residual_button", "_toggle_physics_residual", "review"),
    ActionBinding("view.canvas_focus", "canvas_focus_button", "_toggle_canvas_focus", "review"),
)

PRIMARY_BUTTON_ATTRIBUTES = {
    binding.key: binding.button_attribute for binding in ACTION_BINDINGS
}


def bind_primary_actions(owner: Any, registry: ActionRegistry) -> None:
    menus = {
        "file": owner.menuBar().addMenu("&File"),
        "run": owner.menuBar().addMenu("&Run"),
        "review": owner.menuBar().addMenu("Re&view"),
    }
    for key, menu in menus.items():
        menu.setObjectName(f"{key}Menu")

    for binding in ACTION_BINDINGS:
        button = getattr(owner, binding.button_attribute)

        def invoke(binding: ActionBinding = binding) -> Any:
            method = getattr(owner, binding.method_name)
            if binding.argument is None:
                return method()
            return method(binding.argument)

        action = registry.register(binding.key, button.text(), invoke)
        registry.bind_button(binding.key, button)
        menus[binding.menu].addAction(action)
