from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from neo_tracker.application.task_supervisor import BackgroundTaskToken

if TYPE_CHECKING:
    from neo_tracker.analysis import AnalysisConfig
    from neo_tracker.core import TrackerResult
    from neo_tracker.media import MediaIdentity
    from neo_tracker.ui.analysis_controller import AnalysisRun, AnalysisSource
    from neo_tracker.ui.isolated_media import PreviewDecoderSession
    from neo_tracker.ui.preview_decode_worker import PreviewDecodeRequest, PreviewDecodeResult
    from neo_tracker.ui.project_controller import DesktopTask


@dataclass
class TrackingJob:
    token: BackgroundTaskToken
    task: DesktopTask
    mode: str
    start_frame: int
    prefix: list[TrackerResult] = field(default_factory=list)
    anchor_frame: int | None = None
    completed: int = 0
    cancelled: bool = False
    failed: bool = False
    ended_early: bool = False
    completion_note: str = ""
    started_at: str = ""
    started_monotonic: float = 0.0
    pipeline_config: dict[str, object] = field(default_factory=dict)
    previous_results: list[TrackerResult] = field(default_factory=list)
    previous_edit_history: list[dict[str, object]] = field(default_factory=list)
    previous_tracking_outcome: str = ""
    previous_tracking_note: str = ""
    previous_analysis_run: AnalysisRun | None = None
    previous_result_state_restored: bool = False
    result_replacement_committed: bool = False
    superseded_edit_count: int = 0
    tracking_elapsed_s: float = 0.0
    tracking_input_s: float = 0.0
    tracking_processing_s: float = 0.0
    tracking_peak_debug_bytes: int = 0
    tracking_prefetch_frames: int = 0
    source_path: str = ""
    source_identity: MediaIdentity | None = None
    source_changed: bool = False
    run_outcome: str = ""
    run_note: str = ""
    processed_frames: int = 0


@dataclass
class AnalysisJob:
    token: BackgroundTaskToken
    task: DesktopTask
    source: AnalysisSource
    config: AnalysisConfig
    cancelled: bool = False
    cancel_message: str = "Processing canceled."
    cancel_state: str = "canceled"


@dataclass
class MediaProbeJob:
    token: BackgroundTaskToken
    paths: tuple[str, ...]
    pipeline_key: str
    cancelled: bool = False
    completed: bool = False
    failure_detail: str = ""


@dataclass
class ProjectOpenJob:
    token: BackgroundTaskToken
    path: Path
    cancelled: bool = False
    completed: bool = False
    failure_detail: str = ""


@dataclass
class ProjectSaveJob:
    token: BackgroundTaskToken
    path: Path
    content_revision: int
    completed: bool = False
    failure_detail: str = ""


@dataclass
class ReviewResponseJob:
    token: BackgroundTaskToken
    request: ReviewResponseRequest
    cancelled: bool = False
    completed: bool = False
    failure_detail: str = ""


@dataclass
class PreviewDecodeJob:
    token: BackgroundTaskToken
    task: DesktopTask
    request: PreviewDecodeRequest
    session: PreviewDecoderSession
    cancelled: bool = False
    result: PreviewDecodeResult | None = None
    failure_detail: str = ""
