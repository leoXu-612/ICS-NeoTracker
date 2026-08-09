from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from neo_tracker.media import MediaReader
from neo_tracker.ui.main_window import NeoTrackerWindow


AUDIT_DIR = Path(__file__).resolve().parent
VIDEO_PATH = AUDIT_DIR / "synthetic-red-marker.mp4"


def process_for(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.002)
    QCoreApplication.processEvents()


def wait_until(predicate, timeout_s: float, description: str) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.002)
    QCoreApplication.processEvents()
    if not predicate():
        raise RuntimeError(f"Timed out waiting for {description}")


def save(window: NeoTrackerWindow, filename: str, *, settle_s: float = 0.05) -> dict[str, object]:
    process_for(settle_s)
    path = AUDIT_DIR / filename
    # QWidget.grab() can preserve stale child backing-store regions with the
    # offscreen plugin while the tracking UI is changing quickly. Rendering the
    # complete hierarchy gives the audit a deterministic, current-state image.
    window.repaint()
    process_for(0.01)
    image = QPixmap(window.size())
    image.fill(Qt.GlobalColor.transparent)
    window.render(image)
    header_object_names = (
        "appTitle",
        "globalProjectDirtyLabel",
        "globalDraftLabel",
        "mediaTitle",
        "statusChip",
        "summaryChip",
    )
    # The offscreen Qt plugin intermittently omits transparent QLabel children
    # from a whole-window render. Overlay the real child widgets themselves;
    # this remains a direct render of the live UI rather than a reconstruction.
    header_overlays: list[tuple[QPoint, QPixmap]] = []
    for object_name in header_object_names:
        widget = window.findChild(QWidget, object_name)
        if widget is not None and not widget.isHidden():
            overlay = QPixmap(widget.size())
            overlay.fill(Qt.GlobalColor.transparent)
            widget.render(overlay)
            header_overlays.append((widget.mapTo(window, QPoint()), overlay))
    painter = QPainter(image)
    try:
        for position, overlay in header_overlays:
            painter.drawPixmap(position, overlay)
    finally:
        painter.end()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"Could not save {path}")
    header_widgets: dict[str, dict[str, object]] = {}
    for object_name in header_object_names:
        widget = window.findChild(QWidget, object_name)
        if widget is None:
            continue
        geometry = widget.geometry()
        mapped = widget.mapTo(window, QPoint())
        header_widgets[object_name] = {
            "visible": widget.isVisible(),
            "geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()],
            "mapped_position": [mapped.x(), mapped.y()],
            "size_hint": [widget.sizeHint().width(), widget.sizeHint().height()],
            "minimum_size_hint": [widget.minimumSizeHint().width(), widget.minimumSizeHint().height()],
        }
    return {
        "file": str(path),
        "size": [image.width(), image.height()],
        "status": window.tracking_status_label.text(),
        "summary": window.tracking_summary_label.text(),
        "sidebar_tab": window.sidebar_tabs.tabText(window.sidebar_tabs.currentIndex()),
        "run_button": window.run_tracking_button.text(),
        "status_bar": window.statusBar().currentMessage(),
        "results": len(window.current_task.pipeline.results),
        "thread_active": window._tracking_thread is not None,
        "header_widgets": header_widgets,
    }


def make_video() -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    width, height = 640, 360
    fps, frame_count = 30.0, 180
    writer = cv2.VideoWriter(
        str(VIDEO_PATH),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the synthetic MP4")
    try:
        for index in range(frame_count):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:] = (24, 30, 42)
            x = 70 + int((width - 140) * index / max(1, frame_count - 1))
            y = height // 2 + int(65 * np.sin(index / 18.0))
            cv2.circle(frame, (x, y), 13, (0, 0, 255), -1, lineType=cv2.LINE_AA)
            cv2.line(frame, (36, height - 44), (width - 36, height - 44), (76, 92, 112), 2)
            cv2.putText(
                frame,
                f"Synthetic marker  Frame {index:03d}",
                (32, 42),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (210, 220, 235),
                2,
                cv2.LINE_AA,
            )
            writer.write(frame)
    finally:
        writer.release()


class DelayedReader:
    def __init__(self, path: str, delay_s: float = 0.20) -> None:
        self.reader = MediaReader(path)
        self.info = self.reader.info
        self.delay_s = delay_s

    def read_frame(self, frame_index: int):
        time.sleep(self.delay_s)
        return self.reader.read_frame(frame_index)

    def read_frame_for_processing(self, frame_index: int):
        time.sleep(self.delay_s)
        return self.reader.read_frame_for_processing(frame_index)

    def close(self) -> None:
        self.reader.close()


