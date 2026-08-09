from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.ui.isolated_media import PreviewDecoderSession, probe_media_isolated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure isolated persistent-session preview decode throughput."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()

    info = probe_media_isolated(str(args.video))
    if not info.available or info.kind != "video":
        raise SystemExit(info.error or "A readable video is required")
    frame_total = min(max(2, int(args.frames)), max(0, int(info.frame_count)))
    if frame_total < 2:
        raise SystemExit("The benchmark requires at least two frames")

    durations: list[float] = []
    with PreviewDecoderSession(
        str(args.video),
        expected_width=info.width,
        expected_height=info.height,
        expected_identity=info.source_identity,
    ) as session:
        wall_started = perf_counter()
        for frame_index in range(frame_total):
            started = perf_counter()
            frame = session.decode(frame_index)
            durations.append(perf_counter() - started)
        wall_s = perf_counter() - wall_started
        session_starts = session.start_count

    stable = np.asarray(durations[1:], dtype=np.float64)
    print(
        json.dumps(
            {
                "video": str(args.video),
                "resolution": [int(frame.shape[1]), int(frame.shape[0])],
                "frames": frame_total,
                "session_starts": session_starts,
                "first_frame_ms": round(durations[0] * 1000.0, 3),
                "stable_median_ms": round(float(np.median(stable)) * 1000.0, 3),
                "stable_p95_ms": round(float(np.percentile(stable, 95)) * 1000.0, 3),
                "wall_s": round(wall_s, 6),
                "effective_fps": round(frame_total / wall_s, 3),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
