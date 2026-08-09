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

from neo_tracker.roi import CurveBandROI


def _previous_full_frame_mask(roi: CurveBandROI, shape: tuple[int, int, int]) -> np.ndarray:
    height, width = shape[:2]
    yy, xx = np.ogrid[:height, :width]
    distance = np.full((height, width), np.inf, dtype=float)
    points = np.asarray(roi.polyline, dtype=float)
    for start, end in zip(points[:-1], points[1:]):
        sx, sy = start
        ex, ey = end
        vx = ex - sx
        vy = ey - sy
        denominator = vx * vx + vy * vy + 1e-12
        t = np.clip(((xx - sx) * vx + (yy - sy) * vy) / denominator, 0.0, 1.0)
        projection_x = sx + t * vx
        projection_y = sy + t * vy
        distance = np.minimum(
            distance,
            np.hypot(xx - projection_x, yy - projection_y),
        )
    return distance <= roi.half_width


def _current_cold_mask(roi: CurveBandROI, shape: tuple[int, int, int]) -> np.ndarray:
    CurveBandROI.mask.cache_clear()
    return roi.mask(shape)


def _measure(operation, rounds: int) -> tuple[float, int, np.ndarray]:
    operation()
    samples: list[float] = []
    peaks: list[int] = []
    result = None
    for _ in range(max(2, int(rounds))):
        tracemalloc.start()
        started = perf_counter()
        result = operation()
        samples.append((perf_counter() - started) * 1000.0)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(int(peak))
    assert result is not None
    return median(samples), int(median(peaks)), result


def benchmark(width: int, height: int, rounds: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    shape = (height, width, 3)
    roi = CurveBandROI(
        (
            (width * 0.08, height * 0.72),
            (width * 0.35, height * 0.25),
            (width * 0.62, height * 0.64),
            (width * 0.90, height * 0.30),
        ),
        height * 0.035,
    )
    previous_ms, previous_peak, previous = _measure(
        lambda: _previous_full_frame_mask(roi, shape),
        rounds,
    )
    current_ms, current_peak, current = _measure(
        lambda: _current_cold_mask(roi, shape),
        rounds,
    )
    return {
        "resolution": f"{width}x{height}",
        "rounds": max(2, int(rounds)),
        "segments": len(roi.polyline) - 1,
        "active_pixels": int(np.count_nonzero(current)),
        "previous_full_frame_median_ms": previous_ms,
        "current_segment_bounds_median_ms": current_ms,
        "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
        "previous_python_peak_bytes": previous_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (previous_peak - current_peak) / previous_peak
            if previous_peak > 0
            else 0.0
        ),
        "mask_exact": bool(np.array_equal(previous, current)),
        "current_read_only": bool(not current.flags.writeable),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark full-frame versus segment-bounded cold Curve Band masks."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds), indent=2))


if __name__ == "__main__":
    main()
