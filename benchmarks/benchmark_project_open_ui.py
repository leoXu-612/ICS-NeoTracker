from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from neo_tracker.core import TrackerResult
from neo_tracker.project import (
    NeoTrackerProject,
    ProjectTaskSnapshot,
    project_content_fingerprint,
)
from neo_tracker.ui.main_window import NeoTrackerWindow


def _result(frame_index: int) -> TrackerResult:
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 30.0,
        state={"x_px": 12.0, "y_px": 18.0},
        filtered_state={"x_px": 12.2, "y_px": 18.1},
        confidence=0.9,
        status="ok",
        observation=None,
        prediction={"x_px": 12.1, "y_px": 18.0},
        debug={},
    )


def _write_project(path: Path, result_count: int) -> None:
    project = NeoTrackerProject(
        name=f"large-{result_count}",
        tasks=[
            ProjectTaskSnapshot(
                media_path=None,
                pipeline_key="color_marker",
                results=[_result(index) for index in range(result_count)],
            )
        ],
    )
    project.save(path)


def _pump_events(app: QApplication, rounds: int = 5) -> None:
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.003)


def _measure(
    app: QApplication,
    operation: Callable[[], None],
    finished: Callable[[], bool],
    *,
    timeout_s: float = 15.0,
    phase: Callable[[], str] | None = None,
) -> dict[str, float]:
    ticks: list[tuple[float, str]] = []
    timer = QTimer()
    timer.setInterval(2)
    timer.timeout.connect(
        lambda: ticks.append((time.perf_counter(), phase() if phase is not None else ""))
    )
    timer.start()
    _pump_events(app)
    started = time.perf_counter()
    operation()
    call_ms = (time.perf_counter() - started) * 1000.0
    while not finished() and time.perf_counter() - started < timeout_s:
        app.processEvents()
        time.sleep(0.001)
    batch_ms = (time.perf_counter() - started) * 1000.0
    _pump_events(app)
    timer.stop()
    gaps = [
        ((second[0] - first[0]) * 1000.0, first[1], second[1])
        for first, second in zip(ticks, ticks[1:])
    ]
    max_gap = max(gaps, default=(0.0, "", ""), key=lambda item: item[0])
    return {
        "call_ms": call_ms,
        "batch_ms": batch_ms,
        "max_heartbeat_ms": max_gap[0],
        "max_gap_phase_before": max_gap[1],
        "max_gap_phase_after": max_gap[2],
    }


def _synchronous_open(window: NeoTrackerWindow, path: Path) -> None:
    project = window.project_loader(path)
    snapshots = project.tasks or [
        ProjectTaskSnapshot(media_path=media_path, pipeline_key=window.default_pipeline_key)
        for media_path in project.media_paths
    ]
    tasks = tuple(window._task_from_snapshot(snapshot) for snapshot in snapshots)
    canonical_project = NeoTrackerProject(
        name=path.stem,
        tasks=[window._snapshot_from_task(task) for task in tasks],
    )
    fingerprint = project_content_fingerprint(canonical_project)
    window._apply_loaded_project(
        project,
        path,
        prepared_tasks=tasks,
        clean_fingerprint=fingerprint,
    )


def _signature(window: NeoTrackerWindow) -> dict[str, Any]:
    return {
        "project_path": str(window.project_path) if window.project_path else "",
        "tasks": len(window.tasks),
        "results": len(window.current_task.pipeline.results),
        "fingerprint": window._current_project_fingerprint,
        "dirty": window._project_dirty,
    }


def benchmark(
    result_counts: list[int],
    *,
    background_repeats: int = 1,
) -> list[dict[str, Any]]:
    app = QApplication.instance() or QApplication([])
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        for result_count in result_counts:
            path = root / f"large-{result_count}.ntproj"
            _write_project(path, result_count)

            synchronous_window = NeoTrackerWindow()
            synchronous = _measure(
                app,
                lambda: _synchronous_open(synchronous_window, path),
                lambda: True,
            )
            synchronous_signature = _signature(synchronous_window)
            synchronous_payload = synchronous_window._project_from_window(path).to_dict()
            synchronous_window.close()
            synchronous_window.deleteLater()
            _pump_events(app)
            del synchronous_window
            # Keep the background measurement independent of the intentionally
            # blocking reference implementation and its 100k-result object graph.
            gc.collect()

            background_window = NeoTrackerWindow()
            background_runs: list[dict[str, float]] = []
            for _ in range(max(1, int(background_repeats))):
                measurement = _measure(
                    app,
                    lambda: background_window._start_project_open(path),
                    lambda: (
                        background_window._project_open_thread is None
                        and not background_window._project_open_diagnostics_pending
                        and not background_window._project_open_analysis_pending
                    ),
                    phase=lambda: (
                        background_window.media_probe_status_label.text()
                        if background_window._project_open_thread is not None
                        else "finished"
                    ),
                )
                measurement["apply_ms"] = background_window._last_project_open_apply_ms
                background_runs.append(measurement)
            background = background_runs[-1]
            background_signature = _signature(background_window)
            deferred_state = {
                "diagnostics_pending": background_window._project_open_diagnostics_pending,
                "analysis_pending": background_window._project_open_analysis_pending,
            }
            background_payload = background_window._project_from_window(path).to_dict()
            background_window._saved_project_fingerprint = None
            background_window._project_dirty = False
            background_window.close()

            row: dict[str, Any] = {
                    "results": result_count,
                    "file_mib": path.stat().st_size / (1024.0 * 1024.0),
                    "synchronous": synchronous,
                    "background": background,
                    "results_exact": synchronous_signature == background_signature,
                    "synchronous_signature": synchronous_signature,
                    "background_signature": background_signature,
                    "payload_equal": synchronous_payload == background_payload,
                    "deferred_state": deferred_state,
                }
            if len(background_runs) > 1:
                row["background_runs"] = background_runs
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure GUI heartbeat while opening persisted projects with large result histories."
    )
    parser.add_argument(
        "--results",
        type=int,
        nargs="+",
        default=[20_000, 50_000],
    )
    parser.add_argument(
        "--repeat-background",
        type=int,
        default=1,
        help="Open each project repeatedly in the same window to exercise replacement cleanup.",
    )
    parser.add_argument(
        "--max-heartbeat-ms",
        type=float,
        default=None,
        help="Exit non-zero if any measured background heartbeat exceeds this limit.",
    )
    args = parser.parse_args()
    counts = [max(1, int(value)) for value in args.results]
    rows = benchmark(
        counts,
        background_repeats=max(1, int(args.repeat_background)),
    )
    print(json.dumps(rows, indent=2))
    if args.max_heartbeat_ms is not None:
        measured = [
            run["max_heartbeat_ms"]
            for row in rows
            for run in row.get("background_runs", [row["background"]])
        ]
        if any(value > float(args.max_heartbeat_ms) for value in measured):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
