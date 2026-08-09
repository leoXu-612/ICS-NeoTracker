from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np


State = dict[str, float]
DEFAULT_DEBUG_HISTORY_MAX_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ObservationCandidate:
    """A possible measurement produced by an observation model."""

    state: State
    score: float
    image_point: tuple[float, float] | None = None
    label: str = "candidate"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservationResult:
    candidates: list[ObservationCandidate]
    response_map: np.ndarray | None = None
    debug_layers: dict[str, np.ndarray] = field(default_factory=dict)
    response_origin: tuple[int, int] | None = None
    response_frame_shape: tuple[int, int] | None = None

    def best(self) -> ObservationCandidate | None:
        if not self.candidates:
            return None
        return max(self.candidates, key=lambda candidate: candidate.score)


@dataclass
class TrackerResult:
    frame_index: int
    time_s: float
    state: State
    filtered_state: State
    confidence: float
    status: str
    observation: ObservationCandidate | None = None
    prediction: State | None = None
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class FrameContext:
    frame_index: int
    time_s: float
    frame: np.ndarray
    previous_result: TrackerResult | None = None
    prediction: State | None = None
    compact_response_map: bool = False


@dataclass
class FilterUpdate:
    state: State
    debug: dict[str, Any] = field(default_factory=dict)


class ROIModel(ABC):
    name: str

    @abstractmethod
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        """Return a boolean mask for an image shape."""

    @abstractmethod
    def contains_point(self, point: tuple[float, float]) -> bool:
        """Return whether a pixel-space point is inside the ROI."""

    @abstractmethod
    def bounds(self) -> tuple[int, int, int, int]:
        """Return x_min, y_min, x_max, y_max in pixel coordinates."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class CoordinateModel(ABC):
    name: str

    @abstractmethod
    def image_to_state_space(self, point: tuple[float, float]) -> State:
        """Map an image-space point to this model's state coordinates."""

    @abstractmethod
    def state_to_image_space(self, state: State) -> tuple[float, float]:
        """Map state coordinates back to image-space."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class ObservationModel(ABC):
    name: str

    @abstractmethod
    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        """Extract candidate observations from a frame."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class StateModel(ABC):
    name: str

    @abstractmethod
    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        """Convert an observation candidate into the tracked physical state."""

    @abstractmethod
    def normalize(self, state: State, previous_state: State | None) -> State:
        """Normalize periodic or constrained state components."""

    @abstractmethod
    def units(self) -> dict[str, str]:
        """Return physical units per state key."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class MotionModel(ABC):
    name: str

    @abstractmethod
    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        """Predict the next state before looking at the frame."""

    @abstractmethod
    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        """Return a consistency score in [0, 1] for a measurement."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class TrackerFilter(ABC):
    name: str

    @abstractmethod
    def update(
        self,
        measurement: State,
        previous_result: TrackerResult | None,
        dt: float,
    ) -> FilterUpdate:
        """Update a state estimate from a measurement."""

    @abstractmethod
    def reset(self) -> None:
        """Clear internal state."""

    def prime(self, previous_result: TrackerResult | None) -> None:
        """Restore internal state from an existing result before continuing tracking."""
        self.reset()

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


class Optimizer(ABC):
    name: str

    @abstractmethod
    def optimize(self, objective: Any, search_space: dict[str, tuple[float, float]]) -> Any:
        """Optimize an objective over a numeric search space."""

    @abstractmethod
    def to_config(self) -> dict[str, Any]:
        """Return a JSON-serializable configuration."""


