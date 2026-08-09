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


def _legacy_color(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: ColorBlobObservation,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    response = _color_distance_response(frame, observation.sample_rgb, observation.tolerance)
    response = np.where(roi.mask(frame.shape), response, 0.0)
    components = _component_centroids(
        response,
        observation.min_response,
        max_candidates=observation.max_candidates,
        min_area=observation.min_component_area,
    )
    return response, components


def _legacy_brightness(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: BrightnessPeakObservation,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    response = _intensity(frame)
    if observation.polarity == "dark":
        response = 1.0 - response
    roi_mask = roi.mask(frame.shape)
    floor = float(np.percentile(response[roi_mask], observation.percentile_floor)) if np.any(roi_mask) else 0.0
    response = np.clip((response - floor) / max(1.0 - floor, 1e-12), 0.0, 1.0)
    response = np.where(roi_mask, response, 0.0)
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


def _measurement(
    legacy_operation,
    current_operation,
    rounds: int,
) -> dict[str, float | int]:
    legacy_ms, legacy_peak = _measure(legacy_operation, rounds)
    current_ms, current_peak = _measure(current_operation, rounds)
    return {
        "legacy_median_ms": legacy_ms,
        "current_median_ms": current_ms,
        "speedup": legacy_ms / current_ms if current_ms > 0.0 else 0.0,
        "legacy_python_peak_bytes": legacy_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (legacy_peak - current_peak) / legacy_peak if legacy_peak > 0 else 0.0
        ),
    }


def benchmark(width: int, height: int, rounds: int, seed: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    roi = RectangularROI(width / 6.0, height / 6.0, width * 2.0 / 3.0, height * 2.0 / 3.0)
    roi.mask(frame.shape)
    coordinate = ImageCoordinate()
    context = FrameContext(0, 0.0, frame)
    color = ColorBlobObservation(sample_rgb=(255.0, 0.0, 0.0), tolerance=0.12)
    brightness = BrightnessPeakObservation(polarity="bright")
    dark_brightness = BrightnessPeakObservation(polarity="dark")

    def current_observe(observation):
        return observation.observe(frame, roi, coordinate, context)

    measurements = {
        "color": _measurement(
            lambda: _legacy_color(frame, roi, color),
            lambda: current_observe(color),
            rounds,
        ),
        "brightness": _measurement(
            lambda: _legacy_brightness(frame, roi, brightness),
            lambda: current_observe(brightness),
            rounds,
        ),
        "dark_brightness": _measurement(
            lambda: _legacy_brightness(frame, roi, dark_brightness),
            lambda: current_observe(dark_brightness),
            rounds,
        ),
    }

    rng = np.random.default_rng(int(seed))
    probe = rng.integers(0, 256, size=(71, 83, 3), dtype=np.uint8)
    probe_roi = RectangularROI(9.0, 7.0, 61.0, 49.0)
    probe_context = FrameContext(0, 0.0, probe)
    parity: dict[str, float] = {}
    for name, observation, legacy in (
        ("color", color, _legacy_color),
        ("brightness", brightness, _legacy_brightness),
        ("dark_brightness", dark_brightness, _legacy_brightness),
    ):
        expected, _components = legacy(probe, probe_roi, observation)
        actual = observation.observe(probe, probe_roi, coordinate, probe_context).response_map
        parity[name] = float(np.max(np.abs(expected - actual), initial=0.0))

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "response_dtype": "float32",
        "response_bytes": int(width * height * np.dtype(np.float32).itemsize),
        "measurements": measurements,
        "max_response_error": parity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark in-place Color/Brightness ROI response transforms against the copy-based path."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260718)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
