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

from neo_tracker.coordinates import AnnularCoordinate
from neo_tracker.core import FrameContext, ObservationResult
import neo_tracker.observations as observation_module
from neo_tracker.observations import AnnularRadialFrontObservation
from neo_tracker.roi import AnnularROI


def _measure(operation, rounds: int) -> tuple[float, int]:
    operation()
    elapsed: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        elapsed.append((perf_counter() - started) * 1000.0)
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    del result
    tracemalloc.stop()
    return median(elapsed), int(peak)


def _candidate_summary(result: ObservationResult) -> dict[str, object] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "state": dict(candidate.state),
        "score": float(candidate.score),
        "image_point": list(candidate.image_point) if candidate.image_point is not None else None,
    }


def _case(
    *,
    n_radii: int,
    n_angles: int,
    response_kind: str,
    rounds: int,
    seed: int,
) -> dict[str, object]:
    rng = np.random.default_rng(int(seed))
    bgr = rng.integers(0, 256, size=(1080, 1920, 3), dtype=np.uint8)
    frame = bgr[:, :, ::-1]
    center = (960.0, 540.0)
    roi = AnnularROI(center=center, inner_radius=210.0, outer_radius=490.0)
    coordinate = AnnularCoordinate(
        center_px=center,
        inner_radius=roi.inner_radius,
        outer_radius=roi.outer_radius,
    )
    observation = AnnularRadialFrontObservation(
        n_angles=int(n_angles),
        n_radii=int(n_radii),
        response_kind=response_kind,
        min_response=0.0,
        smoothing=4,
    )
    context = FrameContext(0, 0.0, frame)
    observation.observe(frame, roi, coordinate, context)
    grid = observation._sample_grid_cache
    linear_indices = observation._sample_linear_indices_cache
    assert grid is not None and linear_indices is not None
    _angles, iy, ix = grid

    def previous_sample() -> np.ndarray:
        return observation_module._annular_sample_response(frame, iy, ix, response_kind)

    def current_sample() -> np.ndarray:
        return observation_module._annular_sample_response(
            frame,
            iy,
            ix,
            response_kind,
            linear_indices=linear_indices,
        )

    previous_sample_ms, previous_sample_peak = _measure(previous_sample, rounds)
    current_sample_ms, current_sample_peak = _measure(current_sample, rounds)

    accepted_sample = observation_module._annular_sample_response

    def previous_gather(frame, iy, ix, response_kind, **_kwargs):
        return accepted_sample(frame, iy, ix, response_kind)

    try:
        observation_module._annular_sample_response = previous_gather
        previous_observe_ms, previous_observe_peak = _measure(
            lambda: observation.observe(frame, roi, coordinate, context),
            rounds,
        )
        previous_result = observation.observe(frame, roi, coordinate, context)
    finally:
        observation_module._annular_sample_response = accepted_sample

    current_observe_ms, current_observe_peak = _measure(
        lambda: observation.observe(frame, roi, coordinate, context),
        rounds,
    )
    current_result = observation.observe(frame, roi, coordinate, context)
    previous_samples = previous_result.debug_layers["polar_samples"]
    current_samples = current_result.debug_layers["polar_samples"]
    previous_signal = previous_result.debug_layers["theta_signal"]
    current_signal = current_result.debug_layers["theta_signal"]

    return {
        "sample_grid": [int(n_radii), int(n_angles)],
        "response_kind": response_kind,
        "sample_count": int(n_radii * n_angles),
        "linear_index_bytes": int(linear_indices.nbytes),
        "linear_indices_read_only": not linear_indices.flags.writeable,
        "sample_kernel": {
            "previous_grid_gather_median_ms": previous_sample_ms,
            "current_linear_gather_median_ms": current_sample_ms,
            "speedup": previous_sample_ms / current_sample_ms,
            "time_reduction_percent": 100.0 * (previous_sample_ms - current_sample_ms) / previous_sample_ms,
            "previous_python_peak_bytes": previous_sample_peak,
            "current_python_peak_bytes": current_sample_peak,
            "python_peak_reduction_percent": 100.0
            * (previous_sample_peak - current_sample_peak)
            / previous_sample_peak,
        },
        "complete_observe": {
            "previous_grid_gather_median_ms": previous_observe_ms,
            "current_linear_gather_median_ms": current_observe_ms,
            "speedup": previous_observe_ms / current_observe_ms,
            "time_reduction_percent": 100.0 * (previous_observe_ms - current_observe_ms) / previous_observe_ms,
            "previous_python_peak_bytes": previous_observe_peak,
            "current_python_peak_bytes": current_observe_peak,
            "python_peak_reduction_percent": 100.0
            * (previous_observe_peak - current_observe_peak)
            / previous_observe_peak,
        },
        "parity": {
            "max_polar_samples_error": float(
                np.max(np.abs(previous_samples - current_samples), initial=0.0)
            ),
            "max_theta_signal_error": float(
                np.max(np.abs(previous_signal - current_signal), initial=0.0)
            ),
            "previous_candidate": _candidate_summary(previous_result),
            "current_candidate": _candidate_summary(current_result),
        },
    }


def benchmark(rounds: int, seed: int) -> dict[str, object]:
    return {
        "rounds": max(3, int(rounds)),
        "seed": int(seed),
        "frame_shape": [1080, 1920, 3],
        "source_layout": "negative-channel-stride uint8 RGB view",
        "cases": {
            "preset_fire": _case(
                n_radii=24,
                n_angles=720,
                response_kind="fire",
                rounds=rounds,
                seed=seed,
            ),
            "preset_brightness": _case(
                n_radii=24,
                n_angles=720,
                response_kind="brightness",
                rounds=rounds,
                seed=seed + 1,
            ),
            "dense_fire": _case(
                n_radii=128,
                n_angles=2048,
                response_kind="fire",
                rounds=rounds,
                seed=seed + 2,
            ),
            "dense_brightness": _case(
                n_radii=128,
                n_angles=2048,
                response_kind="brightness",
                rounds=rounds,
                seed=seed + 3,
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark annular RGB grid gathering against cached flattened pixel indices."
    )
    parser.add_argument("--rounds", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260715)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
