"""Ordinary Qt application-flow verification; no native runtime modifications.

Run from the development checkout with PYTHONPATH=. and an explicit output
directory argument. QT_QPA_PLATFORM selects the Qt backend normally.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess

import numpy as np
import PySide6
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from neo_tracker.kinematics import FitStatus
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore
from tests.integration_kinematics_support import pump_until


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, default=Path("artifacts/experiment-videos/red-dot-tracking.mp4"))
    parser.add_argument("--provenance", default="repository fixture; acquisition provenance unverified")
    parser.add_argument("--marker-point", nargs=2, type=int, metavar=("X", "Y"))
    parser.add_argument("--roi", nargs=4, type=float, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--tracking-timeout-s", type=float, default=30.0)
    args = parser.parse_args()
    source_path = args.source.resolve()
    with source_path.open("rb") as handle:
        before = hashlib.file_digest(handle, "sha256").hexdigest()
    output = args.output.resolve()
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
            "tab_focus_behavior": str(app.styleHints().tabFocusBehavior()),
            "screens": [
                {
                    "name": screen.name(),
                    "geometry": list(screen.geometry().getRect()),
                    "device_pixel_ratio": screen.devicePixelRatio(),
                }
                for screen in app.screens()
            ],
        },
        "source": {
            "path": str(source_path),
            "sha256": before,
            "provenance": args.provenance,
            "stream": json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
                "-of", "json", str(source_path),
            ], text=True))["streams"][0],
        },
        "scientific_validation": "not performed; no calibration or ground truth",
        "native_accessibility_validation": "not performed",
        "passed": False,
        "milestones": [],
    }

    def mark(name: str) -> None:
        report["milestones"].append(name)
        print(name, flush=True)

    def track_and_fit(target: NeoTrackerWindow):
        assert target.run_tracking_button.isEnabled()
        QTest.mouseClick(target.run_tracking_button, Qt.MouseButton.LeftButton)
        timer = QTimer(target)
        timer.setInterval(10000)
        timer.timeout.connect(lambda: print(
            f"tracking progress: {target.tracking_summary_label.text()} | active={target._background_tasks.active_kinds}",
            flush=True,
        ))
        timer.start()
        try:
            pump_until(lambda: target._background_tasks.idle, timeout_s=args.tracking_timeout_s)
        finally:
            timer.stop()
            timer.deleteLater()
        results = target.current_task.pipeline.results
        assert len(results) == target.results_model.rowCount() == frame_count
        series = target._physics_series_by_id["state:x_px"]
        np.testing.assert_array_equal(series.frame_indices, np.arange(frame_count))
        np.testing.assert_array_equal(series.time_s, [item.time_s for item in results])
        np.testing.assert_allclose(series.time_s, reference_times, rtol=0.0, atol=1e-6)
        counts = {
            status: sum(item.status == status for item in results)
            for status in sorted({item.status for item in results})
        }
        report.setdefault("tracking_runs", []).append({
            "status_counts": counts,
            "frames": len(results),
            "max_pts_error_s": float(np.max(np.abs(series.time_s - reference_times))),
        })
        mark("tracking completed; result rows and true-time series agree")
        target.physics_workspace.show_page("Fit")
        index = target.fit_panel.series_combo.findData(series.series_id)
        assert index >= 0
        target.fit_panel.series_combo.setCurrentIndex(index)
        assert target.fit_panel.run_button.isEnabled()
        QTest.mouseClick(target.fit_panel.run_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: target._background_tasks.idle, timeout_s=10)
        fit = target.analysis_workspace_controller.state.fit_result
        assert fit is not None and fit.status is FitStatus.OK
        assert fit.series_id == series.series_id
        mark("fit completed through the Run Fit button")
        return series, fit

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
        pump_until(lambda: window._background_tasks.idle, timeout_s=60)
        assert len(window.tasks) == 1
        frame_count = int(report["source"]["stream"]["nb_frames"])
        assert frame_count > 37, "the keyboard-selection checks require at least 38 frames"
        assert window.current_task.media_info.frame_count == frame_count
        packets = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time", "-of", "json", str(source_path),
        ], text=True))["packets"]
        presentation_times = np.asarray(sorted(float(item["pts_time"]) for item in packets))
        assert len(presentation_times) == len(np.unique(presentation_times)) == frame_count, (
            "the reference check requires one unique presentation timestamp per decoded frame"
        )
        reference_times = presentation_times - presentation_times[0]
        report["timestamp_reference"] = {
            "source": "ffprobe packet PTS sorted in presentation order; one packet per frame",
            "origin_s": float(presentation_times[0]),
            "minimum_interval_s": float(np.min(np.diff(reference_times))),
            "maximum_interval_s": float(np.max(np.diff(reference_times))),
        }
        report["app_media"] = {
            "width": window.current_task.media_info.width,
            "height": window.current_task.media_info.height,
            "fps": window.current_task.media_info.fps,
            "frame_count": frame_count,
        }
        pump_until(
            lambda: window._preview_decode_cache is not None
            and window._preview_decode_cache.request.frame_index == 0
            and window.preview_label.has_frame(),
            timeout_s=30,
        )
        if args.marker_point is not None:
            x, y = args.marker_point
            frame = window._preview_decode_cache.bgr_frame
            assert 0 <= x < frame.shape[1] and 0 <= y < frame.shape[0]
            expected_rgb = tuple(float(value) for value in frame[y, x, ::-1])
            window._color_sample_selected((x, y))
            assert window._color_blob_observation().sample_rgb == expected_rgb
            report["marker_sample"] = {"point_px": [x, y], "rgb": expected_rgb}
        if args.roi is not None:
            window._roi_selected(tuple(args.roi))
            assert window.current_task.roi == window._normalize_roi_selection(tuple(args.roi))
        report["tracking_roi"] = window.current_task.roi
        mark(f"imported {frame_count}-frame source with decoded preview and requested marker/ROI")

        source, result = track_and_fit(window)
        report["tracking_status_counts"] = report["tracking_runs"][0]["status_counts"]
        report["fit_sample_count"] = result.sample_count

        QTest.mouseClick(window.fit_panel.export_button, Qt.MouseButton.LeftButton)
        pump_until(lambda: window._background_tasks.idle, timeout_s=10)
        files = {path.suffix: path for path in exports.iterdir()}
        assert set(files) == {".csv", ".npz", ".md"}
        with files[".csv"].open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == frame_count
        np.testing.assert_array_equal([float(row["time_s"]) for row in rows], source.time_s)
        np.testing.assert_array_equal([float(row["value"]) for row in rows], source.values)
        with np.load(files[".npz"], allow_pickle=False) as archive:
            np.testing.assert_array_equal(archive["values"], source.values)
            np.testing.assert_array_equal(archive["fit_predicted"], result.predicted)
            assert all(not archive[name].dtype.hasobject for name in archive.files)
        assert source.source_revision in files[".md"].read_text(encoding="utf-8")
        mark("CSV, NPZ and Markdown exported and read back")

        project_path = output / "video-flow.ntproj"
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
        pending_relink = restored.current_task.pending_media_relink
        report["project_reopen"] = {
            "results_and_fit_equal": True,
            "media_available": restored.current_task.media_info.available,
            "source_review_required": restored.current_task.media_identity_requires_review,
            "assessment_state": pending_relink[1].state if pending_relink else None,
            "identity_state": pending_relink[1].identity_state if pending_relink else None,
        }
        mark("saved project reopened with equal source series and fit result")
        if restored.current_task.media_identity_requires_review:
            assert pending_relink is not None
            assessment = pending_relink[1]
            assert assessment.state == "match" and assessment.identity_state == "sampled"
            assert assessment.clear_results and not restored.current_task.media_info.available
            assert not restored.run_tracking_button.isEnabled()
            assert not restored.export_tracking_csv_button.isEnabled()
            assert not restored.export_physics_analysis_button.isEnabled()
            assert not restored.fit_panel.export_button.isEnabled()
            assert not restored.physics_workspace.export_plot_image_button.isEnabled()
            assert not restored.preview_label.has_frame()
            restored.physics_workspace.show_page("Plot")
            restored.resize(1024, 768)
            QTest.qWait(100)
            assert restored.size().toTuple() == (1024, 768)
            assert restored.grab().save(str(output / "source-review.png"))
            with source_path.open("rb") as handle:
                assert hashlib.file_digest(handle, "sha256").hexdigest() == before
            # This window owns only disposable test results. Exercise the explicit
            # destructive UI action; do not change the source-verification policy.
            apply_button = restored.media_relink_panel.apply_button
            assert apply_button.isEnabled() and "Clear Results/Edits" in apply_button.text()
            QTest.mouseClick(apply_button, Qt.MouseButton.LeftButton)
            assert not restored.current_task.media_identity_requires_review
            assert restored.current_task.media_info.available
            assert not restored.current_task.pipeline.results
            replacement_source, replacement_fit = track_and_fit(restored)
            np.testing.assert_array_equal(replacement_source.values, source.values)
            np.testing.assert_array_equal(replacement_source.time_s, source.time_s)
            source, result = replacement_source, replacement_fit
            report["source_review_recovery"] = "explicit clear-and-retrack button; equal values and times"
            mark("sampled-source review preserved until explicit test-only clear and deterministic retracking")
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

        workspace = restored.physics_workspace
        workspace.show_page("Data")
        workspace.series_combo.setCurrentIndex(workspace.series_combo.findData(source.series_id))
        table = workspace.series_table
        middle = len(source) // 2
        table.setCurrentIndex(workspace.series_model.index(middle - 1, 0))
        QTest.qWait(100)
        report["keyboard_selection"] = []
        for page, key, expected in (
            ("Data", Qt.Key.Key_Down, middle),
            ("Plot", Qt.Key.Key_Right, middle + 1),
            ("Plot", Qt.Key.Key_Left, middle),
        ):
            workspace.show_page(page)
            target = table if page == "Data" else plot
            target.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.qWait(50)
            assert restored.focusWidget() is target
            before_revision = restored.selection_session.state.selection_revision
            QTest.keyClick(target, key)
            pump_until(
                lambda: restored._preview_decode_cache is not None
                and restored._preview_decode_cache.request.frame_index == int(source.frame_indices[expected])
                and restored.preview_label.has_frame(),
                timeout_s=15,
            )
            state = restored.selection_session.state
            assert state.selection_revision == before_revision + 1
            assert state.selected_series_id == source.series_id
            assert state.selected_sample_index == expected
            assert state.selected_frame_index == int(source.frame_indices[expected])
            assert state.selected_time_s == float(source.time_s[expected])
            assert restored.current_task.preview_frame_index == state.selected_frame_index
            assert restored.preview_frame_spin.value() == state.selected_frame_index
            assert table.selectionModel().selectedRows()[0].row() == expected
            assert plot.selected_sample_index == expected
            report["keyboard_selection"].append({
                "page": page,
                "key": key.name,
                "sample_index": expected,
                "frame_index": state.selected_frame_index,
                "time_s": state.selected_time_s,
                "selection_revision_delta": state.selection_revision - before_revision,
                "preview_loaded": True,
            })
        assert restored._physics_series_by_id[source.series_id] == source
        assert restored.analysis_workspace_controller.state.fit_result == result
        assert restored.grab().save(str(output / "keyboard-plot-selection.png"))
        mark("Data and Plot arrow keys update one shared selection revision and aligned frame/time")

        report["keyboard_tab"] = {"event_source": "Qt QTest; not physical OS key events"}
        for direction, modifiers in (
            ("forward", Qt.KeyboardModifier.NoModifier),
            ("reverse", Qt.KeyboardModifier.ShiftModifier),
        ):
            plot.setFocus(Qt.FocusReason.TabFocusReason)
            QTest.qWait(50)
            seen = [plot]
            for _ in range(64):
                QTest.keyClick(restored.focusWidget(), Qt.Key.Key_Tab, modifiers)
                QTest.qWait(10)
                focused = restored.focusWidget()
                assert focused is not None
                if focused in seen:
                    assert focused is plot
                    break
                seen.append(focused)
            else:
                raise AssertionError("keyboard focus traversal did not close within 64 steps")
            report["keyboard_tab"][direction] = {
                "loop_closed": True,
                "export_png_reachable": export_button in seen,
                "controls": [
                    {
                        "class": widget.metaObject().className(),
                        "object_name": widget.objectName(),
                        "accessible_name": widget.accessibleName(),
                    }
                    for widget in seen
                ],
            }
        mark("Tab and Shift+Tab focus loops recorded under the current platform policy")
        report["passed"] = True
    except Exception as error:
        report["failure"] = f"{type(error).__name__}: {error}"
        report["failure_context"] = [
            {
                "active_kinds": item._background_tasks.active_kinds,
                "result_count": len(item.current_task.pipeline.results),
                "tracking_status": item.tracking_summary_label.text(),
                "status": item.statusBar().currentMessage(),
                "media_error": item.current_task.media_info.error if item.current_task.media_info else None,
            }
            for item in windows
        ]
        raise
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
        report["passed"] = report["passed"] and report["source_unchanged"]
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        assert report["source_unchanged"]
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
