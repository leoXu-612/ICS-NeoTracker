from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from neo_tracker.core import TrackerResult
from neo_tracker.ui.main_window import NeoTrackerWindow


def pump_until(predicate: Callable[[], bool], *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        QCoreApplication.processEvents()
        if predicate():
            QCoreApplication.processEvents()
            if predicate():
                return
        if time.monotonic() >= deadline:
            raise AssertionError("integration operation did not reach the expected state")
        time.sleep(0.001)


def tracker_results(
    times: Sequence[float],
    *,
    lost_indices: set[int] | None = None,
    quadratic: bool = False,
) -> list[TrackerResult]:
    lost = lost_indices or set()
    results: list[TrackerResult] = []
    for frame, time_s in enumerate(times):
        value = (
            1.25 * float(time_s) ** 2 + 2.0 * float(time_s) - 0.5
            if quadratic
            else 2.0 * float(time_s) + 1.0
        )
        status = "lost" if frame in lost else "ok"
        state = {} if frame in lost else {"x": value}
        filtered = {} if frame in lost else {"x": value}
        results.append(
            TrackerResult(
                frame,
                float(time_s),
                state,
                filtered,
                0.0 if frame in lost else 1.0,
                status,
                debug={
                    "filter": {
                        "velocity": {} if frame in lost else {"v_x": 2.0}
                    }
                },
            )
        )
    return results


def close_window(window: NeoTrackerWindow) -> None:
    if window._kinematics_workspace_coordinator.busy:
        window._kinematics_workspace_coordinator.cancel()
        pump_until(lambda: not window._kinematics_workspace_coordinator.busy)
    if window.analysis_workspace_controller.busy:
        window.analysis_workspace_controller.cancel("Test cleanup.")
        pump_until(lambda: not window.analysis_workspace_controller.busy)
    window._discard_unapplied_drafts(show_status=False)
    window._set_project_clean()
    window.close()
    QApplication.processEvents()
