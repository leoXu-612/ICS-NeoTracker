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
from neo_tracker.observations import AnnularRadialFrontObservation, _fire_response
from neo_tracker.roi import AnnularROI


def _previous_fire_response(frame: np.ndarray) -> np.ndarray:
    """Reference the prior fused mapping with its redundant reduction/copy."""
    source = np.asarray(frame)
    if source.ndim == 2:
        return observation_module._as_float01(source)
    if source.dtype != np.uint8:
        return _fire_response(source)
    response = source[:, :, 0].astype(np.float32)
    response *= np.float32((0.45 * 0.299 + 0.35) / 255.0)
    scratch = source[:, :, 1].astype(np.float32)
    scratch *= np.float32((0.45 * 0.587 + 0.25) / 255.0)
    response += scratch
    scratch[:] = source[:, :, 2]
    scratch *= np.float32((0.45 * 0.114 - 0.25) / 255.0)
    response += scratch
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


def _candidate_summary(result) -> dict[str, float] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "theta": float(candidate.state["theta"]),
        "score": float(candidate.score),
    }


def benchmark(width: int, height: int, rounds: int, seed: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    rng = np.random.default_rng(int(seed))
    random_bgr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    random_frame = random_bgr[:, :, ::-1]
    constant_frame = np.full((height, width, 3), 127, dtype=np.uint8)[:, :, ::-1]
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

    def observe(frame: np.ndarray):
        context = FrameContext(0, 0.0, frame)
        return observation.observe(frame, roi, coordinate, context)

    def compare_case(frame: np.ndarray) -> dict[str, object]:
        expected = _previous_fire_response(frame)
        actual = _fire_response(frame)
        previous_ms, previous_peak = _measure(lambda: _previous_fire_response(frame), rounds)
        current_ms, current_peak = _measure(lambda: _fire_response(frame), rounds)
        with patch.object(observation_module, "_fire_response", _previous_fire_response):
            previous_observation = observe(frame)
            previous_observe_ms, previous_observe_peak = _measure(lambda: observe(frame), rounds)
        current_observation = observe(frame)
        current_observe_ms, current_observe_peak = _measure(lambda: observe(frame), rounds)
        theta_error = float(
            np.max(
                np.abs(
                    previous_observation.debug_layers["theta_signal"]
                    - current_observation.debug_layers["theta_signal"]
                ),
                initial=0.0,
            )
        )
        polar_error = float(
            np.max(
                np.abs(
                    previous_observation.debug_layers["polar_samples"]
                    - current_observation.debug_layers["polar_samples"]
                ),
                initial=0.0,
            )
        )
        return {
            "previous_response_median_ms": previous_ms,
            "current_response_median_ms": current_ms,
            "response_speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
            "previous_response_peak_bytes": previous_peak,
            "current_response_peak_bytes": current_peak,
            "response_peak_reduction_percent": (
                100.0 * (previous_peak - current_peak) / previous_peak
                if previous_peak > 0
                else 0.0
            ),
            "previous_observe_median_ms": previous_observe_ms,
            "current_observe_median_ms": current_observe_ms,
            "observe_speedup": (
                previous_observe_ms / current_observe_ms if current_observe_ms > 0.0 else 0.0
            ),
            "previous_observe_peak_bytes": previous_observe_peak,
            "current_observe_peak_bytes": current_observe_peak,
            "observe_peak_reduction_percent": (
                100.0 * (previous_observe_peak - current_observe_peak) / previous_observe_peak
                if previous_observe_peak > 0
                else 0.0
            ),
            "max_response_error": float(np.max(np.abs(expected - actual), initial=0.0)),
            "response_exact": bool(np.array_equal(expected, actual)),
            "max_polar_samples_error": polar_error,
            "max_theta_signal_error": theta_error,
            "previous_candidate": _candidate_summary(previous_observation),
            "current_candidate": _candidate_summary(current_observation),
        }

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "negative_channel_stride": bool(random_frame.strides[-1] < 0),
        "random": compare_case(random_frame),
        "constant": compare_case(constant_frame),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark single-scan fire normalization and constant-frame buffer reuse."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20260714)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
