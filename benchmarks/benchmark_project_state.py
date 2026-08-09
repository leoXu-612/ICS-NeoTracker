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

from neo_tracker.core import TrackerResult
from neo_tracker.ui.main_window import NeoTrackerWindow


def _result(frame_index: int) -> TrackerResult:
    value = float(frame_index)
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 30.0,
        state={"x_px": value, "y_px": value % 100.0},
        filtered_state={"x_px": value, "y_px": value % 100.0},
        confidence=0.9,
        status="ok",
        prediction={"x_px": value + 1.0, "y_px": value % 100.0},
        debug={"candidate_count": 3, "innovation": 0.25},
    )


def _median_ms(samples: list[float]) -> float:
    return median(samples) * 1000.0


def benchmark(result_count: int, rounds: int) -> dict[str, float | int]:
    QApplication.instance() or QApplication([])
    window = NeoTrackerWindow()
    window.current_task.pipeline.results = [
        _result(frame_index) for frame_index in range(max(1, int(result_count)))
    ]

    started = perf_counter()
    window._set_project_clean()
    clean_commit_ms = (perf_counter() - started) * 1000.0

    calls = max(3, int(rounds))
    forced: list[float] = []
    cached: list[float] = []
    for _ in range(calls):
        started = perf_counter()
        window._render_project_label()
        forced.append(perf_counter() - started)

        started = perf_counter()
        window._render_project_label(use_cached=True)
        cached.append(perf_counter() - started)

    fingerprint_bytes = len(window._current_project_fingerprint.encode("utf-8"))
    window.close()

    forced_ms = _median_ms(forced)
    cached_ms = _median_ms(cached)
    return {
        "results": max(1, int(result_count)),
        "rounds": calls,
        "fingerprint_bytes": fingerprint_bytes,
        "clean_commit_ms": clean_commit_ms,
        "forced_project_state_refresh_ms": forced_ms,
        "cached_project_state_render_ms": cached_ms,
        "cached_render_speedup": forced_ms / max(cached_ms, 1e-12),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark exact and cached project dirty-state rendering.")
    parser.add_argument("--results", type=int, default=20_000)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.rounds), indent=2))


if __name__ == "__main__":
    main()
