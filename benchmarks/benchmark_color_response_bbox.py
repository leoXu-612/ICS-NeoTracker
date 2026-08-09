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

from neo_tracker import observations as observation_module
from neo_tracker.media import MediaReader, probe_media
from neo_tracker.presets import color_marker_preset


def _evaluate(
    frames: list[np.ndarray],
    *,
    local_bbox_ratio: float,
    sample_rgb: tuple[float, float, float],
    tolerance: float,
) -> list[np.ndarray]:
    original_ratio = observation_module._COLOR_RESPONSE_LOCAL_BBOX_RATIO
    try:
        observation_module._COLOR_RESPONSE_LOCAL_BBOX_RATIO = float(local_bbox_ratio)
        return [
            observation_module._color_distance_response(frame, sample_rgb, tolerance)
            for frame in frames
        ]
    finally:
        observation_module._COLOR_RESPONSE_LOCAL_BBOX_RATIO = original_ratio


def _measure_peak(
    frame: np.ndarray,
    *,
    local_bbox_ratio: float,
    sample_rgb: tuple[float, float, float],
    tolerance: float,
) -> int:
    tracemalloc.start()
    _evaluate(
        [frame],
        local_bbox_ratio=local_bbox_ratio,
        sample_rgb=sample_rgb,
        tolerance=tolerance,
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return int(peak)


def _measure_dense(
    frame: np.ndarray,
    *,
    rounds: int,
    sample_rgb: tuple[float, float, float],
    tolerance: float,
    current_ratio: float,
) -> tuple[float, float]:
    baseline: list[float] = []
    current: list[float] = []
    for round_index in range(max(1, int(rounds))):
        order = (
            ((0.0, baseline), (current_ratio, current))
            if round_index % 2 == 0
            else ((current_ratio, current), (0.0, baseline))
        )
        for ratio, samples in order:
            started = perf_counter()
            _evaluate(
                [frame],
                local_bbox_ratio=ratio,
                sample_rgb=sample_rgb,
                tolerance=tolerance,
            )
            samples.append(perf_counter() - started)
    return median(baseline), median(current)


def benchmark(media_path: Path, rounds: int, repeats: int) -> dict[str, object]:
    resolved_path = media_path.expanduser().resolve()
    info = probe_media(str(resolved_path))
    if not info.available or info.kind != "video":
        raise RuntimeError(info.error or f"Could not open video: {resolved_path}")
    observation = color_marker_preset().observation_model
    frames: list[np.ndarray] = []
    with MediaReader(str(resolved_path)) as reader:
        for frame_index in range(info.frame_count):
            frames.append(reader.read_frame_for_processing(frame_index))

    sample_rgb = observation.sample_rgb
    tolerance = observation.tolerance
    current_ratio = observation_module._COLOR_RESPONSE_LOCAL_BBOX_RATIO
    baseline_results = _evaluate(
        frames,
        local_bbox_ratio=0.0,
        sample_rgb=sample_rgb,
        tolerance=tolerance,
    )
    current_results = _evaluate(
        frames,
        local_bbox_ratio=current_ratio,
        sample_rgb=sample_rgb,
        tolerance=tolerance,
    )
    maximum_error = max(
        float(np.max(np.abs(old - new), initial=0.0))
        for old, new in zip(baseline_results, current_results)
    )
    exact_match = all(
        np.array_equal(old, new)
        for old, new in zip(baseline_results, current_results)
    )

    rounds = max(1, int(rounds))
    repeats = max(1, int(repeats))
    baseline_samples: list[float] = []
    current_samples: list[float] = []
    for round_index in range(rounds):
        order = (
            ((0.0, baseline_samples), (current_ratio, current_samples))
            if round_index % 2 == 0
            else ((current_ratio, current_samples), (0.0, baseline_samples))
        )
        for ratio, samples in order:
            started = perf_counter()
            for _ in range(repeats):
                _evaluate(
                    frames,
                    local_bbox_ratio=ratio,
                    sample_rgb=sample_rgb,
                    tolerance=tolerance,
                )
            samples.append(perf_counter() - started)

    baseline_s = median(baseline_samples)
    current_s = median(current_samples)
    evaluations = len(frames) * repeats
    sample_u8 = np.asarray(sample_rgb, dtype=np.uint8)
    dense_bgr = np.empty_like(frames[0][:, :, 2::-1])
    dense_bgr[:] = sample_u8[::-1]
    dense_rgb = dense_bgr[:, :, ::-1]
    dense_baseline_s, dense_current_s = _measure_dense(
        dense_rgb,
        rounds=101,
        sample_rgb=sample_rgb,
        tolerance=tolerance,
        current_ratio=current_ratio,
    )
    return {
        "media": str(resolved_path),
        "resolution": f"{info.width}x{info.height}",
        "frames": info.frame_count,
        "rounds": rounds,
        "evaluations_per_round": evaluations,
        "baseline_full_plane_us_per_frame": 1_000_000.0 * baseline_s / evaluations,
        "current_local_bbox_us_per_frame": 1_000_000.0 * current_s / evaluations,
        "sparse_speedup": baseline_s / current_s if current_s > 0.0 else 0.0,
        "sparse_reduction_percent": (
            100.0 * (baseline_s - current_s) / baseline_s if baseline_s > 0.0 else 0.0
        ),
        "baseline_python_peak_bytes": _measure_peak(
            frames[len(frames) // 2],
            local_bbox_ratio=0.0,
            sample_rgb=sample_rgb,
            tolerance=tolerance,
        ),
        "current_python_peak_bytes": _measure_peak(
            frames[len(frames) // 2],
            local_bbox_ratio=current_ratio,
            sample_rgb=sample_rgb,
            tolerance=tolerance,
        ),
        "dense_baseline_us_per_frame": 1_000_000.0 * dense_baseline_s,
        "dense_current_us_per_frame": 1_000_000.0 * dense_current_s,
        "dense_speedup": dense_baseline_s / dense_current_s if dense_current_s > 0.0 else 0.0,
        "maximum_response_error": maximum_error,
        "responses_match_exactly": exact_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare full-plane and conservative local-box Color Marker response work."
    )
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.media, args.rounds, args.repeats), indent=2))


if __name__ == "__main__":
    main()
