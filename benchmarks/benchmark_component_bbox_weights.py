from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker import observations as observation_module
from neo_tracker.media import MediaReader, probe_media
from neo_tracker.presets import color_marker_preset


def _evaluate(
    responses: list[np.ndarray],
    *,
    local_bbox_ratio: float,
    threshold: float,
    max_candidates: int,
    min_area: int,
    cv2,
) -> list[list[dict[str, object]]]:
    original_ratio = observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO
    try:
        observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = float(local_bbox_ratio)
        return [
            observation_module._component_centroids_cv2(
                response,
                threshold,
                max_candidates=max_candidates,
                min_area=min_area,
                cv2=cv2,
            )
            for response in responses
        ]
    finally:
        observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = original_ratio


def _candidate_error(
    baseline: list[list[dict[str, object]]],
    current: list[list[dict[str, object]]],
) -> tuple[float, bool]:
    if len(baseline) != len(current):
        return float("inf"), False
    maximum = 0.0
    metadata_match = True
    for baseline_frame, current_frame in zip(baseline, current):
        if len(baseline_frame) != len(current_frame):
            return float("inf"), False
        for old, new in zip(baseline_frame, current_frame):
            metadata_match = (
                metadata_match
                and old["area"] == new["area"]
                and old["bbox"] == new["bbox"]
            )
            for key in ("x", "y", "score", "peak_response"):
                maximum = max(maximum, abs(float(old[key]) - float(new[key])))
    return maximum, metadata_match


def benchmark(media_path: Path, rounds: int, repeats: int) -> dict[str, object]:
    cv2 = observation_module._load_component_cv2()
    if cv2 is None:
        raise RuntimeError("OpenCV is required for this benchmark")
    resolved_path = media_path.expanduser().resolve()
    info = probe_media(str(resolved_path))
    if not info.available or info.kind != "video":
        raise RuntimeError(info.error or f"Could not open video: {resolved_path}")
    observation = color_marker_preset().observation_model
    responses: list[np.ndarray] = []
    with MediaReader(str(resolved_path)) as reader:
        for frame_index in range(info.frame_count):
            frame = reader.read_frame_for_processing(frame_index)
            responses.append(
                observation_module._color_distance_response(
                    frame,
                    observation.sample_rgb,
                    observation.tolerance,
                )
            )
    kwargs = {
        "threshold": observation.min_response,
        "max_candidates": observation.max_candidates,
        "min_area": observation.min_component_area,
        "cv2": cv2,
    }
    current_ratio = observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO
    baseline_results = _evaluate(responses, local_bbox_ratio=0.0, **kwargs)
    current_results = _evaluate(responses, local_bbox_ratio=current_ratio, **kwargs)
    maximum_error, metadata_match = _candidate_error(baseline_results, current_results)

    rounds = max(1, int(rounds))
    repeats = max(1, int(repeats))
    _evaluate(responses, local_bbox_ratio=0.0, **kwargs)
    _evaluate(responses, local_bbox_ratio=current_ratio, **kwargs)
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
                _evaluate(responses, local_bbox_ratio=ratio, **kwargs)
            samples.append(perf_counter() - started)

    baseline_s = median(baseline_samples)
    current_s = median(current_samples)
    evaluations = len(responses) * repeats
    return {
        "media": str(resolved_path),
        "resolution": f"{info.width}x{info.height}",
        "frames": info.frame_count,
        "rounds": rounds,
        "evaluations_per_round": evaluations,
        "baseline_full_scan_us_per_frame": 1_000_000.0 * baseline_s / evaluations,
        "current_local_bbox_us_per_frame": 1_000_000.0 * current_s / evaluations,
        "speedup": baseline_s / current_s if current_s > 0.0 else 0.0,
        "reduction_percent": (
            100.0 * (baseline_s - current_s) / baseline_s if baseline_s > 0.0 else 0.0
        ),
        "maximum_candidate_error": maximum_error,
        "candidate_metadata_match": metadata_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare full-response and adaptive local-box component weighting."
    )
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--rounds", type=int, default=21)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.media, args.rounds, args.repeats), indent=2))


if __name__ == "__main__":
    main()