@dataclass
class TrackingPipeline:
    name: str
    roi: ROIModel
    coordinate_model: CoordinateModel
    observation_model: ObservationModel
    state_model: StateModel
    motion_model: MotionModel
    tracker_filter: TrackerFilter
    optimizer: Optimizer | None = None
    min_confidence: float = 0.05
    debug_history_limit: int = 4
    debug_history_max_bytes: int = DEFAULT_DEBUG_HISTORY_MAX_BYTES
    metadata: dict[str, Any] = field(default_factory=dict)
    results: list[TrackerResult] = field(default_factory=list)
    _debug_retained_results: deque[tuple[TrackerResult, int]] = field(
        default_factory=deque,
        init=False,
        repr=False,
        compare=False,
    )
    _debug_retained_bytes: int = field(default=0, init=False, repr=False, compare=False)
    _debug_cache_signature: tuple[int, int, int, int, int] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def reset(self) -> None:
        self.results.clear()
        self._debug_retained_results.clear()
        self._debug_retained_bytes = 0
        self._mark_debug_cache_current()
        self.tracker_filter.reset()

    def process_frame(
        self,
        frame: np.ndarray,
        frame_index: int,
        time_s: float,
        *,
        compact_response_map: bool = False,
    ) -> TrackerResult:
        previous = self.results[-1] if self.results else None
        dt = 0.0 if previous is None else max(0.0, time_s - previous.time_s)
        prediction = self.motion_model.predict(previous, dt)
        context = FrameContext(
            frame_index,
            time_s,
            frame,
            previous,
            prediction,
            compact_response_map=bool(compact_response_map),
        )
        observation_result = self.observation_model.observe(
            frame,
            self.roi,
            self.coordinate_model,
            context,
        )
        selected, measurement, candidate_debug = self._select_candidate(
            observation_result.candidates,
            previous,
            prediction,
            dt,
        )
        if selected is None:
            result = self._predicted_or_lost(frame_index, time_s, previous, prediction, observation_result, candidate_debug)
            self._append_result(result)
            return result

        if measurement is None:
            raise RuntimeError("selected observation candidate has no measurement")
        filter_update = self.tracker_filter.update(measurement, previous, dt)
        confidence = float(max(0.0, min(1.0, selected.score)))
        status = "ok" if confidence >= self.min_confidence else "low_confidence"
        result = TrackerResult(
            frame_index=frame_index,
            time_s=time_s,
            state=measurement,
            filtered_state=filter_update.state,
            confidence=confidence,
            status=status,
            observation=selected,
            prediction=prediction,
            debug={
                "filter": filter_update.debug,
                "candidates": candidate_debug,
                "response_map": observation_result.response_map,
                "response_origin": observation_result.response_origin,
                "response_frame_shape": observation_result.response_frame_shape,
                "debug_layers": observation_result.debug_layers,
            },
        )
        self._append_result(result)
        return result

    def run(self, frames: Iterable[np.ndarray], fps: float, reset: bool = True) -> list[TrackerResult]:
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be finite and positive")
        if reset:
            self.reset()
        if self.results:
            previous = self.results[-1]
            start_frame_index = previous.frame_index + 1
            start_time_s = previous.time_s + 1.0 / fps
        else:
            start_frame_index = 0
            start_time_s = 0.0
        for offset, frame in enumerate(frames):
            self.process_frame(
                frame,
                start_frame_index + offset,
                start_time_s + offset / fps,
            )
        return list(self.results)

    def to_config(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "roi": self.roi.to_config(),
            "coordinate_model": self.coordinate_model.to_config(),
            "observation_model": self.observation_model.to_config(),
            "state_model": self.state_model.to_config(),
            "motion_model": self.motion_model.to_config(),
            "tracker_filter": self.tracker_filter.to_config(),
            "optimizer": self.optimizer.to_config() if self.optimizer else None,
            "min_confidence": self.min_confidence,
            "debug_history_limit": self.debug_history_limit,
            "debug_history_max_bytes": self.debug_history_max_bytes,
            "metadata": self.metadata,
        }

    def _append_result(self, result: TrackerResult) -> None:
        if not self._debug_cache_is_current():
            self.rebuild_debug_history()
        self.results.append(result)
        frame_limit = max(0, int(self.debug_history_limit))
        stale_index = len(self.results) - frame_limit - 1
        if stale_index >= 0:
            stale_result = self.results[stale_index]
            if self._debug_retained_results and self._debug_retained_results[0][0] is stale_result:
                _removed, removed_bytes = self._debug_retained_results.popleft()
                self._debug_retained_bytes -= removed_bytes
            self._drop_heavy_debug_arrays(stale_result)

        result_bytes = self._heavy_debug_array_bytes(result)
        byte_limit = max(0, int(self.debug_history_max_bytes))
        if frame_limit > 0 and byte_limit > 0 and result_bytes > 0:
            self._debug_retained_results.append((result, result_bytes))
            self._debug_retained_bytes += result_bytes
            if len(self._debug_retained_results) > 1 and self._debug_retained_bytes > byte_limit:
                retained_reverse: list[tuple[TrackerResult, int]] = []
                retained_bytes = 0
                for retained, retained_size in reversed(self._debug_retained_results):
                    within_byte_limit = not retained_reverse or retained_bytes + retained_size <= byte_limit
                    if within_byte_limit:
                        retained_reverse.append((retained, retained_size))
                        retained_bytes += retained_size
                        continue
                    self._drop_heavy_debug_arrays(retained)
                self._debug_retained_results.clear()
                self._debug_retained_results.extend(reversed(retained_reverse))
                self._debug_retained_bytes = retained_bytes
        elif result_bytes > 0:
            self._drop_heavy_debug_arrays(result)
        self._mark_debug_cache_current()

    def rebuild_debug_history(self) -> None:
        """Reapply retention limits after replacing results or changing config."""

        self._debug_retained_results.clear()
        self._debug_retained_bytes = 0
        frame_limit = max(0, int(self.debug_history_limit))
        stale_end = max(0, len(self.results) - frame_limit)
        for result in self.results[:stale_end]:
            self._drop_heavy_debug_arrays(result)
        byte_limit = max(0, int(self.debug_history_max_bytes))
        retained_reverse: list[tuple[TrackerResult, int]] = []
        if frame_limit > 0 and byte_limit > 0:
            for result in reversed(self.results[-frame_limit:]):
                result_bytes = self._heavy_debug_array_bytes(result)
                if result_bytes <= 0:
                    continue
                within_byte_limit = not retained_reverse or self._debug_retained_bytes + result_bytes <= byte_limit
                if within_byte_limit:
                    retained_reverse.append((result, result_bytes))
                    self._debug_retained_bytes += result_bytes
                    continue
                self._drop_heavy_debug_arrays(result)
        else:
            for result in self.results[-frame_limit:] if frame_limit > 0 else self.results[-1:]:
                self._drop_heavy_debug_arrays(result)
        self._debug_retained_results.extend(reversed(retained_reverse))
        self._mark_debug_cache_current()

    def debug_history_usage(self) -> tuple[int, int]:
        """Return retained heavy-debug bytes and the number of owning results."""

        frame_limit = max(0, int(self.debug_history_limit))
        if frame_limit <= 0:
            return 0, 0
        if self._debug_cache_is_current():
            return self._debug_retained_bytes, len(self._debug_retained_results)
        retained_bytes = 0
        retained_frames = 0
        for result in self.results[-frame_limit:]:
            result_bytes = self._heavy_debug_array_bytes(result)
            if result_bytes <= 0:
                continue
            retained_frames += 1
            retained_bytes += result_bytes
        return retained_bytes, retained_frames

    def _debug_history_signature(self) -> tuple[int, int, int, int, int]:
        return (
            id(self.results),
            len(self.results),
            id(self.results[-1]) if self.results else 0,
            max(0, int(self.debug_history_limit)),
            max(0, int(self.debug_history_max_bytes)),
        )

    def _debug_cache_is_current(self) -> bool:
        return self._debug_cache_signature == self._debug_history_signature()

    def _mark_debug_cache_current(self) -> None:
        self._debug_cache_signature = self._debug_history_signature()

    @staticmethod
    def _heavy_debug_array_bytes(result: TrackerResult) -> int:
        arrays: list[np.ndarray] = []
        response_map = result.debug.get("response_map")
        if isinstance(response_map, np.ndarray):
            arrays.append(response_map)
        debug_layers = result.debug.get("debug_layers")
        if isinstance(debug_layers, dict):
            arrays.extend(
                value
                for value in debug_layers.values()
                if isinstance(value, np.ndarray) and value.ndim >= 2
            )
        seen: set[int] = set()
        total = 0
        for array in arrays:
            identity = id(array)
            if identity in seen:
                continue
            seen.add(identity)
            total += int(array.nbytes)
        return total

    @staticmethod
    def _drop_heavy_debug_arrays(result: TrackerResult) -> None:
        if isinstance(result.debug.get("response_map"), np.ndarray):
            result.debug["response_map"] = None
            result.debug["response_origin"] = None
            result.debug["response_frame_shape"] = None
        debug_layers = result.debug.get("debug_layers")
        if not isinstance(debug_layers, dict):
            return
        for key, value in debug_layers.items():
            if isinstance(value, np.ndarray) and value.ndim >= 2:
                debug_layers[key] = None

    def _select_candidate(
        self,
        candidates: list[ObservationCandidate],
        previous: TrackerResult | None,
        prediction: State | None,
        dt: float,
    ) -> tuple[ObservationCandidate | None, State | None, list[dict[str, Any]]]:
        if not candidates:
            return None, None, []
        previous_state = previous.filtered_state if previous else None
        best_candidate: ObservationCandidate | None = None
        best_measurement: State | None = None
        best_score = -1.0
        best_index = -1
        candidate_debug: list[dict[str, Any]] = []
        for candidate_index, candidate in enumerate(candidates):
            measurement = self.state_model.measurement_from_candidate(
                candidate,
                self.coordinate_model,
                previous_state,
            )
            measurement = self.state_model.normalize(measurement, previous_state)
            consistency = self.motion_model.score(measurement, prediction, dt)
            combined = float(candidate.score) * consistency
            diagnostic = {
                "state": dict(candidate.state),
                "measurement": dict(measurement),
                "score": float(candidate.score),
                "motion_score": float(consistency),
                "combined_score": float(combined),
                "image_point": list(candidate.image_point) if candidate.image_point is not None else None,
                "label": candidate.label,
                "selected": False,
            }
            candidate_debug.append(diagnostic)
            if combined > best_score:
                best_score = combined
                best_index = candidate_index
                best_candidate = ObservationCandidate(
                    state=candidate.state,
                    score=combined,
                    image_point=candidate.image_point,
                    label=candidate.label,
                    raw={**candidate.raw, "observation_score": candidate.score, "motion_score": consistency},
                )
                best_measurement = measurement
        if candidate_debug and best_candidate is not None:
            candidate_debug[best_index]["selected"] = True
        return best_candidate, best_measurement, candidate_debug

    def _predicted_or_lost(
        self,
        frame_index: int,
        time_s: float,
        previous: TrackerResult | None,
        prediction: State | None,
        observation_result: ObservationResult,
        candidate_debug: list[dict[str, Any]],
    ) -> TrackerResult:
        debug = {
            "candidates": candidate_debug,
            "response_map": observation_result.response_map,
            "response_origin": observation_result.response_origin,
            "response_frame_shape": observation_result.response_frame_shape,
            "debug_layers": observation_result.debug_layers,
        }
        if previous is not None:
            previous_filter_debug = previous.debug.get("filter")
            if isinstance(previous_filter_debug, dict):
                debug["filter"] = dict(previous_filter_debug)
        if prediction is None:
            state = previous.filtered_state if previous else {}
            return TrackerResult(frame_index, time_s, state, state, 0.0, "lost", prediction=None, debug=debug)
        return TrackerResult(
            frame_index=frame_index,
            time_s=time_s,
            state=prediction,
            filtered_state=prediction,
            confidence=0.0,
            status="predicted",
            prediction=prediction,
            debug=debug,
        )
