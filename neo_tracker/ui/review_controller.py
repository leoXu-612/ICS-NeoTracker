from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, MutableSequence, Sequence

from neo_tracker.core import ObservationCandidate, TrackerResult, TrackingPipeline
from neo_tracker.ui.language import tr


MANUAL_EDIT_TYPES = frozenset({"manual_correction", "mark_lost"})


@dataclass(frozen=True)
class ReviewRow:
    result_index: int
    frame_index: int
    values: tuple[str, ...]
    tone: str


@dataclass(frozen=True)
class ReviewTable:
    headers: tuple[str, ...]
    state_headers: tuple[str, ...]
    state_keys: tuple[str, ...]
    rows: tuple[ReviewRow, ...]
    summary: str
    summary_tooltip: str


@dataclass(frozen=True)
class ReviewSelection:
    result_index: int
    frame_index: int
    text: str
    detail: str
    tone: str


@dataclass(frozen=True)
class ReviewEdit:
    result_index: int
    frame_index: int
    event_type: str
    details: dict[str, object]


@dataclass(frozen=True)
class ReviewOverlay:
    trajectory: tuple[tuple[float, float], ...]
    current_point: tuple[float, float] | None
    observation_point: tuple[float, float] | None
    candidate_points: tuple[tuple[float, float, float, bool], ...]
    prediction_point: tuple[float, float] | None
    measurements: tuple[tuple[float, float], ...]
    selected_result: TrackerResult | None


@dataclass(frozen=True)
class _OverlayStatic:
    signature: tuple[int, ...]
    points_by_index: tuple[tuple[float, float] | None, ...]
    trajectory: tuple[tuple[float, float], ...]
    trajectory_result_indices: tuple[int, ...]
    measurements: tuple[tuple[float, float], ...]
    measurement_result_indices: tuple[int, ...]


