from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.media import MediaReader
from neo_tracker.presets import color_marker_preset


def benchmark(media_path: Path, rounds: int) -> dict[str, float | int | str]:
    media_path = media_path.expanduser().resolve()
    if not media_path.exists():
        raise FileNotFoundError(media_path)

    input_samples: list[float] = []
    full_samples: list[float] = []
    compute_samples: list[float] = []
    frame_count = 0
    width = 0
    height = 0

    for _ in range(max(1, int(rounds))):
        pipeline = color_marker_preset()
        input_s = 0.0
        compute_s = 0.0
        with MediaReader(str(media_path)) as reader:
            frame_count = reader.info.frame_count
            width = reader.info.width
            height = reader.info.height
            for frame_index in range(frame_count):
                started = perf_counter()
                frame = reader.read_frame_for_processing(frame_index)
                input_s += perf_counter() - started
                started = perf_counter()
                pipeline.process_frame(frame, frame_index, frame_index / reader.info.fps)
                compute_s += perf_counter() - started
        input_samples.append(input_s)
        compute_samples.append(compute_s)
        full_samples.append(input_s + compute_s)

    input_s = median(input_samples)
    compute_s = median(compute_samples)
    total_s = median(full_samples)
    count = max(1, frame_count)
    return {
        "media": str(media_path),
        "resolution": f"{width}x{height}",
        "frames": frame_count,
        "rounds": max(1, int(rounds)),
        "input_ms_per_frame": 1000.0 * input_s / count,
        "compute_ms_per_frame": 1000.0 * compute_s / count,
        "total_ms_per_frame": 1000.0 * total_s / count,
        "throughput_fps": frame_count / total_s if total_s > 0.0 else 0.0,
        "input_share_percent": 100.0 * input_s / total_s if total_s > 0.0 else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark sequential media input and Color Marker tracking.")
    parser.add_argument(
        "media",
        nargs="?",
        type=Path,
        default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"),
    )
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.media, args.rounds), indent=2))


if __name__ == "__main__":
    main()
