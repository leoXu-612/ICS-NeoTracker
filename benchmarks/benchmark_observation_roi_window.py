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
from neo_tracker.observations import (
    BrightnessPeakObservation,
    ColorBlobObservation,
    _color_distance_response,
    _component_centroids,
    _intensity,
)
from neo_tracker.roi import RectangularROI


def _previous_color(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: ColorBlobObservation,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    response = _color_distance_response(frame, observation.sample_rgb, observation.tolerance)
    np.multiply(response, roi.mask(frame.shape), out=response)
    components = _component_centroids(
        response,
        observation.min_response,
        max_candidates=observation.max_candidates,
        min_area=observation.min_component_area,
    )
    return response, components


def _previous_brightness(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: BrightnessPeakObservation,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    response = _intensity(frame)
    if observation.polarity == "dark":
        np.subtract(np.float32(1.0), response, out=response)
    roi_mask = roi.mask(frame.shape)
    floor = float(np.percentile(response[roi_mask], observation.percentile_floor))
    np.subtract(response, floor, out=response)
    np.divide(response, max(1.0 - floor, 1e-12), out=response)
    np.clip(response, 0.0, 1.0, out=response)
    np.multiply(response, roi_mask, out=response)
    components = _component_centroids(
        response,
        observation.min_response,
        max_candidates=observation.max_candidates,
        min_area=observation.min_component_area,
    )
    return response, components


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


def _component_signature(components: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "x": float(component["x"]),
            "y": float(component["y"]),
            "score": float(component["score"]),
            "area": int(component["area"]),
            "bbox": list(component["bbox"]),
            "peak_response": float(component["peak_response"]),
        }
        for component in components
    ]


def _measurement(previous, current, rounds: int) -> dict[str, float | int]:
    previous_ms, previous_peak = _measure(previous, rounds)
    current_ms, current_peak = _measure(current, rounds)
    return {
        "previous_median_ms": previous_ms,
        "current_median_ms": current_ms,
        "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
        "previous_python_peak_bytes": previous_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
        ),
    }


def benchmark(width: int, height: int, rounds: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    roi = RectangularROI(width / 6.0, height / 6.0, width * 2.0 / 3.0, height * 2.0 / 3.0)
    center_x = width // 2
    center_y = height // 2
    frame[center_y - 8 : center_y + 9, center_x - 9 : center_x + 10, 0] = 255
    roi.mask(frame.shape)
    context = FrameContext(0, 0.0, frame)
    coordinate = ImageCoordinate()
    observations = {
        "color": ColorBlobObservation(
            sample_rgb=(255.0, 0.0, 0.0),
            tolerance=0.08,
            min_response=0.35,
        ),
        "brightness": BrightnessPeakObservation(polarity="bright", min_response=0.25),
        "dark_brightness": BrightnessPeakObservation(polarity="dark", min_response=0.25),
    }
    previous_functions = {
        "color": _previous_color,
        "brightness": _previous_brightness,
        "dark_brightness": _previous_brightness,
    }

    measurements: dict[str, object] = {}
    parity: dict[str, object] = {}
    for name, observation in observations.items():
        previous_function = previous_functions[name]

        def previous(observation=observation, previous_function=previous_function):
            return previous_function(frame, roi, observation)

        def current(observation=observation):
            return observation.observe(frame, roi, coordinate, context)

        measurements[name] = _measurement(previous, current, rounds)
        previous_response, previous_components = previous()
        current_result = current()
        current_components = [
            {
                "x": candidate.image_point[0],
                "y": candidate.image_point[1],
                "score": candidate.score,
                "area": candidate.raw["component_area"],
                "bbox": candidate.raw["bbox"],
                "peak_response": candidate.raw["peak_response"],
            }
            for candidate in current_result.candidates
        ]
        parity[name] = {
            "max_response_error": float(
                np.max(np.abs(previous_response - current_result.response_map), initial=0.0)
            ),
            "previous_components": _component_signature(previous_components),
            "current_components": _component_signature(current_components),
        }

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "roi_bounds": list(roi.bounds()),
        "roi_window_ratio": float(
            ((int(np.ceil(roi.width)) + 1) * (int(np.ceil(roi.height)) + 1)) / (width * height)
        ),
        "full_response_bytes": int(width * height * np.dtype(np.float32).itemsize),
        "measurements": measurements,
        "parity": parity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark ROI-windowed Color/Brightness observation against full-frame compute."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds), indent=2))


if __name__ == "__main__":
    main()
