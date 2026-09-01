from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from math import isfinite
from numbers import Real
from pathlib import Path
from uuid import uuid4

import numpy as np

from neo_tracker.config import apply_pipeline_config, validate_roi_config
from neo_tracker.coordinates import (
    AnnularCoordinate,
    ImageCoordinate,
    LinearWorldCoordinate,
    PathCoordinate,
    PolarCoordinate,
)
from neo_tracker.core import TrackerResult, TrackingPipeline
from neo_tracker.media import MediaIdentity, MediaInfo, MediaReader, probe_media
from neo_tracker.presets import PresetDescriptor
from neo_tracker.project import (
    AnalysisWorkspaceSnapshot,
    ProjectTaskSnapshot,
    TrackingRunRecord,
)
from neo_tracker.roi import AnnularROI, CircularROI, CurveBandROI, PolygonROI, RectangularROI
from neo_tracker.states import FrontState, ScalarState, XYState


@dataclass
class CalibrationRod:
    start_px: tuple[float, float] | None = None
    end_px: tuple[float, float] | None = None
    real_length: float | None = None
    unit: str = "cm"
    y_positive: str = "up"

    def label(self) -> str:
        if self.start_px is None or self.end_px is None or self.real_length is None:
            return "Not set"
        return f"{self.real_length:g} {self.unit}: {self.start_px} -> {self.end_px}"

    def pixel_length(self) -> float:
        if self.start_px is None or self.end_px is None:
            return 0.0
        start = np.asarray(self.start_px, dtype=float)
        end = np.asarray(self.end_px, dtype=float)
        return float(np.linalg.norm(end - start))

    def unit_per_pixel(self) -> float | None:
        pixel_length = self.pixel_length()
        if self.real_length is None or not isfinite(self.real_length) or self.real_length <= 0.0:
            return None
        if not isfinite(pixel_length) or pixel_length <= 1e-12:
            return None
        return float(self.real_length / pixel_length)


@dataclass
class DesktopTask:
    media_path: str | None
    pipeline_key: str
    pipeline: TrackingPipeline
    preview_frame_index: int = 0
    roi: dict[str, object] | None = None
    calibration_rod: CalibrationRod | None = None
    media_info: MediaInfo | None = None
    media_reader: MediaReader | None = None
    edit_history: list[dict[str, object]] = field(default_factory=list)
    tracking_outcome: str = ""
    tracking_note: str = ""
    run_history: list[TrackingRunRecord] = field(default_factory=list)
    saved_media_info: MediaInfo | None = None
    pending_media_relink: tuple[MediaInfo, MediaRelinkAssessment] | None = None
    media_identity_requires_review: bool = False
    task_id: str = field(default_factory=lambda: str(uuid4()))
    analysis_workspace: AnalysisWorkspaceSnapshot = field(default_factory=AnalysisWorkspaceSnapshot)
    results_generation: int = 0

    def title(self) -> str:
        if self.media_path is None:
            return "Untitled experiment"
        return Path(self.media_path).name

    def close_reader(self) -> None:
        if self.media_reader is not None:
            self.media_reader.close()
            self.media_reader = None

    def mark_results_changed(self) -> int:
        """Invalidate every runtime analysis derived from the current Results."""

        self.results_generation += 1
        return self.results_generation


@dataclass(frozen=True)
class MediaRelinkAssessment:
    state: str
    summary: str
    differences: tuple[str, ...] = ()
    can_apply: bool = False
    clear_results: bool = False
    identity_state: str = "unavailable"

    @property
    def requires_review(self) -> bool:
        """Whether applying this assessment needs an explicit, auditable decision."""

        if self.state in {"mismatch", "incompatible"}:
            return True
        if not self.clear_results:
            return False
        return self.identity_state in {"unverified", "unavailable", "sampled"}


def first_expected_config_diff(
    expected: object,
    actual: object,
    path: str = "$",
) -> tuple[str, object, object] | None:
    """Return the first requested field that a permissive config loader changed."""

    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, expected_value in expected.items():
            child_path = f"{path}.{key}"
            if key not in actual:
                return child_path, expected_value, "<missing>"
            diff = first_expected_config_diff(expected_value, actual[key], child_path)
            if diff is not None:
                return diff
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return path, expected, actual
        for index, (expected_value, actual_value) in enumerate(zip(expected, actual)):
            diff = first_expected_config_diff(expected_value, actual_value, f"{path}[{index}]")
            if diff is not None:
                return diff
        return None
    if isinstance(expected, bool) != isinstance(actual, bool) or expected != actual:
        return path, expected, actual
    return None


