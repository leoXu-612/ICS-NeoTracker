from __future__ import annotations

from collections import OrderedDict
from copy import copy
from dataclasses import dataclass
from typing import Literal

import numpy as np

from neo_tracker.core import (
    CoordinateModel,
    FrameContext,
    ObservationModel,
    ROIModel,
    TrackingPipeline,
    TrackerResult,
)


ResponseSource = Literal["stored", "cached", "recomputed", "unavailable"]


@dataclass(frozen=True)
class ReviewResponse:
    response_map: np.ndarray | None
    source: ResponseSource
    detail: str


@dataclass(frozen=True)
class ReviewResponseRequest:
    owner: object
    pipeline_token: int
    result: TrackerResult
    observation_model: ObservationModel
    roi: ROIModel
    coordinate_model: CoordinateModel
    context: FrameContext


@dataclass(frozen=True)
class _CacheEntry:
    owner: object
    result: TrackerResult
    response_map: np.ndarray | None
    detail: str
    fresh_for_lookup: bool = False


class ReviewResponseService:
    """Recover old response maps without mutating tracking pipeline state."""

    def __init__(self, max_entries: int = 4) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[tuple[int, int], _CacheEntry] = OrderedDict()

    def response_for(
        self,
        owner: object,
        pipeline: TrackingPipeline,
        result: TrackerResult,
        frame: np.ndarray,
    ) -> ReviewResponse:
        available = self.lookup(owner, result)
        if available is not None:
            return available
        request = self.prepare_request(owner, pipeline, result, frame)
        return self.commit(request, self.compute(request))

    def lookup(self, owner: object, result: TrackerResult) -> ReviewResponse | None:
        stored = result.debug.get("response_map")
        has_placement_metadata = bool(
            result.debug.get("response_origin") is not None
            or result.debug.get("response_frame_shape") is not None
        )
        placement = self._response_placement(result, stored)
        if isinstance(stored, np.ndarray) and not has_placement_metadata:
            return ReviewResponse(stored, "stored", "Stored with the recent tracking result.")

        key = (id(owner), int(result.frame_index))
        cached = self._entries.pop(key, None)
        if cached is not None and cached.owner is owner and cached.result is result:
            source: ResponseSource
            if cached.response_map is None:
                source = "unavailable"
            elif cached.fresh_for_lookup:
                source = "recomputed"
            else:
                source = "cached"
            self._entries[key] = _CacheEntry(
                cached.owner,
                cached.result,
                cached.response_map,
                cached.detail,
                fresh_for_lookup=False,
            )
            return ReviewResponse(cached.response_map, source, cached.detail)
        if isinstance(stored, np.ndarray) and placement is not None:
            x0, y0, frame_shape = placement
            response_map = np.zeros(frame_shape, dtype=stored.dtype)
            height, width = stored.shape
            response_map[y0 : y0 + height, x0 : x0 + width] = stored
            response_map.setflags(write=False)
            detail = "Stored as an ROI-local tracking response and expanded for review."
            self._entries[key] = _CacheEntry(
                owner,
                result,
                response_map,
                detail,
                fresh_for_lookup=False,
            )
            self._trim()
            return ReviewResponse(response_map, "stored", detail)
        # A local response with invalid or partial placement metadata must not
        # be mistaken for a full image/polar response. Fall through so the
        # normal source-frame recomputation path can recover it safely.
        return None

    @staticmethod
    def _response_placement(
        result: TrackerResult,
        response_map: object,
    ) -> tuple[int, int, tuple[int, int]] | None:
        if not isinstance(response_map, np.ndarray) or response_map.ndim != 2:
            return None
        origin = result.debug.get("response_origin")
        frame_shape = result.debug.get("response_frame_shape")
        if not isinstance(origin, (list, tuple)) or len(origin) != 2:
            return None
        if not isinstance(frame_shape, (list, tuple)) or len(frame_shape) != 2:
            return None
        try:
            x0, y0 = (int(origin[0]), int(origin[1]))
            height, width = (int(frame_shape[0]), int(frame_shape[1]))
        except (TypeError, ValueError, OverflowError):
            return None
        local_height, local_width = response_map.shape
        if (
            x0 < 0
            or y0 < 0
            or height < 0
            or width < 0
            or x0 + local_width > width
            or y0 + local_height > height
        ):
            return None
        return x0, y0, (height, width)

    def prepare_request(
        self,
        owner: object,
        pipeline: TrackingPipeline,
        result: TrackerResult,
        frame: np.ndarray,
    ) -> ReviewResponseRequest:
        previous_result = self._previous_result(pipeline.results, result)
        context = FrameContext(
            frame_index=int(result.frame_index),
            time_s=float(result.time_s),
            frame=frame,
            previous_result=self._snapshot_result(previous_result),
            prediction=dict(result.prediction) if isinstance(result.prediction, dict) else None,
        )
        return ReviewResponseRequest(
            owner=owner,
            pipeline_token=id(pipeline),
            result=result,
            observation_model=copy(pipeline.observation_model),
            roi=copy(pipeline.roi),
            coordinate_model=copy(pipeline.coordinate_model),
            context=context,
        )

    @staticmethod
    def compute(request: ReviewResponseRequest) -> ReviewResponse:
        try:
            observed = request.observation_model.observe(
                request.context.frame,
                request.roi,
                request.coordinate_model,
                request.context,
            )
            if isinstance(observed.response_map, np.ndarray):
                response_map = np.array(observed.response_map, copy=True)
                response_map.setflags(write=False)
                detail = "Recomputed from the source frame for review. Tracking state was not changed."
            else:
                response_map = None
                detail = "This observation model did not produce a response map."
        except Exception as exc:
            response_map = None
            detail = f"Could not recompute the response map: {exc}"
        source: ResponseSource = "recomputed" if response_map is not None else "unavailable"
        return ReviewResponse(response_map, source, detail)

    def commit(
        self,
        request: ReviewResponseRequest,
        response: ReviewResponse,
        *,
        fresh_for_lookup: bool = False,
    ) -> ReviewResponse:
        key = (id(request.owner), int(request.result.frame_index))
        self._entries[key] = _CacheEntry(
            request.owner,
            request.result,
            response.response_map,
            response.detail,
            fresh_for_lookup=fresh_for_lookup,
        )
        self._trim()
        return response

    def invalidate(self, owner: object, first_frame: int | None = None) -> None:
        threshold = int(first_frame) if first_frame is not None else None
        stale_keys = [
            key
            for key, entry in self._entries.items()
            if entry.owner is owner and (threshold is None or entry.result.frame_index >= threshold)
        ]
        for key in stale_keys:
            self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def cached_frames(self, owner: object) -> list[int]:
        return [int(entry.result.frame_index) for entry in self._entries.values() if entry.owner is owner]

    def __len__(self) -> int:
        return len(self._entries)

    def _trim(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    @staticmethod
    def _previous_result(results: list[TrackerResult], current: TrackerResult) -> TrackerResult | None:
        for index, result in enumerate(results):
            if result is current:
                return results[index - 1] if index > 0 else None
        return None

    @staticmethod
    def _snapshot_result(result: TrackerResult | None) -> TrackerResult | None:
        if result is None:
            return None
        return TrackerResult(
            frame_index=int(result.frame_index),
            time_s=float(result.time_s),
            state=dict(result.state),
            filtered_state=dict(result.filtered_state),
            confidence=float(result.confidence),
            status=str(result.status),
            observation=result.observation,
            prediction=dict(result.prediction) if isinstance(result.prediction, dict) else None,
            debug=dict(result.debug),
        )
