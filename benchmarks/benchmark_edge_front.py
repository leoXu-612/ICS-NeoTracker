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
from neo_tracker.core import FrameContext
from neo_tracker.observations import EdgeFrontObservation, _intensity
from neo_tracker.roi import RectangularROI


def _legacy_observe(
    frame: np.ndarray,
    roi: RectangularROI,
    axis: str,
) -> tuple[np.ndarray, tuple[float, float]]:
    image = _intensity(frame)
    gradient_y, gradient_x = np.gradient(image)
    response = np.abs(gradient_x if axis == "x" else gradient_y)
    max_value = float(response.max())
    if max_value > 1e-12:
        response = response / max_value
    response = np.where(roi.mask(frame.shape), response, 0.0)
    y, x = np.unravel_index(int(np.argmax(response)), response.shape)
    return response, (float(x), float(y))


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


def benchmark(width: int, height: int, rounds: int, axis: str) -> dict[str, object]:
    width = int(width)
    height = int(height)
    axis = str(axis).strip().lower()
    if axis not in {"x", "y"}:
        raise ValueError("axis must be 'x' or 'y'")

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    if axis == "x":
        frame[:, width // 2 :, :] = 255
    else:
        frame[height // 2 :, :, :] = 255
    roi = RectangularROI(0.0, 0.0, float(width - 1), float(height - 1))
    roi.mask(frame.shape)  # Exclude one-time cached ROI construction from both measurements.
    context = FrameContext(0, 0.0, frame)
    coordinate = ImageCoordinate()
    observation = EdgeFrontObservation(axis=axis)

    def current_observe():
        return observation.observe(frame, roi, coordinate, context)

    legacy_response, legacy_candidate = _legacy_observe(frame, roi, axis)
    current_result = current_observe()
    current_candidate = current_result.candidates[0].image_point
    if current_candidate is None or current_result.response_map is None:
        raise RuntimeError("edge-front benchmark unexpectedly produced no candidate or response")

    legacy_ms, legacy_peak = _measure(lambda: _legacy_observe(frame, roi, axis), rounds)
    current_ms, current_peak = _measure(current_observe, rounds)
    primary_index = 0 if axis == "x" else 1

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "axis": axis,
        "legacy_median_ms": legacy_ms,
        "current_median_ms": current_ms,
        "speedup": legacy_ms / current_ms if current_ms > 0.0 else 0.0,
        "legacy_python_peak_bytes": legacy_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (legacy_peak - current_peak) / legacy_peak if legacy_peak > 0 else 0.0
        ),
        "max_response_error": float(
            np.max(np.abs(legacy_response - current_result.response_map), initial=0.0)
        ),
        "legacy_candidate": list(legacy_candidate),
        "current_candidate": list(current_candidate),
        "primary_coordinate_delta": float(
            current_candidate[primary_index] - legacy_candidate[primary_index]
        ),
        "response_dtype": str(current_result.response_map.dtype),
        "response_bytes": int(current_result.response_map.nbytes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the single-axis edge-front observation against the previous two-axis path."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--axis", choices=("x", "y"), default="x")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.axis), indent=2))


if __name__ == "__main__":
    main()