def first_config_diff(
    expected: object,
    actual: object,
) -> tuple[str, object, object] | None:
    """Return the first differing field, including unexpected actual fields."""

    diff = first_expected_config_diff(expected, actual)
    if diff is not None:
        return diff
    reverse = first_expected_config_diff(actual, expected)
    if reverse is None:
        return None
    path, actual_value, expected_value = reverse
    return path, expected_value, actual_value


def short_config_value(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 80 else f"{text[:77]}..."


class ProjectTaskController:
    """UI-independent creation and persistence rules for desktop tasks."""

    def __init__(
        self,
        registry: Mapping[str, PresetDescriptor],
        default_pipeline_key: str,
        media_probe: Callable[[str], MediaInfo] = probe_media,
    ) -> None:
        if default_pipeline_key not in registry:
            raise ValueError(f"unknown default pipeline {default_pipeline_key!r}")
        self.registry = registry
        self.default_pipeline_key = default_pipeline_key
        self.media_probe = media_probe

    def new_task(
        self,
        media_path: str | None,
        pipeline_key: str,
        *,
        media_info: MediaInfo | None = None,
    ) -> DesktopTask:
        if pipeline_key not in self.registry:
            pipeline_key = self.default_pipeline_key
        pipeline = self.registry[pipeline_key].factory()
        resolved_media_info = media_info
        if resolved_media_info is None and media_path:
            resolved_media_info = self.media_probe(media_path)
        return DesktopTask(
            media_path=media_path,
            pipeline_key=pipeline_key,
            pipeline=pipeline,
            calibration_rod=CalibrationRod(),
            media_info=resolved_media_info,
            saved_media_info=resolved_media_info,
        )

    @classmethod
    def assess_media_relink(
        cls,
        saved: MediaInfo | None,
        candidate: MediaInfo,
        *,
        has_result_state: bool,
    ) -> MediaRelinkAssessment:
        if not candidate.available:
            detail = candidate.error.strip() or "The selected media cannot be opened."
            return MediaRelinkAssessment("unavailable", detail)

        if saved is not None and saved.kind and candidate.kind != saved.kind:
            return MediaRelinkAssessment(
                "incompatible",
                f"Saved source is {saved.kind}; selected file is {candidate.kind}.",
            )

        differences = cls._media_relink_differences(saved, candidate)
        identity_state = cls._media_identity_state(saved, candidate)
        identity_difference = cls._media_identity_difference(saved, candidate, identity_state)
        if identity_difference is not None:
            differences.insert(0, identity_difference)
        if differences:
            clear_results = bool(has_result_state)
            summary = (
                "Apply clears current results, manual edits, and outcome details; run history stays."
                if clear_results
                else "This task has no current results or edits, so the replacement can be applied."
            )
            return MediaRelinkAssessment(
                "mismatch",
                summary,
                tuple(differences),
                can_apply=True,
                clear_results=clear_results,
                identity_state=identity_state,
            )

        if identity_state in {"full", "sampled"}:
            verification = "Full-file SHA-256" if identity_state == "full" else "Bounded sampled SHA-256"
            if identity_state == "sampled" and has_result_state:
                sampled = candidate.source_identity
                coverage = (
                    f"{sampled.sampled_bytes:,} of {sampled.size_bytes:,} bytes"
                    if sampled is not None
                    else "only bounded samples"
                )
                return MediaRelinkAssessment(
                    "match",
                    (
                        "Bounded sampled SHA-256 source identity matches, but exact byte "
                        "equality is not verified. Applying relink clears current results, "
                        "manual edits, and outcome details; run history is kept."
                    ),
                    (f"Sampled digest covers {coverage}; exact byte equality is not established.",),
                    can_apply=True,
                    clear_results=True,
                    identity_state=identity_state,
                )
            return MediaRelinkAssessment(
                "match",
                f"{verification} source identity matches the saved project.",
                can_apply=True,
                identity_state=identity_state,
            )

        if identity_state == "unverified":
            return MediaRelinkAssessment(
                "unverified",
                "The saved project has a source digest, but the selected file could not be verified."
                + (
                    " Applying it will clear current results, manual edits, and outcome details; "
                    "run history is kept."
                    if has_result_state
                    else " The replacement can be applied because this task has no current results or edits."
                ),
                can_apply=True,
                clear_results=bool(has_result_state),
                identity_state=identity_state,
            )

        if (
            identity_state == "unavailable"
            and saved is not None
            and saved.source_identity is None
            and has_result_state
        ):
            verification_detail = (
                "neither the older project nor the selected file has a comparable source digest"
                if candidate.source_identity is None
                else "the older project has no source digest to compare with the selected file"
            )
            return MediaRelinkAssessment(
                "unverified",
                f"Saved metadata matches, but {verification_detail}. "
                "Applying it will clear current results, manual "
                "edits, and outcome details; run history is kept.",
                can_apply=True,
                clear_results=True,
                identity_state=identity_state,
            )

        known_metrics = cls._media_relink_has_comparable_metrics(saved)
        if known_metrics:
            return MediaRelinkAssessment(
                "match",
                "Saved metadata matches the selected file. No comparable source digest is available.",
                can_apply=True,
                identity_state=identity_state,
            )

        return MediaRelinkAssessment(
            "unverified",
            "No saved media metrics are available for comparison."
            + (
                " Applying this replacement will clear current results, manual edits, and outcome details; "
                "run history is kept."
                if has_result_state
                else " The replacement can be applied because this task has no current results or edits."
            ),
            can_apply=True,
            clear_results=bool(has_result_state),
            identity_state=identity_state,
        )

    @staticmethod
    def _media_identity_state(saved: MediaInfo | None, candidate: MediaInfo) -> str:
        saved_identity = saved.source_identity if saved is not None else None
        candidate_identity = candidate.source_identity
        if saved_identity is not None and candidate_identity is None:
            return "unverified"
        if saved_identity is None or candidate_identity is None:
            return "unavailable"
        if saved_identity.size_bytes != candidate_identity.size_bytes:
            return "mismatch"
        if saved_identity.strategy != candidate_identity.strategy:
            return "unavailable"
        if saved_identity.sha256 != candidate_identity.sha256:
            return "mismatch"
        return "full" if saved_identity.complete else "sampled"

    @staticmethod
    def _media_identity_difference(
        saved: MediaInfo | None,
        candidate: MediaInfo,
        identity_state: str,
    ) -> str | None:
        if identity_state != "mismatch" or saved is None:
            return None
        saved_identity = saved.source_identity
        candidate_identity = candidate.source_identity
        if saved_identity is None or candidate_identity is None:
            return None
        if saved_identity.size_bytes != candidate_identity.size_bytes:
            return f"File size {saved_identity.size_bytes:,} → {candidate_identity.size_bytes:,} bytes"
        return "Source content digest differs"

    @staticmethod
    def relink_media(
        task: DesktopTask,
        media_path: str,
        media_info: MediaInfo,
        *,
        clear_results: bool,
    ) -> None:
        if not media_info.available:
            raise ValueError("replacement media must be available")
        task.close_reader()
        task.media_path = str(media_path)
        task.media_info = media_info
        task.saved_media_info = media_info
        task.pending_media_relink = None
        task.media_identity_requires_review = False
        if media_info.kind == "video" and media_info.frame_count > 0:
            task.preview_frame_index = min(max(0, task.preview_frame_index), media_info.frame_count - 1)
        else:
            task.preview_frame_index = 0
        if clear_results:
            task.pipeline.reset()
            task.mark_results_changed()
            task.edit_history.clear()
            task.tracking_outcome = ""
            task.tracking_note = ""

    @classmethod
    def _media_relink_differences(
        cls,
        saved: MediaInfo | None,
        candidate: MediaInfo,
    ) -> list[str]:
        if saved is None:
            return []
        differences: list[str] = []
        if candidate.kind == "audio":
            cls._append_float_media_difference(
                differences,
                "Sample rate",
                saved.sample_rate_hz or saved.fps,
                candidate.sample_rate_hz or candidate.fps,
                "Hz",
            )
            cls._append_int_media_difference(differences, "Samples", saved.frame_count, candidate.frame_count)
            cls._append_int_media_difference(differences, "Channels", saved.channels, candidate.channels)
        else:
            if saved.width > 0 and saved.height > 0 and candidate.width > 0 and candidate.height > 0:
                if (saved.width, saved.height) != (candidate.width, candidate.height):
                    differences.append(
                        f"Resolution {saved.width}×{saved.height} → {candidate.width}×{candidate.height}"
                    )
            cls._append_int_media_difference(differences, "Frames", saved.frame_count, candidate.frame_count)
            cls._append_float_media_difference(differences, "FPS", saved.fps, candidate.fps)
        return differences

    @staticmethod
    def _append_int_media_difference(
        differences: list[str],
        label: str,
        saved: int,
        candidate: int,
    ) -> None:
        if saved > 0 and candidate > 0 and saved != candidate:
            differences.append(f"{label} {saved} → {candidate}")

    @staticmethod
    def _append_float_media_difference(
        differences: list[str],
        label: str,
        saved: float,
        candidate: float,
        suffix: str = "",
    ) -> None:
        if saved <= 0.0 or candidate <= 0.0:
            return
        tolerance = max(1e-6, abs(saved) * 1e-5)
        if abs(saved - candidate) > tolerance:
            unit = f" {suffix}" if suffix else ""
            differences.append(f"{label} {saved:g}{unit} → {candidate:g}{unit}")

    @staticmethod
    def _media_relink_has_comparable_metrics(info: MediaInfo | None) -> bool:
        if info is None:
            return False
        if info.kind == "audio":
            return bool((info.sample_rate_hz > 0 or info.fps > 0) and info.frame_count > 0 and info.channels > 0)
        return bool(info.fps > 0 and info.frame_count > 0 and info.width > 0 and info.height > 0)

    def snapshot_from_task(self, task: DesktopTask) -> ProjectTaskSnapshot:
        persisted_media_info = (
            task.saved_media_info
            if task.media_identity_requires_review and task.saved_media_info is not None
            else task.media_info
        )
        return ProjectTaskSnapshot(
            media_path=task.media_path,
            pipeline_key=task.pipeline_key,
            preview_frame_index=int(task.preview_frame_index),
            media_info=self.media_info_to_dict(persisted_media_info),
            roi=task.pipeline.roi.to_config() if isinstance(task.roi, dict) else None,
            calibration_rod=self.calibration_rod_to_dict(task.calibration_rod),
            pipeline_config=task.pipeline.to_config(),
            results=list(task.pipeline.results),
            edit_history=[dict(entry) for entry in task.edit_history],
            tracking_outcome=task.tracking_outcome,
            tracking_note=task.tracking_note,
            run_history=list(task.run_history),
            task_id=task.task_id,
            analysis_workspace=task.analysis_workspace,
        )

    def task_from_snapshot(
        self,
        snapshot: ProjectTaskSnapshot,
        *,
        live_media_info: MediaInfo | None = None,
        persisted_debug_is_compact: bool = False,
    ) -> DesktopTask:
        if snapshot.pipeline_key not in self.registry:
            raise ValueError(f"unknown project task pipeline {snapshot.pipeline_key!r}")
        pipeline_key = snapshot.pipeline_key
        task = self.new_task(
            snapshot.media_path,
            pipeline_key,
            media_info=live_media_info,
        )
        task.task_id = snapshot.task_id
        task.analysis_workspace = snapshot.analysis_workspace
        live_media_info = task.media_info
        saved_media_info = self.media_info_from_snapshot(snapshot.media_info, None)
        task.saved_media_info = saved_media_info or live_media_info
        has_result_state = bool(
            snapshot.results
            or snapshot.edit_history
            or snapshot.tracking_outcome
            or snapshot.tracking_note
        )
        if live_media_info is not None and live_media_info.available and saved_media_info is not None:
            assessment = self.assess_media_relink(
                saved_media_info,
                live_media_info,
                has_result_state=has_result_state,
            )
            requires_review = assessment.requires_review
            if requires_review:
                detail = (
                    "Media at the saved path differs from the project snapshot. "
                    "Review the staged source before using results or preview."
                )
                task.media_info = replace(saved_media_info, available=False, error=detail)
                task.pending_media_relink = (live_media_info, assessment)
                task.media_identity_requires_review = True
            else:
                task.media_info = live_media_info
        else:
            task.media_info = self.media_info_from_snapshot(snapshot.media_info, live_media_info)
        task.preview_frame_index = int(snapshot.preview_frame_index)
        if isinstance(snapshot.pipeline_config, dict):
            if "roi" in snapshot.pipeline_config:
                roi_error = validate_roi_config(snapshot.pipeline_config.get("roi"))
                if roi_error is not None:
                    raise ValueError(f"project pipeline $.roi is invalid: {roi_error}")
            apply_pipeline_config(task.pipeline, snapshot.pipeline_config)
            applied = task.pipeline.to_config()
            config_diff = first_expected_config_diff(snapshot.pipeline_config, applied)
            if config_diff is not None:
                path, expected, actual = config_diff
                raise ValueError(
                    f"project pipeline {path} did not apply exactly: "
                    f"expected {short_config_value(expected)}, got {short_config_value(actual)}"
                )
            roi_error = validate_roi_config(applied.get("roi"))
            if roi_error is not None:
                raise ValueError(f"project pipeline $.roi is invalid: {roi_error}")
        if snapshot.roi is not None:
            roi_error = validate_roi_config(snapshot.roi)
            if roi_error is not None:
                raise ValueError(f"project task $.roi is invalid: {roi_error}")
            if not self.apply_roi_config_to_task(task, snapshot.roi):
                raise ValueError("project task $.roi could not be applied")
        rod = self.calibration_rod_from_dict(snapshot.calibration_rod)
        if rod is not None:
            if (
                "y_positive" not in snapshot.calibration_rod
                and isinstance(task.pipeline.coordinate_model, LinearWorldCoordinate)
            ):
                rod.y_positive = task.pipeline.coordinate_model.y_positive
            self.apply_calibration_rod_to_task(task, rod)
        task.pipeline.results = list(snapshot.results)
        if persisted_debug_is_compact:
            # Project JSON never contains live ndarray payloads: persistence
            # replaces those with compact metadata. Avoid rescanning every
            # historical result solely to rediscover that invariant.
            task.pipeline._debug_retained_results.clear()
            task.pipeline._debug_retained_bytes = 0
            task.pipeline._mark_debug_cache_current()
        else:
            task.pipeline.rebuild_debug_history()
        task.edit_history = [dict(entry) for entry in snapshot.edit_history]
        task.tracking_outcome = snapshot.tracking_outcome
        task.tracking_note = snapshot.tracking_note
        task.run_history = list(snapshot.run_history)
        return task

    @staticmethod
    def media_info_to_dict(info: MediaInfo | None) -> dict[str, object] | None:
        if info is None:
            return None
        return {
            "kind": info.kind,
            "available": bool(info.available),
            "fps": float(info.fps),
            "frame_count": int(info.frame_count),
            "width": int(info.width),
            "height": int(info.height),
            "duration_s": float(info.duration_s),
            "sample_rate_hz": float(info.sample_rate_hz),
            "channels": int(info.channels),
            "sample_width_bytes": int(info.sample_width_bytes),
            "error": info.error,
            "source_identity": info.source_identity.to_dict() if info.source_identity is not None else None,
        }

    @classmethod
    def media_info_from_snapshot(
        cls,
        snapshot_info: dict[str, object] | None,
        live_info: MediaInfo | None,
    ) -> MediaInfo | None:
        if not isinstance(snapshot_info, dict):
            return live_info
        if live_info is not None and live_info.available:
            return live_info

        kind = cls._snapshot_string(
            snapshot_info.get("kind"),
            live_info.kind if live_info else "video",
            "kind",
        )
        if kind not in {"audio", "video"}:
            raise ValueError("project media_info kind must be audio or video")
        sample_rate = cls._snapshot_float(
            snapshot_info.get("sample_rate_hz"), 0.0, "sample_rate_hz"
        )
        fps = cls._snapshot_float(
            snapshot_info.get("fps"),
            sample_rate if kind == "audio" else 0.0,
            "fps",
        )
        if kind == "audio":
            if sample_rate <= 0.0:
                sample_rate = fps
            if fps <= 0.0:
                fps = sample_rate
        snapshot_error = cls._snapshot_string(
            snapshot_info.get("error"), "", "error"
        )
        error = live_info.error if live_info is not None and live_info.error else snapshot_error
        available = snapshot_info.get("available", False)
        if not isinstance(available, bool):
            raise ValueError("project media_info available must be a boolean")
        source_identity_data = snapshot_info.get("source_identity")
        source_identity = MediaIdentity.from_dict(source_identity_data)
        if source_identity_data is not None and source_identity is None:
            raise ValueError("project media_info source_identity must be a valid media identity")
        return MediaInfo(
            fps=fps,
            frame_count=cls._snapshot_int(
                snapshot_info.get("frame_count"), 0, "frame_count"
            ),
            width=cls._snapshot_int(snapshot_info.get("width"), 0, "width"),
            height=cls._snapshot_int(snapshot_info.get("height"), 0, "height"),
            duration_s=cls._snapshot_float(
                snapshot_info.get("duration_s"), 0.0, "duration_s"
            ),
            available=False if live_info is not None else available,
            kind=kind,
            sample_rate_hz=sample_rate,
            channels=cls._snapshot_int(snapshot_info.get("channels"), 0, "channels"),
            sample_width_bytes=cls._snapshot_int(
                snapshot_info.get("sample_width_bytes"), 0, "sample_width_bytes"
            ),
            error=error,
            source_identity=source_identity,
        )

    @staticmethod
    def _snapshot_string(value: object, default: str, label: str) -> str:
        if value is None or value == "":
            return default
        if not isinstance(value, str):
            raise ValueError(f"project media_info {label} must be a string")
        return value

    @staticmethod
    def _snapshot_float(value: object, default: float, label: str) -> float:
        if value is None:
            return float(default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"project media_info {label} must be a finite non-negative number"
            )
        parsed = float(value)
        if not isfinite(parsed) or parsed < 0.0:
            raise ValueError(
                f"project media_info {label} must be a finite non-negative number"
            )
        return parsed

    @staticmethod
    def _snapshot_int(value: object, default: int, label: str) -> int:
        if value is None:
            return int(default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"project media_info {label} must be a non-negative integer")
        return int(value)

    @classmethod
    def calibration_rod_to_dict(
        cls,
        rod: CalibrationRod | None,
    ) -> dict[str, object] | None:
        if rod is None:
            return None
        parsed = cls.calibration_rod_from_dict(
            {
                "start_px": rod.start_px,
                "end_px": rod.end_px,
                "real_length": rod.real_length,
                "unit": rod.unit,
                "y_positive": rod.y_positive,
            }
        )
        if parsed is None:
            return None
        return {
            "start_px": [parsed.start_px[0], parsed.start_px[1]],  # type: ignore[index]
            "end_px": [parsed.end_px[0], parsed.end_px[1]],  # type: ignore[index]
            "real_length": parsed.real_length,
            "unit": parsed.unit,
            "y_positive": parsed.y_positive,
        }

    @staticmethod
    def calibration_rod_from_dict(data: dict[str, object] | None) -> CalibrationRod | None:
        if not isinstance(data, dict):
            return None
        try:
            start = data["start_px"]
            end = data["end_px"]
            real_length = data["real_length"]
        except KeyError:
            return None
        unit = data.get("unit", "cm")
        y_positive = data.get("y_positive", "up")
        if (
            not isinstance(start, (list, tuple))
            or len(start) != 2
            or not isinstance(end, (list, tuple))
            or len(end) != 2
            or any(
                isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
                for value in (*start, *end, real_length)
            )
            or not isinstance(unit, str)
            or not isinstance(y_positive, str)
        ):
            return None
        try:
            start_px = (float(start[0]), float(start[1]))  # type: ignore[index]
            end_px = (float(end[0]), float(end[1]))  # type: ignore[index]
            parsed_length = float(real_length)
        except (TypeError, ValueError, OverflowError):
            return None
        unit = unit.strip()
        rod = CalibrationRod(
            start_px=start_px,
            end_px=end_px,
            real_length=parsed_length,
            unit=unit,
            y_positive=y_positive,
        )
        if (
            not 1 <= len(unit) <= 12
            or not unit.isprintable()
            or y_positive not in {"up", "down"}
            or rod.unit_per_pixel() is None
        ):
            return None
        return rod

    @staticmethod
    def apply_roi_config_to_task(task: DesktopTask, roi: dict[str, object]) -> bool:
        if validate_roi_config(roi) is not None:
            return False
        roi_type = roi.get("type")
        if roi_type == "rectangle":
            x, y = float(roi["x"]), float(roi["y"])
            width, height = float(roi["width"]), float(roi["height"])
            task.roi = {
                "type": "rectangle",
                "x": round(x, 3),
                "y": round(y, 3),
                "width": round(width, 3),
                "height": round(height, 3),
            }
            task.pipeline.roi = RectangularROI(x=x, y=y, width=width, height=height)
            return True
        if roi_type == "circle":
            center_data = roi["center"]
            center = (float(center_data[0]), float(center_data[1]))  # type: ignore[index]
            radius = float(roi["radius"])
            task.roi = {
                "type": "circle",
                "center": [round(center[0], 3), round(center[1], 3)],
                "radius": round(radius, 3),
            }
            task.pipeline.roi = CircularROI(center=center, radius=radius)
            if isinstance(task.pipeline.coordinate_model, AnnularCoordinate):
                old = task.pipeline.coordinate_model
                task.pipeline.coordinate_model = AnnularCoordinate(
                    center_px=center,
                    theta_zero_px=old.theta_zero_px,
                    direction=old.direction,
                    inner_radius=old.inner_radius,
                    outer_radius=old.outer_radius,
                    unit_per_pixel=old.unit_per_pixel,
                    unit=old.unit,
                )
            elif isinstance(task.pipeline.coordinate_model, PolarCoordinate):
                old = task.pipeline.coordinate_model
                task.pipeline.coordinate_model = PolarCoordinate(
                    center_px=center,
                    theta_zero_px=old.theta_zero_px,
                    direction=old.direction,
                )
            return True
        if roi_type == "annulus":
            center_data = roi["center"]
            center = (float(center_data[0]), float(center_data[1]))  # type: ignore[index]
            inner_radius = float(roi["inner_radius"])
            outer_radius = float(roi["outer_radius"])
            task.roi = {
                "type": "annulus",
                "center": [round(center[0], 3), round(center[1], 3)],
                "inner_radius": round(inner_radius, 3),
                "outer_radius": round(outer_radius, 3),
            }
            task.pipeline.roi = AnnularROI(center=center, inner_radius=inner_radius, outer_radius=outer_radius)
            if isinstance(task.pipeline.coordinate_model, AnnularCoordinate):
                old = task.pipeline.coordinate_model
                task.pipeline.coordinate_model = AnnularCoordinate(
                    center_px=center,
                    theta_zero_px=old.theta_zero_px,
                    direction=old.direction,
                    inner_radius=inner_radius,
                    outer_radius=outer_radius,
                    unit_per_pixel=old.unit_per_pixel,
                    unit=old.unit,
                )
            elif isinstance(task.pipeline.coordinate_model, PolarCoordinate):
                old = task.pipeline.coordinate_model
                task.pipeline.coordinate_model = PolarCoordinate(
                    center_px=center,
                    theta_zero_px=old.theta_zero_px,
                    direction=old.direction,
                )
            return True
        if roi_type == "polygon":
            points = tuple((float(point[0]), float(point[1])) for point in roi["points"])  # type: ignore[index]
            task.roi = {
                "type": "polygon",
                "points": [[round(x, 3), round(y, 3)] for x, y in points],
            }
            task.pipeline.roi = PolygonROI(points=points)
            return True
        if roi_type == "curve_band":
            polyline = tuple(
                (float(point[0]), float(point[1])) for point in roi["polyline"]  # type: ignore[index]
            )
            half_width = float(roi["half_width"])
            task.roi = {
                "type": "curve_band",
                "polyline": [[round(x, 3), round(y, 3)] for x, y in polyline],
                "half_width": round(half_width, 3),
            }
            task.pipeline.roi = CurveBandROI(polyline=polyline, half_width=half_width)
            if isinstance(task.pipeline.coordinate_model, PathCoordinate):
                old = task.pipeline.coordinate_model
                task.pipeline.coordinate_model = PathCoordinate(
                    polyline=polyline,
                    unit_per_pixel=old.unit_per_pixel,
                    unit=old.unit,
                )
            return True
        return False

    @classmethod
    def apply_calibration_rod_to_task(cls, task: DesktopTask, rod: CalibrationRod) -> bool:
        if not cls.supports_calibration_rod(task.pipeline.coordinate_model):
            return False
        parsed = cls.calibration_rod_from_dict(cls.calibration_rod_to_dict(rod))
        if parsed is None:
            return False
        rod = parsed
        unit_per_pixel = rod.unit_per_pixel()
        if unit_per_pixel is None:
            return False
        task.calibration_rod = rod
        old_coordinate_model = task.pipeline.coordinate_model
        old_state_model = task.pipeline.state_model
        old_state_key = cls.single_state_key(old_state_model)
        old_unit_scale = cls.state_length_unit_per_pixel(old_coordinate_model, old_state_model)
        if isinstance(task.pipeline.coordinate_model, (ImageCoordinate, LinearWorldCoordinate)):
            task.pipeline.coordinate_model = LinearWorldCoordinate.from_calibration_rod(
                start_px=rod.start_px,
                end_px=rod.end_px,
                real_length=float(rod.real_length),  # type: ignore[arg-type]
                unit=rod.unit,
                y_positive=rod.y_positive,
            )
        elif isinstance(task.pipeline.coordinate_model, AnnularCoordinate):
            old = task.pipeline.coordinate_model
            task.pipeline.coordinate_model = AnnularCoordinate(
                center_px=old.center_px,
                theta_zero_px=old.theta_zero_px,
                direction=old.direction,
                inner_radius=old.inner_radius,
                outer_radius=old.outer_radius,
                unit_per_pixel=unit_per_pixel,
                unit=rod.unit,
            )
        elif isinstance(task.pipeline.coordinate_model, PathCoordinate):
            old = task.pipeline.coordinate_model
            task.pipeline.coordinate_model = PathCoordinate(
                polyline=old.polyline,
                unit_per_pixel=unit_per_pixel,
                unit=rod.unit,
            )
        new_state_model = cls.state_model_with_calibration_unit(
            old_state_model,
            task.pipeline.coordinate_model,
            rod.unit,
        )
        new_state_key = cls.single_state_key(new_state_model)
        task.pipeline.state_model = new_state_model
        if old_state_key and new_state_key:
            task.pipeline.motion_model = cls.retarget_keyed_model(
                task.pipeline.motion_model,
                old_state_key,
                new_state_key,
            )
            task.pipeline.tracker_filter = cls.retarget_keyed_model(
                task.pipeline.tracker_filter,
                old_state_key,
                new_state_key,
            )
            new_unit_scale = cls.state_length_unit_per_pixel(task.pipeline.coordinate_model, new_state_model)
            if old_unit_scale and new_unit_scale:
                task.pipeline.motion_model = cls.scale_motion_model_units(
                    task.pipeline.motion_model,
                    new_state_key,
                    new_unit_scale / old_unit_scale,
                )
        return True

    @staticmethod
    def supports_calibration_rod(coordinate_model: object) -> bool:
        return isinstance(
            coordinate_model,
            (ImageCoordinate, LinearWorldCoordinate, AnnularCoordinate, PathCoordinate),
        )

    @staticmethod
    def state_model_with_calibration_unit(state_model: object, coordinate_model: object, unit: str) -> object:
        if isinstance(state_model, XYState):
            return replace(state_model, unit=unit)
        if isinstance(state_model, ScalarState):
            key = getattr(state_model, "key", None)
            if isinstance(coordinate_model, LinearWorldCoordinate):
                if key == "x_px":
                    return replace(state_model, key="x_world", unit=unit)
                if key == "y_px":
                    return replace(state_model, key="y_world", unit=unit)
                if key in {"x_world", "y_world"}:
                    return replace(state_model, unit=unit)
            if key == "s" and isinstance(coordinate_model, (AnnularCoordinate, PathCoordinate)):
                return replace(state_model, unit=unit)
        if isinstance(state_model, FrontState) and getattr(state_model, "key", None) == "s":
            if isinstance(coordinate_model, (AnnularCoordinate, PathCoordinate)):
                return replace(state_model, unit=unit)
        return state_model

    @staticmethod
    def state_model_with_pixel_unit(state_model: object) -> object:
        if isinstance(state_model, XYState):
            return replace(state_model, unit="px")
        if isinstance(state_model, ScalarState):
            key = getattr(state_model, "key", None)
            if key == "x_world":
                return replace(state_model, key="x_px", unit="px")
            if key == "y_world":
                return replace(state_model, key="y_px", unit="px")
            if key == "s":
                return replace(state_model, unit="px")
        if isinstance(state_model, FrontState) and getattr(state_model, "key", None) == "s":
            return replace(state_model, unit="px")
        return state_model

    @staticmethod
    def single_state_key(state_model: object) -> str | None:
        if isinstance(state_model, (FrontState, ScalarState)):
            return str(state_model.key)
        return None

    @classmethod
    def state_length_unit_per_pixel(cls, coordinate_model: object, state_model: object) -> float | None:
        key = cls.single_state_key(state_model)
        if key in {"x_px", "y_px"}:
            return 1.0
        if key in {"x_world", "y_world"} and isinstance(coordinate_model, LinearWorldCoordinate):
            return float(coordinate_model.unit_per_pixel)
        if key == "s" and isinstance(coordinate_model, (AnnularCoordinate, PathCoordinate)):
            return float(coordinate_model.unit_per_pixel)
        return None

    @staticmethod
    def retarget_keyed_model(model: object, old_key: str, new_key: str) -> object:
        if old_key == new_key:
            return model
        updates: dict[str, object] = {}
        keys = getattr(model, "keys", None)
        if isinstance(keys, (tuple, list)):
            new_keys = tuple(new_key if str(key) == old_key else key for key in keys)
            if tuple(keys) != new_keys:
                updates["keys"] = new_keys
        key = getattr(model, "key", None)
        if key == old_key:
            updates["key"] = new_key
        if not updates:
            return model
        try:
            return replace(model, **updates)
        except TypeError:
            return model

    @staticmethod
    def scale_motion_model_units(model: object, key: str, scale: float) -> object:
        if abs(float(scale) - 1.0) <= 1e-12:
            return model
        keys = getattr(model, "keys", None)
        relates_to_key = bool(isinstance(keys, (tuple, list)) and key in keys) or getattr(model, "key", None) == key
        if not relates_to_key:
            return model
        updates: dict[str, object] = {}
        for attr in ("max_speed", "margin", "max_residual"):
            value = getattr(model, attr, None)
            if isinstance(value, (int, float)):
                updates[attr] = float(value) * float(scale)
        if not updates:
            return model
        try:
            return replace(model, **updates)
        except TypeError:
            return model