def tab_focus_sample(window: NeoTrackerWindow, count: int = 16) -> list[dict[str, str]]:
    window.sidebar_tabs.setCurrentWidget(window.review_tab)
    window.results_table.setFocus(Qt.FocusReason.OtherFocusReason)
    process_for(0.02)
    entries: list[dict[str, str]] = []
    for _ in range(count):
        widget = QApplication.focusWidget()
        if widget is not None:
            entries.append(
                {
                    "class": type(widget).__name__,
                    "object": widget.objectName(),
                    "accessible_name": widget.accessibleName(),
                    "text": getattr(widget, "text", lambda: "")(),
                }
            )
        QTest.keyClick(window, Qt.Key.Key_Tab)
        process_for(0.01)
    return entries


def main() -> int:
    make_video()
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = NeoTrackerWindow()
    window.resize(1440, 900)
    window.show()
    process_for(0.15)

    screenshots: list[dict[str, object]] = []
    if not window._start_media_probe([str(VIDEO_PATH)]):
        raise RuntimeError("Media probe did not start")
    wait_until(lambda: window._media_probe_thread is None, 8.0, "media import")
    wait_until(lambda: window._preview_decode_thread is None, 8.0, "initial preview decode")
    window.sidebar_tabs.setCurrentIndex(0)
    process_for(0.15)
    screenshots.append(save(window, "current-01-media-ready.png"))
    window.resize(1024, 768)
    screenshots.append(save(window, "current-01b-media-ready-1024x768.png"))
    window.resize(1440, 900)
    process_for(0.05)

    task = window.current_task
    window._reader_for_task = lambda _task: DelayedReader(str(VIDEO_PATH))  # type: ignore[method-assign]
    window._tracking_reader_for_path = lambda _path: DelayedReader(str(VIDEO_PATH))  # type: ignore[method-assign]
    window._run_tracking()
    wait_until(
        lambda: window._tracking_thread is not None and len(task.pipeline.results) >= 2,
        5.0,
        "visible tracking progress",
    )
    screenshots.append(save(window, "current-02-tracking-running.png"))

    window._cancel_tracking()
    screenshots.append(save(window, "current-03-cancel-requested.png", settle_s=0.01))
    wait_until(lambda: window._tracking_thread is None, 8.0, "tracking cancellation")
    screenshots.append(save(window, "current-04-canceled-review.png", settle_s=0.25))

    window._ask_result_replacement = lambda _task: True  # type: ignore[method-assign]
    window._reader_for_task = lambda _task: MediaReader(str(VIDEO_PATH))  # type: ignore[method-assign]
    window._tracking_reader_for_path = lambda _path: MediaReader(str(VIDEO_PATH))  # type: ignore[method-assign]
    window._run_tracking()
    wait_until(lambda: window._tracking_thread is None, 20.0, "complete tracking run")
    screenshots.append(save(window, "current-05-complete-review.png"))
    window.resize(1024, 768)
    screenshots.append(save(window, "current-05b-complete-review-1024x768.png"))
    window.resize(1440, 900)
    process_for(0.05)

    focus_chain = tab_focus_sample(window)

    def complete_during_close(worker) -> None:
        # Leave a deterministic cancellation window before the terminal signal,
        # then keep the worker thread alive long enough to capture the UI's
        # post-signal/pre-thread-finished state.
        time.sleep(0.25)
        worker.completed.emit(len(task.pipeline.results), False, False, "")
        time.sleep(3.0)

    with patch("neo_tracker.ui.main_window.TrackingWorker.run", new=complete_during_close):
        window._run_tracking()
        wait_until(lambda: window._tracking_thread is not None, 2.0, "closing test tracking start")
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        window.close()
        wait_until(
            lambda: window.isVisible()
            and window.tracking_status_label.text() == "Closing…",
            5.0,
            "closing terminal gap",
        )
        screenshots.append(save(window, "current-06-closing-waits.png", settle_s=0.01))
        wait_until(
            lambda: window._tracking_thread is None and not window.isVisible(),
            12.0,
            "safe close after tracking cancellation",
        )

    report = {
        "video": str(VIDEO_PATH),
        "minimum_window_size": [
            window.minimumSizeHint().width(),
            window.minimumSizeHint().height(),
        ],
        "screenshots": screenshots,
        "focus_chain": focus_chain,
        "close_result": {
            "window_visible": window.isVisible(),
            "tracking_thread_active": window._tracking_thread is not None,
            "background_idle": window._background_tasks.idle,
            "close_when_workers_stop": window._close_when_workers_stop,
        },
    }
    (AUDIT_DIR / "runtime-evidence-current.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
