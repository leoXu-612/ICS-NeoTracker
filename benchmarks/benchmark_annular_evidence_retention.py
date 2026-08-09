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
from neo_tracker.core import FrameContext, ObservationResult, TrackerResult
from neo_tracker.observations import AnnularRadialFrontObservation
from neo_tracker.roi import AnnularROI
from neo_tracker.ui.review_diagnostics import angular_response


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


def _previous_tiled_result(result: ObservationResult, n_radii: int) -> ObservationResult:
    theta_signal = np.asarray(result.debug_layers["theta_signal"])
    return ObservationResult(
        candidates=result.candidates,
        response_map=np.tile(theta_signal[np.newaxis, :], (int(n_radii), 1)),
        debug_layers=result.debug_layers,
    )


def _array_bytes(result: ObservationResult) -> tuple[int, int]:
    arrays: list[np.ndarray] = []
    if isinstance(result.response_map, np.ndarray):
        arrays.append(result.response_map)
    arrays.extend(
        value for value in result.debug_layers.values() if isinstance(value, np.ndarray)
    )
    unique = {id(value): value for value in arrays}
    total = sum(int(value.nbytes) for value in unique.values())
    heavy = sum(int(value.nbytes) for value in unique.values() if value.ndim >= 2)
    return total, heavy


def _case(
    *,
    n_radii: int,
    n_angles: int,
    rounds: int,
    seed: int,
) -> dict[str, object]:
    rng = np.random.default_rng(int(seed))
    frame = rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8)
    center = (256.0, 256.0)
    roi = AnnularROI(center=center, inner_radius=110.0, outer_radius=190.0)
    coordinate = AnnularCoordinate(
        center_px=center,
        inner_radius=roi.inner_radius,
        outer_radius=roi.outer_radius,
    )
    observation = AnnularRadialFrontObservation(
        n_angles=int(n_angles),
        n_radii=int(n_radii),
        response_kind="fire",
        min_response=0.0,
        smoothing=4,
    )
    context = FrameContext(0, 0.0, frame)

    def current_observe() -> ObservationResult:
        return observation.observe(frame, roi, coordinate, context)

    def previous_observe() -> ObservationResult:
        return _previous_tiled_result(current_observe(), observation.n_radii)

    def previous_review() -> np.ndarray:
        response_map = previous_observe().response_map
        assert isinstance(response_map, np.ndarray)
        return np.array(response_map, copy=True)

    current_result = current_observe()
    retained_result = TrackerResult(
        frame_index=0,
        time_s=0.0,
        state={},
        filtered_state={},
        confidence=1.0,
        status="ok",
        debug={"debug_layers": current_result.debug_layers},
    )

    def current_review() -> np.ndarray | None:
        return angular_response(retained_result)

    previous_observe_ms, previous_observe_peak = _measure(previous_observe, rounds)
    current_observe_ms, current_observe_peak = _measure(current_observe, rounds)
    previous_review_ms, previous_review_peak = _measure(previous_review, rounds)
    current_review_ms, current_review_peak = _measure(current_review, rounds)
    previous_result = previous_observe()
    previous_total, previous_heavy = _array_bytes(previous_result)
    current_total, current_heavy = _array_bytes(current_result)
    previous_theta = np.asarray(previous_result.debug_layers["theta_signal"])
    current_theta = np.asarray(current_result.debug_layers["theta_signal"])

    return {
        "sample_grid": [int(n_radii), int(n_angles)],
        "sample_count": int(n_radii * n_angles),
        "previous_response_map_bytes": int(previous_result.response_map.nbytes),
        "current_response_map_bytes": 0,
        "previous_result_array_bytes": previous_total,
        "current_result_array_bytes": current_total,
        "result_array_reduction_percent": 100.0 * (previous_total - current_total) / previous_total,
        "previous_heavy_debug_bytes": previous_heavy,
        "current_heavy_debug_bytes": current_heavy,
        "heavy_debug_reduction_percent": 100.0 * (previous_heavy - current_heavy) / previous_heavy,
        "four_frame_heavy_history_bytes": {
            "previous": previous_heavy * 4,
            "current": current_heavy * 4,
        },
        "observe": {
            "previous_median_ms": previous_observe_ms,
            "current_median_ms": current_observe_ms,
            "speedup": previous_observe_ms / current_observe_ms,
            "previous_python_peak_bytes": previous_observe_peak,
            "current_python_peak_bytes": current_observe_peak,
            "python_peak_reduction_percent": (
                100.0 * (previous_observe_peak - current_observe_peak) / previous_observe_peak
            ),
        },
        "review_response_access": {
            "previous_recompute_and_copy_median_ms": previous_review_ms,
            "current_retained_signal_median_ms": current_review_ms,
            "speedup": previous_review_ms / current_review_ms,
            "previous_python_peak_bytes": previous_review_peak,
            "current_python_peak_bytes": current_review_peak,
            "python_peak_reduction_percent": (
                100.0 * (previous_review_peak - current_review_peak) / previous_review_peak
            ),
        },
        "parity": {
            "max_theta_signal_error": float(
                np.max(np.abs(previous_theta - current_theta), initial=0.0)
            ),
            "candidate_count_equal": len(previous_result.candidates) == len(current_result.candidates),
            "candidate_state_equal": (
                previous_result.candidates[0].state == current_result.candidates[0].state
            ),
            "candidate_score_delta": float(
                previous_result.candidates[0].score - current_result.candidates[0].score
            ),
        },
    }


def benchmark(rounds: int, seed: int) -> dict[str, object]:
    return {
        "rounds": max(3, int(rounds)),
        "seed": int(seed),
        "frame_shape": [512, 512, 3],
        "cases": {
            "preset": _case(n_radii=24, n_angles=720, rounds=rounds, seed=seed),
            "dense": _case(n_radii=128, n_angles=2048, rounds=rounds, seed=seed),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark retained annular evidence without the repeated polar response matrix."
    )
    parser.add_argument("--rounds", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260714)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
