from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.core import TrackerResult, TrackingPipeline
from neo_tracker.media import MediaReader, probe_media
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.tracking_worker import TrackingProgress, TrackingWorker


class _FullResponsePipelineAdapter:
    """Reproduce the previous full-frame Color tracking storage path."""

    def __init__(self, pipeline: TrackingPipeline) -> None:
        self.pipeline = pipeline

    @property
    def results(self) -> list[TrackerResult]:
        return self.pipeline.results

    @property
    def debug_history_max_bytes(self) -> int:
        return self.pipeline.debug_history_max_bytes

    def reset(self) -> None:
        self.pipeline.reset()

    def process_frame(self, frame: np.ndarray, frame_index: int, time_s: float) -> TrackerResult:
        return self.pipeline.process_frame(
            frame,
            frame_index,
            time_s,
            compact_response_map=False,
        )

    def debug_history_usage(self) -> tuple[int, int]:
        return self.pipeline.debug_history_usage()


def _run_once(
    media_path: Path,
    *,
    frame_count: int,
    fps: float,
    compact_window: bool,
) -> tuple[TrackingProgress, list[TrackerResult]]:
    pipeline = color_marker_preset()
    worker_pipeline: object = pipeline if compact_window else _FullResponsePipelineAdapter(pipeline)
    progress: list[TrackingProgress] = []
    failures: list[tuple[str, int]] = []
    worker = TrackingWorker(
        pipeline=worker_pipeline,  # type: ignore[arg-type]
        reader_factory=lambda: MediaReader(str(media_path)),
        frame_count=frame_count,
        fps=fps,
        prefetch_frames=1,
    )
    worker.progress.connect(progress.append)
    worker.failed.connect(lambda message, completed: failures.append((message, completed)))
    worker.run()
    if failures:
        raise RuntimeError(f"Tracking failed at frame {failures[0][1]}: {failures[0][0]}")
    if not progress or progress[-1].completed != frame_count:
        raise RuntimeError("Tracking did not produce a terminal performance sample")
    return progress[-1], list(pipeline.results)


def _expanded_response(result: TrackerResult) -> np.ndarray | None:
    response = result.debug.get("response_map")
    if not isinstance(response, np.ndarray):
        return None
    origin = result.debug.get("response_origin")
    frame_shape = result.debug.get("response_frame_shape")
    if origin is None and frame_shape is None:
        return response
    if not isinstance(origin, (tuple, list)) or not isinstance(frame_shape, (tuple, list)):
        raise RuntimeError("Compact response placement metadata is incomplete")
    expanded = np.zeros((int(frame_shape[0]), int(frame_shape[1])), dtype=response.dtype)
    x0, y0 = int(origin[0]), int(origin[1])
    height, width = response.shape
    expanded[y0 : y0 + height, x0 : x0 + width] = response
    return expanded


def _result_error(
    baseline: list[TrackerResult],
    current: list[TrackerResult],
) -> tuple[float, float, bool, bool]:
    if len(baseline) != len(current):
        return float("inf"), float("inf"), False, False
    state_error = 0.0
    response_error = 0.0
    statuses_match = True
    candidates_match = True
    for old, new in zip(baseline, current):
        statuses_match = statuses_match and old.status == new.status
        keys = set(old.filtered_state) | set(new.filtered_state)
        for key in keys:
            state_error = max(
                state_error,
                abs(float(old.filtered_state.get(key, 0.0)) - float(new.filtered_state.get(key, 0.0))),
            )
        old_observation = old.observation
        new_observation = new.observation
        candidates_match = candidates_match and bool(
            (old_observation is None and new_observation is None)
            or (
                old_observation is not None
                and new_observation is not None
                and old_observation.image_point == new_observation.image_point
                and old_observation.raw == new_observation.raw
                and old_observation.score == new_observation.score
            )
        )
        old_response = _expanded_response(old)
        new_response = _expanded_response(new)
        if old_response is None or new_response is None:
            continue
        response_error = max(
            response_error,
            float(np.max(np.abs(old_response - new_response), initial=0.0)),
        )
    return state_error, response_error, statuses_match, candidates_match


def benchmark(media_path: Path, rounds: int) -> dict[str, object]:
    resolved_path = media_path.expanduser().resolve()
    info = probe_media(str(resolved_path))
    if not info.available or info.kind != "video":
        raise RuntimeError(info.error or f"Could not benchmark video: {resolved_path}")
    rounds = max(1, int(rounds))
    samples: dict[str, list[TrackingProgress]] = {"baseline_full": [], "compact_window": []}
    representative: dict[str, list[TrackerResult]] = {}
    for round_index in range(rounds):
        order = (
            (("baseline_full", False), ("compact_window", True))
            if round_index % 2 == 0
            else (("compact_window", True), ("baseline_full", False))
        )
        for label, compact_window in order:
            progress, results = _run_once(
                resolved_path,
                frame_count=info.frame_count,
                fps=info.fps,
                compact_window=compact_window,
            )
            samples[label].append(progress)
            representative[label] = results

    def summarize(values: list[TrackingProgress]) -> dict[str, float | int]:
        elapsed_s = median(item.elapsed_s for item in values)
        return {
            "elapsed_s": elapsed_s,
            "throughput_fps": info.frame_count / elapsed_s if elapsed_s > 0.0 else 0.0,
            "input_ms_per_frame": median(item.input_ms_per_frame for item in values),
            "compute_ms_per_frame": median(item.processing_ms_per_frame for item in values),
            "retained_debug_bytes": int(median(item.retained_debug_bytes for item in values)),
            "retained_debug_frames": int(median(item.retained_debug_frames for item in values)),
        }

    baseline = summarize(samples["baseline_full"])
    current = summarize(samples["compact_window"])
    state_error, response_error, statuses_match, candidates_match = _result_error(
        representative["baseline_full"],
        representative["compact_window"],
    )
    return {
        "media": str(resolved_path),
        "resolution": f"{info.width}x{info.height}",
        "frames": info.frame_count,
        "rounds": rounds,
        "baseline_full_response": baseline,
        "compact_color_window": current,
        "throughput_speedup": (
            float(current["throughput_fps"]) / float(baseline["throughput_fps"])
            if float(baseline["throughput_fps"]) > 0.0
            else 0.0
        ),
        "compute_reduction_percent": (
            100.0
            * (float(baseline["compute_ms_per_frame"]) - float(current["compute_ms_per_frame"]))
            / float(baseline["compute_ms_per_frame"])
            if float(baseline["compute_ms_per_frame"]) > 0.0
            else 0.0
        ),
        "retained_debug_reduction_percent": (
            100.0
            * (int(baseline["retained_debug_bytes"]) - int(current["retained_debug_bytes"]))
            / int(baseline["retained_debug_bytes"])
            if int(baseline["retained_debug_bytes"]) > 0
            else 0.0
        ),
        "maximum_filtered_state_error": state_error,
        "maximum_expanded_response_error": response_error,
        "statuses_match": statuses_match,
        "selected_candidates_match": candidates_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure full-frame versus compact Color Marker tracking responses."
    )
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--rounds", type=int, default=21)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.media, args.rounds), indent=2))


if __name__ == "__main__":
    main()
