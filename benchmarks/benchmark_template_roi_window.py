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

from neo_tracker.coordinates import ImageCoordinate
from neo_tracker.core import FrameContext
from neo_tracker.observations import (
    TemplateObservation,
    _intensity,
    _load_template_cv2,
    _template_scores_cv2,
    _template_scores_numpy,
)
from neo_tracker.roi import RectangularROI


def _previous_template(
    frame: np.ndarray,
    roi: RectangularROI,
    observation: TemplateObservation,
) -> tuple[np.ndarray, dict[str, object] | None]:
    image = _intensity(frame)
    template = _intensity(observation.template)
    th, tw = template.shape
    h, w = image.shape
    response = np.zeros((h, w), dtype=np.float32)
    if th > h or tw > w:
        return response, None

    template_norm = template - template.mean()
    template_energy = float(
        np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
    )
    roi_mask = roi.mask(frame.shape)
    x0, y0, x1, y1 = roi.bounds()
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(w - tw, x1)
    y1 = min(h - th, y1)
    if x0 > x1 or y0 > y1:
        return response, None

    region = image[y0 : y1 + th, x0 : x1 + tw]
    output_shape = (y1 - y0 + 1, x1 - x0 + 1)
    if template_energy <= 1e-12:
        scores = np.full(output_shape, 0.5, dtype=np.float32)
    else:
        cv2 = _load_template_cv2()
        if cv2 is not None:
            try:
                scores = _template_scores_cv2(region, template, cv2=cv2)
            except Exception:
                scores = _template_scores_numpy(region, template_norm, template_energy)
        else:
            scores = _template_scores_numpy(region, template_norm, template_energy)

    center_y = np.arange(y0, y1 + 1, dtype=np.intp) + th // 2
    center_x = np.arange(x0, x1 + 1, dtype=np.intp) + tw // 2
    valid = roi_mask[np.ix_(center_y, center_x)]
    response[np.ix_(center_y, center_x)] = np.where(valid, scores, 0.0)
    y, x = np.unravel_index(int(np.argmax(response)), response.shape)
    score = float(response[y, x])
    candidate = None
    if score >= observation.min_score:
        candidate = {"image_point": [float(x), float(y)], "score": score}
    return response, candidate


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


def benchmark(width: int, height: int, rounds: int) -> dict[str, object]:
    width = int(width)
    height = int(height)
    rng = np.random.default_rng(20260714)
    frame = rng.integers(0, 80, size=(height, width, 3), dtype=np.uint8)
    template = rng.integers(0, 256, size=(31, 29, 3), dtype=np.uint8)
    template_top = height // 2
    template_left = width // 2
    frame[template_top : template_top + template.shape[0], template_left : template_left + template.shape[1]] = (
        template
    )
    roi = RectangularROI(width / 6.0, height / 6.0, width * 2.0 / 3.0, height * 2.0 / 3.0)
    roi.mask(frame.shape)
    observation = TemplateObservation(template=template, min_score=0.9)
    coordinate = ImageCoordinate()
    context = FrameContext(0, 0.0, frame)

    def previous():
        return _previous_template(frame, roi, observation)

    def current():
        return observation.observe(frame, roi, coordinate, context)

    previous_ms, previous_peak = _measure(previous, rounds)
    current_ms, current_peak = _measure(current, rounds)
    previous_response, previous_candidate = previous()
    current_result = current()
    current_candidate = None
    if current_result.candidates:
        candidate = current_result.candidates[0]
        current_candidate = {
            "image_point": [float(candidate.image_point[0]), float(candidate.image_point[1])],
            "score": float(candidate.score),
        }

    x0, y0, x1, y1 = roi.bounds()
    search_width = min(width, x1 + template.shape[1]) - max(0, x0)
    search_height = min(height, y1 + template.shape[0]) - max(0, y0)
    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "roi_bounds": list(roi.bounds()),
        "search_source_ratio": float(search_width * search_height / (width * height)),
        "measurements": {
            "previous_median_ms": previous_ms,
            "current_median_ms": current_ms,
            "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
            "previous_python_peak_bytes": previous_peak,
            "current_python_peak_bytes": current_peak,
            "python_peak_reduction_percent": (
                100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
            ),
        },
        "parity": {
            "max_response_error": float(
                np.max(np.abs(previous_response - current_result.response_map), initial=0.0)
            ),
            "previous_candidate": previous_candidate,
            "current_candidate": current_candidate,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark ROI-windowed uint8 Template observation against full-frame intensity preprocessing."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=12)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.width, args.height, args.rounds), indent=2))


if __name__ == "__main__":
    main()
