from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

import numpy as np
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.ui.preview_canvas import PreviewCanvas, _response_overlay_rgba


def _legacy_response_overlay_rgba(response_map: np.ndarray) -> np.ndarray | None:
    response = np.asarray(response_map, dtype=float)
    finite = np.isfinite(response)
    if not np.any(finite):
        return None
    safe = np.where(finite, response, 0.0)
    min_value = float(np.min(safe[finite]))
    max_value = float(np.max(safe[finite]))
    if max_value - min_value <= 1e-12:
        return None
    normalized = np.clip((safe - min_value) / (max_value - min_value), 0.0, 1.0)
    alpha = np.clip(normalized * 135.0, 0.0, 135.0).astype(np.uint8)
    rgba = np.zeros((*response.shape, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.clip(60 + normalized * 195.0, 0, 255).astype(np.uint8)
    rgba[:, :, 1] = np.clip(normalized * 118.0, 0, 255).astype(np.uint8)
    rgba[:, :, 2] = np.clip(255.0 - normalized * 210.0, 0, 255).astype(np.uint8)
    rgba[:, :, 3] = alpha
    return np.ascontiguousarray(rgba)


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


def benchmark(
    width: int,
    height: int,
    rounds: int,
    seed: int,
    canvas_width: int,
    canvas_height: int,
) -> dict[str, float | int | str]:
    app = QApplication.instance() or QApplication([])
    rng = np.random.default_rng(int(seed))
    response = rng.random((int(height), int(width)), dtype=np.float32)
    frame_size = (int(width), int(height))

    expected = _legacy_response_overlay_rgba(response)
    actual = _response_overlay_rgba(response, frame_size)
    if expected is None or actual is None:
        raise RuntimeError("seeded response unexpectedly produced no overlay")
    max_channel_delta = int(np.max(np.abs(expected.astype(np.int16) - actual.astype(np.int16))))

    legacy_ms, legacy_peak = _measure(lambda: _legacy_response_overlay_rgba(response), rounds)
    current_ms, current_peak = _measure(lambda: _response_overlay_rgba(response, frame_size), rounds)

    canvas = PreviewCanvas()
    canvas.resize(max(1, int(canvas_width)), max(1, int(canvas_height)))
    canvas._frame_size = frame_size
    canvas.set_tracking_overlay([], None, response_map=response)
    target = QImage(canvas.width(), canvas.height(), QImage.Format.Format_ARGB32)

    def cold_display_overlay() -> QImage | None:
        canvas.set_tracking_overlay([], None, response_map=response)
        return canvas._response_overlay_image()

    display_cold_ms, display_cold_peak = _measure(cold_display_overlay, rounds)
    display_image = canvas._response_overlay_image()
    if display_image is None:
        raise RuntimeError("seeded response unexpectedly produced no display overlay")

    def paint_cached_overlay() -> None:
        painter = QPainter(target)
        canvas._paint_response_map(painter)
        painter.end()

    paint_cached_overlay()
    cached_ms, cached_peak = _measure(paint_cached_overlay, rounds)
    canvas.close()
    app.processEvents()

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "legacy_colorize_median_ms": legacy_ms,
        "current_cold_colorize_median_ms": current_ms,
        "cold_colorize_speedup": legacy_ms / current_ms if current_ms > 0.0 else 0.0,
        "legacy_python_peak_bytes": legacy_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": (
            100.0 * (legacy_peak - current_peak) / legacy_peak if legacy_peak > 0 else 0.0
        ),
        "canvas_size": f"{canvas.width()}x{canvas.height()}",
        "display_rect_size": (
            f"{canvas._display_rect().width():.1f}x{canvas._display_rect().height():.1f}"
        ),
        "display_overlay_size": f"{display_image.width()}x{display_image.height()}",
        "display_bounded_cold_median_ms": display_cold_ms,
        "display_bounded_speedup_vs_full": current_ms / display_cold_ms if display_cold_ms > 0.0 else 0.0,
        "display_bounded_python_peak_bytes": display_cold_peak,
        "display_bounded_peak_reduction_percent": (
            100.0 * (current_peak - display_cold_peak) / current_peak if current_peak > 0 else 0.0
        ),
        "cached_repaint_median_ms": cached_ms,
        "cached_repaint_python_peak_bytes": cached_peak,
        "max_rgba_channel_delta": max_channel_delta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark response-overlay colorization and repeated cached Qt paints."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--canvas-width", type=int, default=865)
    parser.add_argument("--canvas-height", type=int, default=731)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                args.width,
                args.height,
                args.rounds,
                args.seed,
                args.canvas_width,
                args.canvas_height,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
