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


def _clear_grid_cache(observation: AnnularRadialFrontObservation) -> None:
    observation._sample_grid_cache_key = None
    observation._sample_grid_cache = None


def _candidate_summary(result: ObservationResult) -> dict[str, object] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "state": dict(candidate.state),
        "score": float(candidate.score),
        "image_point": list(candidate.image_point) if candidate.image_point is not None else None,
    }


def _case(*, n_radii: int, n_angles: int, rounds: int, seed: int) -> dict[str, object]:
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

    def previous_rebuild() -> ObservationResult:
        _clear_grid_cache(observation)
        return observation.observe(frame, roi, coordinate, context)

    def current_cached() -> ObservationResult:
        return observation.observe(frame, roi, coordinate, context)

    previous_ms, previous_peak = _measure(previous_rebuild, rounds)
    current_ms, current_peak = _measure(current_cached, rounds)
    previous_result = previous_rebuild()
    current_result = current_cached()
    grid = observation._sample_grid_cache
    assert grid is not None
    cache_bytes = sum(int(values.nbytes) for values in grid)
    previous_theta = np.asarray(previous_result.debug_layers["theta_signal"])
    current_theta = np.asarray(current_result.debug_layers["theta_signal"])

    return {
        "sample_grid": [int(n_radii), int(n_angles)],
        "sample_count": int(n_radii * n_angles),
        "persistent_read_only_grid_bytes": cache_bytes,
        "grid_arrays_read_only": all(not values.flags.writeable for values in grid),
        "previous_rebuild_median_ms": previous_ms,
        "current_cached_median_ms": current_ms,
        "speedup": previous_ms / current_ms,
        "time_reduction_percent": 100.0 * (previous_ms - current_ms) / previous_ms,
        "previous_rebuild_python_peak_bytes": previous_peak,
        "current_cached_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": 100.0 * (previous_peak - current_peak) / previous_peak,
        "parity": {
            "max_polar_samples_error": float(
                np.max(
                    np.abs(
                        previous_result.debug_layers["polar_samples"]
                        - current_result.debug_layers["polar_samples"]
                    ),
                    initial=0.0,
                )
            ),
            "max_theta_signal_error": float(
                np.max(np.abs(previous_theta - current_theta), initial=0.0)
            ),
            "previous_candidate": _candidate_summary(previous_result),
            "current_candidate": _candidate_summary(current_result),
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
        description="Benchmark reusable annular sample geometry against rebuilding it per frame."
    )
    parser.add_argument("--rounds", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260714)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
