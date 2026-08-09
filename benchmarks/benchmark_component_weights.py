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

from neo_tracker.media import MediaReader
from neo_tracker.observations import (
    ColorBlobObservation,
    _color_distance_response,
    _component_centroids_cv2,
    _component_threshold,
    _load_component_cv2,
)
from neo_tracker.presets import color_marker_preset


def _previous_component_centroids_cv2(
    response: np.ndarray,
    threshold: float,
    *,
    max_candidates: int,
    min_area: int,
    cv2,
) -> list[dict[str, object]]:
    """Reference the previous full-frame float64 component-weight path."""

    threshold = _component_threshold(response, threshold)
    active = np.asarray(response >= threshold, dtype=np.uint8)
    active_count = int(np.count_nonzero(active))
    if active_count == 0:
        return []
    response64 = np.asarray(response, dtype=np.float64)
    if active_count == active.size:
        area = int(active.size)
        weight = float(response64.sum())
        if area < max(1, int(min_area)) or weight <= 1e-12:
            return []
        row_weights = response64.sum(axis=1)
        column_weights = response64.sum(axis=0)
        return [
            {
                "x": float(np.dot(np.arange(response.shape[1]), column_weights)) / weight,
                "y": float(np.dot(np.arange(response.shape[0]), row_weights)) / weight,
                "score": float(np.clip(weight / area, 0.0, 1.0)),
                "area": area,
                "peak_response": float(response64.max(initial=0.0)),
                "bbox": [0, 0, response.shape[1] - 1, response.shape[0] - 1],
            }
        ]

    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        active,
        connectivity=8,
        ltype=cv2.CV_32S,
    )
    if component_count <= 1:
        return []
    weights = np.bincount(
        labels.reshape(-1),
        weights=response64.reshape(-1),
        minlength=component_count,
    )
    minimum_area = max(1, int(min_area))
    ranked: list[tuple[float, int, int, int, int]] = []
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        weight = float(weights[label])
        if area < minimum_area or weight <= 1e-12:
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        ranked.append((float(np.clip(weight / area, 0.0, 1.0)), area, top, left, label))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))

    candidates: list[dict[str, object]] = []
    for score, area, top, left, label in ranked[: max(1, int(max_candidates))]:
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        local_labels = labels[top : top + height, left : left + width]
        local_values = response64[top : top + height, left : left + width]
        if area == width * height:
            component_values = local_values.reshape(-1)
            uniform_response = bool(np.all(component_values == component_values[0]))
            local_mask = None
        else:
            local_mask = local_labels == label
            component_values = local_values[local_mask]
            uniform_response = bool(np.all(component_values == component_values[0]))
        weight = float(component_values.sum())
        if uniform_response:
            x = float(centroids[label, 0])
            y = float(centroids[label, 1])
        else:
            if local_mask is None:
                y_indices, x_indices = np.indices((height, width), dtype=np.float64)
                y_indices = y_indices.reshape(-1)
                x_indices = x_indices.reshape(-1)
            else:
                y_indices, x_indices = np.nonzero(local_mask)
            x = left + float(np.dot(x_indices, component_values)) / weight
            y = top + float(np.dot(y_indices, component_values)) / weight
        candidates.append(
            {
                "x": x,
                "y": y,
                "score": score,
                "area": area,
                "peak_response": float(component_values.max(initial=0.0)),
                "bbox": [left, top, left + width - 1, top + height - 1],
            }
        )
    return candidates


def _measure(function, response: np.ndarray, rounds: int, **kwargs):
    samples: list[float] = []
    peaks: list[int] = []
    result = None
    for _ in range(rounds):
        tracemalloc.start()
        started = perf_counter()
        result = function(response, **kwargs)
        samples.append(perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
    return median(samples), int(median(peaks)), result


def _candidate_error(previous, current) -> tuple[float, bool]:
    if len(previous) != len(current):
        return float("inf"), False
    maximum = 0.0
    metadata_match = True
    for old, new in zip(previous, current):
        metadata_match = metadata_match and old["area"] == new["area"] and old["bbox"] == new["bbox"]
        for key in ("x", "y", "score", "peak_response"):
            maximum = max(maximum, abs(float(old[key]) - float(new[key])))
    return maximum, metadata_match


def benchmark(media_path: Path, frame_index: int, rounds: int) -> dict[str, object]:
    cv2 = _load_component_cv2()
    if cv2 is None:
        raise RuntimeError("OpenCV is required for this benchmark")
    pipeline = color_marker_preset()
    observation = pipeline.observation_model
    if not isinstance(observation, ColorBlobObservation):
        raise RuntimeError("Color Marker preset did not create a ColorBlobObservation")
    reader = MediaReader(str(media_path.expanduser().resolve()))
    try:
        frame = reader.read_frame_for_processing(max(0, int(frame_index)))
    finally:
        reader.close()
    response = _color_distance_response(frame, observation.sample_rgb, observation.tolerance)
    kwargs = {
        "threshold": observation.min_response,
        "max_candidates": observation.max_candidates,
        "min_area": observation.min_component_area,
        "cv2": cv2,
    }
    previous_s, previous_peak, previous = _measure(
        _previous_component_centroids_cv2,
        response,
        max(1, int(rounds)),
        **kwargs,
    )
    current_s, current_peak, current = _measure(
        _component_centroids_cv2,
        response,
        max(1, int(rounds)),
        **kwargs,
    )
    maximum_error, metadata_match = _candidate_error(previous, current)
    active_count = int(np.count_nonzero(response >= observation.min_response))
    return {
        "media": str(media_path.expanduser().resolve()),
        "resolution": f"{frame.shape[1]}x{frame.shape[0]}",
        "frame_index": max(0, int(frame_index)),
        "rounds": max(1, int(rounds)),
        "active_pixels": active_count,
        "active_percent": 100.0 * active_count / response.size,
        "previous": {"elapsed_ms": 1000.0 * previous_s, "python_peak_bytes": previous_peak},
        "current": {"elapsed_ms": 1000.0 * current_s, "python_peak_bytes": current_peak},
        "speedup": previous_s / current_s if current_s > 0.0 else 0.0,
        "python_peak_reduction_percent": (
            100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
        ),
        "maximum_candidate_error": maximum_error,
        "candidate_metadata_match": metadata_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare full-frame float64 and sparse-aware OpenCV component weighting."
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("--frame", type=int, default=35)
    parser.add_argument("--rounds", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.media, args.frame, args.rounds), indent=2))


if __name__ == "__main__":
    main()
