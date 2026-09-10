"""Ordinary Qt application-flow verification; no native runtime modifications.

Run from the development checkout with PYTHONPATH=. and an explicit output
directory argument. QT_QPA_PLATFORM selects the Qt backend normally.
"""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import PySide6
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from neo_tracker.kinematics import FitStatus
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore
from tests.integration_kinematics_support import pump_until


def main() -> None:
    source_path = Path("artifacts/experiment-videos/red-dot-tracking.mp4").resolve()
    with source_path.open("rb") as handle:
        before = hashlib.file_digest(handle, "sha256").hexdigest()
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    exports = output / "exports"
    exports.mkdir()
    source_diff = subprocess.check_output([
        "git", "diff", "HEAD", "--", "neo_tracker", "tests", __file__,
    ])
    (output / "source.diff").write_bytes(source_diff)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    windows = []
    report = {
        "recorded_at": datetime.now().astimezone().isoformat(),
        "code_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_diff_sha256": hashlib.sha256(source_diff).hexdigest(),
        "runtime": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "pyside6": PySide6.__version__,
            "qt_platform": app.platformName(),
        },
        "source": {
            "path": str(source_path),
            "sha256": before,
            "provenance": "repository fixture; acquisition provenance unverified",
            "stream": json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
                "-of", "json", str(source_path),
            ], text=True))["streams"][0],
        },
        "scientific_validation": "not performed; no calibration or ground truth",
        "native_accessibility_validation": "not performed",
        "milestones": [],
    }

    def mark(name: str) -> None:
        report["milestones"].append(name)
        print(name, flush=True)

    try:
        window = NeoTrackerWindow(
            physics_layout_store=PhysicsWorkspaceStateStore(),
            physics_export_directory_picker=lambda _parent: str(exports),
        )
        windows.append(window)
        window.resize(1440, 900)
        window.show()
        pump_until(lambda: window.isVisible())
        assert window._start_media_probe([str(source_path)])
        pump_until(lambda: window._background_tasks.idle, timeout_s=20)
        assert len(window.tasks) == 1
        assert window.current_task.media_info.frame_count == 72
        mark("imported 72-frame repository fixture")

        assert window.run_tracking_button.isEnabled()
        QTest.mouseClick(window.run_tracking_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: window._background_tasks.idle, timeout_s=30)
        results = window.current_task.pipeline.results
        assert len(results) == window.results_model.rowCount() == 72
        source = window._physics_series_by_id["state:x_px"]
        np.testing.assert_array_equal(source.frame_indices, np.arange(72))
        np.testing.assert_array_equal(source.time_s, [item.time_s for item in results])
        report["tracking_status_counts"] = {
            status: sum(item.status == status for item in results)
            for status in sorted({item.status for item in results})
        }
        mark("tracking completed; result rows and true-time series agree")

        window.physics_workspace.show_page("Fit")
        index = window.fit_panel.series_combo.findData(source.series_id)
        assert index >= 0
        window.fit_panel.series_combo.setCurrentIndex(index)
        assert window.fit_panel.run_button.isEnabled()
        QTest.mouseClick(window.fit_panel.run_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: window._background_tasks.idle, timeout_s=10)
        result = window.analysis_workspace_controller.state.fit_result
        assert result is not None and result.status is FitStatus.OK
        assert result.series_id == source.series_id
        report["fit_sample_count"] = result.sample_count
        mark("fit completed through the Run Fit button")

        QTest.mouseClick(window.fit_panel.export_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: window._background_tasks.idle, timeout_s=10)
        files = {path.suffix: path for path in exports.iterdir()}
        assert set(files) == {".csv", ".npz", ".md"}
        with files[".csv"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 72
        np.testing.assert_array_equal([float(row["time_s"]) for row in rows], source.time_s)
        np.testing.assert_array_equal([float(row["value"]) for row in rows], source.values)
        with np.load(files[".npz"], allow_pickle=False) as archive:
            np.testing.assert_array_equal(archive["values"], source.values)
            np.testing.assert_array_equal(archive["fit_predicted"], result.predicted)
            assert all(not archive[name].dtype.hasobject for name in archive.files)
        assert source.source_revision in files[".md"].read_text(encoding="utf-8")
        mark("CSV, NPZ and Markdown exported and read back")

        project_path = output / "repository-fixture-flow.ntproj"
        window.project_path = project_path
        assert window._save_project()
        pump_until(lambda: window._background_tasks.idle, timeout_s=10)
        assert project_path.is_file() and not window._project_dirty
        restored = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
        windows.append(restored)
        restored.resize(1440, 900)
        restored.show()
        assert restored._start_project_open(project_path)
        pump_until(
            lambda: restored._background_tasks.idle
            and restored.analysis_workspace_controller.state.fit_result is not None,
            timeout_s=20,
        )
        assert restored.current_task.task_id == window.current_task.task_id
        assert restored._physics_series_by_id[source.series_id] == source
        assert restored.analysis_workspace_controller.state.fit_result == result
        assert not restored.fit_panel.is_dirty() and not restored._project_dirty
        mark("saved project reopened with equal source series and fit result")
        restored.physics_workspace.show_page("Fit")
        QTest.qWait(100)
        assert restored.grab().save(str(output / "restored-fit.png"))
        restored.physics_workspace.show_page("Plot")
        plot = restored.physics_workspace.plot
        export_button = restored.physics_workspace.export_plot_image_button
        plot.set_selected_sample(len(source) - 1, source.series_id)
        report["plot_layout"] = []
        for width, height in ((1024, 768), (1280, 808), (1440, 900)):
            restored.resize(width, height)
            for stage in ("compressed", "expanded"):
                if stage == "compressed":
                    restored.workspace_splitter.setSizes([restored.workspace_splitter.height(), 120])
                else:
                    restored.physics_workspace.set_collapsed(True)
                    QTest.qWait(50)
                    assert restored.physics_workspace.height() <= 38
                    assert not export_button.isVisible()
                    restored.physics_workspace.set_collapsed(False)
                QTest.qWait(100)
                assert (restored.width(), restored.height()) == (width, height), (
                    stage, restored.size(), restored.minimumSizeHint(),
                )
                assert plot.height() >= 180
                assert export_button.isVisible()
                assert export_button.height() >= export_button.minimumSizeHint().height()
                for widget in (plot, export_button):
                    parent = widget.parentWidget()
                    while parent is not None:
                        rectangle = QRect(widget.mapTo(parent, QPoint()), widget.size())
                        assert parent.rect().contains(rectangle), (parent.objectName(), parent.rect(), rectangle)
                        parent = parent.parentWidget()
                report["plot_layout"].append({
                    "window_size": [width, height],
                    "stage": stage,
                    "workspace_height": restored.physics_workspace.height(),
                    "plot_size": [plot.width(), plot.height()],
                    "fully_contained": True,
                })
            assert restored.grab().save(str(output / f"plot-layout-{width}x{height}.png"))
        mark("plot stays fully inside its containers at three window sizes after compression and expansion")
        QTest.qWait(100)
        report["window_exposed"] = restored.windowHandle().isExposed()
        report["device_pixel_ratio"] = restored.devicePixelRatioF()
        assert restored.grab().save(str(output / "restored-plot.png"))
        image_path = output / "restored-plot-export.png"
        assert plot.export_image(image_path)
        image = QImage(str(image_path))
        assert not image.isNull()
        assert (image.width(), image.height()) == (
            math.ceil(plot.width() * plot.devicePixelRatioF()),
            math.ceil(plot.height() * plot.devicePixelRatioF()),
        )
        report["plot_cursor"] = {
            "sample_index": plot.selected_sample_index,
            "time_label": f"{source.time_s[-1]:.6f} s",
            "export_size": [image.width(), image.height()],
        }
        mark("last-sample cursor rendered in the window and exported PNG")
    finally:
        for window in reversed(windows):
            window._discard_unapplied_drafts(show_status=False)
            window._set_project_clean()
            window.close()
            pump_until(
                lambda: window._background_tasks.idle and not window.isVisible(),
                timeout_s=15,
            )
    mark("all application windows closed and background tasks idle")
    with source_path.open("rb") as handle:
        report["source_unchanged"] = hashlib.file_digest(handle, "sha256").hexdigest() == before
    assert report["source_unchanged"]
    report["passed"] = True
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
