from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.coordinates import ImageCoordinate
from neo_tracker.core import FrameContext, ObservationCandidate, ObservationResult
from neo_tracker.observations import EdgeFrontObservation, _absolute_axis_gradient, _intensity
from neo_tracker.roi import RectangularROI


def _previous_full_frame_observe(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: EdgeFrontObservation,
) -> ObservationResult:
    image = _intensity(frame)
    gradient_axis = 1 if observation.axis == "x" else 0
    response = _absolute_axis_gradient(image, gradient_axis)
    max_value = float(response.max(initial=0.0))
    if max_value > 1e-12:
        response *= np.float32(1.0 / max_value)
    np.multiply(response, roi.mask(frame.shape), out=response)
    local_y, local_x = np.unravel_index(int(np.argmax(response)), response.shape)
    score = float(response[local_y, local_x])
    candidates = []
    if score >= observation.min_response:
        x = float(local_x)
        y = float(local_y)
        if observation.axis == "x":
            weights = response[:, local_x]
            total_weight = float(np.sum(weights, dtype=np.float64))
            if total_weight > 1e-12:
                y = float(np.dot(weights, np.arange(response.shape[0], dtype=np.float64))) / total_weight
        else:
            weights = response[local_y, :]
            total_weight = float(np.sum(weights, dtype=np.float64))
            if total_weight > 1e-12:
                x = float(np.dot(weights, np.arange(response.shape[1], dtype=np.float64))) / total_weight
        candidates.append(
            ObservationCandidate(
                state={"x_px": x, "y_px": y},
                score=score,
                image_point=(x, y),
                label=observation.name,
            )
        )
    return ObservationResult(candidates=candidates, response_map=response)


def _measure(operation, rounds: int) -> tuple[float, int]:
    operation()
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000.0)
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    del result
    tracemalloc.stop()
    return median(samples), int(peak)


def _expanded_response(result: ObservationResult, shape: tuple[int, int]) -> np.ndarray:
    if result.response_map is None:
        raise RuntimeError("edge-front benchmark requires a response map")
    if result.response_origin is None:
        return result.response_map
    response = np.zeros(shape, dtype=result.response_map.dtype)
    x0, y0 = result.response_origin
    height, width = result.response_map.shape
    response[y0 : y0 + height, x0 : x0 + width] = result.response_map
    return response


def _candidate_summary(result: ObservationResult) -> dict[str, object] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "image_point": list(candidate.image_point) if candidate.image_point is not None else None,
        "score": float(candidate.score),
    }


def _distractor_check(axis: str) -> dict[str, object]:
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    if axis == "x":
        roi = RectangularROI(60, 10, 40, 60)
        frame[:, 76:, :] = 40
        target_only = frame.copy()
        frame[:, 20:30, :] = 255
    else:
        roi = RectangularROI(10, 40, 80, 30)
        frame[56:, :, :] = 40
        target_only = frame.copy()
        frame[10:20, :, :] = 255
    observation = EdgeFrontObservation(axis=axis)
    coordinate = ImageCoordinate()
    target_context = FrameContext(0, 0.0, target_only)
    distractor_context = FrameContext(0, 0.0, frame)
    previous_target = _previous_full_frame_observe(target_only, roi, observation)
    previous_distractor = _previous_full_frame_observe(frame, roi, observation)
    current_target = observation.observe(target_only, roi, coordinate, target_context)
    current_distractor = observation.observe(frame, roi, coordinate, distractor_context)
    return {
        "previous_target_only": _candidate_summary(previous_target),
        "previous_with_outside_edge": _candidate_summary(previous_distractor),
        "current_target_only": _candidate_summary(current_target),
        "current_with_outside_edge": _candidate_summary(current_distractor),
        "current_max_response_error": float(
            np.max(
                np.abs(current_target.response_map - current_distractor.response_map),
                initial=0.0,
            )
        ),
    }


def benchmark(
    width: int,
    height: int,
    rounds: int,
    axis: str,
    roi_ratio: float,
) -> dict[str, object]:
    width = int(width)
    height = int(height)
    axis = str(axis).strip().lower()
    ratio = float(roi_ratio)
    if axis not in {"x", "y"}:
        raise ValueError("axis must be 'x' or 'y'")
    if not 0.0 < ratio <= 1.0:
        raise ValueError("roi_ratio must be in (0, 1]")

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    roi_width = width * ratio
    roi_height = height * ratio
    roi = RectangularROI(
        (width - roi_width) * 0.5,
        (height - roi_height) * 0.5,
        roi_width,
        roi_height,
    )
    if axis == "x":
        frame[:, width // 2 :, :] = 160
    else:
        frame[height // 2 :, :, :] = 160
    roi_mask = roi.mask(frame.shape)
    observation = EdgeFrontObservation(axis=axis)
    coordinate = ImageCoordinate()
    compact_context = FrameContext(0, 0.0, frame, compact_response_map=True)

    def previous():
        return _previous_full_frame_observe(frame, roi, observation)

    def current():
        return observation.observe(frame, roi, coordinate, compact_context)

    previous_ms, previous_peak = _measure(previous, rounds)
    current_ms, current_peak = _measure(current, rounds)
    previous_result = previous()
    current_result = current()
    previous_response = _expanded_response(previous_result, frame.shape[:2])
    current_response = _expanded_response(current_result, frame.shape[:2])
    current_local_bytes = 0 if current_result.response_map is None else int(current_result.response_map.nbytes)

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "axis": axis,
        "roi_linear_ratio": ratio,
        "roi_pixel_ratio": float(np.count_nonzero(roi_mask) / roi_mask.size),
        "roi_bounds": list(roi.bounds()),
        "measurements": {
            "previous_median_ms": previous_ms,
            "current_median_ms": current_ms,
            "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
            "previous_python_peak_bytes": previous_peak,
            "current_python_peak_bytes": current_peak,
            "python_peak_reduction_percent": (
                100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
            ),
            "previous_response_bytes": int(previous_response.nbytes),
            "current_retained_response_bytes": current_local_bytes,
        },
        "parity": {
            "max_response_error": float(
                np.max(np.abs(previous_response - current_response), initial=0.0)
            ),
            "previous_candidate": _candidate_summary(previous_result),
            "current_candidate": _candidate_summary(current_result),
        },
        "outside_roi_distractor": _distractor_check(axis),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark ROI-local EdgeFront observation against the previous full-frame path."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--axis", choices=("x", "y"), default="x")
    parser.add_argument(
        "--roi-ratio",
        type=float,
        default=0.2,
        help="ROI width and height as a fraction of the frame (0.2 means about 4%% of frame pixels).",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(args.width, args.height, args.rounds, args.axis, args.roi_ratio),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
