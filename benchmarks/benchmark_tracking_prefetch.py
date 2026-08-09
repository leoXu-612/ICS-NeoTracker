from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.media import MediaReader, probe_media
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.tracking_worker import (
    DEFAULT_TRACKING_PREFETCH_FRAMES,
    TrackingProgress,
    TrackingWorker,
)


def _run_once(media_path: Path, prefetch_frames: int):
    pipeline = color_marker_preset()
    info = probe_media(str(media_path))
    if not info.available or info.kind != "video":
        raise RuntimeError(info.error or f"Could not benchmark video input: {media_path}")
    progress: list[TrackingProgress] = []
    failures: list[tuple[str, int]] = []
    worker = TrackingWorker(
        pipeline=pipeline,
        reader_factory=lambda: MediaReader(str(media_path)),
        frame_count=info.frame_count,
        fps=info.fps,
        prefetch_frames=prefetch_frames,
    )
    worker.progress.connect(progress.append)
    worker.failed.connect(lambda message, completed: failures.append((message, completed)))
    worker.run()
    if failures:
        raise RuntimeError(f"Tracking failed at frame {failures[0][1]}: {failures[0][0]}")
    if not progress or progress[-1].completed != info.frame_count:
        raise RuntimeError("Tracking did not produce a terminal performance sample")
    return progress[-1], list(pipeline.results), info


def _result_error(serial_results, pipelined_results) -> tuple[float, bool]:
    if len(serial_results) != len(pipelined_results):
        return float("inf"), False
    max_error = 0.0
    statuses_match = True
    for serial, pipelined in zip(serial_results, pipelined_results):
        statuses_match = statuses_match and serial.status == pipelined.status
        keys = set(serial.filtered_state) | set(pipelined.filtered_state)
        for key in keys:
            serial_value = float(serial.filtered_state.get(key, 0.0))
            pipelined_value = float(pipelined.filtered_state.get(key, 0.0))
            max_error = max(
                max_error,
                abs(serial_value - pipelined_value),
            )
    return max_error, statuses_match


def _median_absolute_deviation(values: list[float]) -> float:
    center = median(values)
    return median(abs(value - center) for value in values)


def benchmark(
    media_path: Path,
    rounds: int,
    *,
    warmup_rounds: int = 2,
    prefetch_frames: int = DEFAULT_TRACKING_PREFETCH_FRAMES,
) -> dict[str, object]:
    media_path = media_path.expanduser().resolve()
    if not media_path.exists():
        raise FileNotFoundError(media_path)
    rounds = max(1, int(rounds))
    warmup_rounds = max(0, int(warmup_rounds))
    prefetch_frames = max(1, int(prefetch_frames))
    conditions = (("serial", 0), ("prefetched", prefetch_frames))

    # A two-condition balanced Latin square is AB/BA. Warming and measuring
    # in the same alternating order prevents either mode from always owning
    # the cold decoder/cache or the later, potentially thermally shifted runs.
    for warmup_index in range(warmup_rounds):
        ordered = conditions if warmup_index % 2 == 0 else tuple(reversed(conditions))
        for _label, depth in ordered:
            _run_once(media_path, depth)

    samples: dict[str, list[TrackingProgress]] = {"serial": [], "prefetched": []}
    representative_results = {}
    paired_samples: list[tuple[TrackingProgress, TrackingProgress]] = []
    info = None
    for round_index in range(rounds):
        ordered = conditions if round_index % 2 == 0 else tuple(reversed(conditions))
        round_samples: dict[str, TrackingProgress] = {}
        for label, depth in ordered:
            progress, results, info = _run_once(media_path, depth)
            samples[label].append(progress)
            round_samples[label] = progress
            representative_results[label] = results
        paired_samples.append((round_samples["serial"], round_samples["prefetched"]))
    if info is None:
        raise RuntimeError("Media probe did not return video metadata")

    def summarize(values: list[TrackingProgress]) -> dict[str, float | int]:
        elapsed_values = [item.elapsed_s for item in values]
        throughput_values = [item.throughput_fps for item in values]
        input_values = [item.input_ms_per_frame for item in values]
        compute_values = [item.processing_ms_per_frame for item in values]
        overlap_values = [item.stage_overlap_ms_per_frame for item in values]
        elapsed_s = median(elapsed_values)
        return {
            "elapsed_s": elapsed_s,
            "elapsed_mad_s": _median_absolute_deviation(elapsed_values),
            "throughput_fps": median(throughput_values),
            "throughput_mad_fps": _median_absolute_deviation(throughput_values),
            "input_ms_per_frame": median(input_values),
            "input_mad_ms_per_frame": _median_absolute_deviation(input_values),
            "compute_ms_per_frame": median(compute_values),
            "compute_mad_ms_per_frame": _median_absolute_deviation(compute_values),
            "stage_overlap_ms_per_frame": median(overlap_values),
            "stage_overlap_mad_ms_per_frame": _median_absolute_deviation(overlap_values),
            "prefetch_frames": values[-1].prefetch_frames,
        }

    serial = summarize(samples["serial"])
    prefetched = summarize(samples["prefetched"])
    max_state_error, statuses_match = _result_error(
        representative_results["serial"],
        representative_results["prefetched"],
    )
    paired_speedups = [
        serial.elapsed_s / prefetched.elapsed_s
        if prefetched.elapsed_s > 0.0
        else 0.0
        for serial, prefetched in paired_samples
    ]
    paired_reductions = [
        100.0 * (serial.elapsed_s - prefetched.elapsed_s) / serial.elapsed_s
        if serial.elapsed_s > 0.0
        else 0.0
        for serial, prefetched in paired_samples
    ]
    return {
        "media": str(media_path),
        "resolution": f"{info.width}x{info.height}",
        "frames": info.frame_count,
        "rounds": rounds,
        "warmup_rounds": warmup_rounds,
        "measurement_order": "balanced two-condition Latin square (AB/BA alternating)",
        "serial": serial,
        "prefetched": prefetched,
        "throughput_speedup": (
            float(prefetched["throughput_fps"]) / float(serial["throughput_fps"])
            if float(serial["throughput_fps"]) > 0.0
            else 0.0
        ),
        "elapsed_reduction_percent": (
            100.0
            * (float(serial["elapsed_s"]) - float(prefetched["elapsed_s"]))
            / float(serial["elapsed_s"])
            if float(serial["elapsed_s"]) > 0.0
            else 0.0
        ),
        "paired_throughput_speedup": {
            "median_ratio": median(paired_speedups),
            "mad_ratio": _median_absolute_deviation(paired_speedups),
        },
        "paired_elapsed_reduction_percent": {
            "median": median(paired_reductions),
            "mad": _median_absolute_deviation(paired_reductions),
        },
        "maximum_extra_decoded_frame_bytes": int(
            info.width * info.height * 3 * prefetch_frames
        ),
        "max_filtered_state_error": max_state_error,
        "statuses_match": statuses_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare serialized tracking input with the bounded one-frame prefetch pipeline."
    )
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument(
        "--warmup-rounds",
        type=int,
        default=2,
        help="Unmeasured AB/BA warm-up pairs before the measured rounds.",
    )
    parser.add_argument(
        "--prefetch-frames",
        type=int,
        default=DEFAULT_TRACKING_PREFETCH_FRAMES,
        help="Bounded prefetch depth to compare against serial input (default: 1).",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                args.media,
                args.rounds,
                warmup_rounds=args.warmup_rounds,
                prefetch_frames=args.prefetch_frames,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