class ReviewController:
    """UI-independent review table, edit, selection, and overlay logic."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._overlay_static: _OverlayStatic | None = None

    def table(
        self,
        results: Sequence[TrackerResult],
        state_units: dict[str, str],
        tracking_note: str = "",
    ) -> ReviewTable:
        state_keys = tuple(self.result_state_keys(results))
        state_headers = tuple(self.state_key_label(key, state_units) for key in state_keys)
        headers = ("Frame", "Time", *state_headers, "Conf", "Status")
        rows: list[ReviewRow] = []
        for result_index, result in enumerate(results):
            values = [str(result.frame_index), f"{result.time_s:.6g}"]
            values.extend(self.format_state_value(result.filtered_state.get(key)) for key in state_keys)
            values.extend((f"{result.confidence:.3f}", result.status))
            rows.append(
                ReviewRow(
                    result_index=result_index,
                    frame_index=result.frame_index,
                    values=tuple(values),
                    tone=self.result_tone(result),
                )
            )

        summary = self.review_summary_text(results, state_keys, state_units)
        clean_note = tracking_note.strip()
        if clean_note:
            compact_note = clean_note if len(clean_note) <= 240 else clean_note[:237] + "..."
            summary += f"\nRun note: {compact_note}"
        return ReviewTable(
            headers=headers,
            state_headers=state_headers,
            state_keys=state_keys,
            rows=tuple(rows),
            summary=summary,
            summary_tooltip=clean_note,
        )

    def preferred_result_index(
        self,
        results: Sequence[TrackerResult],
        selected_index: int | None,
        current_frame: int,
    ) -> int | None:
        if selected_index is not None and 0 <= selected_index < len(results):
            return selected_index
        return self._result_index_for_frame(results, current_frame)

    def result_for_frame(
        self,
        results: Sequence[TrackerResult],
        current_frame: int,
    ) -> TrackerResult | None:
        index = self._result_index_for_frame(results, current_frame)
        return results[index] if index is not None else None

    @staticmethod
    def _result_index_for_frame(results: Sequence[TrackerResult], current_frame: int) -> int | None:
        target = int(current_frame)
        low = 0
        high = len(results) - 1
        while low <= high:
            middle = (low + high) // 2
            frame_index = int(results[middle].frame_index)
            if frame_index < target:
                low = middle + 1
            elif frame_index > target:
                high = middle - 1
            else:
                return middle
        # Plugin/custom results are allowed to be unsorted. Preserve the old
        # behavior for that uncommon path while keeping tracked sequences O(log n).
        return next((index for index, result in enumerate(results) if result.frame_index == target), None)

    def invalidate_overlay_cache(self) -> None:
        self._overlay_static = None

    def refresh_overlay_result(
        self,
        pipeline: TrackingPipeline,
        result_index: int,
        *,
        previous_result: TrackerResult,
    ) -> bool:
        """Refresh one cached trajectory point after a copy-on-write edit."""

        cached = self._overlay_static
        results = pipeline.results
        index = int(result_index)
        if (
            cached is None
            or index < 0
            or index >= len(results)
            or cached.signature[0] != id(pipeline)
            or cached.signature[1] != id(results)
            or cached.signature[2] != len(results)
            or cached.signature[5] != id(pipeline.coordinate_model)
        ):
            self.invalidate_overlay_cache()
            return False

        result = results[index]
        points_by_index = list(cached.points_by_index)
        current_point = self.result_image_point(pipeline, result)
        points_by_index[index] = current_point

        trajectory = list(cached.trajectory)
        trajectory_indices = list(cached.trajectory_result_indices)
        previous_in_trajectory = (
            self.result_image_point(pipeline, previous_result) is not None
            and previous_result.status not in {"lost", "manual_lost"}
        )
        current_in_trajectory = (
            current_point is not None and result.status not in {"lost", "manual_lost"}
        )
        try:
            trajectory_index = trajectory_indices.index(index)
        except ValueError:
            trajectory_index = -1
        if previous_in_trajectory and not current_in_trajectory and trajectory_index >= 0:
            trajectory.pop(trajectory_index)
            trajectory_indices.pop(trajectory_index)
        elif current_in_trajectory and trajectory_index >= 0:
            trajectory[trajectory_index] = current_point  # type: ignore[assignment]
        elif current_in_trajectory:
            insertion = 0
            while insertion < len(trajectory_indices) and trajectory_indices[insertion] < index:
                insertion += 1
            trajectory.insert(insertion, current_point)  # type: ignore[arg-type]
            trajectory_indices.insert(insertion, index)

        measurements = list(cached.measurements)
        measurement_indices = list(cached.measurement_result_indices)
        show_measurement = bool(cached.signature[-1])
        previous_measurement = (
            self.state_image_point(pipeline, previous_result.state)
            if show_measurement
            and previous_result.status not in {"lost", "predicted", "manual_lost"}
            else None
        )
        current_measurement = (
            self.state_image_point(pipeline, result.state)
            if show_measurement and result.status not in {"lost", "predicted", "manual_lost"}
            else None
        )
        try:
            measurement_index = measurement_indices.index(index)
        except ValueError:
            measurement_index = -1
        if previous_measurement is not None and current_measurement is None and measurement_index >= 0:
            measurements.pop(measurement_index)
            measurement_indices.pop(measurement_index)
        elif current_measurement is not None and measurement_index >= 0:
            measurements[measurement_index] = current_measurement
        elif current_measurement is not None:
            insertion = 0
            while insertion < len(measurement_indices) and measurement_indices[insertion] < index:
                insertion += 1
            measurements.insert(insertion, current_measurement)
            measurement_indices.insert(insertion, index)

        signature = (
            id(pipeline),
            id(results),
            len(results),
            id(results[0]) if results else 0,
            id(results[-1]) if results else 0,
            id(pipeline.coordinate_model),
            int(show_measurement),
        )
        self._overlay_static = _OverlayStatic(
            signature=signature,
            points_by_index=tuple(points_by_index),
            trajectory=tuple(trajectory),
            trajectory_result_indices=tuple(trajectory_indices),
            measurements=tuple(measurements),
            measurement_result_indices=tuple(measurement_indices),
        )
        return True

    def selection(
        self,
        results: Sequence[TrackerResult],
        result_index: int | None,
        state_units: dict[str, str],
    ) -> ReviewSelection | None:
        if result_index is None or result_index < 0 or result_index >= len(results):
            return None
        result = results[result_index]
        status = tr(result.status.replace("_", " "))
        text = tr("Frame {frame}  ·  {time:.6g} s  ·  {status}  ·  confidence {confidence:.3f}",
                  frame=result.frame_index, time=result.time_s, status=status, confidence=result.confidence)
        state_keys = self.result_state_keys((result,))
        state_parts = []
        for key in state_keys:
            value = self.format_state_value(result.filtered_state.get(key))
            unit = state_units.get(key, "")
            state_parts.append(f"{key} {value}{f' {unit}' if unit else ''}")
        detail = "  ·  ".join(state_parts) if state_parts else "No numeric state on this result"
        return ReviewSelection(
            result_index=result_index,
            frame_index=result.frame_index,
            text=text,
            detail=detail,
            tone=self.result_tone(result),
        )

    def manual_correction(
        self,
        pipeline: TrackingPipeline,
        result_index: int,
        point_px: tuple[float, float],
    ) -> ReviewEdit:
        if result_index < 0 or result_index >= len(pipeline.results):
            raise IndexError("review result index is out of range")
        result = pipeline.results[result_index]
        original_result = result
        previous_status = result.status
        previous_point = self.result_image_point(pipeline, result)
        prior_result = pipeline.results[result_index - 1] if result_index > 0 else None
        previous_state = prior_result.filtered_state if prior_result is not None else None
        candidate = ObservationCandidate(
            state=pipeline.coordinate_model.image_to_state_space(point_px),
            score=1.0,
            image_point=point_px,
            label="manual",
            raw={"manual": True},
        )
        state = pipeline.state_model.measurement_from_candidate(
            candidate,
            pipeline.coordinate_model,
            previous_state,
        )
        state = pipeline.state_model.normalize(state, previous_state)
        result = replace(
            result,
            state=dict(state),
            filtered_state=dict(state),
            confidence=1.0,
            status="manual",
            observation=candidate,
            debug={
                **result.debug,
                "filter": {"velocity": self.velocity_from_previous(state, prior_result, result.time_s)},
                "manual_correction": {
                    "point_px": [point_px[0], point_px[1]],
                    "previous_status": previous_status,
                    "previous_point_px": list(previous_point) if previous_point is not None else None,
                },
            },
        )
        pipeline.results[result_index] = result
        self.refresh_overlay_result(pipeline, result_index, previous_result=original_result)
        return ReviewEdit(
            result_index=result_index,
            frame_index=result.frame_index,
            event_type="manual_correction",
            details={
                "point_px": [point_px[0], point_px[1]],
                "previous_status": previous_status,
                "previous_point_px": list(previous_point) if previous_point is not None else None,
            },
        )

    def mark_lost(
        self,
        results: MutableSequence[TrackerResult],
        result_index: int,
        *,
        pipeline: TrackingPipeline | None = None,
    ) -> ReviewEdit:
        if result_index < 0 or result_index >= len(results):
            raise IndexError("review result index is out of range")
        result = results[result_index]
        previous_result = result
        previous_status = result.status
        result = replace(
            result,
            status="manual_lost",
            confidence=0.0,
            debug={**result.debug, "manual_status": "lost"},
        )
        results[result_index] = result
        if pipeline is None:
            self.invalidate_overlay_cache()
        else:
            self.refresh_overlay_result(pipeline, result_index, previous_result=previous_result)
        return ReviewEdit(
            result_index=result_index,
            frame_index=result.frame_index,
            event_type="mark_lost",
            details={"previous_status": previous_status},
        )

    def append_edit(
        self,
        history: list[dict[str, object]],
        edit: ReviewEdit,
    ) -> dict[str, object]:
        timestamp = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        entry: dict[str, object] = {
            "time": timestamp.isoformat().replace("+00:00", "Z"),
            "type": edit.event_type,
            "frame_index": int(edit.frame_index),
            "details": dict(edit.details),
        }
        history.append(entry)
        return entry

    def append_event(
        self,
        history: list[dict[str, object]],
        event_type: str,
        frame_index: int | None = None,
        **details: object,
    ) -> dict[str, object]:
        timestamp = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        entry: dict[str, object] = {
            "time": timestamp.isoformat().replace("+00:00", "Z"),
            "type": event_type,
            "details": details,
        }
        if frame_index is not None:
            entry["frame_index"] = int(frame_index)
        history.append(entry)
        return entry

    def overlay(
        self,
        pipeline: TrackingPipeline,
        current_frame: int,
        *,
        show_observation: bool,
        show_measurement: bool,
        show_candidates: bool,
        show_prediction: bool,
    ) -> ReviewOverlay:
        static = self._overlay_static_for(pipeline, show_measurement=show_measurement)
        current_point: tuple[float, float] | None = None
        observation_point: tuple[float, float] | None = None
        candidate_points: tuple[tuple[float, float, float, bool], ...] = ()
        prediction_point: tuple[float, float] | None = None
        selected_index = self._result_index_for_frame(pipeline.results, current_frame)
        selected_result = pipeline.results[selected_index] if selected_index is not None else None
        if selected_result is not None and selected_index is not None:
            current_point = static.points_by_index[selected_index]
            if show_observation and selected_result.observation is not None:
                observation_point = selected_result.observation.image_point
            if show_candidates:
                candidate_points = tuple(self.candidate_points_from_debug(selected_result.debug.get("candidates")))
            if show_prediction and selected_result.prediction is not None:
                prediction_point = self.state_image_point(pipeline, selected_result.prediction)
        return ReviewOverlay(
            trajectory=static.trajectory,
            current_point=current_point,
            observation_point=observation_point,
            candidate_points=candidate_points,
            prediction_point=prediction_point,
            measurements=static.measurements,
            selected_result=selected_result,
        )

    def _overlay_static_for(self, pipeline: TrackingPipeline, *, show_measurement: bool) -> _OverlayStatic:
        results = pipeline.results
        signature = (
            id(pipeline),
            id(results),
            len(results),
            id(results[0]) if results else 0,
            id(results[-1]) if results else 0,
            id(pipeline.coordinate_model),
            int(bool(show_measurement)),
        )
        cached = self._overlay_static
        if cached is not None and cached.signature == signature:
            return cached

        points_by_index: list[tuple[float, float] | None] = []
        trajectory: list[tuple[float, float]] = []
        trajectory_result_indices: list[int] = []
        measurements: list[tuple[float, float]] = []
        measurement_result_indices: list[int] = []
        for result_index, result in enumerate(results):
            point = self.result_image_point(pipeline, result)
            points_by_index.append(point)
            if point is not None and result.status not in {"lost", "manual_lost"}:
                trajectory.append(point)
                trajectory_result_indices.append(result_index)
            if show_measurement and result.status not in {"lost", "predicted", "manual_lost"}:
                measurement_point = self.state_image_point(pipeline, result.state)
                if measurement_point is not None:
                    measurements.append(measurement_point)
                    measurement_result_indices.append(result_index)
        cached = _OverlayStatic(
            signature=signature,
            points_by_index=tuple(points_by_index),
            trajectory=tuple(trajectory),
            trajectory_result_indices=tuple(trajectory_result_indices),
            measurements=tuple(measurements),
            measurement_result_indices=tuple(measurement_result_indices),
        )
        self._overlay_static = cached
        return cached

    @staticmethod
    def result_tone(result: TrackerResult) -> str:
        status = result.status.lower()
        if status in {"lost", "manual_lost"}:
            return "lost"
        if status.startswith("manual"):
            return "manual"
        if status in {"predicted", "partial"} or result.confidence < 0.5:
            return "attention"
        return "trusted"

    @staticmethod
    def result_state_keys(results: Sequence[TrackerResult]) -> list[str]:
        priority = ["x_px", "y_px", "x_world", "y_world", "theta", "theta_unwrapped", "r", "s", "offset"]
        keys = {key for result in results for key in result.filtered_state.keys()}
        ordered = [key for key in priority if key in keys]
        ordered.extend(sorted(keys.difference(ordered)))
        return ordered

    @staticmethod
    def format_state_value(value: object) -> str:
        if value is None:
            return ""
        try:
            return f"{float(value):.6g}"
        except Exception:
            return str(value)

    @staticmethod
    def state_key_label(key: str, state_units: dict[str, str]) -> str:
        unit = state_units.get(key, "")
        return f"{key} ({unit})" if unit else key

    @classmethod
    def tracking_summary_text(cls, results: Sequence[TrackerResult]) -> str:
        if not results:
            return tr("Results: none")
        avg_confidence = sum(float(result.confidence) for result in results) / len(results)
        statuses: dict[str, int] = {}
        for result in results:
            statuses[result.status] = statuses.get(result.status, 0) + 1
        status_summary = ", ".join(f"{tr(key)} {value}" for key, value in sorted(statuses.items()))
        return tr("Results: {count} | avg confidence {confidence:.2f} | {statuses}",
                  count=len(results), confidence=avg_confidence, statuses=status_summary)

    @classmethod
    def review_summary_text(
        cls,
        results: Sequence[TrackerResult],
        state_keys: Sequence[str],
        state_units: dict[str, str] | None = None,
    ) -> str:
        if not results:
            return "Run tracking to populate the review table. Use calibration and ROI first for better physical units."
        units = state_units or {}
        labels = [cls.state_key_label(key, units) for key in state_keys]
        keys = ", ".join(labels) if labels else "no numeric state"
        return f"{cls.tracking_summary_text(results)} | State columns: {keys}"

    @staticmethod
    def edit_entry_is_superseded(entry: dict[str, object]) -> bool:
        return bool(entry.get("superseded_at"))

    @classmethod
    def active_manual_edit_count_from_frame(
        cls,
        history: Sequence[dict[str, object]],
        start_frame: int,
    ) -> int:
        count = 0
        for entry in history:
            if str(entry.get("type", "")) not in MANUAL_EDIT_TYPES:
                continue
            if cls.edit_entry_is_superseded(entry):
                continue
            try:
                frame_index = int(entry["frame_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if frame_index >= int(start_frame):
                count += 1
        return count

    @classmethod
    def supersede_manual_edits_from_frame(
        cls,
        history: list[dict[str, object]],
        start_frame: int,
        *,
        superseded_at: str,
    ) -> int:
        count = 0
        for entry in history:
            if str(entry.get("type", "")) not in MANUAL_EDIT_TYPES:
                continue
            if cls.edit_entry_is_superseded(entry):
                continue
            try:
                frame_index = int(entry["frame_index"])
            except (KeyError, TypeError, ValueError):
                continue
            if frame_index < int(start_frame):
                continue
            entry["superseded_at"] = str(superseded_at)
            entry["superseded_by_rerun_start_frame"] = int(start_frame)
            count += 1
        return count

    @staticmethod
    def format_edit_history_entry(entry: dict[str, object]) -> str:
        timestamp = str(entry.get("time", ""))
        timestamp = timestamp.replace("T", " ").replace("Z", "")
        if len(timestamp) > 19:
            timestamp = timestamp[:19]
        frame_text = ""
        if entry.get("frame_index") is not None:
            frame_text = f"frame {int(entry['frame_index'])} | "
        event_type = str(entry.get("type", "edit"))
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        if event_type == "manual_correction":
            point = details.get("point_px") if isinstance(details, dict) else None
            point_text = ""
            if isinstance(point, list) and len(point) == 2:
                point_text = f" -> ({float(point[0]):.1f}, {float(point[1]):.1f})"
            action = f"corrected point{point_text}"
        elif event_type == "mark_lost":
            action = "marked lost"
        elif event_type == "rerun_after":
            start_frame = details.get("start_frame") if isinstance(details, dict) else None
            end_frame = details.get("end_frame") if isinstance(details, dict) else None
            status = details.get("status", "done") if isinstance(details, dict) else "done"
            if start_frame is not None and end_frame is not None:
                action = f"reran frames {int(start_frame)}-{int(end_frame)} ({status})"
            else:
                action = f"reran segment ({status})"
            superseded_count = details.get("superseded_edit_count", 0) if isinstance(details, dict) else 0
            try:
                superseded_count = int(superseded_count)
            except (TypeError, ValueError):
                superseded_count = 0
            if superseded_count:
                edit_label = "edit" if superseded_count == 1 else "edits"
                action += f" | {superseded_count} {edit_label} superseded"
        else:
            action = event_type.replace("_", " ")
        if ReviewController.edit_entry_is_superseded(entry):
            rerun_start = entry.get("superseded_by_rerun_start_frame")
            try:
                rerun_start_frame = int(rerun_start) if rerun_start is not None else None
            except (TypeError, ValueError):
                rerun_start_frame = None
            if rerun_start_frame is None:
                action += " | superseded by rerun"
            else:
                action += f" | superseded by rerun from frame {rerun_start_frame}"
        return " | ".join(part for part in (f"{frame_text}{action}", timestamp) if part)

    @staticmethod
    def velocity_from_previous(
        state: dict[str, float],
        previous_result: TrackerResult | None,
        time_s: float,
    ) -> dict[str, float]:
        if previous_result is None:
            return {}
        dt = max(0.0, float(time_s) - float(previous_result.time_s))
        if dt <= 1e-12:
            return {}
        velocity: dict[str, float] = {}
        for key, value in state.items():
            if key in previous_result.filtered_state and isinstance(value, (int, float)):
                velocity[f"v_{key}"] = (float(value) - float(previous_result.filtered_state[key])) / dt
        if "theta_unwrapped" in state and "v_theta_unwrapped" in velocity:
            velocity["omega"] = velocity["v_theta_unwrapped"]
        return velocity

    @classmethod
    def result_image_point(
        cls,
        pipeline: TrackingPipeline,
        result: TrackerResult,
    ) -> tuple[float, float] | None:
        point = cls.state_image_point(pipeline, result.filtered_state)
        if point is not None:
            return point
        if result.observation and result.observation.image_point:
            return result.observation.image_point
        return None

    @staticmethod
    def state_image_point(
        pipeline: TrackingPipeline,
        state: dict[str, float] | None,
    ) -> tuple[float, float] | None:
        if not state:
            return None
        try:
            return pipeline.coordinate_model.state_to_image_space(state)
        except Exception:
            pass
        if "x_px" in state and "y_px" in state:
            return float(state["x_px"]), float(state["y_px"])
        return None

    @staticmethod
    def candidate_points_from_debug(debug_candidates: object) -> list[tuple[float, float, float, bool]]:
        if not isinstance(debug_candidates, list):
            return []
        points: list[tuple[float, float, float, bool]] = []
        for candidate in debug_candidates:
            if not isinstance(candidate, dict):
                continue
            image_point = candidate.get("image_point")
            if not isinstance(image_point, (list, tuple)) or len(image_point) != 2:
                continue
            try:
                points.append(
                    (
                        float(image_point[0]),
                        float(image_point[1]),
                        float(candidate.get("combined_score", candidate.get("score", 0.0))),
                        bool(candidate.get("selected", False)),
                    )
                )
            except Exception:
                continue
        return points
