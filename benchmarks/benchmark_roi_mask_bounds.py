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

from neo_tracker.roi import AnnularROI, CircularROI, PolygonROI, RectangularROI


def _previous_full_frame_mask(roi, shape: tuple[int, int, int]) -> np.ndarray:
    height, width = shape[:2]
    yy, xx = np.ogrid[:height, :width]
    if isinstance(roi, RectangularROI):
        return (
            (xx >= roi.x)
            & (xx <= roi.x + roi.width)
            & (yy >= roi.y)
            & (yy <= roi.y + roi.height)
        )
    if isinstance(roi, CircularROI):
        cx, cy = roi.center
        return (xx - cx) ** 2 + (yy - cy) ** 2 <= roi.radius**2
    if isinstance(roi, AnnularROI):
        cx, cy = roi.center
        radius_squared = (xx - cx) ** 2 + (yy - cy) ** 2
        return (radius_squared >= roi.inner_radius**2) & (
            radius_squared <= roi.outer_radius**2
        )
    inside = np.zeros((height, width), dtype=bool)
    points = np.asarray(roi.points, dtype=float)
    x_vertices = points[:, 0]
    y_vertices = points[:, 1]
    previous = len(points) - 1
    for index in range(len(points)):
        yi = y_vertices[index]
        yj = y_vertices[previous]
        xi = x_vertices[index]
        xj = x_vertices[previous]
        crosses = ((yi > yy) != (yj > yy)) & (
            xx < (xj - xi) * (yy - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= crosses
        previous = index
    return inside


def _current_cold_mask(roi, shape: tuple[int, int, int]) -> np.ndarray:
    type(roi).mask.cache_clear()
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
    rois = [
        RectangularROI(width * 0.12, height * 0.15, width * 0.58, height * 0.52),
        CircularROI((width * 0.5, height * 0.5), height * 0.31),
        AnnularROI((width * 0.5, height * 0.5), height * 0.15, height * 0.31),
        PolygonROI(
            (
                (width * 0.12, height * 0.20),
                (width * 0.74, height * 0.14),
                (width * 0.82, height * 0.72),
                (width * 0.31, height * 0.81),
            )
        ),
    ]
    cases: list[dict[str, object]] = []
    for roi in rois:
        previous_ms, previous_peak, previous = _measure(
            lambda roi=roi: _previous_full_frame_mask(roi, shape),
            rounds,
        )
        current_ms, current_peak, current = _measure(
            lambda roi=roi: _current_cold_mask(roi, shape),
            rounds,
        )
        cases.append(
            {
                "roi": roi.name,
                "previous_full_frame_median_ms": previous_ms,
                "current_bounded_median_ms": current_ms,
                "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
                "previous_python_peak_bytes": previous_peak,
                "current_python_peak_bytes": current_peak,
                "python_peak_reduction_percent": (
                    100.0 * (previous_peak - current_peak) / previous_peak
                    if previous_peak > 0
                    else 0.0
                ),
                "active_pixels": int(np.count_nonzero(current)),
                "mask_exact": bool(np.array_equal(previous, current)),
                "current_read_only": bool(not current.flags.writeable),
            }
        )
    return {
        "resolution": f"{width}x{height}",
        "rounds": max(2, int(rounds)),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark full-frame versus geometry-bounded cold ROI masks."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds), indent=2))


if __name__ == "__main__":
    main()
