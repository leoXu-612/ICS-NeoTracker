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

from neo_tracker.observations import _intensity


def _legacy_intensity(frame: np.ndarray) -> np.ndarray:
    arr = np.asarray(frame, dtype=np.float32)
    if arr.size and arr.max() > 1.0:
        arr = arr / 255.0
    arr = np.clip(arr, 0.0, 1.0)
    if arr.ndim == 2:
        return arr
    if arr.shape[2] == 1:
        return arr[:, :, 0]
    return 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]


def _measure(operation, rounds: int) -> tuple[float, int]:
    operation()
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000.0)
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    del result
    tracemalloc.stop()
    return median(samples), int(peak)


def benchmark(width: int, height: int, rounds: int, seed: int) -> dict[str, float | int | str | bool]:
    rng = np.random.default_rng(int(seed))
    bgr = rng.integers(0, 256, size=(int(height), int(width), 3), dtype=np.uint8)
    rgb_view = bgr[:, :, ::-1]
    expected = _legacy_intensity(rgb_view)
    actual = _intensity(rgb_view)
    max_abs_error = float(np.max(np.abs(expected - actual), initial=0.0))
    legacy_ms, legacy_peak = _measure(lambda: _legacy_intensity(rgb_view), rounds)
    current_ms, current_peak = _measure(lambda: _intensity(rgb_view), rounds)
    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "negative_channel_stride": bool(rgb_view.strides[-1] < 0),
        "legacy_median_ms": legacy_ms,
        "current_median_ms": current_ms,
        "speedup": legacy_ms / current_ms if current_ms > 0.0 else 0.0,
        "legacy_peak_bytes": legacy_peak,
        "current_peak_bytes": current_peak,
        "peak_reduction_percent": (
            100.0 * (legacy_peak - current_peak) / legacy_peak if legacy_peak > 0 else 0.0
        ),
        "max_abs_error": max_abs_error,
        "output_bytes": int(actual.nbytes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark low-copy RGB-to-intensity preprocessing against the previous implementation."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=15)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.seed), indent=2))


if __name__ == "__main__":
    main()
