from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc
from unittest.mock import patch

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker import observations as observation_module
from neo_tracker.coordinates import AnnularCoordinate
from neo_tracker.core import FrameContext
from neo_tracker.observations import AnnularRadialFrontObservation, _fire_response, _intensity
from neo_tracker.roi import AnnularROI


def _previous_fire_response(frame: np.ndarray) -> np.ndarray:
    """Reference the previous two-pass intensity plus chromatic mapping."""
    source = np.asarray(frame)
    if source.ndim == 2:
        return observation_module._as_float01(source)
    if source.dtype == np.uint8:
        response = _intensity(source)
        response *= np.float32(0.45)
        scratch = source[:, :, 0].astype(np.float32)
        scratch *= np.float32(0.35 / 255.0)
        response += scratch
        scratch[:] = source[:, :, 1]
        scratch *= np.float32(0.25 / 255.0)
        response += scratch
        scratch[:] = source[:, :, 2]
        scratch *= np.float32(0.25 / 255.0)
        response -= scratch
    else:
        arr = observation_module._as_float01(source)
        red = arr[:, :, 0]
        green = arr[:, :, 1]
        blue = arr[:, :, 2]
        brightness = _intensity(arr)
        response = 0.45 * brightness + 0.35 * red + 0.25 * green - 0.25 * blue
    response -= response.min()
    denom = response.max() - response.min()
    if denom <= 1e-12:
        return np.zeros_like(response)
    response /= denom
    return response


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


def benchmark(width: int, height: int, rounds: int, seed: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    rng = np.random.default_rng(int(seed))
    bgr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    rgb_view = bgr[:, :, ::-1]
    center = (width * 0.5, height * 0.5)
    scale = min(width, height)
    roi = AnnularROI(center=center, inner_radius=scale * 0.18, outer_radius=scale * 0.31)
    coordinate = AnnularCoordinate(
        center_px=center,
        inner_radius=roi.inner_radius,
        outer_radius=roi.outer_radius,
    )
    observation = AnnularRadialFrontObservation(
        n_angles=720,
        n_radii=24,
        response_kind="fire",
        min_response=0.0,
        smoothing=3,
    )
    context = FrameContext(0, 0.0, rgb_view)

    def observe():
        return observation.observe(rgb_view, roi, coordinate, context)

    expected = _previous_fire_response(rgb_view)
    actual = _fire_response(rgb_view)
    previous_ms, previous_peak = _measure(lambda: _previous_fire_response(rgb_view), rounds)
    current_ms, current_peak = _measure(lambda: _fire_response(rgb_view), rounds)
    with patch.object(observation_module, "_fire_response", _previous_fire_response):
        previous_observation = observe()
        previous_observe_ms, _ = _measure(observe, rounds)
    current_observation = observe()
    current_observe_ms, _ = _measure(observe, rounds)
    previous_candidate = previous_observation.candidates[0]
    current_candidate = current_observation.candidates[0]

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "negative_channel_stride": bool(rgb_view.strides[-1] < 0),
        "previous_fire_median_ms": previous_ms,
        "current_fire_median_ms": current_ms,
        "fire_speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
        "previous_python_peak_bytes": previous_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
        ),
        "previous_observe_median_ms": previous_observe_ms,
        "current_observe_median_ms": current_observe_ms,
        "observe_speedup": (
            previous_observe_ms / current_observe_ms if current_observe_ms > 0.0 else 0.0
        ),
        "max_fire_response_error": float(np.max(np.abs(expected - actual), initial=0.0)),
        "max_theta_signal_error": float(
            np.max(
                np.abs(
                    previous_observation.debug_layers["theta_signal"]
                    - current_observation.debug_layers["theta_signal"]
                ),
                initial=0.0,
            )
        ),
        "candidate_theta_delta": float(
            current_candidate.state["theta"] - previous_candidate.state["theta"]
        ),
        "candidate_score_delta": float(current_candidate.score - previous_candidate.score),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark fused uint8 fire response against the previous two-pass mapping."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260714)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
