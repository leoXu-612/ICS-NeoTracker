from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from PySide6.QtWidgets import QApplication

import numpy as np

from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaInfo
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.playback_controller import PlaybackClock
from neo_tracker.ui.review_controller import ReviewController
from neo_tracker.ui.review_diagnostics import ReviewDiagnosticsPanel


def _result(frame_index: int) -> TrackerResult:
    value = float(frame_index)
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 30.0,
        state={"x_px": value, "y_px": 20.0},
        filtered_state={"x_px": value, "y_px": 20.0},
        confidence=0.9,
        status="ok",
        debug={"filter": {"velocity": {"v_x_px": 30.0}}},
    )


def _median_ms(samples: list[float]) -> float:
    return median(samples) * 1000.0


def benchmark(result_count: int, rounds: int) -> dict[str, float | int]:
    QApplication.instance() or QApplication([])
    results = [_result(index) for index in range(max(1, int(result_count)))]
    pipeline = color_marker_preset()
    pipeline.results = results
    controller = ReviewController()
    calls = max(5, int(rounds))

    cold_overlay: list[float] = []
    warm_overlay: list[float] = []
    lookup: list[float] = []
    for iteration in range(calls):
        frame_index = (iteration * 997) % len(results)
        controller.invalidate_overlay_cache()
        started = perf_counter()
        controller.overlay(
            pipeline,
            frame_index,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )
        cold_overlay.append(perf_counter() - started)

        started = perf_counter()
        controller.overlay(
            pipeline,
            frame_index,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )
        warm_overlay.append(perf_counter() - started)

        started = perf_counter()
        controller.preferred_result_index(results, None, frame_index)
        lookup.append(perf_counter() - started)

    panel = ReviewDiagnosticsPanel()
    build: list[float] = []
    for _ in range(max(3, calls // 20)):
        started = perf_counter()
        panel.set_results(results, {"x_px": "px"}, selected_frame=0)
        build.append(perf_counter() - started)
    panel.select_mode("velocity")

    selection: list[float] = []
    for iteration in range(calls):
        started = perf_counter()
        panel.set_selected_frame((iteration * 997) % len(results))
        selection.append(perf_counter() - started)
    panel.close()

    class StaticReader:
        def __init__(self) -> None:
            self.frame = np.zeros((360, 640, 3), dtype=np.uint8)

        def read_frame(self, _frame_index: int) -> np.ndarray:
            return self.frame

        def close(self) -> None:
            pass

    window = NeoTrackerWindow()
    task = window.current_task
    task.pipeline.results = results
    task.tracking_outcome = "complete"
    task.media_path = "/synthetic/long-review.mp4"
    task.media_info = MediaInfo(
        fps=30.0,
        frame_count=len(results),
        width=640,
        height=360,
        duration_s=len(results) / 30.0,
        available=True,
    )
    task.media_reader = StaticReader()
    window.preview_label.set_frame(np.zeros((360, 640, 3), dtype=np.uint8))
    window.review_controller.invalidate_overlay_cache()
    window._preview_frame_changed(0)
    preview_selection: list[float] = []
    for iteration in range(calls):
        started = perf_counter()
        window._preview_frame_changed((iteration * 997) % len(results))
        preview_selection.append(perf_counter() - started)
    window.close()

    now = [100.0]
    clock = PlaybackClock(clock=lambda: now[0])
    clock.start(current_frame=0, frame_count=300, fps=30.0)
    now[0] += 0.67
    tick = clock.tick(0)

    return {
        "results": len(results),
        "rounds": calls,
        "overlay_cold_ms": _median_ms(cold_overlay),
        "overlay_warm_ms": _median_ms(warm_overlay),
        "frame_lookup_ms": _median_ms(lookup),
        "diagnostics_build_ms": _median_ms(build),
        "diagnostics_selection_ms": _median_ms(selection),
        "full_preview_frame_change_ms": _median_ms(preview_selection),
        "source_time_target_frame_at_0_67_s_30_fps": tick.frame_index,
        "preview_frames_skipped": tick.skipped_total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark preview playback review hot paths.")
    parser.add_argument("--results", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
