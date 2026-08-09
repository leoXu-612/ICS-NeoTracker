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

from neo_tracker.observations import BrightnessPeakObservation, ColorBlobObservation
from neo_tracker.presets import color_marker_preset
from neo_tracker.roi import RectangularROI


def _pipeline(observation, roi: RectangularROI):
    pipeline = color_marker_preset(roi=roi, tolerance=0.08)
    pipeline.observation_model = observation
    pipeline.debug_history_limit = 4
    return pipeline


def _run(
    frame: np.ndarray,
    observation,
    roi: RectangularROI,
    frame_count: int,
    *,
    compact: bool,
):
    pipeline = _pipeline(observation, roi)
    for frame_index in range(frame_count):
        pipeline.process_frame(
            frame,
            frame_index,
            frame_index / 30.0,
            compact_response_map=compact,
        )
    return pipeline


def _measure(operation, rounds: int) -> tuple[float, int, object]:
    operation()
    samples: list[float] = []
    representative = None
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        representative = operation()
        samples.append((perf_counter() - started) * 1000.0)
    tracemalloc.start()
    representative = operation()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return median(samples), int(peak), representative


def _materialize(result) -> np.ndarray | None:
    stored = result.debug.get("response_map")
    if not isinstance(stored, np.ndarray):
        return None
    origin = result.debug.get("response_origin")
    frame_shape = result.debug.get("response_frame_shape")
    if not isinstance(origin, (list, tuple)) or not isinstance(frame_shape, (list, tuple)):
        return stored
    response = np.zeros(tuple(int(value) for value in frame_shape), dtype=stored.dtype)
    x0, y0 = (int(origin[0]), int(origin[1]))
    response[y0 : y0 + stored.shape[0], x0 : x0 + stored.shape[1]] = stored
    return response


def _summarize(
    label: str,
    frame: np.ndarray,
    observation,
    roi: RectangularROI,
    frame_count: int,
    rounds: int,
) -> dict[str, object]:
    full_ms, full_peak, full_pipeline = _measure(
        lambda: _run(frame, observation, roi, frame_count, compact=False),
        rounds,
    )
    compact_ms, compact_peak, compact_pipeline = _measure(
        lambda: _run(frame, observation, roi, frame_count, compact=True),
        rounds,
    )
    full_results = full_pipeline.results
    compact_results = compact_pipeline.results
    errors = []
    states_match = True
    statuses_match = True
    for full_result, compact_result in zip(full_results, compact_results):
        full_response = _materialize(full_result)
        compact_response = _materialize(compact_result)
        if full_response is not None and compact_response is not None:
            errors.append(float(np.max(np.abs(full_response - compact_response), initial=0.0)))
        states_match = states_match and full_result.filtered_state == compact_result.filtered_state
        statuses_match = statuses_match and full_result.status == compact_result.status
    full_bytes, full_frames = full_pipeline.debug_history_usage()
    compact_bytes, compact_frames = compact_pipeline.debug_history_usage()
    return {
        "observation": label,
        "frames_per_run": frame_count,
        "full_frame_history_ms": full_ms,
        "roi_local_history_ms": compact_ms,
        "speedup": full_ms / compact_ms if compact_ms > 0.0 else 0.0,
        "full_frame_python_peak_bytes": full_peak,
        "roi_local_python_peak_bytes": compact_peak,
        "python_peak_reduction_percent": (
            100.0 * (full_peak - compact_peak) / full_peak if full_peak > 0 else 0.0
        ),
        "full_frame_retained_bytes": full_bytes,
        "full_frame_retained_frames": full_frames,
        "roi_local_retained_bytes": compact_bytes,
        "roi_local_retained_frames": compact_frames,
        "retained_byte_reduction_percent": (
            100.0 * (full_bytes - compact_bytes) / full_bytes if full_bytes > 0 else 0.0
        ),
        "max_materialized_response_error": max(errors, default=0.0),
        "filtered_states_match": states_match,
        "statuses_match": statuses_match,
    }


def benchmark(width: int, height: int, frames: int, rounds: int) -> dict[str, object]:
    width = max(64, int(width))
    height = max(64, int(height))
    frames = max(2, int(frames))
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    x0 = int(round(width * 0.30))
    y0 = int(round(height * 0.25))
    roi_width = max(8, int(round(width * 0.40)))
    roi_height = max(8, int(round(height * 0.50)))
    roi = RectangularROI(float(x0), float(y0), float(roi_width), float(roi_height))
    marker_x = min(width - 1, x0 + roi_width // 2)
    marker_y = min(height - 1, y0 + roi_height // 2)
    radius = max(3, min(width, height) // 128)
    frame[
        max(0, marker_y - radius) : min(height, marker_y + radius + 1),
        max(0, marker_x - radius) : min(width, marker_x + radius + 1),
        0,
    ] = 255
    roi.mask(frame.shape)
    return {
        "resolution": f"{width}x{height}",
        "roi_bounds": list(roi.bounds()),
        "roi_frame_fraction": (roi_width * roi_height) / float(width * height),
        "rounds": max(3, int(rounds)),
        "results": [
            _summarize(
                "Color Marker",
                frame,
                ColorBlobObservation(sample_rgb=(255.0, 0.0, 0.0), tolerance=0.08),
                roi,
                frames,
                rounds,
            ),
            _summarize(
                "Brightness Peak",
                frame,
                BrightnessPeakObservation(min_response=0.25),
                roi,
                frames,
                rounds,
            ),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare full-frame and ROI-local tracking response history storage."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.frames, args.rounds), indent=2))


if __name__ == "__main__":
    main()
