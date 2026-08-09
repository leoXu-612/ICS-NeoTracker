from __future__ import annotations

import argparse
from contextlib import contextmanager
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

from neo_tracker.coordinates import ImageCoordinate
from neo_tracker.core import FrameContext
from neo_tracker import observations as observation_module
from neo_tracker.observations import TemplateObservation, _intensity
from neo_tracker.roi import RectangularROI


class RebuildingTemplateObservation(TemplateObservation):
    """Reference matching the previous per-frame template rebuild behavior."""

    def _cached_template_preprocessing(self) -> tuple[np.ndarray, np.ndarray, float]:
        template = _intensity(self.template)
        template_norm = template - template.mean()
        template_energy = float(
            np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
        )
        return template, template_norm, template_energy

    def _cached_template_fft_spectrum(
        self,
        template_norm: np.ndarray,
        fft_shape: tuple[int, int],
    ) -> np.ndarray:
        return np.fft.rfftn(
            template_norm[::-1, ::-1],
            s=fft_shape,
            axes=(0, 1),
        )


@contextmanager
def _selected_backend(backend: str):
    original_loader = observation_module._load_template_cv2
    if backend == "numpy":
        observation_module._load_template_cv2 = lambda: None
    elif original_loader() is None:
        raise RuntimeError("OpenCV template matching is unavailable; use --backend numpy")
    try:
        yield
    finally:
        observation_module._load_template_cv2 = original_loader


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


def _candidate(result) -> dict[str, object] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "image_point": [float(candidate.image_point[0]), float(candidate.image_point[1])],
        "score": float(candidate.score),
    }


def benchmark(width: int, height: int, rounds: int, backend: str) -> dict[str, object]:
    width = int(width)
    height = int(height)
    template_size = max(17, min(151, height // 4, width // 4))
    if template_size % 2 == 0:
        template_size -= 1
    rng = np.random.default_rng(2026071405)
    frame = rng.integers(0, 80, size=(height, width, 3), dtype=np.uint8)
    template = rng.integers(
        0,
        256,
        size=(template_size, template_size - 2, 3),
        dtype=np.uint8,
    )
    top = height // 2 - template.shape[0] // 2
    left = width // 2 - template.shape[1] // 2
    frame[top : top + template.shape[0], left : left + template.shape[1]] = template
    roi = RectangularROI(width * 0.18, height * 0.18, width * 0.64, height * 0.64)
    roi.mask(frame.shape)
    coordinate = ImageCoordinate()
    context = FrameContext(0, 0.0, frame, compact_response_map=True)
    rebuilding = RebuildingTemplateObservation(template=template.copy(), min_score=0.9)
    cached = TemplateObservation(template=template.copy(), min_score=0.9)

    with _selected_backend(backend):
        rebuilding_ms, rebuilding_peak = _measure(
            lambda: rebuilding.observe(frame, roi, coordinate, context),
            rounds,
        )
        cached_ms, cached_peak = _measure(
            lambda: cached.observe(frame, roi, coordinate, context),
            rounds,
        )
        rebuilding_result = rebuilding.observe(frame, roi, coordinate, context)
        cached_result = cached.observe(frame, roi, coordinate, context)

    return {
        "resolution": f"{width}x{height}",
        "template_shape": list(template.shape),
        "roi_bounds": list(roi.bounds()),
        "backend": backend,
        "rounds": max(3, int(rounds)),
        "measurements": {
            "rebuilding_median_ms": rebuilding_ms,
            "cached_median_ms": cached_ms,
            "speedup": rebuilding_ms / cached_ms if cached_ms > 0.0 else 0.0,
            "rebuilding_python_peak_bytes": rebuilding_peak,
            "cached_python_peak_bytes": cached_peak,
            "python_peak_reduction_percent": (
                100.0 * (rebuilding_peak - cached_peak) / rebuilding_peak
                if rebuilding_peak > 0
                else 0.0
            ),
        },
        "parity": {
            "max_response_error": float(
                np.max(
                    np.abs(rebuilding_result.response_map - cached_result.response_map),
                    initial=0.0,
                )
            ),
            "rebuilding_candidate": _candidate(rebuilding_result),
            "cached_candidate": _candidate(cached_result),
        },
        "cache": {
            "template_planes_read_only": bool(
                cached._template_cache is not None
                and all(not values.flags.writeable for values in cached._template_cache[:2])
            ),
            "fft_kernel_cached": bool(cached._template_fft_cache is not None),
            "fft_kernel_read_only": bool(
                cached._template_fft_cache is not None
                and not cached._template_fft_cache.flags.writeable
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark cached Template preprocessing against the previous per-frame rebuild."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--backend", choices=("opencv", "numpy"), default="opencv")
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds, args.backend), indent=2))


if __name__ == "__main__":
    main()
