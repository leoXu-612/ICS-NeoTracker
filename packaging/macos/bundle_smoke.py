"""Opt-in check of the actual frozen app; uses only disposable synthetic media."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import traceback


def run(report_path: str) -> int:
    import cv2
    import numpy as np
    from PySide6.QtWidgets import QApplication
    from scipy import signal
    from scipy.optimize import least_squares
    from neo_tracker.ui.main_window import NeoTrackerWindow
    from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore

    report = {"frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
              "passed": False, "checks": []}
    report["bundled_modules"] = {
        name: str(sys.modules[name].__file__)
        for name in ("numpy", "cv2", "scipy", "PySide6", "neo_tracker")
    }
    app = QApplication.instance() or QApplication(["Neo-Tracker packaging check"])
    windows = []

    def wait_for(predicate, timeout=30.0):
        deadline = monotonic() + timeout
        while True:
            app.processEvents()
            if predicate():
                app.processEvents()
                if predicate():
                    return
            if monotonic() >= deadline:
                raise TimeoutError("Frozen app operation did not finish")
            sleep(0.005)

    try:
        with TemporaryDirectory(prefix="neotracker-bundle-") as directory:
            root = Path(directory)
            video = root / "marker.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (320, 240))
            if not writer.isOpened():
                raise RuntimeError("Bundled OpenCV cannot create the synthetic MP4")
            try:
                for index in range(24):
                    frame = np.zeros((240, 320, 3), dtype=np.uint8)
                    cv2.circle(frame, (60 + 4 * index, 120), 12, (0, 0, 255), -1)
                    writer.write(frame)
            finally:
                writer.release()
            exports = root / "exports"
            exports.mkdir()
            window = NeoTrackerWindow(
                physics_layout_store=PhysicsWorkspaceStateStore(),
                physics_export_directory_picker=lambda _parent: str(exports),
            )
            windows.append(window)
            window.show()
            wait_for(lambda: window.windowHandle() is not None and window.windowHandle().isExposed())
            report["checks"].append("Cocoa window exposed")
            assert window._start_media_probe([str(video)])
            wait_for(lambda: window._background_tasks.idle)
            assert window.current_task.media_info.available
            assert window.current_task.media_info.frame_count == 24
            report["checks"].append("frozen probe and preview helpers")
            window._run_tracking()
            wait_for(lambda: window._background_tasks.idle)
            results = window.current_task.pipeline.results
            assert len(results) == 24
            source = window._physics_series_by_id["state:x_px"]
            np.testing.assert_allclose(source.values, 60 + 4 * np.arange(24), atol=2)
            np.testing.assert_allclose(source.time_s, np.arange(24) / 30, atol=1e-9)
            report["checks"].append("frozen tracking helper: 24 frames, positions and times")
            assert window.analysis_workspace_controller.select_series(source.series_id)
            window._run_physics_fit(window.fit_panel.draft())
            wait_for(lambda: window._background_tasks.idle)
            fit = window.analysis_workspace_controller.state.fit_result
            assert fit is not None and fit.sample_count == 24
            window._export_physics_analysis()
            wait_for(lambda: window._background_tasks.idle)
            assert {p.suffix for p in exports.iterdir()} == {".csv", ".npz", ".md"}
            with np.load(next(exports.glob("*.npz")), allow_pickle=False) as archive:
                np.testing.assert_array_equal(archive["time_s"], source.time_s)
            report["checks"].append("fit and CSV/NPZ/Markdown export")
            window.project_path = root / "smoke.ntproj"
            assert window._save_project()
            wait_for(lambda: window._background_tasks.idle)
            restored = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
            windows.append(restored)
            assert restored._start_project_open(window.project_path)
            wait_for(lambda: restored._background_tasks.idle
                     and restored.analysis_workspace_controller.state.fit_result is not None)
            assert restored._physics_series_by_id[source.series_id] == source
            assert restored.analysis_workspace_controller.state.fit_result == fit
            report["checks"].append("frozen project-open helper and persistence")
            assert signal.stft(np.arange(128.0), nperseg=32)[2].size > 0
            np.testing.assert_allclose(least_squares(lambda x: x - 2, [0.0]).x, [2.0])
            report["checks"].append("bundled SciPy signal and optimizer")
            report["qt_platform"] = app.platformName()
            report["opencv"] = cv2.__version__
            for owned_window in reversed(windows):
                owned_window._discard_unapplied_drafts(show_status=False)
                owned_window._set_project_clean()
                owned_window.close()
                wait_for(lambda w=owned_window: w._background_tasks.idle and not w.isVisible())
            windows.clear()
            report["checks"].append("all test windows closed; temporary media removed")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        for owned_window in reversed(windows):
            owned_window._discard_unapplied_drafts(show_status=False)
            owned_window._set_project_clean()
            owned_window.close()
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["passed"] else 1
