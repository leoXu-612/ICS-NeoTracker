from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import unittest
import wave
from functools import partial
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import shiboken6
from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QWidget,
)

from neo_tracker import media
from neo_tracker.core import TrackerResult
from neo_tracker.coordinates import AnnularCoordinate, PathCoordinate
from neo_tracker.export import markdown_report
from neo_tracker.filters import AlphaBetaFilter
from neo_tracker.media import (
    EndOfMediaError,
    MediaIdentity,
    MediaInfo,
    MediaReader,
    probe_media,
    probe_media_identity,
)
from neo_tracker.motion import PeriodicAngularPrior
from neo_tracker.observations import AnnularRadialFrontObservation, TemplateObservation
from neo_tracker.optimizers import GridSearchOptimizer
from neo_tracker.presets import synthetic_ring_frame, travelling_flame_preset
from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot, TrackingRunRecord
from neo_tracker.roi import AnnularROI
from neo_tracker.ui.analysis_controller import AnalysisController
from neo_tracker.ui.main_window import NeoTrackerWindow, PreviewDecodeJob
from neo_tracker.ui.isolated_media import (
    IsolatedMediaLimits,
    PreviewDecoderSession,
    probe_media_isolated,
)
from neo_tracker.ui.playback_controller import PlaybackClock
from neo_tracker.ui.preview_canvas import PreviewCanvas, _resize_response_bilinear, _response_overlay_rgba
from neo_tracker.ui.preview_decode_worker import PreviewDecodeRequest, PreviewDecodeResult
from neo_tracker.ui.review_response import ReviewResponseService
from neo_tracker.ui.tracking_worker import TRACKING_SOURCE_CHANGED_PREFIX, TrackingProgress


def red_dot_frame(shape: tuple[int, int, int], x: float, y: float, radius: float = 3.0) -> np.ndarray:
    h, w = shape[:2]
    yy, xx = np.indices((h, w))
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius**2
    frame = np.zeros(shape, dtype=np.uint8)
    frame[mask, 0] = 255
    return frame


def vertical_edge_frame(shape: tuple[int, int, int], x: int) -> np.ndarray:
    frame = np.zeros(shape, dtype=np.uint8)
    frame[:, max(0, int(x)) :, :] = 255
    return frame


def write_silent_wav(path: Path, sample_rate: int = 8000, samples: int = 400, channels: int = 2) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * channels * samples)


class FakeReader:
    def __init__(self, frames: list[np.ndarray], fps: float = 10.0) -> None:
        self.frames = frames
        first = frames[0]
        self.info = MediaInfo(
            fps=fps,
            frame_count=len(frames),
            width=first.shape[1],
            height=first.shape[0],
            duration_s=len(frames) / fps,
            available=True,
        )

    def read_frame(self, frame_index: int) -> np.ndarray:
        return self.frames[int(frame_index)]

    def close(self) -> None:
        pass


def bind_verified_fake_media(
    test_case: unittest.TestCase,
    window: NeoTrackerWindow,
    reader: FakeReader,
    filename: str,
) -> Path:
    """Give rerun fixtures a real, stable identity without exercising video decoding."""

    source_directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(source_directory.cleanup)
    source_path = Path(source_directory.name) / filename
    source_path.write_bytes(f"stable test source: {filename}".encode("utf-8"))
    source_identity = probe_media_identity(source_path)
    test_case.assertIsNotNone(source_identity)
    reader.info = MediaInfo(**{**reader.info.__dict__, "source_identity": source_identity})
    window.current_task.media_path = str(source_path)
    window.current_task.media_info = reader.info
    window.current_task.saved_media_info = reader.info
    return source_path


class SlowReader(FakeReader):
    def read_frame(self, frame_index: int) -> np.ndarray:
        time.sleep(0.01)
        return super().read_frame(frame_index)


def blocking_preview_session_process(_connection, *_args) -> None:
    while True:
        time.sleep(1.0)


class EarlyEOFReader(FakeReader):
    def __init__(self, frames: list[np.ndarray], declared_frame_count: int, fps: float = 10.0) -> None:
        super().__init__(frames, fps=fps)
        self.info = MediaInfo(
            fps=fps,
            frame_count=declared_frame_count,
            width=self.info.width,
            height=self.info.height,
            duration_s=declared_frame_count / fps,
            available=True,
        )

    def read_frame(self, frame_index: int) -> np.ndarray:
        if frame_index >= len(self.frames):
            raise EndOfMediaError(frame_index, "early-eof.mp4")
        return super().read_frame(frame_index)


class FailingReader(FakeReader):
    def __init__(self, frames: list[np.ndarray], fail_at: int = 2, fps: float = 10.0) -> None:
        super().__init__(frames, fps=fps)
        self.fail_at = int(fail_at)

    def read_frame(self, frame_index: int) -> np.ndarray:
        if frame_index >= self.fail_at:
            raise RuntimeError(f"synthetic decoder failure at frame {frame_index}")
        return super().read_frame(frame_index)


def wait_for_tracking(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._tracking_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._tracking_thread is not None:
        raise AssertionError("tracking worker did not finish before timeout")


def wait_for_analysis(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._analysis_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._analysis_thread is not None:
        raise AssertionError("analysis worker did not finish before timeout")


def wait_for_media_probe(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._media_probe_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._media_probe_thread is not None:
        raise AssertionError("media probe worker did not finish before timeout")


def wait_for_project_open(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._project_open_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._project_open_thread is not None:
        raise AssertionError("project open worker did not finish before timeout")


def wait_for_project_save(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._project_save_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._project_save_thread is not None:
        raise AssertionError("project save worker did not finish before timeout")


def wait_for_review_response(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while window._review_response_thread is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if window._review_response_thread is not None:
        raise AssertionError("review response worker did not finish before timeout")


def close_window_safely(window: NeoTrackerWindow, timeout_s: float = 5.0) -> None:
    """Close a window while allowing asynchronous production workers to retire."""

    window._ask_unsaved_changes = (  # type: ignore[method-assign]
        lambda _action: QMessageBox.StandardButton.Discard
    )
    window._discard_unapplied_drafts(show_status=False)
    window._apply_project_state(False)
    window.close()
    deadline = time.monotonic() + timeout_s
    worker_fields = (
        "_tracking_thread",
        "_analysis_thread",
        "_media_probe_thread",
        "_project_open_thread",
        "_project_save_thread",
        "_review_response_thread",
        "_preview_decode_thread",
    )
    while (
        any(getattr(window, field, None) is not None for field in worker_fields)
        and time.monotonic() < deadline
    ):
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if any(getattr(window, field, None) is not None for field in worker_fields):
        raise AssertionError("window workers did not finish before cleanup timeout")
    if window.isVisible():
        window.close()
        QCoreApplication.processEvents()


def result_table_headers(window: NeoTrackerWindow) -> list[str]:
    return [
        str(window.results_model.headerData(index, Qt.Orientation.Horizontal))
        for index in range(window.results_model.columnCount())
    ]


def current_result_row(window: NeoTrackerWindow) -> int:
    return int(window.results_table.currentIndex().row())


class MainWindowStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        # Individual tests exercise the real close confirmation explicitly.
        # Cleanup-owned programmatic close calls must not leave a modal dialog
        # waiting after a test that intentionally leaves dirty editor state.
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, NeoTrackerWindow):
                widget._discard_unapplied_drafts(show_status=False)
                widget._set_project_clean()
        QCoreApplication.processEvents()

    def test_stale_preview_completion_cannot_overwrite_newer_request(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.media_path = "/tmp/latest-preview-wins.mp4"
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=3,
            width=4,
            height=3,
            available=True,
        )
        old_request = PreviewDecodeRequest(id(task), task.media_path, 0, 4, 3)
        new_request = PreviewDecodeRequest(id(task), task.media_path, 2, 4, 3)
        token = window._background_tasks.start("preview-decode")
        self.assertIsNotNone(token)
        session = PreviewDecoderSession(task.media_path, expected_width=4, expected_height=3)
        job = PreviewDecodeJob(token, task, new_request, session)  # type: ignore[arg-type]
        window._preview_decode_job = job

        stale = PreviewDecodeResult(old_request, np.zeros((3, 4, 3), dtype=np.uint8))
        window._preview_decode_completed(stale)
        self.assertIsNone(job.result)

        newest_frame = np.full((3, 4, 3), 77, dtype=np.uint8)
        newest = PreviewDecodeResult(new_request, newest_frame)
        window._preview_decode_completed(newest)
        self.assertIs(job.result, newest)

        window._preview_decode_job = None
        window._background_tasks.finish(token)  # type: ignore[arg-type]
        session.close()

    def test_retired_preview_sessions_close_for_pre_run_late_cancel_and_media_switch(self) -> None:
        class CloseCountingSession:
            def __init__(self) -> None:
                self.close_count = 0

            def close(self) -> None:
                self.close_count += 1

        for mode in ("pre-run-cancel", "late-cancel", "media-switch"):
            with self.subTest(mode=mode):
                window = NeoTrackerWindow()
                task = window.current_task
                task.media_path = "/tmp/retired-preview-session.mp4"
                task.media_info = MediaInfo(
                    fps=30.0,
                    frame_count=1,
                    width=4,
                    height=3,
                    available=True,
                )
                request = PreviewDecodeRequest(id(task), task.media_path, 0, 4, 3)
                token = window._background_tasks.start("preview-decode")
                self.assertIsNotNone(token)
                retired = CloseCountingSession()
                result = (
                    PreviewDecodeResult(request, np.zeros((3, 4, 3), dtype=np.uint8))
                    if mode != "pre-run-cancel"
                    else None
                )
                window._preview_decode_job = PreviewDecodeJob(  # type: ignore[arg-type]
                    token,
                    task,
                    request,
                    retired,
                    cancelled=mode != "media-switch",
                    result=result,
                )
                window._preview_decoder_session = retired  # type: ignore[assignment]
                if mode == "media-switch":
                    task.media_path = "/tmp/replacement-preview-session.mp4"

                window._preview_decode_thread_finished()

                self.assertEqual(retired.close_count, 1)
                window._preview_decoder_session = None
                window._discard_unapplied_drafts(show_status=False)
                window._set_project_clean()
                window.close()

    @unittest.skipUnless(media.has_media_backend(), "OpenCV is required for isolated UI preview")
    def test_isolated_ui_preview_commits_only_latest_exact_frame(self) -> None:
        cv2 = media._load_cv2()
        self.assertIsNotNone(cv2)
        source_directory = tempfile.TemporaryDirectory()
        self.addCleanup(source_directory.cleanup)
        path = Path(source_directory.name) / "latest-preview.avi"
        writer = cv2.VideoWriter(  # type: ignore[union-attr]
            str(path),
            cv2.VideoWriter_fourcc(*"MJPG"),  # type: ignore[union-attr]
            12.0,
            (48, 32),
        )
        if not writer.isOpened():
            self.skipTest("OpenCV MJPG writer is unavailable")
        try:
            for index in range(3):
                frame = np.zeros((32, 48, 3), dtype=np.uint8)
                frame[:, :, 0] = 25 + index * 50
                frame[5:15, 8 + index : 28 + index, 1] = 220
                writer.write(frame)
        finally:
            writer.release()

        info = probe_media_isolated(str(path))
        self.assertTrue(info.available)
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.media_path = str(path)
        task.media_info = info
        task.saved_media_info = info

        task.preview_frame_index = 0
        window._render_preview()
        window._preview_frame_changed(1)
        window._preview_frame_changed(2)

        deadline = time.monotonic() + 8.0
        while (
            window._preview_decode_thread is not None
            or window._pending_preview_decode is not None
        ) and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.002)
        QCoreApplication.processEvents()

        self.assertIsNone(window._preview_decode_thread)
        self.assertIsNone(window._pending_preview_decode)
        self.assertIsNotNone(window._preview_decode_cache)
        self.assertEqual(window._preview_decode_cache.request.frame_index, 2)
        with MediaReader(str(path)) as reader:
            expected_frame_2 = reader.read_frame_for_display(2)
            expected_frame_1 = reader.read_frame_for_display(1)
        self.assertTrue(np.array_equal(window._preview_decode_cache.bgr_frame, expected_frame_2))
        self.assertTrue(window.preview_label.has_frame())

        session = window._preview_decoder_session
        self.assertIsNotNone(session)
        session_starts = session.start_count
        window._preview_frame_changed(1)
        deadline = time.monotonic() + 5.0
        while window._preview_decode_thread is not None and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.002)
        QCoreApplication.processEvents()
        self.assertIs(window._preview_decoder_session, session)
        self.assertEqual(session.start_count, session_starts)
        self.assertEqual(window._preview_decode_cache.request.frame_index, 1)
        self.assertTrue(np.array_equal(window._preview_decode_cache.bgr_frame, expected_frame_1))

    def test_closing_window_terminates_active_preview_session_and_qthread(self) -> None:
        source_directory = tempfile.TemporaryDirectory()
        self.addCleanup(source_directory.cleanup)
        source_path = Path(source_directory.name) / "blocking-preview.mp4"
        source_path.write_bytes(b"blocking preview source")
        limits = IsolatedMediaLimits(
            preview_timeout_s=5.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        window = NeoTrackerWindow()
        task = window.current_task
        task.media_path = str(source_path)
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=1,
            width=4,
            height=3,
            available=True,
        )
        session = PreviewDecoderSession(
            str(source_path),
            expected_width=4,
            expected_height=3,
            limits=limits,
            process_target=blocking_preview_session_process,
        )
        window._preview_decoder_session = session
        window._set_project_clean()
        window.show()
        window._render_preview()

        startup_deadline = time.monotonic() + 2.0
        while not session.is_alive and time.monotonic() < startup_deadline:
            QCoreApplication.processEvents()
            time.sleep(0.002)
        self.assertTrue(session.is_alive)
        self.assertIsNotNone(window._preview_decode_thread)

        started = time.monotonic()
        window.close()
        close_deadline = started + 1.5
        while (
            (window.isVisible() or window._preview_decode_thread is not None)
            and time.monotonic() < close_deadline
        ):
            QCoreApplication.processEvents()
            time.sleep(0.002)

        self.assertLess(time.monotonic() - started, 1.5)
        self.assertFalse(window.isVisible())
        self.assertIsNone(window._preview_decode_thread)
        self.assertFalse(session.is_alive)
        self.assertTrue(window._background_tasks.idle)

    def test_sidebar_tabs_replace_default_configuration_panel(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.show()
        QCoreApplication.processEvents()
        tabs = window.findChild(QTabWidget, "sidebarTabs")
        self.assertIsNotNone(tabs)
        self.assertEqual(
            [tabs.tabText(i) for i in range(tabs.count())],
            ["Media", "Tracking", "Review", "Signal", "Calib", "Flow", "JSON"],
        )
        self.assertIsNotNone(window.findChild(QFrame, "topToolbar"))
        self.assertIsNotNone(window.findChild(QFrame, "transportBar"))
        self.assertIn("video or WAV", window.preview_label.text())
        add_button = window.findChild(QPushButton, "addMediaButton")
        self.assertIsNotNone(add_button)
        self.assertIn("video or WAV", add_button.toolTip())
        run_analysis = window.findChild(QPushButton, "runAnalysisButton")
        self.assertIsNotNone(run_analysis)
        self.assertIn("FFT", run_analysis.toolTip())
        self.assertFalse(run_analysis.isEnabled())
        self.assertEqual(window.analysis_status_label.text(), "No source")
        self.assertEqual(window.review_history_tabs.count(), 2)
        self.assertEqual(
            [window.review_history_tabs.tabText(i) for i in range(2)],
            ["Runs (0)", "Edits (0)"],
        )
        self.assertIn("selected video tracking", window.run_tracking_button.toolTip())
        visible_labels = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
        self.assertIn("TASKS", visible_labels)
        self.assertIn("Media backend", visible_labels)
        self.assertIn(window.tracking_backend_label.text(), {"OpenCV components", "NumPy components"})
        self.assertIn(window.tracking_backend_label.property("backendState"), {"accelerated", "fallback"})
        self.assertTrue(window.tracking_backend_label.toolTip())
        self.assertNotIn("Custom observation active", window.preset_description.toPlainText())
        tabs.setCurrentIndex(3)
        QCoreApplication.processEvents()
        visible_labels = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
        self.assertIn("DATA SOURCE", visible_labels)
        self.assertNotIn("Configuration", visible_labels)
        tabs.setCurrentWidget(window.calibration_tab)
        QCoreApplication.processEvents()
        visible_labels = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
        self.assertIn("New curve half-width", visible_labels)
        self.assertNotIn("New curve width", visible_labels)
        advanced = window.findChild(QPlainTextEdit, "advancedConfigView")
        self.assertIsNotNone(advanced)
        self.assertFalse(advanced.isReadOnly())
        self.assertEqual(window.json_status_label.text(), "Synced")
        self.assertEqual(window.validate_json_button.text(), "Validate")
        self.assertEqual(window.apply_json_button.text(), "Apply JSON")
        self.assertEqual(window.reset_json_button.text(), "Reset View")
        self.assertEqual(window.json_status_label.property("jsonState"), "synced")
        self.assertFalse(window.json_validation_message.isVisible())
        self.assertEqual(window.task_list.count(), 1)
        placeholder = window.task_list.item(0)
        self.assertEqual(placeholder.text(), "No media tasks yet")
        self.assertFalse(bool(placeholder.flags() & Qt.ItemFlag.ItemIsEnabled))

    def test_tracking_tab_keeps_every_current_module_visible_at_minimum_window_size(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.resize(window.minimumSizeHint())
        window.show()
        window.sidebar_tabs.setCurrentWidget(window.tracking_tab)
        QCoreApplication.processEvents()

        self.assertGreater(window.module_summary_list.count(), 0)
        last_item = window.module_summary_list.item(window.module_summary_list.count() - 1)
        last_rect = window.module_summary_list.visualItemRect(last_item)
        self.assertTrue(
            window.module_summary_list.viewport().rect().contains(last_rect),
            f"last module row is clipped: viewport={window.module_summary_list.viewport().rect()} row={last_rect}",
        )

    def test_main_window_fits_1024_with_dynamic_statuses_without_toolbar_overlap(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.resize(1024, 768)
        window.show()
        QCoreApplication.processEvents()

        self.assertLessEqual(window.minimumSizeHint().width(), 1024)
        self.assertLessEqual(window.minimumSizeHint().height(), 768)
        self.assertEqual((window.width(), window.height()), (1024, 768))
        self.assertIs(window.focusWidget(), window.add_media_button)
        self.assertEqual(window.task_list.accessibleName(), "Media tasks")

        window.preview_title_label.setText(
            "a-very-long-experiment-video-filename-that-must-not-cover-actions-2026-07-22.mp4"
        )
        window.global_project_dirty_label.show()
        window.global_draft_label.setText("Draft: Media replacement")
        window.global_draft_label.show()
        window.tracking_status_label.setText("Source changed")
        window.tracking_summary_label.setText("Results: 20,000 · source review required")
        QCoreApplication.processEvents()

        self.assertGreater(window.global_project_dirty_label.width(), 0)
        self.assertTrue(window.global_project_dirty_label.displayedText())
        self.assertGreater(window.global_draft_label.width(), 0)
        self.assertTrue(window.global_draft_label.displayedText())
        self.assertEqual(
            window.tracking_summary_label.text(),
            "Results: 20,000 · source review required",
        )
        self.assertTrue(window.tracking_summary_label.displayedText().endswith("…"))
        self.assertEqual((window.width(), window.height()), (1024, 768))
        self.assertLessEqual(window.minimumSizeHint().width(), 1024)

        toolbar = window.findChild(QFrame, "topToolbar")
        self.assertIsNotNone(toolbar)
        self.assertLessEqual(toolbar.height(), 60)
        visible_controls = sorted(
            (
                child
                for child in toolbar.findChildren(
                    QWidget,
                    options=Qt.FindChildOption.FindDirectChildrenOnly,
                )
                if child.isVisible()
            ),
            key=lambda child: child.geometry().left(),
        )
        for left, right in zip(visible_controls, visible_controls[1:]):
            self.assertLess(
                left.geometry().right(),
                right.geometry().left(),
                f"toolbar controls overlap: {left.objectName()} and {right.objectName()}",
            )
        self.assertLessEqual(
            max(control.geometry().right() for control in visible_controls),
            toolbar.contentsRect().right(),
        )

        for index in range(window.sidebar_tabs.count()):
            page = window.sidebar_tabs.widget(index)
            self.assertIsInstance(page, QScrollArea)
            window.sidebar_tabs.setCurrentIndex(index)
            QCoreApplication.processEvents()
            self.assertFalse(page.horizontalScrollBar().isVisible())

    def test_keyboard_focus_has_a_visible_style_and_changes_rendered_pixels(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.resize(1024, 768)
        window.show()
        window.add_media_button.setFocus(Qt.FocusReason.TabFocusReason)
        QCoreApplication.processEvents()
        add_focus = window.grab().toImage().constBits().tobytes()
        window.open_project_button.setFocus(Qt.FocusReason.TabFocusReason)
        QCoreApplication.processEvents()
        open_focus = window.grab().toImage().constBits().tobytes()

        self.assertIn("QPushButton:focus", window.styleSheet())
        self.assertIn("border: 2px solid #0071e3", window.styleSheet())
        self.assertNotEqual(add_focus, open_focus)

    def test_application_font_size_is_inherited_instead_of_forced_to_pixels(self) -> None:
        app = QApplication.instance()
        self.assertIsNotNone(app)
        original_font = app.font()
        large_font = app.font()
        large_font.setPointSize(24)
        app.setFont(large_font)
        try:
            window = NeoTrackerWindow()
            window.show()
            QCoreApplication.processEvents()
            self.assertGreaterEqual(window.add_media_button.font().pointSizeF(), 24.0)
            app_title = window.findChild(QLabel, "appTitle")
            self.assertIsNotNone(app_title)
            self.assertGreater(app_title.font().pointSizeF(), 24.0)
            self.assertNotIn("font-size:", window.styleSheet())
            window.close()
        finally:
            app.setFont(original_font)

    def test_preview_transport_exposes_named_frame_controls_and_label_buddy(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)

        frame_label = window.findChild(QLabel, "previewFrameLabel")
        self.assertIsNotNone(frame_label)
        self.assertIs(frame_label.buddy(), window.preview_frame_spin)
        self.assertEqual(window.previous_frame_button.accessibleName(), "Previous video frame")
        self.assertTrue(window.previous_frame_button.accessibleDescription())
        self.assertEqual(window.next_frame_button.accessibleName(), "Next video frame")
        self.assertTrue(window.next_frame_button.accessibleDescription())
        self.assertEqual(window.preview_frame_spin.accessibleName(), "Preview frame number")
        self.assertTrue(window.preview_frame_spin.accessibleDescription())
        self.assertEqual(window.frame_slider.accessibleName(), "Preview frame timeline")
        self.assertTrue(window.frame_slider.accessibleDescription())
        self.assertEqual(
            window.export_tracking_csv_button.accessibleName(),
            "Export current tracking results to CSV",
        )
        self.assertTrue(window.export_tracking_csv_button.accessibleDescription())
        self.assertEqual(window.export_report_button.accessibleName(), "Create tracking report")
        self.assertTrue(window.export_report_button.accessibleDescription())
        self.assertEqual(window.sidebar_tabs.accessibleName(), "Neo-Tracker workflow sections")
        self.assertEqual(
            window.sidebar_tabs.tabBar().accessibleName(),
            "Neo-Tracker workflow section tabs",
        )
        self.assertEqual(window.review_history_tabs.accessibleName(), "Review history")
        self.assertEqual(window.review_history_tabs.tabBar().accessibleName(), "Review history tabs")

    def test_tracking_tab_reports_cached_template_backend(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.observation_model = TemplateObservation(
            np.arange(12, dtype=np.uint8).reshape(3, 4)
        )

        descriptor = window.registry[window.current_task.pipeline_key]
        window._render_preset(descriptor, window.current_task.pipeline)

        if window.tracking_backend_label.property("backendState") == "accelerated":
            self.assertEqual(window.tracking_backend_label.text(), "OpenCV cached NCC")
        else:
            self.assertEqual(window.tracking_backend_label.text(), "NumPy cached NCC")
        self.assertIn("reused until the template changes", window.tracking_backend_label.toolTip())
        self.assertIn("Custom observation active · Template matching", window.preset_description.toPlainText())
        self.assertIn("base pipeline", window.preset_description.toPlainText())

    def test_roi_summaries_use_geometry_terms_and_pixel_units(self) -> None:
        cases = [
            (
                {"type": "rectangle", "x": 5.0, "y": 7.0, "width": 40.0, "height": 20.0},
                "Rectangle · 40.0 × 20.0 px · origin (5.0, 7.0) px",
            ),
            (
                {"type": "circle", "center": [30.0, 35.0], "radius": 12.0},
                "Circle · 12.0 px radius · center (30.0, 35.0) px",
            ),
            (
                {
                    "type": "annulus",
                    "center": [30.0, 35.0],
                    "inner_radius": 8.0,
                    "outer_radius": 15.0,
                },
                "Annulus · 8.0–15.0 px radii · center (30.0, 35.0) px",
            ),
            (
                {"type": "polygon", "points": [[0.0, 0.0], [4.0, 0.0], [2.0, 3.0]]},
                "Polygon · 3 nodes",
            ),
            (
                {
                    "type": "curve_band",
                    "polyline": [[0.0, 0.0], [4.0, 0.0], [6.0, 3.0]],
                    "half_width": 2.5,
                },
                "Curve band · 3 nodes · 2.5 px half-width",
            ),
        ]
        for config, expected in cases:
            with self.subTest(roi_type=config["type"]):
                self.assertEqual(NeoTrackerWindow._format_roi_summary(config), expected)

    def test_state_unit_summaries_hide_internal_keys(self) -> None:
        cases = [
            ({"s": "px"}, "Path distance · px"),
            ({"theta": "rad", "theta_unwrapped": "rad", "r": "px"}, "Angle · rad"),
            (
                {"x_px": "px", "y_px": "px", "x_world": "px", "y_world": "px"},
                "Position · px",
            ),
            ({"area": "px^2", "x_px": "px", "y_px": "px"}, "Area · px²"),
            ({"target_count": "count"}, "Targets · count"),
            ({}, "Pixel units"),
        ]
        for units, expected in cases:
            with self.subTest(units=units):
                self.assertEqual(NeoTrackerWindow._format_state_unit_summary(units), expected)

    def test_json_tab_validates_applies_and_rejects_pipeline_config(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        config = window.current_task.pipeline.to_config()
        config["observation_model"]["tolerance"] = 0.123
        config["metadata"]["json_applied"] = True
        window.current_task.pipeline.results = [
            TrackerResult(7, 0.25, {"x_px": 10.0}, {"x_px": 10.0}, 0.9, "manual")
        ]
        window.current_task.edit_history = [
            {"type": "manual_correction", "frame_index": 7, "point_px": [10.0, 12.0]}
        ]

        window.advanced_config_view.setPlainText(json.dumps(config, indent=2))
        QCoreApplication.processEvents()
        self.assertEqual(window.json_status_label.text(), "Edited")

        self.assertTrue(window._validate_advanced_config())
        self.assertEqual(window.json_status_label.text(), "JSON valid")
        self.assertTrue(window._apply_advanced_config())

        self.assertAlmostEqual(window.current_task.pipeline.observation_model.tolerance, 0.123)
        self.assertTrue(window.current_task.pipeline.metadata["json_applied"])
        self.assertEqual(window.current_task.pipeline.results, [])
        self.assertEqual(window.current_task.edit_history, [])
        self.assertEqual(window.json_status_label.text(), "JSON applied")

        invalid_config = window.current_task.pipeline.to_config()
        invalid_config["roi"] = {"type": "circle", "center": [10.0, 10.0]}
        window.advanced_config_view.setPlainText(json.dumps(invalid_config, indent=2))
        self.assertFalse(window._validate_advanced_config())
        self.assertEqual(window.json_status_label.text(), "Invalid")
        self.assertEqual(window.json_status_label.property("jsonState"), "invalid")
        self.assertIn("missing or non-numeric geometry", window.json_validation_message.text())
        self.assertFalse(window.json_validation_message.isHidden())
        self.assertEqual(window.json_status_label.toolTip(), window.json_validation_message.text())
        self.assertAlmostEqual(window.current_task.pipeline.observation_model.tolerance, 0.123)

        unsafe_config = window.current_task.pipeline.to_config()
        unsafe_config["roi"]["width"] = 0.0
        window.advanced_config_view.setPlainText(json.dumps(unsafe_config, indent=2))
        self.assertFalse(window._validate_advanced_config())
        self.assertIn("width and height must be positive", window.json_validation_message.text())

        window.advanced_config_view.setPlainText(json.dumps(window.current_task.pipeline.to_config(), indent=2))
        self.assertEqual(window.json_status_label.text(), "Edited")
        self.assertTrue(window.json_validation_message.isHidden())

    def test_switching_preset_updates_workflow_summary(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.show()
        QCoreApplication.processEvents()
        combo = window.preset_combo
        flame_index = combo.findData("travelling_flame")
        self.assertGreaterEqual(flame_index, 0)
        combo.setCurrentIndex(flame_index)
        QCoreApplication.processEvents()
        workflow_text = "\n".join(window.workflow_list.item(i).text() for i in range(window.workflow_list.count()))
        self.assertIn("annulus", workflow_text)
        self.assertIn("annular", workflow_text)
        self.assertIn("periodic_angular", workflow_text)
        self.assertEqual(window.tracking_backend_label.text(), "NumPy cached linear fire")
        self.assertEqual(window.tracking_backend_label.property("backendState"), "optimized")
        self.assertIn("17,280 annular sample points", window.tracking_backend_label.toolTip())
        self.assertIn("Cached linear indices gather RGB channels", window.tracking_backend_label.toolTip())
        self.assertIn("Indices are reused", window.tracking_backend_label.toolTip())
        self.assertEqual(
            window.preset_description.toPlainText(),
            "Annular ROI with cached linear fire sampling, polar unwrap, and angular front state.",
        )
        self.assertTrue(window.tracking_performance_title_label.isHidden())
        self.assertTrue(window.marker_controls_widget.isHidden())
        self.assertFalse(window.color_tolerance_spin.isEnabled())
        self.assertFalse(window.color_max_candidates_spin.isEnabled())
        self.assertFalse(window.color_min_area_spin.isEnabled())

        combo.setCurrentIndex(combo.findData("color_marker"))
        QCoreApplication.processEvents()
        self.assertFalse(window.marker_controls_widget.isHidden())
        self.assertTrue(window.color_tolerance_spin.isEnabled())

        combo.setCurrentIndex(combo.findData("wavefront"))
        QCoreApplication.processEvents()
        self.assertEqual(window.tracking_backend_label.text(), "NumPy ROI gradient")
        self.assertEqual(window.tracking_backend_label.property("backendState"), "optimized")
        self.assertIn("applied ROI", window.tracking_backend_label.toolTip())
        self.assertEqual(
            window.preset_description.toPlainText(),
            "Single-axis edge tracking normalized inside the applied ROI.",
        )

    def test_processing_tab_sources_and_method_controls(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.show()
        QCoreApplication.processEvents()
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=i,
                time_s=i / 30.0,
                state={"x_px": float(i)},
                filtered_state={"x_px": float(i)},
                confidence=1.0,
                status="ok",
            )
            for i in range(16)
        ]
        window._refresh_analysis_sources()
        self.assertGreaterEqual(window.analysis_source_combo.findText("Tracking: x_px (px)"), 0)
        self.assertEqual(window._selected_analysis_series().unit, "px")
        self.assertEqual(window.analysis_sample_rate_spin.value(), 0.0)
        self.assertIn("16 samples", window.analysis_source_detail_label.text())
        self.assertIn("30 Hz", window.analysis_source_detail_label.text())
        self.assertIn(
            "1 tracking signal indexed from 16 results",
            window.analysis_source_detail_label.text(),
        )
        self.assertIn(
            "1 tracking signal indexed from 16 results",
            window.refresh_analysis_sources_button.toolTip(),
        )
        self.assertTrue(window.run_analysis_button.isEnabled())
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        self.assertFalse(window.analysis_export_npz_button.isEnabled())
        window._run_analysis()
        wait_for_analysis(window)
        self.assertTrue(window.analysis_export_csv_button.isEnabled())
        self.assertTrue(window.analysis_export_npz_button.isEnabled())
        self.assertIn("FFT spectrum", window.analysis_result_view.toPlainText())
        self.assertEqual(window.analysis_status_label.text(), "FFT ready")
        self.assertEqual(window.analysis_status_label.property("analysisState"), "complete")
        window.analysis_window_combo.setCurrentText("hamming")
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        self.assertFalse(window.analysis_export_npz_button.isEnabled())
        self.assertIn("Run processing again", window.analysis_result_view.toPlainText())
        self.assertEqual(window.analysis_status_label.text(), "Needs run")

    def test_refresh_sources_button_forces_in_place_metadata_rescan(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 30.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(4)
        ]
        window._refresh_analysis_sources()
        window.current_task.pipeline.results[1].filtered_state["y_px"] = 2.0

        window._refresh_analysis_sources()
        self.assertLess(window.analysis_source_combo.findText("Tracking: y_px (px)"), 0)

        window.refresh_analysis_sources_button.click()
        QCoreApplication.processEvents()

        self.assertGreaterEqual(window.analysis_source_combo.findText("Tracking: y_px (px)"), 0)
        self.assertIn(
            "2 tracking signals indexed from 4 results",
            window.analysis_source_detail_label.text(),
        )
        self.assertIn(
            "Signal sources refreshed · 2 tracking signals indexed from 4 results.",
            window.statusBar().currentMessage(),
        )

    def test_processing_tab_explains_omitted_tracking_samples_and_preparation_stage(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 30.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float("nan") if index == 4 else float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(16)
        ]
        window._refresh_analysis_sources()

        self.assertIn("15 usable samples of 16 results", window.analysis_source_detail_label.text())
        self.assertIn("1 omitted", window.analysis_source_detail_label.text())
        self.assertIn("missing or non-finite", window.analysis_source_detail_label.toolTip())

        original_series_for_source = AnalysisController.series_for_source
        gui_thread_ident = threading.get_ident()
        loader_thread_idents: list[int] = []

        def slow_series_for_source(*args, **kwargs):
            loader_thread_idents.append(threading.get_ident())
            time.sleep(0.05)
            return original_series_for_source(*args, **kwargs)

        with patch.object(AnalysisController, "series_for_source", side_effect=slow_series_for_source):
            window._run_analysis()
            self.assertEqual(window.analysis_status_label.text(), "Preparing 15 samples…")
            self.assertIn("Stage 1 of 2", window.analysis_result_view.toPlainText())
            self.assertFalse(window.correct_point_button.isEnabled())
            self.assertFalse(window.mark_lost_button.isEnabled())
            self.assertFalse(window.rerun_after_button.isEnabled())
            wait_for_analysis(window)
        self.assertEqual(len(loader_thread_idents), 1)
        self.assertNotEqual(loader_thread_idents[0], gui_thread_ident)
        self.assertEqual(window.analysis_status_label.text(), "FFT ready")
        self.assertTrue(window.mark_lost_button.isEnabled())

        window.analysis_freq_min_spin.setValue(20.0)
        window.analysis_freq_max_spin.setValue(5.0)
        window._run_analysis()
        wait_for_analysis(window)
        self.assertEqual(window.analysis_status_label.text(), "Failed")
        self.assertIn("minimum frequency must not exceed maximum", window.analysis_result_view.toPlainText())
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        window.analysis_freq_min_spin.setValue(0.0)
        self.assertEqual(window.analysis_status_label.text(), "Needs run")
        window.analysis_freq_max_spin.setValue(0.0)
        window._run_analysis()
        wait_for_analysis(window)
        self.assertTrue(window.analysis_export_csv_button.isEnabled())

        window.current_task = window._new_task(None, window.default_pipeline_key)
        window._render_task()
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        self.assertEqual(window.analysis_status_label.text(), "No source")
        self.assertIn("Task or signal data changed", window.analysis_result_view.toPlainText())
        window.analysis_method_combo.setCurrentText("STFT")
        QCoreApplication.processEvents()
        self.assertTrue(window.analysis_stft_window_spin.isEnabled())
        self.assertIsNotNone(window.analysis_parameters_form)
        self.assertTrue(window.analysis_parameters_form.isRowVisible(window.analysis_stft_window_spin))
        self.assertTrue(window.analysis_parameters_form.isRowVisible(window.analysis_stft_overlap_spin))
        window.analysis_method_combo.setCurrentText("FFT")
        QCoreApplication.processEvents()
        self.assertFalse(window.analysis_stft_window_spin.isEnabled())
        self.assertFalse(window.analysis_parameters_form.isRowVisible(window.analysis_stft_window_spin))
        self.assertFalse(window.analysis_parameters_form.isRowVisible(window.analysis_stft_overlap_spin))

    def test_analysis_worker_keeps_ui_responsive_and_cancels_stale_results(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 100.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(2048)
        ]
        window._refresh_analysis_sources()
        original_compute = AnalysisController.compute_run

        def slow_compute(source, series, config, owner_token):
            time.sleep(0.08)
            return original_compute(source, series, config, owner_token)

        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        with patch.object(AnalysisController, "compute_run", side_effect=slow_compute):
            window._run_analysis()
            self.assertIsNotNone(window._analysis_thread)
            self.assertEqual(window.run_analysis_button.text(), "Cancel")
            self.assertEqual(window.analysis_status_label.text(), "Preparing 2,048 samples…")
            stage_deadline = time.monotonic() + 0.2
            while window.analysis_status_label.text() != "FFT running…" and time.monotonic() < stage_deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertEqual(window.analysis_status_label.text(), "FFT running…")
            self.assertEqual(window.analysis_status_label.property("analysisState"), "running")
            deadline = time.monotonic() + 0.06
            while len(heartbeats) < 3 and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertGreaterEqual(len(heartbeats), 3)

            window._run_analysis()
            self.assertEqual(window.run_analysis_button.text(), "Cancelling…")
            wait_for_analysis(window)
            self.assertEqual(window.analysis_status_label.text(), "Canceled")
            self.assertFalse(window.analysis_export_csv_button.isEnabled())
            self.assertFalse(window.analysis_controller.has_result)

            window._run_analysis()
            self.assertIsNotNone(window._analysis_thread)
            window.analysis_window_combo.setCurrentText("hamming")
            wait_for_analysis(window)
            self.assertEqual(window.analysis_status_label.text(), "Needs run")
            self.assertIn("settings changed", window.analysis_result_view.toPlainText())
            self.assertFalse(window.analysis_controller.has_result)

    def test_closing_window_cancels_analysis_worker_safely(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        window.show()
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 50.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(256)
        ]
        window._refresh_analysis_sources()
        original_compute = AnalysisController.compute_run

        def slow_compute(source, series, config, owner_token):
            time.sleep(0.06)
            return original_compute(source, series, config, owner_token)

        with patch.object(AnalysisController, "compute_run", side_effect=slow_compute):
            window._run_analysis()
            self.assertIsNotNone(window._analysis_thread)
            window.close()
            self.assertTrue(window._close_when_workers_stop)
            wait_for_analysis(window)
            deadline = time.monotonic() + 1.0
            while window.isVisible() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertFalse(window.isVisible())

    def test_analysis_terminal_signal_stays_locked_until_thread_exits(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 20.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(16)
        ]
        window._refresh_analysis_sources()

        def fail_before_thread_exit(worker) -> None:
            worker.failed.emit("synthetic terminal gap")
            time.sleep(0.08)

        with patch("neo_tracker.ui.main_window.AnalysisWorker.run", new=fail_before_thread_exit):
            window._run_analysis()
            deadline = time.monotonic() + 1.0
            while window.analysis_status_label.text() != "Failed" and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)

            self.assertIsNotNone(window._analysis_thread)
            self.assertEqual(window.run_analysis_button.text(), "Finishing…")
            self.assertFalse(window.run_analysis_button.isEnabled())
            self.assertEqual(window.analysis_status_label.text(), "Failed")
            wait_for_analysis(window)
            self.assertEqual(window.run_analysis_button.text(), "Run processing")
            self.assertTrue(window.run_analysis_button.isEnabled())

    def test_wav_media_renders_as_audio_processing_source_not_video_tracking(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tone.wav"
            write_silent_wav(path, sample_rate=8000, samples=400, channels=2)

            window.current_task = window._new_task(str(path), window.current_task.pipeline_key)
            window._render_task()

            self.assertEqual(window.current_task.media_info.kind, "audio")
            self.assertEqual(window.backend_label.text(), "WAV audio")
            self.assertEqual(window.media_fps_title_label.text(), "Sample rate")
            self.assertEqual(window.media_fps_label.text(), "8000 Hz")
            self.assertEqual(window.media_frames_title_label.text(), "Samples")
            self.assertEqual(window.media_frames_label.text(), "400")
            self.assertEqual(window.media_resolution_title_label.text(), "Channels")
            self.assertEqual(window.media_resolution_label.text(), "2")
            self.assertEqual(window.tracking_status_label.text(), "Audio ready")
            self.assertFalse(window.run_tracking_button.isEnabled())
            self.assertFalse(window.play_button.isEnabled())
            self.assertIn("Audio file loaded", window.preview_label.text())
            self.assertIn(
                "Audio WAV: mono",
                [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
            )
            self.assertIn(
                "Audio WAV: channel 0",
                [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
            )
            self.assertIn(
                "Audio WAV: channel 1",
                [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
            )
            self.assertAlmostEqual(window.analysis_sample_rate_spin.value(), 0.0)
            self.assertIn("400 samples", window.analysis_source_detail_label.text())
            self.assertIn("8000 Hz", window.analysis_source_detail_label.text())
            self.assertIn("3 signal sources available", window.analysis_source_detail_label.text())
            channel_index = window.analysis_source_combo.findText("Audio WAV: channel 1")
            window.analysis_source_combo.setCurrentIndex(channel_index)
            self.assertEqual(window._selected_analysis_series().metadata["channel"], "1")

            original_series_for_source = AnalysisController.series_for_source
            original_compute = AnalysisController.compute_run

            def slow_series_for_source(*args, **kwargs):
                time.sleep(0.03)
                return original_series_for_source(*args, **kwargs)

            def slow_compute(*args, **kwargs):
                time.sleep(0.03)
                return original_compute(*args, **kwargs)

            with (
                patch.object(AnalysisController, "series_for_source", side_effect=slow_series_for_source),
                patch.object(AnalysisController, "compute_run", side_effect=slow_compute),
            ):
                window._run_analysis()
                self.assertEqual(window.analysis_status_label.text(), "Loading WAV…")
                deadline = time.monotonic() + 1.0
                while window.analysis_status_label.text() != "FFT running…" and time.monotonic() < deadline:
                    QCoreApplication.processEvents()
                    time.sleep(0.001)
                self.assertEqual(window.analysis_status_label.text(), "FFT running…")
                wait_for_analysis(window)
            self.assertEqual(window.analysis_status_label.text(), "FFT ready")

    def test_project_load_preserves_offline_audio_metadata_without_enabling_media_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_audio = Path(tmpdir) / "missing.wav"
            project_path = Path(tmpdir) / "offline.ntproj"
            project = NeoTrackerProject(
                name="offline",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path=str(missing_audio),
                        pipeline_key="color_marker",
                        media_info={
                            "kind": "audio",
                            "available": True,
                            "fps": 8000.0,
                            "frame_count": 400,
                            "width": 0,
                            "height": 0,
                            "duration_s": 0.05,
                            "sample_rate_hz": 8000.0,
                            "channels": 2,
                            "error": "",
                        },
                    )
                ],
            )
            project.save(project_path)

            window = NeoTrackerWindow()
            self.addCleanup(close_window_safely, window)
            window._load_project(project_path)

            info = window.current_task.media_info
            self.assertIsNotNone(info)
            self.assertEqual(info.kind, "audio")
            self.assertFalse(info.available)
            self.assertIn("Media file does not exist", info.error)
            self.assertEqual(window.backend_label.text(), "WAV audio")
            self.assertEqual(window.media_fps_title_label.text(), "Sample rate")
            self.assertEqual(window.media_fps_label.text(), "8000 Hz")
            self.assertEqual(window.media_frames_title_label.text(), "Samples")
            self.assertEqual(window.media_frames_label.text(), "400")
            self.assertEqual(window.media_resolution_title_label.text(), "Channels")
            self.assertEqual(window.media_resolution_label.text(), "2")
            self.assertEqual(window.media_duration_label.text(), "0.050 s")
            self.assertEqual(window.tracking_status_label.text(), "Audio unavailable")
            self.assertIn("Saved audio metadata", window.tracking_summary_label.text())
            self.assertFalse(window.run_tracking_button.isEnabled())
            self.assertFalse(window.play_button.isEnabled())
            self.assertIn("Preview unavailable", window.preview_label.text())
            self.assertIn("Media file does not exist", window.preview_label.text())
            self.assertNotIn(
                "Audio WAV: mono",
                [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
            )

    def test_project_load_stages_same_path_metadata_drift_before_preview_or_results_reuse(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        live = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=1280,
            height=720,
            duration_s=4.0,
            available=True,
        )
        window.project_controller.media_probe = lambda _path: live
        window._apply_project(
            NeoTrackerProject(
                name="changed source",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/experiments/source.mp4",
                        pipeline_key=window.default_pipeline_key,
                        media_info={
                            "kind": "video",
                            "available": True,
                            "fps": 20.0,
                            "frame_count": 72,
                            "width": 640,
                            "height": 360,
                            "duration_s": 3.6,
                        },
                        results=[
                            TrackerResult(
                                0,
                                0.0,
                                {"x_px": 12.0},
                                {"x_px": 12.0},
                                0.9,
                                "ok",
                            )
                        ],
                        edit_history=[{"event": "manual_point", "frame_index": 0}],
                        tracking_outcome="complete",
                    )
                ],
            )
        )

        task = window.current_task
        self.assertFalse(task.media_info.available)
        self.assertTrue(task.media_identity_requires_review)
        self.assertEqual(window.media_relink_panel.status_label.text(), "Metadata differs")
        self.assertEqual(window.media_relink_panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertTrue(window.media_relink_panel.apply_button.isEnabled())
        self.assertEqual(window.global_draft_label.text(), "Draft: Media replacement")
        self.assertIn("differs from the project snapshot", task.media_info.error)
        self.assertEqual(window.tracking_status_label.text(), "Source changed")
        self.assertIn("source review required", window.tracking_summary_label.text())
        self.assertFalse(window.export_tracking_csv_button.isEnabled())
        self.assertFalse(window.export_report_button.isEnabled())
        self.assertIn("Media differs from saved project snapshot", window.preview_label.text())

        with patch.object(window, "_render_preview"):
            self.assertTrue(window._apply_media_relink())
        self.assertTrue(task.media_info.available)
        self.assertFalse(task.media_identity_requires_review)
        self.assertEqual(task.pipeline.results, [])
        self.assertEqual(task.edit_history, [])

    def test_project_load_stages_same_metadata_digest_drift(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        saved_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        live_identity = MediaIdentity("full-sha256-v1", "2" * 64, 4096, 4096)
        live = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=live_identity,
        )
        window.project_controller.media_probe = lambda _path: live
        window._apply_project(
            NeoTrackerProject(
                name="changed source content",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/experiments/source.mp4",
                        pipeline_key=window.default_pipeline_key,
                        media_info={
                            "kind": "video",
                            "available": True,
                            "fps": 20.0,
                            "frame_count": 72,
                            "width": 640,
                            "height": 360,
                            "duration_s": 3.6,
                            "source_identity": saved_identity.to_dict(),
                        },
                        results=[
                            TrackerResult(
                                0,
                                0.0,
                                {"x_px": 12.0},
                                {"x_px": 12.0},
                                0.9,
                                "ok",
                            )
                        ],
                        tracking_outcome="complete",
                    )
                ],
            )
        )

        self.assertEqual(window.media_relink_panel.status_label.text(), "Source differs")
        self.assertEqual(window._media_relink_assessment.identity_state, "mismatch")
        self.assertEqual(window._media_relink_assessment.differences, ("Source content digest differs",))
        self.assertEqual(window.tracking_status_label.text(), "Source changed")
        self.assertFalse(window.export_tracking_csv_button.isEnabled())
        self.assertIn("Media differs from saved project snapshot", window.preview_label.text())

    def test_canceling_staged_source_review_keeps_changed_source_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096),
        )
        candidate = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=MediaIdentity("full-sha256-v1", "2" * 64, 4096, 4096),
        )
        task = window.current_task
        task.media_path = "/experiments/changed.mp4"
        task.saved_media_info = saved
        task.media_info = MediaInfo(
            fps=saved.fps,
            frame_count=saved.frame_count,
            width=saved.width,
            height=saved.height,
            duration_s=saved.duration_s,
            available=False,
            error="Media at this path changed after it was loaded.",
            source_identity=saved.source_identity,
        )
        task.media_identity_requires_review = True
        assessment = window.project_controller.assess_media_relink(
            saved,
            candidate,
            has_result_state=True,
        )
        task.pending_media_relink = (candidate, assessment)
        window._stage_media_relink(task.media_path, candidate, assessment=assessment)

        window._cancel_media_relink()

        self.assertTrue(task.media_identity_requires_review)
        self.assertIsNone(task.pending_media_relink)
        self.assertEqual(window.media_relink_panel.status_label.text(), "Source changed")
        self.assertNotEqual(window.media_relink_panel.status_label.text(), "Media missing")
        self.assertEqual(window.media_relink_panel.browse_button.text(), "Review Source…")

    def test_legacy_media_relink_with_results_requires_explicit_clear(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        video_path = Path(__file__).resolve().parents[1] / "artifacts/experiment-videos/red-dot-tracking.mp4"
        task = window.current_task
        task.media_path = "/Volumes/LabSSD/session-04/red-dot-tracking.mp4"
        task.media_info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=False,
            error="Media file does not exist",
        )
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        task.edit_history = [{"event": "manual_point", "frame_index": 0}]
        task.tracking_outcome = "complete"
        window._render_task()

        assessment = window._stage_media_relink(str(video_path))

        self.assertEqual(assessment.state, "unverified")
        self.assertTrue(assessment.clear_results)
        self.assertEqual(window.media_relink_panel.status_label.text(), "Source identity unverified")
        self.assertEqual(window.media_relink_panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertEqual(task.media_path, "/Volumes/LabSSD/session-04/red-dot-tracking.mp4")
        self.assertEqual(len(task.pipeline.results), 1)

        self.assertTrue(window._apply_media_relink())
        self.assertEqual(task.media_path, str(video_path))
        self.assertEqual(task.pipeline.results, [])
        self.assertEqual(task.edit_history, [])
        self.assertEqual(task.tracking_outcome, "")
        self.assertEqual(window.media_relink_panel.status_label.text(), "Relinked")
        self.assertIn("cleared", window.media_relink_panel.detail_label.text())
        self.assertTrue(window.play_button.isEnabled())

    def test_verified_media_relink_preserves_results_and_reader_identity(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        video_path = Path(__file__).resolve().parents[1] / "artifacts/experiment-videos/red-dot-tracking.mp4"
        candidate = probe_media(str(video_path))
        self.assertIsNotNone(candidate.source_identity)
        task = window.current_task
        task.media_path = "/Volumes/LabSSD/session-04/red-dot-tracking.mp4"
        task.media_info = MediaInfo(
            fps=candidate.fps,
            frame_count=candidate.frame_count,
            width=candidate.width,
            height=candidate.height,
            duration_s=candidate.duration_s,
            available=False,
            error="Media file does not exist",
            source_identity=candidate.source_identity,
        )
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        window._render_task()

        assessment = window._stage_media_relink(str(video_path), candidate)

        self.assertEqual(assessment.state, "match")
        self.assertEqual(assessment.identity_state, "full")
        self.assertEqual(window.media_relink_panel.status_label.text(), "Source verified")
        self.assertIn("full digest", window.media_relink_panel.candidate_label.text())
        self.assertTrue(window._apply_media_relink())
        self.assertEqual(len(task.pipeline.results), 1)
        self.assertEqual(task.media_info.source_identity, candidate.source_identity)
        self.assertIsNone(task.media_reader)
        self.assertIsNotNone(window._preview_decoder_session)

    def test_mismatched_media_relink_uses_explicit_destructive_apply(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.media_path = "/Volumes/LabSSD/session-04/red-dot-tracking.mp4"
        task.media_info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=False,
            error="Media file does not exist",
        )
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        task.edit_history = [{"event": "manual_point", "frame_index": 0}]
        task.tracking_outcome = "complete"
        task.run_history = [
            TrackingRunRecord(
                started_at="2026-07-14T00:00:00Z",
                duration_s=1.0,
                mode="full",
                outcome="complete",
                start_frame=0,
                end_frame=0,
                processed_frames=1,
                result_count=1,
                pipeline_config=task.pipeline.to_config(),
            )
        ]
        window._render_task()
        mismatch = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=1280,
            height=720,
            duration_s=4.0,
            available=True,
        )

        assessment = window._stage_media_relink("/new/location/replacement.mp4", mismatch)

        self.assertEqual(assessment.state, "mismatch")
        self.assertTrue(assessment.clear_results)
        self.assertEqual(window.media_relink_panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertIn("Resolution 640×360 → 1280×720", window.media_relink_panel.differences_label.text())
        self.assertEqual(len(task.pipeline.results), 1)

        with patch.object(window, "_render_preview"):
            self.assertTrue(window._apply_media_relink())
        self.assertEqual(task.pipeline.results, [])
        self.assertEqual(task.edit_history, [])
        self.assertEqual(task.tracking_outcome, "")
        self.assertEqual(len(task.run_history), 1)
        self.assertIn("cleared", window.media_relink_panel.detail_label.text())

    def test_mismatched_media_relink_clears_edit_only_result_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.media_path = "/Volumes/LabSSD/session-04/red-dot-tracking.mp4"
        task.media_info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=False,
            error="Media file does not exist",
        )
        task.edit_history = [{"event": "manual_point", "frame_index": 4}]
        task.tracking_outcome = "partial"
        task.tracking_note = "Results were removed outside the editor."
        mismatch = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=1280,
            height=720,
            duration_s=4.0,
            available=True,
        )

        assessment = window._stage_media_relink("/new/location/replacement.mp4", mismatch)

        self.assertTrue(assessment.clear_results)
        self.assertEqual(window.media_relink_panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertIn("manual edits", window.media_relink_panel.detail_label.text())

        with patch.object(window, "_render_preview"):
            self.assertTrue(window._apply_media_relink())
        self.assertEqual(task.edit_history, [])
        self.assertEqual(task.tracking_outcome, "")
        self.assertEqual(task.tracking_note, "")

    def test_preview_canvas_accepts_frames_and_roi_overlay(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.preview_label.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        self.assertTrue(window.preview_label.has_frame())
        window.preview_label.set_roi_rect((10.0, 12.0, 30.0, 24.0))
        self.assertEqual(window.preview_label.roi_rect, (10.0, 12.0, 30.0, 24.0))
        window.preview_label.set_calibration_line(((0.0, 0.0), (100.0, 0.0)))
        self.assertEqual(window.preview_label.calibration_line, ((0.0, 0.0), (100.0, 0.0)))

    def test_preview_canvas_bgr_display_matches_rgb_pixels(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        rgb = np.asarray(
            [
                [[255, 0, 0], [0, 255, 0]],
                [[0, 0, 255], [17, 33, 65]],
            ],
            dtype=np.uint8,
        )

        canvas.set_frame(rgb)
        rgb_image = canvas._frame_pixmap.toImage()  # type: ignore[union-attr]
        canvas.set_bgr_frame(np.ascontiguousarray(rgb[:, :, ::-1]))
        bgr_image = canvas._frame_pixmap.toImage()  # type: ignore[union-attr]

        for y in range(2):
            for x in range(2):
                self.assertEqual(rgb_image.pixelColor(x, y).rgba(), bgr_image.pixelColor(x, y).rgba())

    def test_preview_uses_bgr_display_path_but_keeps_rgb_review_frame(self) -> None:
        class DisplayReader(FakeReader):
            def __init__(self, frames: list[np.ndarray], fps: float = 10.0) -> None:
                super().__init__(frames, fps)
                self.display_reads = 0

            def read_frame_for_display(self, frame_index: int) -> np.ndarray:
                self.display_reads += 1
                return np.ascontiguousarray(self.frames[int(frame_index)][:, :, ::-1])

        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        rgb = np.asarray([[[255, 0, 0], [0, 0, 255]]], dtype=np.uint8)
        reader = DisplayReader([rgb], fps=30.0)
        task = window.current_task
        task.media_path = "/synthetic/display-path.mp4"
        task.media_reader = reader
        task.media_info = reader.info
        empty_overlay = ([], None, None, None, [], None, [])

        with patch.object(window, "_tracking_overlay_for_task", return_value=empty_overlay) as overlay:
            window._render_preview()

        self.assertEqual(reader.display_reads, 1)
        np.testing.assert_array_equal(overlay.call_args.args[1], rgb)
        image = window.preview_label._frame_pixmap.toImage()  # type: ignore[union-attr]
        self.assertEqual(image.pixelColor(0, 0).red(), 255)
        self.assertEqual(image.pixelColor(1, 0).blue(), 255)

    def test_preview_response_overlay_colorization_matches_previous_mapping(self) -> None:
        response = np.asarray(
            [
                [-1.0, -0.5, 0.0, 0.5],
                [1.0, np.nan, np.inf, -np.inf],
            ],
            dtype=np.float32,
        )
        finite = np.isfinite(response)
        safe = np.where(finite, np.asarray(response, dtype=float), 0.0)
        min_value = float(np.min(safe[finite]))
        max_value = float(np.max(safe[finite]))
        normalized = np.clip((safe - min_value) / (max_value - min_value), 0.0, 1.0)
        expected = np.zeros((2, 4, 4), dtype=np.uint8)
        expected[:, :, 0] = np.clip(60 + normalized * 195.0, 0, 255).astype(np.uint8)
        expected[:, :, 1] = np.clip(normalized * 118.0, 0, 255).astype(np.uint8)
        expected[:, :, 2] = np.clip(255.0 - normalized * 210.0, 0, 255).astype(np.uint8)
        expected[:, :, 3] = np.clip(normalized * 135.0, 0, 135).astype(np.uint8)

        actual = _response_overlay_rgba(response, (4, 2))

        self.assertIsNotNone(actual)
        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1.0)
        self.assertIsNone(_response_overlay_rgba(np.ones((2, 4), dtype=np.float32), (4, 2)))
        self.assertIsNone(_response_overlay_rgba(response, (8, 4)))

    def test_preview_response_overlay_bilinear_sampling_matches_pixel_center_reference(self) -> None:
        response = np.arange(32, dtype=np.float32).reshape(4, 8)
        expected = np.empty((2, 4), dtype=np.float32)
        for output_y in range(2):
            source_y = min(max((output_y + 0.5) * 4 / 2 - 0.5, 0.0), 3.0)
            y0 = int(math.floor(source_y))
            y1 = min(y0 + 1, 3)
            y_weight = source_y - y0
            for output_x in range(4):
                source_x = min(max((output_x + 0.5) * 8 / 4 - 0.5, 0.0), 7.0)
                x0 = int(math.floor(source_x))
                x1 = min(x0 + 1, 7)
                x_weight = source_x - x0
                expected[output_y, output_x] = (
                    response[y0, x0] * (1.0 - x_weight) * (1.0 - y_weight)
                    + response[y0, x1] * x_weight * (1.0 - y_weight)
                    + response[y1, x0] * (1.0 - x_weight) * y_weight
                    + response[y1, x1] * x_weight * y_weight
                )

        resized = _resize_response_bilinear(response, (4, 2))
        rgba = _response_overlay_rgba(response, (8, 4), (4, 2))

        np.testing.assert_allclose(resized, expected, rtol=0.0, atol=1e-6)
        self.assertEqual(rgba.shape, (2, 4, 4))
        self.assertEqual(rgba.dtype, np.uint8)

    def test_preview_response_overlay_cache_tracks_physical_display_size(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.resize(100, 100)
        canvas.set_frame(np.zeros((100, 200, 3), dtype=np.uint8))
        response = np.linspace(0.0, 1.0, 100 * 200, dtype=np.float32).reshape(100, 200)
        canvas.set_tracking_overlay([], None, response_map=response)

        first_size = canvas._response_overlay_output_size((200, 100))
        first = canvas._response_overlay_image()

        self.assertIsNotNone(first)
        self.assertEqual((first.width(), first.height()), first_size)
        self.assertLess(first.width(), 200)
        self.assertIs(canvas.response_map, response)

        canvas.resize(160, 100)
        QCoreApplication.processEvents()
        second_size = canvas._response_overlay_output_size((200, 100))
        second = canvas._response_overlay_image()

        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertNotEqual(second_size, first_size)
        self.assertEqual((second.width(), second.height()), second_size)
        self.assertIs(canvas._response_overlay_cache_source, response)

    def test_preview_response_overlay_image_is_reused_and_invalidated(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.set_frame(np.zeros((40, 60, 3), dtype=np.uint8))
        response = np.linspace(0.0, 1.0, 40 * 60, dtype=np.float32).reshape(40, 60)
        canvas.set_tracking_overlay([], None, response_map=response)

        first = canvas._response_overlay_image()
        second = canvas._response_overlay_image()

        self.assertIsNotNone(first)
        self.assertIs(first, second)
        self.assertTrue(canvas._response_overlay_cache_ready)

        canvas.set_tracking_overlay([], None, response_map=response)
        same_object_new_handoff = canvas._response_overlay_image()
        self.assertIsNotNone(same_object_new_handoff)
        self.assertIsNot(first, same_object_new_handoff)

        replacement = response.copy()
        canvas.set_tracking_overlay([], None, response_map=replacement)
        third = canvas._response_overlay_image()
        self.assertIsNotNone(third)
        self.assertIsNot(first, third)
        self.assertIs(canvas._response_overlay_cache_source, replacement)

        canvas.clear_message("No frame")
        self.assertFalse(canvas._response_overlay_cache_ready)
        self.assertIsNone(canvas._response_overlay_cache_image)

    def test_preview_canvas_finishes_polygon_roi_selection(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        emitted: list[dict[str, object]] = []
        canvas.roiSelected.connect(emitted.append)

        self.assertTrue(canvas.begin_polygon_roi_selection())
        canvas._polygon_points = [(5.0, 6.0), (40.0, 8.0), (30.0, 32.0)]
        self.assertTrue(canvas.finish_polygon_roi_selection())

        self.assertEqual(emitted, [{"type": "polygon", "points": [[5.0, 6.0], [40.0, 8.0], [30.0, 32.0]]}])
        self.assertIsNone(canvas._selection_mode)
        self.assertEqual(canvas._polygon_points, [])

    def test_preview_canvas_finishes_curve_band_roi_selection(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        emitted: list[dict[str, object]] = []
        canvas.roiSelected.connect(emitted.append)

        self.assertTrue(canvas.begin_curve_band_roi_selection(17.5))
        canvas._curve_band_points = [(5.0, 6.0), (40.0, 8.0), (60.0, 32.0)]
        self.assertTrue(canvas.finish_curve_band_roi_selection())

        self.assertEqual(
            emitted,
            [
                {
                    "type": "curve_band",
                    "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
                    "half_width": 17.5,
                }
            ],
        )
        self.assertIsNone(canvas._selection_mode)
        self.assertEqual(canvas._curve_band_points, [])

    def test_preview_canvas_selects_and_drags_existing_curve_node(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.resize(600, 400)
        canvas.show()
        canvas.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        canvas.set_editable_roi_config(
            {
                "type": "curve_band",
                "polyline": [[10.0, 20.0], [60.0, 35.0], [100.0, 60.0]],
                "half_width": 8.0,
            }
        )
        QCoreApplication.processEvents()
        selected: list[object] = []
        moved: list[object] = []
        canvas.roiNodeSelected.connect(selected.append)
        canvas.roiNodeMoved.connect(moved.append)
        start = canvas._image_point_to_widget((60.0, 35.0))
        end = canvas._image_point_to_widget((72.0, 44.0))
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)

        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start.toPoint())  # type: ignore[union-attr]
        QTest.mouseMove(canvas, end.toPoint())  # type: ignore[union-attr]
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end.toPoint())  # type: ignore[union-attr]

        self.assertEqual(selected[-1], 1)
        self.assertEqual(canvas.selected_roi_node_index, 1)
        self.assertTrue(moved)
        point = canvas.editable_roi_config["polyline"][1]  # type: ignore[index]
        self.assertAlmostEqual(point[0], 72.0, delta=0.5)
        self.assertAlmostEqual(point[1], 44.0, delta=0.5)

    def test_preview_canvas_nudges_selected_node_with_keyboard(self) -> None:
        canvas = PreviewCanvas()
        self.addCleanup(canvas.close)
        canvas.resize(600, 400)
        canvas.show()
        canvas.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        canvas.set_editable_roi_config(
            {
                "type": "curve_band",
                "polyline": [[10.0, 20.0], [60.0, 35.0], [100.0, 60.0]],
                "half_width": 8.0,
            },
        )
        moved: list[object] = []
        canvas.roiNodeMoved.connect(moved.append)

        handle = canvas._image_point_to_widget((60.0, 35.0))
        self.assertIsNotNone(handle)
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=handle.toPoint())  # type: ignore[union-attr]
        self.assertEqual(canvas.selected_roi_node_index, 1)
        self.assertEqual(canvas.editable_roi_config["polyline"][1], [60.0, 35.0])  # type: ignore[index]
        self.assertEqual(moved, [])
        QTest.keyClick(canvas, Qt.Key.Key_Right)
        QTest.keyClick(canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)

        self.assertEqual(canvas.editable_roi_config["polyline"][1], [61.0, 45.0])  # type: ignore[index]
        self.assertEqual(moved[-1], (1, (61.0, 45.0)))

        canvas.set_editable_roi_config(
            {
                "type": "curve_band",
                "polyline": [[0.0, 0.0], [60.0, 35.0]],
                "half_width": 8.0,
            },
            selected_node_index=0,
        )
        QTest.keyClick(canvas, Qt.Key.Key_Left)
        QTest.keyClick(canvas, Qt.Key.Key_Up)
        self.assertEqual(canvas.editable_roi_config["polyline"][0], [0.0, 0.0])  # type: ignore[index]

    def test_curve_node_canvas_and_table_selection_share_explicit_apply_draft(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("path_motion"))
        frames = [red_dot_frame((80, 120, 3), 60, 35)]
        reader = FakeReader(frames)
        task = window.current_task
        task.media_path = "curve-node-edit.mp4"
        task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        self.assertEqual(window.media_fps_title_label.text(), "Source FPS")
        window.sidebar_tabs.setCurrentWidget(window.calibration_tab)
        QCoreApplication.processEvents()

        editor = window.roi_geometry_editor
        original = editor.current_config()
        self.assertEqual(original["type"], "curve_band")  # type: ignore[index]
        self.assertEqual(window.preview_label.editable_roi_config, original)
        window._set_tracking_busy(True)
        self.assertIsNone(window.preview_label.editable_roi_config)
        window._set_tracking_busy(False)
        self.assertEqual(window.preview_label.editable_roi_config, original)
        editor.select_node(1)
        self.assertEqual(window.preview_label.selected_roi_node_index, 1)
        editor.add_node_button.click()
        self.assertEqual(editor.current_config()["polyline"][2], [335.0, 210.0])  # type: ignore[index]
        self.assertEqual(window.preview_label.editable_roi_config["polyline"][2], [335.0, 210.0])  # type: ignore[index]
        self.assertIsNone(task.roi)
        editor.revert_button.click()
        editor.select_node(1)
        window.preview_label.setFocus()
        QTest.keyClick(window.preview_label, Qt.Key.Key_Right)
        QTest.keyClick(window.preview_label, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.current_config()["polyline"][1], [251.0, 250.0])  # type: ignore[index]
        self.assertIsNone(task.roi)
        editor.revert_button.click()
        window.preview_label.roiNodeSelected.emit(2)
        self.assertEqual(editor.selected_node_index(), 2)

        window.preview_label.roiNodeMoved.emit((1, (78.0, 46.0)))
        draft = editor.current_config()
        self.assertEqual(draft["polyline"][1], [78.0, 46.0])  # type: ignore[index]
        self.assertIsNone(task.roi)
        self.assertNotEqual(task.pipeline.roi.to_config()["polyline"][1], [78.0, 46.0])  # type: ignore[index]
        editor.revert_button.click()
        self.assertEqual(editor.current_config(), original)
        self.assertEqual(window.preview_label.editable_roi_config, original)

        window.preview_label.roiNodeMoved.emit((1, (78.0, 46.0)))
        editor.apply_button.click()
        self.assertEqual(task.roi["polyline"][1], [78.0, 46.0])  # type: ignore[index]
        self.assertEqual(task.pipeline.roi.to_config()["polyline"][1], [78.0, 46.0])  # type: ignore[index]
        self.assertEqual(task.pipeline.coordinate_model.to_config()["polyline"][1], [78.0, 46.0])  # type: ignore[index]

    def test_roi_selection_updates_task_pipeline_and_debug_json(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._roi_selected((10.0, 20.0, 30.0, 40.0))
        self.assertEqual(window.current_task.roi["type"], "rectangle")
        roi_config = window.current_task.pipeline.roi.to_config()
        self.assertEqual(roi_config["type"], "rectangle")
        self.assertEqual(roi_config["x"], 10.0)
        self.assertEqual(roi_config["height"], 40.0)
        self.assertEqual(window.preview_label.roi_rect, (10.0, 20.0, 30.0, 40.0))
        self.assertIn('"x": 10.0', window.advanced_config_view.toPlainText())

    def test_roi_geometry_apply_is_explicit_and_invalidates_stale_result_edits(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._roi_selected((10.0, 20.0, 30.0, 40.0))
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"x_px": 12.0, "y_px": 18.0},
                filtered_state={"x_px": 12.0, "y_px": 18.0},
                confidence=1.0,
                status="manual",
            )
        ]
        window.current_task.edit_history = [
            {"time": "2026-07-13T00:00:00Z", "type": "manual_correction", "frame_index": 0, "details": {}}
        ]
        run_record = TrackingRunRecord(
            started_at="2026-07-13T00:00:00Z",
            duration_s=0.1,
            mode="full",
            outcome="complete",
            start_frame=0,
            end_frame=0,
            processed_frames=1,
            result_count=1,
            pipeline_config=window.current_task.pipeline.to_config(),
        )
        window.current_task.run_history = [run_record]
        window._render_results(window.current_task)
        window._render_edit_history(window.current_task)

        window.roi_geometry_editor.rectangle_x_spin.setValue(14.5)
        QCoreApplication.processEvents()
        self.assertEqual(window.current_task.roi["x"], 10.0)
        self.assertTrue(window.roi_geometry_editor.apply_button.isEnabled())

        window.roi_geometry_editor.apply_button.click()
        QCoreApplication.processEvents()

        self.assertEqual(window.current_task.roi["x"], 14.5)
        self.assertEqual(window.preview_label.roi_rect, (14.5, 20.0, 30.0, 40.0))
        self.assertEqual(window.current_task.pipeline.results, [])
        self.assertEqual(window.current_task.edit_history, [])
        self.assertEqual(window.current_task.run_history, [run_record])
        self.assertEqual(window.edit_history_list.item(0).text(), "No edits yet")
        self.assertEqual(window.roi_geometry_editor.message_label.property("roiGeometryState"), "applied")
        self.assertIn('"x": 14.5', window.advanced_config_view.toPlainText())

    def test_roi_drawing_actions_follow_preview_selection_mode(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.preview_label.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        self.assertFalse(window.finish_roi_drawing_button.isEnabled())
        self.assertFalse(window.cancel_roi_drawing_button.isEnabled())

        window._start_polygon_roi_selection()
        self.assertEqual(window.preview_label.selection_mode(), "roi_polygon")
        self.assertTrue(window.finish_roi_drawing_button.isEnabled())
        self.assertTrue(window.cancel_roi_drawing_button.isEnabled())
        self.assertFalse(window.roi_geometry_editor.pages.isEnabled())
        self.assertEqual(window.roi_geometry_editor.message_label.property("roiGeometryState"), "drawing")
        window._cancel_preview_selection()
        self.assertFalse(window.finish_roi_drawing_button.isEnabled())
        self.assertFalse(window.cancel_roi_drawing_button.isEnabled())
        self.assertTrue(window.roi_geometry_editor.pages.isEnabled())

        window._start_roi_selection()
        self.assertEqual(window.preview_label.selection_mode(), "roi_rectangle")
        self.assertFalse(window.finish_roi_drawing_button.isEnabled())
        self.assertTrue(window.cancel_roi_drawing_button.isEnabled())

    def test_circular_roi_selection_updates_roi_and_polar_mapping(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("circular_motion"))
        window._roi_selected({"type": "circle", "center": [80.0, 90.0], "radius": 42.0})
        roi_config = window.current_task.pipeline.roi.to_config()
        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(window.current_task.roi["type"], "circle")
        self.assertEqual(roi_config["type"], "circle")
        self.assertEqual(roi_config["center"], [80.0, 90.0])
        self.assertEqual(mapping_config["center_px"], [80.0, 90.0])
        self.assertEqual(window.preview_label.roi_config["type"], "circle")

    def test_annular_roi_selection_updates_roi_and_annular_mapping(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("travelling_flame"))
        window._roi_selected(
            {
                "type": "annulus",
                "center": [96.0, 98.0],
                "inner_radius": 30.0,
                "outer_radius": 44.0,
            }
        )
        roi_config = window.current_task.pipeline.roi.to_config()
        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(window.current_task.roi["type"], "annulus")
        self.assertEqual(roi_config["type"], "annulus")
        self.assertEqual(roi_config["inner_radius"], 30.0)
        self.assertEqual(mapping_config["type"], "annular")
        self.assertEqual(mapping_config["center_px"], [96.0, 98.0])
        self.assertEqual(mapping_config["outer_radius"], 44.0)
        self.assertEqual(
            window.roi_status_label.text(),
            "Annulus · 30.0–44.0 px radii · center (96.0, 98.0) px",
        )

    def test_polygon_roi_selection_updates_roi_pipeline_and_project(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        polygon = {"type": "polygon", "points": [[5.0, 5.0], [40.0, 5.0], [38.0, 30.0], [8.0, 28.0]]}
        window._roi_selected(polygon)

        self.assertEqual(window.current_task.roi["type"], "polygon")
        roi_config = window.current_task.pipeline.roi.to_config()
        self.assertEqual(roi_config["type"], "polygon")
        self.assertEqual(roi_config["points"][1], [40.0, 5.0])
        self.assertEqual(window.preview_label.roi_config["type"], "polygon")
        self.assertIsNone(window.preview_label.roi_rect)
        self.assertEqual(window.roi_status_label.text(), "Polygon · 4 nodes")
        self.assertIn('"type": "polygon"', window.advanced_config_view.toPlainText())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "polygon.ntproj"
            window._project_from_window(path).save(path)
            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            self.assertEqual(restored.current_task.roi["type"], "polygon")
            self.assertEqual(restored.current_task.pipeline.roi.to_config()["points"][2], [38.0, 30.0])
            self.assertEqual(restored.roi_status_label.text(), "Polygon · 4 nodes")

    def test_curve_band_roi_selection_updates_path_pipeline_and_project(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("path_motion"))
        curve = {
            "type": "curve_band",
            "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
            "half_width": 17.5,
        }
        window._roi_selected(curve)

        self.assertEqual(window.current_task.roi["type"], "curve_band")
        roi_config = window.current_task.pipeline.roi.to_config()
        self.assertEqual(roi_config["type"], "curve_band")
        self.assertEqual(roi_config["polyline"][1], [40.0, 8.0])
        self.assertAlmostEqual(roi_config["half_width"], 17.5)
        self.assertIsInstance(window.current_task.pipeline.coordinate_model, PathCoordinate)
        self.assertEqual(window.current_task.pipeline.coordinate_model.to_config()["polyline"][2], [60.0, 32.0])
        self.assertEqual(window.preview_label.roi_config["type"], "curve_band")
        self.assertEqual(
            window.roi_status_label.text(),
            "Curve band · 3 nodes · 17.5 px half-width",
        )
        self.assertAlmostEqual(window.curve_half_width_spin.value(), 17.5)

        node_x_spin = window.roi_geometry_editor.node_table.cellWidget(2, 1)
        node_x_spin.setValue(64.0)  # type: ignore[union-attr]
        window.roi_geometry_editor.curve_band_half_width_spin.setValue(22.5)
        self.assertAlmostEqual(window.current_task.roi["half_width"], 17.5)
        window.roi_geometry_editor.apply_button.click()
        QCoreApplication.processEvents()
        self.assertAlmostEqual(window.current_task.roi["half_width"], 22.5)
        self.assertEqual(window.current_task.roi["polyline"][2], [64.0, 32.0])
        self.assertAlmostEqual(window.current_task.pipeline.roi.to_config()["half_width"], 22.5)
        self.assertEqual(window.current_task.pipeline.coordinate_model.to_config()["polyline"][2], [64.0, 32.0])
        self.assertEqual(window.roi_geometry_editor.message_label.property("roiGeometryState"), "applied")
        self.assertIn('"type": "curve_band"', window.advanced_config_view.toPlainText())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "curve.ntproj"
            window._project_from_window(path).save(path)
            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            self.assertEqual(restored.current_task.roi["type"], "curve_band")
            self.assertEqual(restored.current_task.pipeline.roi.to_config()["polyline"][0], [5.0, 6.0])
            self.assertEqual(restored.current_task.pipeline.roi.to_config()["polyline"][2], [64.0, 32.0])
            self.assertAlmostEqual(restored.current_task.pipeline.roi.to_config()["half_width"], 22.5)
            self.assertIsInstance(restored.current_task.pipeline.coordinate_model, PathCoordinate)

    def test_reset_roi_restores_preset_geometry_and_clears_results(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("circular_motion"))
        window._roi_selected({"type": "circle", "center": [80.0, 90.0], "radius": 42.0})
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"theta": 0.1},
                filtered_state={"theta": 0.1},
                confidence=1.0,
                status="ok",
            )
        ]

        window._reset_roi_to_preset()

        self.assertIsNone(window.current_task.roi)
        roi_config = window.current_task.pipeline.roi.to_config()
        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(roi_config["type"], "circle")
        self.assertEqual(roi_config["center"], [320.0, 240.0])
        self.assertEqual(mapping_config["type"], "polar")
        self.assertEqual(mapping_config["center_px"], [320.0, 240.0])
        self.assertEqual(window.current_task.pipeline.results, [])
        self.assertIsNone(window.preview_label.roi_config)
        self.assertEqual(window.roi_status_label.text(), "circle from preset")

    def test_reset_roi_preserves_linear_calibration_for_image_presets(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_calibration_rod((0.0, 0.0), (100.0, 0.0), 50.0, "cm")
        window._roi_selected((10.0, 20.0, 30.0, 40.0))

        window._reset_roi_to_preset()

        self.assertIsNone(window.current_task.roi)
        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(mapping_config["type"], "linear_world")
        self.assertAlmostEqual(mapping_config["unit_per_pixel"], 0.5)
        self.assertEqual(window.current_task.pipeline.roi.to_config()["type"], "rectangle")
        self.assertIn("1 px = 0.5 cm", window.scale_status_label.text())

    def test_playback_controls_are_present_and_safe_without_media(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        self.assertEqual(window.play_button.text(), "Play")
        self.assertFalse(window.play_timer.isActive())
        self.assertFalse(window.play_button.isEnabled())
        self.assertEqual(window.frame_slider.minimum(), 0)
        self.assertEqual(window.frame_slider.maximum(), 0)

    def test_playback_tracks_source_time_and_explains_preview_skips(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((24, 32, 3), index % 32, 12.0) for index in range(40)]
        reader = FakeReader(frames, fps=30.0)
        task = window.current_task
        task.media_path = "/synthetic/playback.mp4"
        task.media_reader = reader
        task.media_info = reader.info
        task.preview_frame_index = 0
        now = [100.0]
        window.playback_clock = PlaybackClock(clock=lambda: now[0])
        window._set_playback_enabled(True)

        window._toggle_playback()
        now[0] += 0.67
        window._advance_playback()

        self.assertEqual(task.preview_frame_index, 20)
        self.assertTrue(window.play_timer.isActive())
        self.assertEqual(window.play_button.text(), "Pause")
        self.assertIn("Source 30 fps", window.playback_status_label.text())
        self.assertIn("30 fps", window.playback_status_label.text())
        self.assertIn("Preview skips 19", window.playback_status_label.text())
        self.assertEqual(window.playback_status_label.property("playbackState"), "catchup")
        self.assertIn("Tracking results and source data are unchanged", window.playback_status_label.toolTip())

        window._preview_frame_selected_by_user(4)

        self.assertEqual(task.preview_frame_index, 4)
        self.assertFalse(window.play_timer.isActive())
        self.assertTrue(window.playback_status_label.isHidden())
        self.assertIn("Skipped 19 preview frames", window.statusBar().currentMessage())

    def test_playback_interval_keeps_one_fps_media_at_source_rate(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.media_info = MediaInfo(
            fps=1.0,
            frame_count=8,
            width=32,
            height=24,
            duration_s=8.0,
            available=True,
        )

        self.assertEqual(window._playback_interval_ms(window.current_task), 1000)

    def test_playback_decode_failure_stops_with_visible_recovery_guidance(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((24, 32, 3), index + 4.0, 12.0) for index in range(6)]
        reader = FailingReader(frames, fail_at=2, fps=30.0)
        task = window.current_task
        task.media_path = "/synthetic/damaged-tail.mp4"
        task.media_reader = reader
        task.media_info = reader.info
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 4.0}, {"x_px": 4.0}, 0.9, "ok")
        ]
        window.preview_label.set_frame(frames[0])
        window._sync_preview_dependent_actions(task)
        self.assertTrue(window.correct_point_button.isEnabled())
        now = [100.0]
        window.playback_clock = PlaybackClock(clock=lambda: now[0])
        window._set_playback_enabled(True)

        window._toggle_playback()
        now[0] += 0.1
        window._advance_playback()

        self.assertEqual(task.preview_frame_index, 3)
        self.assertFalse(window.play_timer.isActive())
        self.assertEqual(window.play_button.text(), "Play")
        self.assertFalse(window.playback_status_label.isHidden())
        self.assertEqual(window.playback_status_label.property("playbackState"), "error")
        self.assertIn("Frame 3 unreadable", window.playback_status_label.text())
        self.assertIn("Tracking results and source data are unchanged", window.playback_status_label.toolTip())
        self.assertIn("Frame 3 could not be decoded", window.preview_label.text())
        self.assertIn("synthetic decoder failure", window.preview_label.toolTip())
        self.assertEqual(window.preview_label.toolTip(), window.preview_label.accessibleDescription())
        self.assertFalse(window.correct_point_button.isEnabled())

        window._preview_frame_selected_by_user(0)

        self.assertEqual(task.preview_frame_index, 0)
        self.assertTrue(window.playback_status_label.isHidden())
        self.assertTrue(window.preview_label.has_frame())
        self.assertEqual(window.preview_label.toolTip(), "")
        self.assertEqual(
            window.preview_label.accessibleDescription(),
            PreviewCanvas.DEFAULT_ACCESSIBLE_DESCRIPTION,
        )
        self.assertTrue(window.correct_point_button.isEnabled())

    def test_preview_navigation_does_not_rebuild_static_tracking_summary(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((24, 32, 3), index + 4.0, 12.0) for index in range(3)]
        reader = FakeReader(frames, fps=30.0)
        task = window.current_task
        task.media_path = "/synthetic/review-playback.mp4"
        task.media_reader = reader
        task.media_info = reader.info
        task.pipeline.results = [
            TrackerResult(
                index,
                index / 30.0,
                {"x_px": float(index + 4), "y_px": 12.0},
                {"x_px": float(index + 4), "y_px": 12.0},
                0.9,
                "ok",
            )
            for index in range(3)
        ]
        window._render_tracking_status(task)

        with patch.object(
            window,
            "_tracking_summary_text",
            wraps=window._tracking_summary_text,
        ) as summary:
            window._preview_frame_changed(2)

        summary.assert_not_called()
        self.assertEqual(task.preview_frame_index, 2)
        self.assertTrue(window.correct_point_button.isEnabled())

    def test_results_without_media_path_keep_truthful_status_and_exports(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"x_px": 12.0},
                filtered_state={"x_px": 12.0},
                confidence=0.9,
                status="ok",
            )
        ]

        window._render_task()

        self.assertEqual(window.tracking_status_label.text(), "Tracked")
        self.assertEqual(window.tracking_status_label.property("trackingOutcome"), "complete")
        self.assertIn("media unavailable", window.tracking_summary_label.text())
        self.assertTrue(window.export_tracking_csv_button.isEnabled())
        self.assertTrue(window.export_report_button.isEnabled())
        self.assertTrue(window.mark_lost_button.isEnabled())
        self.assertTrue(window.jump_to_result_button.isEnabled())
        self.assertFalse(window.correct_point_button.isEnabled())
        self.assertFalse(window.rerun_after_button.isEnabled())

    def test_calibration_rod_updates_scale_and_coordinate_mapping(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_calibration_rod((10.0, 20.0), (110.0, 20.0), 50.0, "cm")
        rod = window.current_task.calibration_rod
        self.assertIsNotNone(rod)
        self.assertAlmostEqual(rod.unit_per_pixel(), 0.5)
        self.assertEqual(window.preview_label.calibration_line, ((10.0, 20.0), (110.0, 20.0)))
        self.assertEqual(window.current_task.pipeline.coordinate_model.to_config()["type"], "linear_world")
        self.assertEqual(window.current_task.pipeline.state_model.units()["x_world"], "cm")
        self.assertIn("1 px = 0.5 cm", window.scale_status_label.text())
        self.assertIn('"unit": "cm"', window.advanced_config_view.toPlainText())
        self.assertIn('"unit_per_pixel": 0.5', window.advanced_config_view.toPlainText())

    def test_calibration_line_uses_editable_draft_before_length_and_unit_apply(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.pipeline.results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"x_px": 10.0, "y_px": 20.0},
                filtered_state={"x_px": 10.0, "y_px": 20.0},
                confidence=0.9,
                status="ok",
            )
        ]

        window._calibration_rod_selected(((10.0, 20.0), (110.0, 20.0)))
        editor = window.calibration_editor
        self.assertEqual(editor.current_config()["real_length"], 10.0)  # type: ignore[index]
        self.assertEqual(editor.current_config()["unit"], "cm")  # type: ignore[index]
        self.assertTrue(editor.apply_button.isEnabled())
        self.assertEqual(window.preview_label.calibration_line, ((10.0, 20.0), (110.0, 20.0)))
        self.assertIsNone(task.calibration_rod.start_px)
        self.assertEqual(task.pipeline.coordinate_model.to_config()["type"], "image")
        self.assertEqual(len(task.pipeline.results), 1)

        editor.length_spin.setValue(25.0)
        editor.unit_combo.setCurrentText("mm")
        editor.apply_button.click()
        self.assertEqual(task.calibration_rod.start_px, (10.0, 20.0))
        self.assertEqual(task.calibration_rod.end_px, (110.0, 20.0))
        self.assertEqual(task.calibration_rod.real_length, 25.0)
        self.assertEqual(task.calibration_rod.unit, "mm")
        self.assertEqual(task.pipeline.results, [])
        self.assertIn("1 px = 0.25 mm", window.scale_status_label.text())
        self.assertEqual(editor.message_label.property("calibrationState"), "applied")

        editor.length_spin.setValue(2.5)
        editor.unit_combo.setCurrentText("cm")
        self.assertIn("Calibration draft changed", window.statusBar().currentMessage())
        editor.apply_button.click()
        self.assertEqual(task.calibration_rod.start_px, (10.0, 20.0))
        self.assertEqual(task.calibration_rod.real_length, 2.5)
        self.assertEqual(task.calibration_rod.unit, "cm")
        self.assertIn("1 px = 0.025 cm", window.scale_status_label.text())

    def test_calibration_rod_updates_annular_coordinate_scale(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("travelling_flame"))

        window._apply_calibration_rod((10.0, 20.0), (110.0, 20.0), 50.0, "cm")

        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(mapping_config["type"], "annular")
        self.assertAlmostEqual(mapping_config["unit_per_pixel"], 0.5)
        self.assertEqual(mapping_config["unit"], "cm")
        self.assertIn('"unit": "cm"', window.advanced_config_view.toPlainText())

    def test_calibration_rod_updates_and_resets_path_coordinate_scale(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("path_motion"))
        curve = {
            "type": "curve_band",
            "polyline": [[5.0, 6.0], [40.0, 8.0], [60.0, 32.0]],
            "half_width": 17.5,
        }
        window._roi_selected(curve)

        window._apply_calibration_rod((10.0, 20.0), (110.0, 20.0), 50.0, "cm")

        mapping_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(mapping_config["type"], "path")
        self.assertEqual(mapping_config["polyline"][0], [5.0, 6.0])
        self.assertAlmostEqual(mapping_config["unit_per_pixel"], 0.5)
        self.assertEqual(mapping_config["unit"], "cm")
        self.assertEqual(window.current_task.pipeline.state_model.units()["s"], "cm")

        window._reset_calibration()

        reset_config = window.current_task.pipeline.coordinate_model.to_config()
        self.assertEqual(reset_config["polyline"][0], [5.0, 6.0])
        self.assertAlmostEqual(reset_config["unit_per_pixel"], 1.0)
        self.assertEqual(reset_config["unit"], "px")
        self.assertEqual(window.current_task.pipeline.state_model.units()["s"], "px")
        self.assertEqual(window.current_task.roi["type"], "curve_band")

    def test_calibration_rod_updates_wavefront_scalar_state_to_world_units(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("wavefront"))
        frames = [vertical_edge_frame((40, 70, 3), 18 + i * 2) for i in range(3)]
        reader = FakeReader(frames)
        window.current_task.media_path = "wavefront.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        window._apply_calibration_rod((0.0, 0.0), (100.0, 0.0), 50.0, "cm")

        pipeline_config = window.current_task.pipeline.to_config()
        self.assertEqual(pipeline_config["coordinate_model"]["type"], "linear_world")
        self.assertEqual(pipeline_config["state_model"]["key"], "x_world")
        self.assertEqual(pipeline_config["state_model"]["unit"], "cm")
        self.assertEqual(pipeline_config["motion_model"]["keys"], ["x_world"])
        self.assertAlmostEqual(pipeline_config["motion_model"]["max_speed"], 250.0)
        self.assertAlmostEqual(pipeline_config["motion_model"]["margin"], 4.0)
        self.assertEqual(pipeline_config["tracker_filter"]["keys"], ["x_world"])

        window._run_tracking()
        wait_for_tracking(window)
        self.assertEqual(len(window.current_task.run_history), 1)
        run_record = window.current_task.run_history[0]
        self.assertEqual(run_record.mode, "full")
        self.assertEqual(run_record.outcome, "complete")
        self.assertEqual(run_record.start_frame, 0)
        self.assertEqual(run_record.end_frame, len(frames) - 1)
        self.assertEqual(run_record.processed_frames, len(frames))
        self.assertEqual(run_record.result_count, len(frames))
        self.assertIn("#1 Full · Complete", window.run_history_list.item(0).text())
        self.assertEqual(window.review_history_tabs.currentIndex(), 0)

        result = window.current_task.pipeline.results[0]
        self.assertIn("x_world", result.filtered_state)
        self.assertNotIn("x_px", result.filtered_state)
        self.assertIn(
            "Tracking: x_world (cm)",
            [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
        )

        window._reset_calibration()

        reset_config = window.current_task.pipeline.to_config()
        self.assertEqual(reset_config["coordinate_model"]["type"], "image")
        self.assertEqual(reset_config["state_model"]["key"], "x_px")
        self.assertEqual(reset_config["state_model"]["unit"], "px")
        self.assertEqual(reset_config["motion_model"]["keys"], ["x_px"])
        self.assertAlmostEqual(reset_config["motion_model"]["max_speed"], 500.0)
        self.assertAlmostEqual(reset_config["motion_model"]["margin"], 8.0)
        self.assertEqual(reset_config["tracker_filter"]["keys"], ["x_px"])

    def test_reset_calibration_restores_preset_coordinate_and_keeps_roi(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_calibration_rod((10.0, 20.0), (110.0, 20.0), 50.0, "cm")
        window._roi_selected((10.0, 20.0, 30.0, 40.0))
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"x_world": 1.0, "y_world": 2.0},
                filtered_state={"x_world": 1.0, "y_world": 2.0},
                confidence=1.0,
                status="ok",
            )
        ]

        window._reset_calibration()

        rod = window.current_task.calibration_rod
        self.assertIsNotNone(rod)
        self.assertIsNone(rod.start_px)
        self.assertIsNone(window.preview_label.calibration_line)
        self.assertEqual(window.current_task.roi["type"], "rectangle")
        self.assertEqual(window.current_task.pipeline.coordinate_model.to_config()["type"], "image")
        self.assertEqual(window.current_task.pipeline.results, [])
        self.assertEqual(window.rod_status_label.text(), "Not set")
        self.assertFalse(window.reset_calibration_button.isEnabled())
        self.assertEqual(window.scale_status_label.text(), "Position · px")
        self.assertIn('"type": "image"', window.advanced_config_view.toPlainText())
        self.assertNotIn('"linear_world"', window.advanced_config_view.toPlainText())

    def test_marker_color_sampling_updates_observation_controls_and_json(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frame = np.zeros((40, 50, 3), dtype=np.uint8)
        frame[11, 13, :] = [12, 180, 70]
        reader = FakeReader([frame])
        window.current_task.media_path = "synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        window._color_sample_selected((13.2, 10.8))
        observation = window.current_task.pipeline.observation_model
        self.assertEqual(tuple(int(value) for value in observation.sample_rgb), (12, 180, 70))
        self.assertEqual(window.marker_sample_label.text(), "RGB 12, 180, 70")
        self.assertIn('"sample_rgb"', window.advanced_config_view.toPlainText())
        self.assertIn("180.0", window.advanced_config_view.toPlainText())

        window._color_tolerance_changed(0.123)
        self.assertAlmostEqual(observation.tolerance, 0.123)
        self.assertAlmostEqual(window.color_tolerance_spin.value(), 0.123)
        self.assertIn('"tolerance": 0.123', window.advanced_config_view.toPlainText())

        window.color_max_candidates_spin.setValue(6)
        window.color_min_area_spin.setValue(9)
        self.assertEqual(observation.max_candidates, 6)
        self.assertEqual(observation.min_component_area, 9)
        self.assertIn('"max_candidates": 6', window.advanced_config_view.toPlainText())
        self.assertIn('"min_component_area": 9', window.advanced_config_view.toPlainText())

    def test_project_load_restores_sampled_marker_parameters(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        observation = window.current_task.pipeline.observation_model
        observation.sample_rgb = (12.0, 180.0, 70.0)
        observation.tolerance = 0.123
        observation.max_candidates = 6
        observation.min_component_area = 9
        window.current_task.media_path = "synthetic.mp4"

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "marker.ntproj"
            project = window._project_from_window(path)
            project.save(path)

            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            restored_observation = restored.current_task.pipeline.observation_model
            self.assertEqual(tuple(restored_observation.sample_rgb), (12.0, 180.0, 70.0))
            self.assertAlmostEqual(restored_observation.tolerance, 0.123)
            self.assertEqual(restored_observation.max_candidates, 6)
            self.assertEqual(restored_observation.min_component_area, 9)
            self.assertEqual(restored.marker_sample_label.text(), "RGB 12, 180, 70")

    def test_project_load_restores_non_default_pipeline_config(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("travelling_flame"))
        window.current_task.media_path = "flame.mp4"
        pipeline = window.current_task.pipeline
        pipeline.roi = AnnularROI(center=(101.0, 102.0), inner_radius=21.0, outer_radius=37.0)
        pipeline.coordinate_model = AnnularCoordinate(
            center_px=(101.0, 102.0),
            inner_radius=21.0,
            outer_radius=37.0,
            unit_per_pixel=0.2,
            unit="cm",
            direction="cw",
        )
        pipeline.observation_model = AnnularRadialFrontObservation(
            n_angles=144,
            n_radii=12,
            response_kind="intensity",
            min_response=0.31,
            smoothing=2,
        )
        pipeline.motion_model = PeriodicAngularPrior(max_angular_speed=3.5, residual_margin=0.12, direction="increasing")
        pipeline.tracker_filter = AlphaBetaFilter(keys=("theta_unwrapped",), alpha=0.66, beta=0.07)
        pipeline.optimizer = GridSearchOptimizer(samples_per_axis=11, maximize=False)
        pipeline.min_confidence = 0.44

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "flame.ntproj"
            window._project_from_window(path).save(path)

            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            restored_pipeline = restored.current_task.pipeline
            self.assertEqual(restored.current_task.pipeline_key, "travelling_flame")
            self.assertEqual(restored_pipeline.roi.to_config()["center"], [101.0, 102.0])
            self.assertEqual(restored_pipeline.roi.to_config()["inner_radius"], 21.0)
            self.assertEqual(restored_pipeline.coordinate_model.to_config()["unit"], "cm")
            self.assertEqual(restored_pipeline.coordinate_model.to_config()["direction"], "cw")
            self.assertEqual(restored_pipeline.observation_model.to_config()["n_angles"], 144)
            self.assertEqual(restored_pipeline.observation_model.to_config()["response_kind"], "intensity")
            self.assertAlmostEqual(restored_pipeline.motion_model.to_config()["max_angular_speed"], 3.5)
            self.assertAlmostEqual(restored_pipeline.tracker_filter.to_config()["alpha"], 0.66)
            self.assertEqual(restored_pipeline.optimizer.to_config()["samples_per_axis"], 11)
            self.assertFalse(restored_pipeline.optimizer.to_config()["maximize"])
            self.assertAlmostEqual(restored_pipeline.min_confidence, 0.44)
            self.assertIn('"n_angles": 144', restored.advanced_config_view.toPlainText())

    def test_project_snapshot_rejects_pipeline_fields_that_silently_fall_back(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        config = window.current_task.pipeline.to_config()
        config["roi"]["x"] = "not-a-number"
        snapshot = ProjectTaskSnapshot(
            media_path=None,
            pipeline_key=window.current_task.pipeline_key,
            pipeline_config=config,
        )

        with self.assertRaisesRegex(ValueError, r"\$\.roi is invalid"):
            window._task_from_snapshot(snapshot)

    def test_failed_project_load_keeps_existing_tasks_usable_and_signals_unblocked(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        valid_project = NeoTrackerProject(
            name="valid",
            tasks=[
                ProjectTaskSnapshot(media_path=None, pipeline_key=window.default_pipeline_key),
                ProjectTaskSnapshot(media_path=None, pipeline_key=window.default_pipeline_key),
            ],
        )
        window._apply_project(valid_project)
        existing_tasks = list(window.tasks)

        invalid_config = window.current_task.pipeline.to_config()
        invalid_config["roi"]["x"] = "not-a-number"
        invalid_project = NeoTrackerProject(
            name="invalid",
            tasks=[
                ProjectTaskSnapshot(
                    media_path=None,
                    pipeline_key=window.default_pipeline_key,
                    pipeline_config=invalid_config,
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, r"\$\.roi is invalid"):
            window._apply_project(invalid_project)

        self.assertFalse(window.task_list.signalsBlocked())
        self.assertEqual(window.tasks, existing_tasks)
        self.assertIs(window.current_task, existing_tasks[0])
        window.task_list.setCurrentRow(1)
        self.assertIs(window.current_task, existing_tasks[1])

    def test_task_removal_requires_confirmation_and_undo_restores_exact_task(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        project = NeoTrackerProject(
            name="three tasks",
            tasks=[
                ProjectTaskSnapshot(
                    media_path="/offline/red-dot-tracking.mp4",
                    pipeline_key=window.default_pipeline_key,
                ),
                ProjectTaskSnapshot(
                    media_path="/offline/calibration-check.mp4",
                    pipeline_key=window.default_pipeline_key,
                ),
                ProjectTaskSnapshot(media_path="/offline/failed-take.mp4", pipeline_key=window.default_pipeline_key),
            ],
        )
        window._apply_project(project)
        window.task_list.setCurrentRow(1)
        removed_task = window.current_task
        removed_task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        removed_task.edit_history = [{"type": "manual_correction", "frame_index": 0}]
        removed_task.run_history = [
            TrackingRunRecord(
                started_at="2026-07-14T00:00:00Z",
                duration_s=1.0,
                mode="full",
                outcome="complete",
                start_frame=0,
                end_frame=0,
                processed_frames=1,
                result_count=1,
                pipeline_config=removed_task.pipeline.to_config(),
            )
        ]
        window._render_task()

        window.task_actions_panel.remove_button.click()

        self.assertEqual(len(window.tasks), 3)
        self.assertEqual(window.task_actions_panel.message_label.property("taskActionState"), "warning")
        self.assertIn("1 results · 1 edits · 1 runs", window.task_actions_panel.message_label.text())

        window.task_actions_panel.remove_button.click()

        self.assertEqual(len(window.tasks), 2)
        self.assertIs(window.current_task, window.tasks[1])
        self.assertEqual(window.current_task.title(), "failed-take.mp4")
        self.assertIs(window._last_removed_task[0], removed_task)
        self.assertIn("media remains on disk", window.statusBar().currentMessage())
        self.assertFalse(window.task_actions_panel.undo_button.isHidden())

        window.task_actions_panel.undo_button.click()

        self.assertEqual(len(window.tasks), 3)
        self.assertIs(window.tasks[1], removed_task)
        self.assertIs(window.current_task, removed_task)
        self.assertEqual(len(removed_task.pipeline.results), 1)
        self.assertEqual(len(removed_task.edit_history), 1)
        self.assertEqual(len(removed_task.run_history), 1)
        self.assertIsNone(window._last_removed_task)
        self.assertIn("Restored calibration-check.mp4", window.statusBar().currentMessage())

    def test_removing_last_task_saves_an_explicit_empty_project_until_undo(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="one task",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/only-task.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )

        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()

        self.assertEqual(window.tasks, [])
        self.assertTrue(window._explicit_empty_project)
        self.assertEqual(window.task_list.item(0).text(), "No media tasks yet")
        self.assertEqual(window._project_from_window().tasks, [])

        window.task_actions_panel.undo_button.click()

        self.assertEqual(len(window.tasks), 1)
        self.assertFalse(window._explicit_empty_project)
        self.assertEqual(window.current_task.title(), "only-task.mp4")
        self.assertEqual(len(window._project_from_window().tasks), 1)

    def test_loading_another_project_discards_task_removal_undo(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="first",
                tasks=[ProjectTaskSnapshot(media_path="/offline/first.mp4", pipeline_key=window.default_pipeline_key)],
            )
        )
        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()
        self.assertIsNotNone(window._last_removed_task)

        window._apply_project(
            NeoTrackerProject(
                name="second",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/second.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )

        self.assertIsNone(window._last_removed_task)
        self.assertTrue(window.task_actions_panel.undo_button.isHidden())
        self.assertEqual(window.current_task.title(), "second.mp4")

    def test_project_save_state_tracks_content_but_ignores_preview_navigation(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="session-04",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/calibration-check.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )
        window.project_path = Path("/experiments/session-04.ntproj")
        window.current_task.media_info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=False,
            error="Media file does not exist",
        )
        window._set_project_clean()

        window._preview_frame_changed(12)
        window._refresh_project_state()

        self.assertFalse(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Saved")
        self.assertTrue(window.global_project_dirty_label.isHidden())

        window.current_task.edit_history.append({"type": "manual_correction", "frame_index": 12})
        window._refresh_project_state()

        self.assertTrue(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Unsaved changes")
        self.assertEqual(window.save_project_button.text(), "Save Changes")
        self.assertEqual(window.save_project_button.property("projectDirty"), True)
        self.assertFalse(window.global_project_dirty_label.isHidden())

    def test_project_state_cache_skips_repeated_clean_and_task_render_fingerprints(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/first.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/second.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )

        with patch.object(
            window,
            "_project_content_fingerprint",
            wraps=window._project_content_fingerprint,
        ) as fingerprint:
            window._set_project_clean()
            self.assertEqual(fingerprint.call_count, 1)

            window.task_list.setCurrentRow(1)
            QCoreApplication.processEvents()
            self.assertEqual(fingerprint.call_count, 1)

            window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
            window._refresh_project_state()
            self.assertEqual(fingerprint.call_count, 2)
            self.assertTrue(window._project_dirty)

            window.current_task.edit_history.pop()
            window._refresh_project_state()
            self.assertEqual(fingerprint.call_count, 3)
            self.assertFalse(window._project_dirty)

    def test_known_project_mutation_marks_dirty_and_revision_without_fingerprinting(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._set_project_clean()
        previous_revision = window._project_content_revision

        with patch.object(
            window,
            "_project_content_fingerprint",
            side_effect=AssertionError("known mutations must stay O(1)"),
        ):
            window._mark_project_changed()

        self.assertTrue(window._project_dirty)
        self.assertIsNone(window._current_project_fingerprint)
        self.assertEqual(window._project_content_revision, previous_revision + 1)

    def test_100k_review_edits_use_incremental_ui_and_avoid_fingerprinting(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        shared_debug = {"filter": {"velocity": {"v_x_px": 1.0, "v_y_px": 0.5}}}
        results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 30.0,
                state={"x_px": float(index), "y_px": 20.0},
                filtered_state={"x_px": float(index), "y_px": 20.0},
                confidence=0.9,
                status="ok",
                debug=shared_debug,
            )
            for index in range(100_000)
        ]
        window.current_task.pipeline.results = results
        window.current_task.preview_frame_index = 50_000
        window._render_results(window.current_task, prefer_preview_frame=True)
        window._refresh_analysis_sources()
        window._saved_project_fingerprint = "baseline"
        window._current_project_fingerprint = "baseline"
        window._apply_project_state(False)
        window.results_table.selectRow(50_000)
        previous_revision = window._project_content_revision

        with patch.object(
            window,
            "_project_content_fingerprint",
            side_effect=AssertionError("review edits must not fingerprint"),
        ), patch.object(
            window,
            "_render_results",
            side_effect=AssertionError("review edits must not reset all results"),
        ):
            started = time.perf_counter()
            window._mark_current_result_lost()
            mark_elapsed = time.perf_counter() - started
            window.results_table.selectRow(50_001)
            started = time.perf_counter()
            window._manual_point_selected((30.0, 35.0))
            correction_elapsed = time.perf_counter() - started

        self.assertLess(mark_elapsed, 0.075)
        self.assertLess(correction_elapsed, 0.075)
        self.assertTrue(window._project_dirty)
        self.assertEqual(window._project_content_revision, previous_revision + 2)
        self.assertEqual(results[50_000].status, "manual_lost")
        self.assertEqual(results[50_001].status, "manual")
        self.assertEqual(window.confidence_plot._values[50_000][2], "manual_lost")
        self.assertEqual(window.confidence_plot._values[50_001][2], "manual")
        self.assertIn("manual_lost 1", window.review_summary_label.text())
        results.clear()

    def test_task_remove_then_undo_returns_to_saved_content_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/first.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/second.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        window.project_path = Path("/experiments/two-tasks.ntproj")
        window._set_project_clean()

        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()

        self.assertTrue(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Unsaved changes")

        window.task_actions_panel.undo_button.click()

        self.assertFalse(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Saved")
        self.assertEqual(window.save_project_button.text(), "Save Project")
        self.assertIn("saved project content is unchanged", window.statusBar().currentMessage())

    def test_task_remove_then_undo_preserves_preexisting_dirty_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/first.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/second.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        window.project_path = Path("/experiments/two-tasks.ntproj")
        window._set_project_clean()
        window._mark_project_changed()

        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.undo_button.click()

        self.assertTrue(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Unsaved changes")
        self.assertEqual(window.save_project_button.text(), "Save Changes")
        self.assertIn("Save Project to persist", window.statusBar().currentMessage())

    def test_task_remove_then_intervening_edit_keeps_undo_dirty(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/first.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/second.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        window.project_path = Path("/experiments/two-tasks.ntproj")
        window._set_project_clean()

        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()
        removal_revision = window._project_content_revision
        window._mark_project_changed()
        window.task_actions_panel.undo_button.click()

        self.assertTrue(window._project_dirty)
        self.assertGreater(window._project_content_revision, removal_revision + 1)
        self.assertEqual(window.project_state_label.text(), "Unsaved changes")

    def test_unsaved_transition_routes_save_discard_and_cancel(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.project_path = Path("/experiments/session-04.ntproj")
        window._set_project_clean()
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
        window._refresh_project_state()
        self.assertTrue(window._project_dirty)

        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Cancel
        )
        self.assertFalse(window._confirm_project_transition("opening another project"))

        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        self.assertTrue(window._confirm_project_transition("opening another project"))

        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Save
        )
        with patch.object(window, "_save_project", return_value=True) as save_project:
            self.assertTrue(window._confirm_project_transition("closing Neo-Tracker"))
        save_project.assert_called_once_with()

    def test_unapplied_editor_work_is_named_and_visible_across_tabs(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.project_path = Path("/experiments/session-04.ntproj")
        window._set_project_clean()

        window.roi_geometry_editor.rectangle_width_spin.setValue(1400.0)
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window.advanced_config_view.insertPlainText(" ")
        window._media_relink_task = window.current_task
        window._media_relink_candidate_path = "/replacement/take.mp4"
        window._refresh_draft_state()

        self.assertEqual(
            window._unapplied_draft_names(),
            ("ROI geometry", "Calibration", "Pipeline JSON", "Media replacement"),
        )
        self.assertFalse(window.global_draft_label.isHidden())
        self.assertEqual(window.global_draft_label.text(), "Drafts: 4")
        for name in window._unapplied_draft_names():
            self.assertIn(name, window.global_draft_label.toolTip())
        self.assertFalse(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Saved")

    def test_validated_json_remains_an_unapplied_draft_until_apply_or_reset(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        config = json.loads(window.advanced_config_view.toPlainText())
        window.advanced_config_view.setPlainText(json.dumps(config, indent=4))

        self.assertTrue(window._validate_advanced_config())
        self.assertEqual(window.json_status_label.property("jsonState"), "valid")
        self.assertTrue(window._advanced_config_dirty)
        self.assertIn("Pipeline JSON", window._unapplied_draft_names())

        window._sync_advanced_config_view()

        self.assertFalse(window._advanced_config_dirty)
        self.assertNotIn("Pipeline JSON", window._unapplied_draft_names())

    def test_draft_transition_keep_editing_preserves_work_and_discard_clears_it(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.project_path = Path("/experiments/session-04.ntproj")
        window._set_project_clean()
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        self.assertTrue(window.calibration_editor.is_dirty())

        captured: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: captured.append((action, names)) or False
        )
        self.assertFalse(window._confirm_project_transition("opening another project"))
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertEqual(captured, [("opening another project", ("Calibration",))])

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        self.assertTrue(window._confirm_project_transition("opening another project"))
        self.assertFalse(window.calibration_editor.is_dirty())
        self.assertEqual(window._unapplied_draft_names(), ())
        self.assertTrue(window.global_draft_label.isHidden())
        self.assertIn("editor work discarded", window.statusBar().currentMessage())

    def test_unsaved_cancel_does_not_prematurely_discard_approved_drafts(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.project_path = Path("/experiments/session-04.ntproj")
        window._set_project_clean()
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
        window._refresh_project_state()
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Cancel
        )

        self.assertFalse(window._confirm_project_transition("closing Neo-Tracker"))
        self.assertTrue(window._project_dirty)
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertIn("Calibration", window._unapplied_draft_names())

    def test_open_cancel_preserves_unapplied_draft_and_current_project(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="current",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/current.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )
        window.project_path = Path("/experiments/current.ntproj")
        window._set_project_clean()
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "target.ntproj"
            NeoTrackerProject(
                name="target",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/target.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            ).save(target_path)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]
                window._open_project()

        self.assertEqual(window.project_path, Path("/experiments/current.ntproj"))
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertIn("Current project and editor work", window.statusBar().currentMessage())

    def test_apply_project_defensively_cancels_active_preview_selection(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.preview_label.set_frame(np.zeros((80, 120, 3), dtype=np.uint8))
        self.assertTrue(window.preview_label.begin_polygon_roi_selection())
        self.assertIn("ROI drawing", window._unapplied_draft_names())
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda _action, _names: self.fail("Direct project application must not open a stale-task prompt")
        )

        window._apply_project(
            NeoTrackerProject(
                name="target",
                tasks=[ProjectTaskSnapshot(media_path="/offline/target.mp4", pipeline_key=window.default_pipeline_key)],
            )
        )

        self.assertIsNone(window.preview_label.selection_mode())
        self.assertNotIn("ROI drawing", window._unapplied_draft_names())

    def test_task_switch_keep_editing_restores_selection_and_preserves_draft(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/take-a.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/take-b.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        original_task = window.current_task
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        captured: list[tuple[str, tuple[str, ...]]] = []
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda action, names: captured.append((action, names)) or False
        )

        window.task_list.setCurrentRow(1)

        self.assertIs(window.current_task, original_task)
        self.assertEqual(window.task_list.currentRow(), 0)
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertEqual(captured, [("switching to take-b.mp4", ("Calibration",))])
        self.assertIn("Task switch canceled", window.statusBar().currentMessage())

    def test_task_switch_discard_changes_task_and_clears_previous_draft(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/take-a.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/take-b.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        original_task = window.current_task
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]

        window.task_list.setCurrentRow(1)

        self.assertIs(window.current_task, window.tasks[1])
        self.assertEqual(window.task_list.currentRow(), 1)
        self.assertIsNot(window.current_task, original_task)
        self.assertEqual(window._unapplied_draft_names(), ())
        self.assertIn("discarded drafts from take-a.mp4", window.statusBar().currentMessage())

    def test_preset_change_keep_editing_then_discard_uses_requested_preset(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        original_key = window.current_task.pipeline_key
        target_key = next(key for key in window.registry if key != original_key)
        target_index = window.preset_combo.findData(target_key)
        window.advanced_config_view.insertPlainText(" ")
        window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]

        window.preset_combo.setCurrentIndex(target_index)

        self.assertEqual(window.current_task.pipeline_key, original_key)
        self.assertEqual(window.preset_combo.currentData(), original_key)
        self.assertTrue(window._advanced_config_dirty)
        self.assertIn("Preset change canceled", window.statusBar().currentMessage())

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window.preset_combo.setCurrentIndex(target_index)

        self.assertEqual(window.current_task.pipeline_key, target_key)
        self.assertEqual(window.preset_combo.currentData(), target_key)
        self.assertFalse(window._advanced_config_dirty)
        self.assertEqual(window._unapplied_draft_names(), ())
        self.assertIn("previous editor drafts were discarded", window.statusBar().currentMessage())

    def test_add_media_to_existing_project_preserves_current_task_draft(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="one task",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/take-a.mp4", pipeline_key=window.default_pipeline_key)
                ],
            )
        )
        original_task = window.current_task
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window._ask_unapplied_drafts = (  # type: ignore[method-assign]
            lambda _action, _names: self.fail("Adding another task must not replace the current editor context")
        )

        with patch.object(QFileDialog, "getOpenFileNames", return_value=(["/offline/take-b.mp4"], "")):
            window._add_media()
        wait_for_media_probe(window)

        self.assertEqual(len(window.tasks), 2)
        self.assertIs(window.current_task, original_task)
        self.assertEqual(window.task_list.currentRow(), 0)
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertIn("Calibration", window._unapplied_draft_names())

    def test_add_media_probe_keeps_ui_responsive_and_avoids_duplicate_probe(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        gui_thread_ident = threading.get_ident()
        probe_thread_idents: list[int] = []
        probe_paths: list[str] = []
        paths = [f"/media/slow-{index}.mp4" for index in range(4)]

        def slow_probe(path: str) -> MediaInfo:
            probe_thread_idents.append(threading.get_ident())
            probe_paths.append(path)
            time.sleep(0.03)
            return MediaInfo(
                fps=30.0,
                frame_count=300,
                width=1920,
                height=1080,
                duration_s=10.0,
                available=True,
            )

        window.project_controller.media_probe = slow_probe
        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        started = time.perf_counter()
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(paths, "")):
            window._add_media()
        return_elapsed = time.perf_counter() - started

        self.assertIsNotNone(window._media_probe_thread)
        self.assertLess(return_elapsed, 0.05)
        self.assertEqual(window.add_media_button.text(), "Cancel Import")
        self.assertFalse(window.media_probe_status_label.isHidden())
        self.assertFalse(window.open_project_button.isEnabled())
        self.assertFalse(window.save_project_button.isEnabled())
        wait_for_media_probe(window)

        self.assertGreater(len(heartbeats), 20)
        self.assertEqual(probe_paths, paths)
        self.assertTrue(probe_thread_idents)
        self.assertTrue(all(ident != gui_thread_ident for ident in probe_thread_idents))
        self.assertEqual(len(window.tasks), len(paths))
        self.assertTrue(all(task.media_info.available for task in window.tasks))
        self.assertEqual(window.add_media_button.text(), "Add media")
        self.assertTrue(window.open_project_button.isEnabled())
        self.assertTrue(window.save_project_button.isEnabled())
        self.assertTrue(window.media_probe_status_label.isHidden())

    def test_cancel_media_import_keeps_selected_batch_out_of_project(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        paths = ["/media/slow-a.mp4", "/media/slow-b.mp4"]

        def slow_probe(_path: str) -> MediaInfo:
            time.sleep(0.05)
            return MediaInfo(available=True)

        window.project_controller.media_probe = slow_probe
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(paths, "")):
            window._add_media()
        self.assertIsNotNone(window._media_probe_thread)

        window._add_media()
        self.assertEqual(window.add_media_button.text(), "Cancelling…")
        self.assertFalse(window.add_media_button.isEnabled())
        wait_for_media_probe(window)

        self.assertEqual(window.tasks, [])
        self.assertEqual(window.add_media_button.text(), "Add media")
        self.assertTrue(window.add_media_button.isEnabled())
        self.assertIn("No selected files were added", window.statusBar().currentMessage())

    def test_closing_window_cancels_media_import_safely(self) -> None:
        window = NeoTrackerWindow()
        window.show()

        def slow_probe(_path: str) -> MediaInfo:
            time.sleep(0.05)
            return MediaInfo(available=True)

        window.project_controller.media_probe = slow_probe
        with patch.object(
            QFileDialog,
            "getOpenFileNames",
            return_value=(["/media/slow-a.mp4", "/media/slow-b.mp4"], ""),
        ):
            window._add_media()
        self.assertIsNotNone(window._media_probe_thread)

        window.close()
        self.assertTrue(window._close_when_workers_stop)
        wait_for_media_probe(window)
        deadline = time.monotonic() + 1.0
        while window.isVisible() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.001)

        self.assertFalse(window.isVisible())
        self.assertEqual(window.tasks, [])

    def test_add_media_from_scratch_keep_editing_then_discard_controls_context_replacement(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        scratch = window.current_task
        window.advanced_config_view.insertPlainText(" ")
        window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]

        with patch.object(QFileDialog, "getOpenFileNames", return_value=(["/offline/take-a.mp4"], "")):
            window._add_media()

        self.assertEqual(window.tasks, [])
        self.assertIs(window.current_task, scratch)
        self.assertTrue(window._advanced_config_dirty)
        self.assertIn("Add media canceled", window.statusBar().currentMessage())

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(["/offline/take-a.mp4"], "")):
            window._add_media()
        wait_for_media_probe(window)

        self.assertEqual(len(window.tasks), 1)
        self.assertIs(window.current_task, window.tasks[0])
        self.assertFalse(window._advanced_config_dirty)
        self.assertEqual(window._unapplied_draft_names(), ())

    def test_task_remove_keep_editing_then_discard_controls_removal(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/take-a.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/take-b.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        removed_task = window.current_task
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]
        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()

        self.assertEqual(len(window.tasks), 2)
        self.assertIs(window.current_task, removed_task)
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertEqual(window.task_actions_panel.remove_button.text(), "Confirm Remove")

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window.task_actions_panel.remove_button.click()

        self.assertEqual(len(window.tasks), 1)
        self.assertIs(window._last_removed_task[0], removed_task)
        self.assertEqual(window._unapplied_draft_names(), ())

    def test_undo_remove_keep_editing_then_discard_controls_task_restore(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="two tasks",
                tasks=[
                    ProjectTaskSnapshot(media_path="/offline/take-a.mp4", pipeline_key=window.default_pipeline_key),
                    ProjectTaskSnapshot(media_path="/offline/take-b.mp4", pipeline_key=window.default_pipeline_key),
                ],
            )
        )
        window.task_list.setCurrentRow(1)
        restored_task = window.current_task
        window.task_actions_panel.remove_button.click()
        window.task_actions_panel.remove_button.click()
        self.assertEqual(len(window.tasks), 1)
        current_task = window.current_task
        window.calibration_editor.set_line((20.0, 30.0), (220.0, 30.0))
        window._ask_unapplied_drafts = lambda _action, _names: False  # type: ignore[method-assign]

        window.task_actions_panel.undo_button.click()

        self.assertEqual(len(window.tasks), 1)
        self.assertIs(window.current_task, current_task)
        self.assertTrue(window.calibration_editor.is_dirty())
        self.assertIsNotNone(window._last_removed_task)

        window._ask_unapplied_drafts = lambda _action, _names: True  # type: ignore[method-assign]
        window.task_actions_panel.undo_button.click()

        self.assertEqual(len(window.tasks), 2)
        self.assertIs(window.current_task, restored_task)
        self.assertEqual(window._unapplied_draft_names(), ())
        self.assertIsNone(window._last_removed_task)

    def test_open_cancel_preserves_dirty_project_and_discard_loads_clean_target(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._apply_project(
            NeoTrackerProject(
                name="current",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/current.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )
        window.project_path = Path("/experiments/current.ntproj")
        window._set_project_clean()
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
        window._refresh_project_state()

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "target.ntproj"
            NeoTrackerProject(
                name="target",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/offline/target.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            ).save(target_path)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._ask_unsaved_changes = (  # type: ignore[method-assign]
                    lambda _action: QMessageBox.StandardButton.Cancel
                )
                window._open_project()

                self.assertEqual(window.project_path, Path("/experiments/current.ntproj"))
                self.assertTrue(window._project_dirty)
                self.assertIn("Open canceled", window.statusBar().currentMessage())

                window._ask_unsaved_changes = (  # type: ignore[method-assign]
                    lambda _action: QMessageBox.StandardButton.Discard
                )
                window._open_project()
                wait_for_project_open(window)

        self.assertEqual(window.project_path, target_path)
        self.assertEqual(window.current_task.title(), "target.mp4")
        self.assertFalse(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Saved")

    def test_open_project_probes_media_in_background_and_keeps_ui_responsive(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        gui_thread_ident = threading.get_ident()
        loader_thread_idents: list[int] = []
        probe_thread_idents: list[int] = []
        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        def slow_probe(path: str) -> MediaInfo:
            probe_thread_idents.append(threading.get_ident())
            time.sleep(0.03)
            index = int(Path(path).stem.rsplit("-", 1)[-1])
            return MediaInfo(
                fps=30.0,
                frame_count=300 + index,
                width=1920,
                height=1080,
                duration_s=(300 + index) / 30.0,
                available=True,
            )

        project_loader = window.project_loader

        def slow_loader(path: str | Path) -> NeoTrackerProject:
            loader_thread_idents.append(threading.get_ident())
            time.sleep(0.03)
            return project_loader(path)

        window.project_loader = slow_loader
        window.project_controller.media_probe = slow_probe
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "batch.ntproj"
            paths = [f"/media/project-{index}.mp4" for index in range(4)]
            NeoTrackerProject(
                name="batch",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path=path,
                        pipeline_key=window.default_pipeline_key,
                    )
                    for path in paths
                ],
            ).save(target_path)

            started = time.perf_counter()
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._open_project()
            return_elapsed = time.perf_counter() - started

            self.assertIsNotNone(window._project_open_thread)
            self.assertLess(return_elapsed, 0.05)
            self.assertEqual(window.open_project_button.text(), "Cancel Open")
            self.assertTrue(window.open_project_button.isEnabled())
            self.assertFalse(window.add_media_button.isEnabled())
            self.assertFalse(window.task_list.isEnabled())
            self.assertFalse(window.task_actions_panel.isEnabled())
            self.assertFalse(window.media_relink_panel.isEnabled())
            self.assertFalse(window.sidebar_tabs.isTabEnabled(1))
            self.assertFalse(window.run_tracking_button.isEnabled())
            self.assertFalse(window.export_report_button.isEnabled())
            self.assertIn("Opening project", window.media_probe_status_label.text())
            self.assertIn("editing is paused", window.media_probe_status_label.accessibleDescription())
            wait_for_project_open(window)

            self.assertEqual(window.project_path, target_path)
            self.assertEqual([task.media_path for task in window.tasks], paths)
            self.assertEqual(
                [task.media_info.frame_count for task in window.tasks],
                [300, 301, 302, 303],
            )
        self.assertGreater(len(heartbeats), 20)
        self.assertTrue(loader_thread_idents)
        self.assertTrue(all(ident != gui_thread_ident for ident in loader_thread_idents))
        self.assertTrue(probe_thread_idents)
        self.assertTrue(all(ident != gui_thread_ident for ident in probe_thread_idents))
        self.assertEqual(window.open_project_button.text(), "Open Project")
        self.assertTrue(window.add_media_button.isEnabled())
        self.assertTrue(window.task_list.isEnabled())
        self.assertTrue(window.task_actions_panel.isEnabled())
        self.assertTrue(window.media_relink_panel.isEnabled())
        self.assertTrue(window.sidebar_tabs.isTabEnabled(1))
        self.assertTrue(window.run_tracking_button.isEnabled())
        self.assertTrue(window.export_report_button.isEnabled())
        self.assertFalse(window._project_dirty)

    def test_open_project_probes_duplicate_media_path_once(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        probed_paths: list[str] = []
        shared_path = "/media/shared-source.mp4"

        def probe(path: str) -> MediaInfo:
            probed_paths.append(path)
            return MediaInfo(
                fps=20.0,
                frame_count=72,
                width=640,
                height=360,
                duration_s=3.6,
                available=True,
            )

        window.project_controller.media_probe = probe
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "shared.ntproj"
            NeoTrackerProject(
                name="shared",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path=shared_path,
                        pipeline_key=window.default_pipeline_key,
                    )
                    for _ in range(3)
                ],
            ).save(target_path)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._open_project()
            wait_for_project_open(window)

            self.assertEqual(window.project_path, target_path)
        self.assertEqual(probed_paths, [shared_path])
        self.assertEqual(len(window.tasks), 3)
        self.assertTrue(all(task.media_info.available for task in window.tasks))

    def test_ui_save_preserves_project_pipeline_library_and_notes(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        library_config = window.registry[window.default_pipeline_key].factory().to_config()
        project = NeoTrackerProject(
            name="source-name",
            pipelines=[library_config],
            tasks=[
                ProjectTaskSnapshot(
                    media_path=None,
                    pipeline_key=window.default_pipeline_key,
                )
            ],
            notes="Keep this experiment note across desktop saves.",
        )
        window._apply_project(project)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "saved-from-ui.ntproj"
            window.project_path = path

            self.assertTrue(window._save_project())
            wait_for_project_save(window)
            saved = NeoTrackerProject.load(path)

        self.assertEqual(saved.name, "saved-from-ui")
        self.assertEqual(saved.pipelines, [library_config])
        self.assertEqual(saved.notes, project.notes)

    def test_project_save_runs_off_gui_thread_and_newer_edits_remain_dirty(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 1})
        window._refresh_project_state()
        save_started = threading.Event()
        release_save = threading.Event()
        saver_thread_idents: list[int] = []
        gui_thread_ident = threading.get_ident()

        def slow_saver(project: NeoTrackerProject, path: Path) -> None:
            saver_thread_idents.append(threading.get_ident())
            save_started.set()
            release_save.wait(timeout=1.0)
            project.save(path)

        window.project_saver = slow_saver
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "background.ntproj"
            window.project_path = path
            started = time.perf_counter()
            self.assertTrue(window._save_project())
            return_elapsed = time.perf_counter() - started
            self.assertLess(return_elapsed, 0.05)

            deadline = time.monotonic() + 0.5
            while not save_started.is_set() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertTrue(save_started.is_set())
            self.assertEqual(window.save_project_button.text(), "Saving…")
            self.assertTrue(window.task_list.isEnabled())

            window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 2})
            window._refresh_project_state()
            release_save.set()
            wait_for_project_save(window)
            saved = NeoTrackerProject.load(path)

        self.assertEqual(len(saved.tasks[0].edit_history), 1)
        self.assertTrue(window._project_dirty)
        self.assertEqual(window.project_state_label.text(), "Unsaved changes")
        self.assertIn("Newer edits still need saving", window.statusBar().currentMessage())
        self.assertEqual(len(saver_thread_idents), 1)
        self.assertNotEqual(saver_thread_idents[0], gui_thread_ident)

    def test_project_save_with_100k_results_keeps_qt_event_loop_responsive(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.pipeline.results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 30.0,
                state={"x_px": float(index)},
                filtered_state={"x_px": float(index)},
                confidence=1.0,
                status="tracked",
            )
            for index in range(100_000)
        ]
        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        with tempfile.TemporaryDirectory() as tmpdir:
            window.project_path = Path(tmpdir) / "large.ntproj"
            # Isolate snapshot/fingerprint responsiveness from disk throughput;
            # the real atomic persistence path is covered by the save/load test.
            window.project_saver = lambda _project, _path: None
            started = time.perf_counter()
            self.assertTrue(window._save_project())
            return_elapsed = time.perf_counter() - started
            self.assertLess(return_elapsed, 0.1)
            wait_for_project_save(window, timeout_s=10.0)

        self.assertGreater(len(heartbeats), 1)
        self.assertFalse(window._project_dirty)

    def test_project_save_failure_preserves_existing_file_and_dirty_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 1})
        window._refresh_project_state()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "existing.ntproj"
            original = b'{"existing": true}\n'
            path.write_bytes(original)
            window.project_path = path
            window.project_saver = lambda _project, _path: (_ for _ in ()).throw(
                OSError("synthetic save failure")
            )
            with patch.object(QMessageBox, "warning") as warning:
                self.assertTrue(window._save_project())
                wait_for_project_save(window)

            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(window._project_dirty)
            warning.assert_called_once()
            self.assertIn("synthetic save failure", warning.call_args.args[2])

    def test_open_and_tracking_wait_for_background_project_save(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        save_started = threading.Event()
        release_save = threading.Event()

        def slow_saver(project: NeoTrackerProject, path: Path) -> None:
            save_started.set()
            release_save.wait(timeout=1.0)
            project.save(path)

        window.project_saver = slow_saver
        with tempfile.TemporaryDirectory() as tmpdir:
            window.project_path = Path(tmpdir) / "coordinated.ntproj"
            self.assertTrue(window._save_project())
            deadline = time.monotonic() + 0.5
            while not save_started.is_set() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertTrue(save_started.is_set())

            with patch.object(QFileDialog, "getOpenFileName") as choose_project:
                window._open_project()
            choose_project.assert_not_called()
            self.assertIn("project saving", window.statusBar().currentMessage())

            with patch.object(window, "_fresh_tracking_media") as fresh_media:
                window._run_tracking()
            fresh_media.assert_not_called()
            self.assertIn("project saving", window.statusBar().currentMessage())

            release_save.set()
            wait_for_project_save(window)

    def test_background_project_open_reuses_prepared_clean_fingerprint(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._confirm_project_transition = lambda _action: True  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "prepared.ntproj"
            NeoTrackerProject(
                name="prepared",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path=None,
                        pipeline_key=window.default_pipeline_key,
                        edit_history=[{"type": "mark_lost", "frame_index": 3}],
                    )
                ],
            ).save(target_path)
            with patch.object(
                window,
                "_project_content_fingerprint",
                wraps=window._project_content_fingerprint,
            ) as fingerprint, patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(target_path), ""),
            ):
                window._open_project()
                wait_for_project_open(window)

                self.assertEqual(fingerprint.call_count, 0)
                self.assertFalse(window._project_dirty)
                window.current_task.edit_history.append(
                    {"type": "mark_lost", "frame_index": 4}
                )
                window._refresh_project_state()
                self.assertEqual(fingerprint.call_count, 1)
                self.assertTrue(window._project_dirty)
                window.current_task.edit_history.pop()
                window._refresh_project_state()
                self.assertEqual(fingerprint.call_count, 2)
                self.assertFalse(window._project_dirty)

    def test_large_replaced_project_releases_result_graph_after_gui_commit(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        result = TrackerResult(
            frame_index=0,
            time_s=0.0,
            state={"x_px": 1.0},
            filtered_state={"x_px": 1.0},
            confidence=1.0,
            status="tracked",
        )
        window._apply_project(
            NeoTrackerProject(
                name="large-old",
                tasks=[
                    ProjectTaskSnapshot(
                        None,
                        window.default_pipeline_key,
                        results=[result] * 10_000,
                    )
                ],
            ),
            set_clean=False,
            defer_heavy_views=True,
        )
        retired = window.current_task

        window._apply_project(NeoTrackerProject(name="replacement"), set_clean=False)

        self.assertIn(retired, window._retired_project_tasks)
        self.assertTrue(window._retired_project_tasks_timer.isActive())
        window._release_retired_project_task()
        self.assertEqual(window._retired_project_tasks, [])

    def test_invalid_background_project_open_keeps_current_project(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        current_path = Path("/experiments/current.ntproj")
        window.project_path = current_path
        window._set_project_clean()
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "invalid.ntproj"
            target_path.write_text("not-json", encoding="utf-8")
            with patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=(str(target_path), ""),
            ), patch.object(QMessageBox, "warning") as warning:
                window._open_project()
                wait_for_project_open(window)

        self.assertEqual(window.project_path, current_path)
        self.assertIs(window.current_task, window.scratch_task)
        warning.assert_called_once()
        self.assertIn("Could not open project", warning.call_args.args[2])

    def test_cancel_project_open_keeps_current_project_unchanged(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        current_path = Path("/experiments/current.ntproj")
        window._apply_project(
            NeoTrackerProject(
                name="current",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/media/current.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            )
        )
        window.project_path = current_path
        window._set_project_clean()

        def slow_probe(_path: str) -> MediaInfo:
            time.sleep(0.05)
            return MediaInfo(available=True)

        window.project_controller.media_probe = slow_probe
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "target.ntproj"
            NeoTrackerProject(
                name="target",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path="/media/target.mp4",
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            ).save(target_path)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._open_project()
            self.assertIsNotNone(window._project_open_thread)

            window._open_project()
            wait_for_project_open(window)

        self.assertEqual(window.project_path, current_path)
        self.assertEqual([task.media_path for task in window.tasks], ["/media/current.mp4"])
        self.assertEqual(window.open_project_button.text(), "Open Project")
        self.assertIn("current project is unchanged", window.statusBar().currentMessage())

    def test_closing_window_cancels_project_open_safely(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.show()
        current_path = Path("/experiments/current.ntproj")
        window.project_path = current_path
        window._set_project_clean()
        loader_started = threading.Event()
        release_loader = threading.Event()

        def slow_loader(path: str | Path) -> NeoTrackerProject:
            loader_started.set()
            release_loader.wait(timeout=0.5)
            return NeoTrackerProject.load(path)

        window.project_loader = slow_loader
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "target.ntproj"
            NeoTrackerProject(
                name="target",
                tasks=[
                    ProjectTaskSnapshot(
                        media_path=None,
                        pipeline_key=window.default_pipeline_key,
                    )
                ],
            ).save(target_path)
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(target_path), "")):
                window._open_project()
            deadline = time.monotonic() + 0.5
            while not loader_started.is_set() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertTrue(loader_started.is_set())

            window.close()
            self.assertTrue(window._close_when_workers_stop)
            self.assertFalse(window.centralWidget().isEnabled())
            release_loader.set()
            wait_for_project_open(window)
            deadline = time.monotonic() + 1.0
            while window.isVisible() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)

        self.assertFalse(window.isVisible())
        self.assertEqual(window.project_path, current_path)

    def test_canceling_scheduled_dirty_close_reopens_window_controls(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window.project_path = Path("/experiments/session-04.ntproj")
        window._set_project_clean()
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
        window._refresh_project_state()
        window._background_tasks.begin_close()
        window._close_requested_by_user = True
        window.centralWidget().setEnabled(False)
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Cancel
        )
        event = QCloseEvent()

        window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertFalse(window._background_tasks.closing)
        self.assertTrue(window.centralWidget().isEnabled())
        self.assertFalse(window._close_requested_by_user)
        self.assertTrue(window._project_dirty)
        self.assertIn("Close canceled", window.statusBar().currentMessage())

    def test_application_level_close_all_windows_cannot_bypass_unsaved_confirmation(self) -> None:
        window = NeoTrackerWindow()
        window.project_path = Path("/experiments/application-quit.ntproj")
        window._set_project_clean()
        window.current_task.edit_history.append({"type": "mark_lost", "frame_index": 3})
        window._refresh_project_state()
        window.show()
        QCoreApplication.processEvents()

        with patch.object(
            window,
            "_ask_unsaved_changes",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as ask:
            QApplication.closeAllWindows()
            QCoreApplication.processEvents()

        self.assertTrue(window.isVisible())
        self.assertTrue(window._project_dirty)
        ask.assert_called_once_with("closing Neo-Tracker")
        window._set_project_clean()
        window.close()

    def test_run_tracking_populates_review_table_and_analysis_sources(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(5)]
        reader = FakeReader(frames)
        window.current_task.media_path = "synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        self.assertEqual(len(window.current_task.pipeline.results), len(frames))
        self.assertEqual(window.results_model.rowCount(), len(frames))
        self.assertEqual(current_result_row(window), len(frames) - 1)
        self.assertIn(f"Frame {len(frames) - 1}", window.review_selection_label.text())
        self.assertEqual(window.review_selection_label.property("reviewTone"), "trusted")
        headers = result_table_headers(window)
        self.assertIn("x_px (px)", headers)
        self.assertIn(
            "Tracking: x_px (px)",
            [window.analysis_source_combo.itemText(i) for i in range(window.analysis_source_combo.count())],
        )
        self.assertTrue(window.preview_label.trajectory_points)
        self.assertEqual(window.preview_label.measurement_points, [])
        self.assertEqual(len(window.confidence_plot._values), len(frames))
        self.assertIsNotNone(window.preview_label.observation_point)
        self.assertTrue(window.preview_label.candidate_points)
        self.assertTrue(any(candidate[3] for candidate in window.preview_label.candidate_points))
        self.assertIsNotNone(window.preview_label.prediction_point)
        window.show_measurement_checkbox.setChecked(True)
        window._render_preview()
        self.assertEqual(len(window.preview_label.measurement_points), len(frames))
        self.assertTrue(
            any(
                abs(measured[0] - filtered[0]) > 1e-6 or abs(measured[1] - filtered[1]) > 1e-6
                for measured, filtered in zip(window.preview_label.measurement_points, window.preview_label.trajectory_points)
            )
        )
        window.show_measurement_checkbox.setChecked(False)
        window.show_candidates_checkbox.setChecked(False)
        window.show_prediction_checkbox.setChecked(False)
        window._render_preview()
        self.assertEqual(window.preview_label.measurement_points, [])
        self.assertEqual(window.preview_label.candidate_points, [])
        self.assertIsNone(window.preview_label.prediction_point)
        window.show_measurement_checkbox.setChecked(True)
        window.show_candidates_checkbox.setChecked(True)
        window.show_prediction_checkbox.setChecked(True)
        window.show_response_checkbox.setChecked(True)
        window._render_preview()
        self.assertIsNotNone(window.preview_label.response_map)
        self.assertEqual(window.preview_label.response_map.shape, frames[0].shape[:2])
        self.assertEqual(window.tracking_status_label.text(), "Tracked")
        self.assertIn("Candidates on frame 4: 1", window.candidate_summary_label.text())
        window._preview_frame_changed(1)
        self.assertEqual(current_result_row(window), 1)
        self.assertIn("Frame 1", window.review_selection_label.text())

    def test_run_tracking_reprobes_and_blocks_same_path_media_replacement(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        saved_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        live_identity = MediaIdentity("full-sha256-v1", "2" * 64, 4096, 4096)
        task = window.current_task
        task.media_path = "/experiments/same-path.mp4"
        task.media_info = MediaInfo(
            fps=30.0,
            frame_count=12,
            width=640,
            height=360,
            duration_s=0.4,
            available=True,
            source_identity=saved_identity,
        )
        live_info = MediaInfo(
            fps=30.0,
            frame_count=9,
            width=640,
            height=360,
            duration_s=0.3,
            available=True,
            source_identity=live_identity,
        )
        probe_calls: list[str] = []
        window.project_controller.media_probe = lambda path: (probe_calls.append(path) or live_info)

        with patch.object(window, "_start_tracking_job") as start_job:
            window._run_tracking()

        start_job.assert_not_called()
        self.assertEqual(probe_calls, [task.media_path])
        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertEqual(task.saved_media_info.source_identity, saved_identity)
        self.assertEqual(task.pending_media_relink[0].source_identity, live_identity)
        self.assertEqual(window._media_relink_assessment.state, "mismatch")
        self.assertIn("Source content digest differs", window.media_relink_panel.differences_label.text())
        self.assertIn("Tracking paused", window.statusBar().currentMessage())
        self.assertIs(window.sidebar_tabs.currentWidget(), window.media_tab)

    def test_rerun_reprobes_and_blocks_same_path_media_replacement(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        saved_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        live_identity = MediaIdentity("full-sha256-v1", "2" * 64, 4096, 4096)
        saved_info = MediaInfo(
            fps=30.0,
            frame_count=4,
            width=640,
            height=360,
            duration_s=4 / 30.0,
            available=True,
            source_identity=saved_identity,
        )
        live_info = MediaInfo(
            fps=30.0,
            frame_count=4,
            width=640,
            height=360,
            duration_s=4 / 30.0,
            available=True,
            source_identity=live_identity,
        )
        task = window.current_task
        task.media_path = "/experiments/rerun-same-path.mp4"
        task.media_info = saved_info
        task.saved_media_info = saved_info
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        probe_calls: list[str] = []
        window.project_controller.media_probe = lambda path: (probe_calls.append(path) or live_info)

        with patch.object(window, "_start_tracking_job") as start_job:
            window._rerun_after_current_result()

        start_job.assert_not_called()
        self.assertEqual(probe_calls, [task.media_path])
        self.assertTrue(task.media_identity_requires_review)
        self.assertEqual(task.saved_media_info.source_identity, saved_identity)
        self.assertEqual(task.pending_media_relink[0].source_identity, live_identity)
        self.assertEqual(window._media_relink_assessment.state, "mismatch")
        self.assertIn("Rerun paused", window.statusBar().currentMessage())
        self.assertIs(window.sidebar_tabs.currentWidget(), window.media_tab)

    def test_rerun_blocks_source_when_saved_digest_cannot_be_reverified(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        saved_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        saved_info = MediaInfo(
            fps=30.0,
            frame_count=4,
            width=640,
            height=360,
            duration_s=4 / 30.0,
            available=True,
            source_identity=saved_identity,
        )
        task = window.current_task
        task.media_path = "/experiments/rerun-unverified.mp4"
        task.media_info = saved_info
        task.saved_media_info = saved_info
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        window.project_controller.media_probe = lambda _path: MediaInfo(
            fps=30.0,
            frame_count=4,
            width=640,
            height=360,
            duration_s=4 / 30.0,
            available=True,
            source_identity=None,
        )

        with patch.object(window, "_start_tracking_job") as start_job:
            window._rerun_after_current_result()

        start_job.assert_not_called()
        self.assertTrue(task.media_identity_requires_review)
        self.assertEqual(window._media_relink_assessment.state, "unverified")
        self.assertEqual(window._media_relink_assessment.identity_state, "unverified")
        self.assertTrue(window._media_relink_assessment.clear_results)
        self.assertEqual(window.media_relink_panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertIn("could not be verified", window.media_relink_panel.detail_label.text())

    def test_rerun_retry_compares_recovered_probe_to_saved_source_baseline(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        source_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        saved_info = MediaInfo(
            fps=30.0,
            frame_count=4,
            width=640,
            height=360,
            duration_s=4 / 30.0,
            available=True,
            source_identity=source_identity,
        )
        task = window.current_task
        task.media_path = "/experiments/retry.mp4"
        task.media_info = saved_info
        task.saved_media_info = saved_info
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        probe_results = iter(
            [
                MediaInfo(available=False, error="Media changed during inspection; retry."),
                saved_info,
            ]
        )
        window.project_controller.media_probe = lambda _path: next(probe_results)
        window._ask_rerun_replacement = lambda *_args, **_kwargs: True  # type: ignore[method-assign]

        with (
            patch("neo_tracker.ui.main_window.QMessageBox.information") as information,
            patch.object(window, "_start_tracking_job") as start_job,
        ):
            window._rerun_after_current_result()
            start_job.assert_not_called()
            self.assertFalse(task.media_info.available)
            self.assertEqual(task.saved_media_info.source_identity, source_identity)

            window._rerun_after_current_result()

        information.assert_called_once()
        start_job.assert_called_once()
        self.assertFalse(task.media_identity_requires_review)
        self.assertTrue(task.media_info.available)
        self.assertEqual(task.media_info.source_identity, source_identity)

    def test_tracking_reader_is_worker_owned_and_not_retained_on_task(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + index * 4, 20) for index in range(3)]

        class ClosingReader(FakeReader):
            def __init__(self, source_frames: list[np.ndarray]) -> None:
                super().__init__(source_frames)
                self.closed = False

            def close(self) -> None:
                self.closed = True

        tracking_reader = ClosingReader(frames)
        source_path = bind_verified_fake_media(self, window, tracking_reader, "worker-owned.mp4")
        original_identity = tracking_reader.info.source_identity
        self.assertIsNotNone(original_identity)
        task = window.current_task
        window.project_controller.media_probe = lambda _path: tracking_reader.info
        window._tracking_reader_for_path = lambda _path: tracking_reader  # type: ignore[method-assign]

        with patch.object(window, "_render_preview"):
            window._run_tracking()
            wait_for_tracking(window)

        self.assertTrue(tracking_reader.closed)
        self.assertIsNone(task.media_reader)
        self.assertEqual(len(task.pipeline.results), len(frames))
        run_record = task.run_history[-1]
        self.assertEqual(run_record.source_path, str(source_path))
        self.assertEqual(run_record.source_identity, original_identity)

        replacement_identity = MediaIdentity("full-sha256-v1", "4" * 64, 4096, 4096)
        replacement_info = MediaInfo(
            fps=tracking_reader.info.fps,
            frame_count=tracking_reader.info.frame_count,
            width=tracking_reader.info.width,
            height=tracking_reader.info.height,
            duration_s=tracking_reader.info.duration_s,
            available=True,
            source_identity=replacement_identity,
        )
        window.project_controller.relink_media(
            task,
            "replacement.mp4",
            replacement_info,
            clear_results=True,
        )
        self.assertEqual(task.media_info.source_identity, replacement_identity)
        self.assertEqual(run_record.source_path, str(source_path))
        self.assertEqual(run_record.source_identity, original_identity)

    def test_large_review_table_keeps_full_row_navigation_with_virtual_model(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        self.assertFalse(
            shiboken6.createdByPython(window.results_table),
            "macOS accessibility must enter a native Qt table, not QTableViewWrapper",
        )
        results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 120.0,
                state={"x_px": float(index), "y_px": float(index + 1)},
                filtered_state={"x_px": float(index), "y_px": float(index + 1)},
                confidence=0.95,
                status="ok",
            )
            for index in range(20_000)
        ]
        window.current_task.pipeline.results = results
        window.current_task.media_info = MediaInfo(
            fps=120.0,
            frame_count=len(results),
            width=1920,
            height=1080,
            duration_s=len(results) / 120.0,
            available=True,
        )
        window.current_task.preview_frame_index = len(results) - 1

        with patch.object(
            window.review_controller,
            "table",
            side_effect=AssertionError("the UI must not materialize every formatted row"),
        ):
            window._render_results(window.current_task, prefer_preview_frame=True)

        self.assertEqual(window.results_model.rowCount(), 20_000)
        self.assertEqual(window.results_model.cached_row_count, 0)
        self.assertEqual(current_result_row(window), 19_999)
        self.assertIn("Results: 20000", window.review_summary_label.text())
        self.assertIn("all results remain selectable", window.results_table.accessibleDescription())
        widths = [
            window.results_table.columnWidth(index)
            for index in range(window.results_model.columnCount())
        ]
        self.assertEqual(widths[:2], [60, 74])
        self.assertTrue(all(width >= 78 for width in widths[2:-2]))
        self.assertEqual(widths[-2:], [60, 74])
        self.assertLessEqual(sum(widths), 424)

        review_tab = next(
            index
            for index in range(window.sidebar_tabs.count())
            if window.sidebar_tabs.tabText(index) == "Review"
        )
        window.sidebar_tabs.setCurrentIndex(review_tab)
        window.resize(1440, 900)
        window.show()
        QCoreApplication.processEvents()
        selected_rect = window.results_table.visualRect(window.results_table.currentIndex())
        self.assertTrue(selected_rect.intersects(window.results_table.viewport().rect()))

        window.results_table.selectRow(10_000)
        QCoreApplication.processEvents()

        self.assertEqual(window._selected_result_index(), 10_000)
        self.assertEqual(window.current_task.preview_frame_index, 10_000)
        self.assertIn("Frame 10000", window.review_selection_label.text())

    def test_review_recomputes_and_caches_response_for_trimmed_old_frame(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + index * 5, 20) for index in range(6)]
        reader = FakeReader(frames)
        task = window.current_task
        task.media_path = "response-history.mp4"
        task.media_info = reader.info
        task.pipeline.debug_history_limit = 1
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        self.assertIsNone(task.pipeline.results[0].debug["response_map"])

        window.show_response_checkbox.blockSignals(True)
        window.show_response_checkbox.setChecked(True)
        window.show_response_checkbox.blockSignals(False)
        result_ids = [id(result) for result in task.pipeline.results]
        filter_velocities = dict(getattr(task.pipeline.tracker_filter, "velocities", {}))
        task.preview_frame_index = 0
        window._render_preview()

        self.assertIsNone(window.preview_label.response_map)
        self.assertEqual(window.response_status_label.text(), "Response: loading…")
        self.assertEqual(window.response_status_label.property("responseState"), "loading")
        wait_for_review_response(window)
        recomputed = window.preview_label.response_map
        self.assertIsNotNone(recomputed)
        self.assertEqual(window.response_status_label.text(), "Response: recomputed for this frame")
        self.assertEqual(window.response_status_label.property("responseState"), "recomputed")
        self.assertEqual(window._review_responses.cached_frames(task), [0])
        self.assertIsNone(task.pipeline.results[0].debug["response_map"])
        self.assertEqual([id(result) for result in task.pipeline.results], result_ids)
        self.assertEqual(dict(getattr(task.pipeline.tracker_filter, "velocities", {})), filter_velocities)

        window._render_preview()
        self.assertIs(window.preview_label.response_map, recomputed)
        self.assertEqual(window.response_status_label.text(), "Response: review cache")
        self.assertEqual(window.response_status_label.property("responseState"), "cached")

        task.preview_frame_index = len(frames) - 1
        window._render_preview()
        self.assertEqual(window.response_status_label.text(), "Response: stored with tracking")
        self.assertEqual(window.response_status_label.property("responseState"), "stored")

    def test_annular_response_routes_to_angular_diagnostic_instead_of_empty_overlay(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        center = (64.0, 64.0)
        frames = [
            synthetic_ring_frame((128, 128, 3), center, 31.0, 5.6 + index * 0.18, angular_width=0.1)
            for index in range(6)
        ]
        reader = FakeReader(frames, fps=30.0)
        pipeline = travelling_flame_preset(center_px=center, inner_radius=25.0, outer_radius=37.0)
        pipeline.run(frames, fps=30.0)
        task = window.current_task
        task.media_path = "polar-response.mp4"
        task.media_info = reader.info
        task.pipeline_key = "travelling_flame"
        task.pipeline = pipeline
        task.roi = pipeline.roi.to_config()
        task.preview_frame_index = len(frames) - 1
        task.tracking_outcome = "complete"
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        window.show_response_checkbox.setChecked(True)
        window._render_preview()
        self.assertIsNone(window.preview_label.response_map)
        self.assertEqual(
            window.response_status_label.text(),
            "Response: angular profile below · 720 samples",
        )
        self.assertEqual(window.response_status_label.property("responseState"), "diagnostic")
        self.assertIn("no source-frame recomputation", window.response_status_label.toolTip())
        self.assertEqual(window.review_diagnostics_panel.mode, "angular_response")
        self.assertEqual(window.confidence_plot.kind, "angular_response")
        self.assertEqual(window.confidence_plot.point_count, 720)

        self.assertTrue(window.review_diagnostics_panel.select_mode("velocity"))
        self.assertIn(
            window.review_diagnostics_panel.series_combo.currentData(),
            {"omega", "v_theta_unwrapped"},
        )
        window.review_diagnostics_panel.frameActivated.emit(2)
        QCoreApplication.processEvents()
        self.assertEqual(task.preview_frame_index, 2)
        self.assertEqual(current_result_row(window), 2)
        self.assertEqual(window.review_diagnostics_panel.mode, "velocity")
        self.assertEqual(
            window.response_status_label.text(),
            "Response: angular profile available · 720 samples",
        )

    def test_annular_retained_theta_signal_skips_review_recompute_without_a_frame(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        center = (64.0, 64.0)
        frames = [
            synthetic_ring_frame((128, 128, 3), center, 31.0, 5.6 + index * 0.18, angular_width=0.1)
            for index in range(6)
        ]
        pipeline = travelling_flame_preset(center_px=center, inner_radius=25.0, outer_radius=37.0)
        pipeline.run(frames, fps=30.0)
        task = window.current_task
        task.pipeline = pipeline
        task.preview_frame_index = 0
        window._render_results(task)
        window.results_table.selectRow(0)
        window.show_response_checkbox.blockSignals(True)
        window.show_response_checkbox.setChecked(True)
        window.show_response_checkbox.blockSignals(False)
        window._response_mode_routing_requested = True

        with patch.object(
            window,
            "_response_map_for_review",
            side_effect=AssertionError("retained angular evidence must not be recomputed"),
        ):
            window._render_preview()

        self.assertIsNone(window.preview_label.response_map)
        self.assertIsNone(window._review_response_thread)
        self.assertIsNone(window._review_response_job)
        self.assertIsNone(window._pending_review_response)
        self.assertEqual(window.review_diagnostics_panel.mode, "angular_response")
        self.assertEqual(
            window.response_status_label.text(),
            "Response: angular profile below · 720 samples",
        )

    def test_review_response_worker_keeps_only_latest_frame_and_can_cancel(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + index * 5, 20) for index in range(6)]
        reader = FakeReader(frames)
        task = window.current_task
        task.media_path = "slow-response-history.mp4"
        task.media_info = reader.info
        task.pipeline.debug_history_limit = 1
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window.show_response_checkbox.blockSignals(True)
        window.show_response_checkbox.setChecked(True)
        window.show_response_checkbox.blockSignals(False)

        original_compute = ReviewResponseService.compute

        def slow_compute(request):
            time.sleep(0.06)
            return original_compute(request)

        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        with patch.object(ReviewResponseService, "compute", side_effect=slow_compute):
            task.preview_frame_index = 0
            window._render_preview()
            self.assertIsNotNone(window._review_response_thread)
            self.assertEqual(window.response_status_label.text(), "Response: loading…")

            window._preview_frame_changed(1)
            self.assertEqual(task.preview_frame_index, 1)
            wait_for_review_response(window)

            self.assertGreater(len(heartbeats), 3)
            self.assertEqual(window._review_responses.cached_frames(task), [1])
            self.assertIsNotNone(window.preview_label.response_map)
            self.assertEqual(window.response_status_label.text(), "Response: recomputed for this frame")

            window._preview_frame_changed(2)
            self.assertIsNotNone(window._review_response_thread)
            window.show_response_checkbox.setChecked(False)
            wait_for_review_response(window)

            self.assertEqual(window._review_responses.cached_frames(task), [1])
            self.assertIsNone(window.preview_label.response_map)
            self.assertEqual(window.response_status_label.text(), "Response overlay: off")

    def test_closing_window_cancels_review_response_worker_safely(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        frames = [red_dot_frame((50, 70, 3), 20 + index * 4, 20) for index in range(3)]
        reader = FakeReader(frames)
        task = window.current_task
        task.media_path = "close-response-history.mp4"
        task.media_info = reader.info
        task.pipeline.debug_history_limit = 0
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window.show_response_checkbox.blockSignals(True)
        window.show_response_checkbox.setChecked(True)
        window.show_response_checkbox.blockSignals(False)
        original_compute = ReviewResponseService.compute

        def slow_compute(request):
            time.sleep(0.06)
            return original_compute(request)

        with patch.object(ReviewResponseService, "compute", side_effect=slow_compute):
            task.preview_frame_index = 0
            window.show()
            window._render_preview()
            self.assertIsNotNone(window._review_response_thread)
            window.close()
            self.assertTrue(window._close_when_workers_stop)
            wait_for_review_response(window)
            deadline = time.monotonic() + 1.0
            while window.isVisible() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertFalse(window.isVisible())

    def test_tracking_worker_keeps_ui_responsive_and_can_cancel(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i, 20) for i in range(40)]
        reader = SlowReader(frames)
        window.current_task.media_path = "slow-synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window.show()
        window.sidebar_tabs.setCurrentIndex(0)
        QCoreApplication.processEvents()

        heartbeats: list[int] = []
        timer = QTimer()
        timer.timeout.connect(lambda: heartbeats.append(len(heartbeats)))
        timer.start(2)
        self.addCleanup(timer.stop)

        window._run_tracking()
        self.assertIsNotNone(window._tracking_thread)
        self.assertFalse(window.sidebar_tabs.isEnabled())
        self.assertIs(window.sidebar_tabs.currentWidget(), window.tracking_tab)
        self.assertEqual(window.run_tracking_button.text(), "Cancel")
        deadline = time.monotonic() + 0.12
        while len(window.current_task.pipeline.results) < 3 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.001)
        self.assertGreater(len(heartbeats), 2)
        self.assertIn("Throughput", window.tracking_summary_label.text())
        self.assertIn("fps", window.tracking_summary_label.text())
        self.assertIn("Throughput", window.statusBar().currentMessage())
        self.assertIn("left", window.tracking_summary_label.text())
        self.assertIn("Average input", window.tracking_summary_label.toolTip())
        self.assertIn("tracking compute", window.tracking_summary_label.toolTip())
        self.assertFalse(window.tracking_performance_label.isHidden())
        self.assertFalse(window.tracking_performance_title_label.isHidden())
        self.assertTrue(window.tracking_performance_label.isVisible())
        self.assertIn("Input", window.tracking_performance_label.text())
        self.assertIn("Compute", window.tracking_performance_label.text())
        self.assertIn("1-frame pipeline", window.tracking_performance_label.text())
        self.assertEqual(window.tracking_performance_label.text().count("\n"), 2)
        self.assertGreaterEqual(window.tracking_performance_label.minimumHeight(), 64)
        self.assertIn("Review cache", window.tracking_performance_label.text())
        self.assertIn("stage averages can overlap", window.tracking_performance_label.toolTip())
        self.assertIn("Review cache retains", window.tracking_performance_label.toolTip())
        self.assertFalse(window.playback_status_label.isHidden())
        self.assertEqual(window.playback_status_label.property("playbackState"), "tracking")
        self.assertIn("Preview paused · Frame 0", window.playback_status_label.text())
        self.assertIn("not the current processing frame", window.playback_status_label.toolTip())
        self.assertIn("throughput is not reduced", window.playback_status_label.toolTip())

        window._cancel_tracking()
        self.assertEqual(
            window.tracking_performance_label.property("performanceState"),
            "cancelling",
        )
        self.assertIn("Cancellation requested", window.tracking_performance_label.text())
        self.assertIn("last completed sample", window.tracking_performance_label.toolTip())
        self.assertIn(
            "last completed sample",
            window.tracking_performance_label.accessibleDescription(),
        )
        wait_for_tracking(window)
        self.assertLess(len(window.current_task.pipeline.results), len(frames))
        self.assertTrue(window.sidebar_tabs.isEnabled())
        self.assertTrue(window.tracking_performance_label.isHidden())
        self.assertTrue(window.tracking_performance_title_label.isHidden())
        self.assertTrue(window.playback_status_label.isHidden())
        self.assertEqual(window.run_tracking_button.text(), "Run Tracking")
        self.assertIn("canceled", window.statusBar().currentMessage().lower())
        self.assertEqual(window.current_task.tracking_outcome, "canceled")
        self.assertIn("canceled", window.current_task.tracking_note.lower())
        self.assertEqual(len(window.current_task.run_history), 1)
        canceled_run = window.current_task.run_history[0]
        self.assertEqual(canceled_run.outcome, "canceled")
        self.assertEqual(canceled_run.result_count, len(window.current_task.pipeline.results))
        self.assertGreater(canceled_run.peak_debug_bytes, 0)
        self.assertEqual(canceled_run.prefetch_frames, 1)
        self.assertIn(canceled_run.compute_backend, {"OpenCV components", "NumPy components"})
        self.assertIn("Canceled", window.run_history_list.item(0).text())
        self.assertEqual(window.run_history_list.currentRow(), 0)
        self.assertEqual(
            [selection.sequence for selection in window.run_history_panel.selected_runs()],
            [1],
        )
        self.assertEqual(
            window.run_history_panel.performance_label.property("performanceState"),
            "ready",
        )
        self.assertIn("Throughput", window.run_history_panel.performance_label.text())
        canceled_snapshot = window._snapshot_from_task(window.current_task)
        self.assertEqual(canceled_snapshot.tracking_outcome, "canceled")
        self.assertEqual(canceled_snapshot.tracking_note, window.current_task.tracking_note)
        self.assertEqual(canceled_snapshot.run_history, window.current_task.run_history)

    def test_tracking_terminal_signal_stays_locked_until_thread_exits(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 16, 20)]
        reader = FakeReader(frames)
        window.current_task.media_path = "terminal-gap.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        def complete_before_thread_exit(worker) -> None:
            worker.completed.emit(0, False, False, "")
            time.sleep(0.08)

        with patch("neo_tracker.ui.main_window.TrackingWorker.run", new=complete_before_thread_exit):
            window._run_tracking()
            deadline = time.monotonic() + 1.0
            while window.current_task.tracking_outcome != "complete" and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)

            self.assertIsNotNone(window._tracking_thread)
            self.assertEqual(window.run_tracking_button.text(), "Finishing…")
            self.assertFalse(window.run_tracking_button.isEnabled())
            self.assertFalse(window.sidebar_tabs.isEnabled())
            self.assertEqual(window.tracking_status_label.text(), "Finishing…")
            self.assertEqual(window.tracking_status_label.property("trackingOutcome"), "running")
            self.assertIn("Finalizing results", window.tracking_summary_label.text())
            self.assertEqual(
                window.tracking_performance_label.property("performanceState"),
                "finishing",
            )
            self.assertIn("Tracking stopped", window.tracking_performance_label.text())
            wait_for_tracking(window)
            self.assertEqual(window.run_tracking_button.text(), "Run Tracking")
            self.assertTrue(window.run_tracking_button.isEnabled())
            self.assertTrue(window.sidebar_tabs.isEnabled())

    def test_closing_tracking_terminal_gap_replaces_stale_success_status(self) -> None:
        window = NeoTrackerWindow()
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        frames = [red_dot_frame((50, 70, 3), 16, 20)]
        reader = FakeReader(frames)
        window.current_task.media_path = "closing-terminal-gap.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window.show()

        def complete_before_thread_exit(worker) -> None:
            worker.completed.emit(1, False, False, "")
            time.sleep(0.08)

        with patch("neo_tracker.ui.main_window.TrackingWorker.run", new=complete_before_thread_exit):
            window._run_tracking()
            window.close()
            deadline = time.monotonic() + 1.0
            while window.run_tracking_button.text() != "Finishing…" and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)

            self.assertIsNotNone(window._tracking_thread)
            self.assertTrue(window._close_when_workers_stop)
            self.assertEqual(window.tracking_status_label.text(), "Closing…")
            self.assertEqual(window.tracking_status_label.property("trackingOutcome"), "running")
            self.assertNotEqual(window.tracking_status_label.text(), "Tracked")
            self.assertIn("Finishing background operations safely", window.tracking_summary_label.text())
            self.assertEqual(
                window.tracking_performance_label.property("performanceState"),
                "cancelling",
            )
            self.assertIn("Closing", window.tracking_performance_label.text())
            self.assertEqual(window.run_tracking_button.accessibleName(), "Closing Neo-Tracker")

            wait_for_tracking(window)
            deadline = time.monotonic() + 1.0
            while window.isVisible() and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.001)
            self.assertFalse(window.isVisible())

    def test_closing_window_cancels_tracking_worker_and_blocks_new_jobs(self) -> None:
        window = NeoTrackerWindow()
        window._ask_unsaved_changes = (  # type: ignore[method-assign]
            lambda _action: QMessageBox.StandardButton.Discard
        )
        frames = [red_dot_frame((50, 70, 3), 12 + i, 20) for i in range(40)]
        reader = SlowReader(frames)
        window.current_task.media_path = "close-slow-tracking.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window.show()

        window._run_tracking()
        self.assertIsNotNone(window._tracking_thread)
        window.close()
        self.assertTrue(window._close_when_workers_stop)
        self.assertFalse(window.centralWidget().isEnabled())
        self.assertIn("Finishing background operations", window.statusBar().currentMessage())
        active_thread = window._tracking_thread
        window._run_tracking()
        self.assertIs(window._tracking_thread, active_thread)

        wait_for_tracking(window)
        deadline = time.monotonic() + 1.0
        while window.isVisible() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.001)
        self.assertFalse(window.isVisible())

    def test_tracking_early_eof_keeps_partial_results_and_reports_partial_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20) for i in range(3)]
        reader = EarlyEOFReader(frames, declared_frame_count=8)
        window.current_task.media_path = "early-eof.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        window._run_tracking()
        wait_for_tracking(window)

        self.assertEqual(len(window.current_task.pipeline.results), 3)
        self.assertEqual(window.current_task.tracking_outcome, "partial")
        self.assertIn("frame 3", window.current_task.tracking_note)
        self.assertEqual(window.tracking_status_label.text(), "Partial")
        self.assertEqual(window.tracking_status_label.property("trackingOutcome"), "partial")
        self.assertIn("source ended early", window.tracking_summary_label.text())
        self.assertIn("partial results were kept", window.statusBar().currentMessage())
        self.assertEqual(len(window.current_task.run_history), 1)
        partial_run = window.current_task.run_history[0]
        self.assertEqual(partial_run.outcome, "partial")
        self.assertEqual(partial_run.end_frame, 2)
        self.assertEqual(partial_run.processed_frames, 3)
        self.assertTrue(partial_run.has_performance_metrics)
        self.assertGreater(partial_run.throughput_fps, 0.0)
        self.assertGreaterEqual(partial_run.input_ms_per_frame, 0.0)
        self.assertGreaterEqual(partial_run.processing_ms_per_frame, 0.0)
        self.assertIn("Partial", window.run_history_list.item(0).text())
        window.run_history_list.setCurrentRow(0)
        QCoreApplication.processEvents()
        self.assertIn("Input", window.run_history_panel.performance_label.text())
        self.assertEqual(window.run_history_panel.performance_label.property("performanceState"), "ready")
        self.assertTrue(window.export_tracking_csv_button.isEnabled())
        self.assertIs(window.sidebar_tabs.currentWidget(), window.review_tab)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "partial.ntproj"
            window._project_from_window(path).save(path)
            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)

        self.assertEqual(restored.current_task.tracking_outcome, "partial")
        self.assertEqual(restored.current_task.tracking_note, window.current_task.tracking_note)
        self.assertEqual(restored.tracking_status_label.text(), "Partial")
        self.assertEqual(restored.tracking_status_label.property("trackingOutcome"), "partial")
        self.assertIn("source ended early", restored.tracking_summary_label.text())
        self.assertIn("media unavailable", restored.tracking_summary_label.text())
        self.assertIn("frame 3", restored.tracking_status_label.toolTip())
        self.assertIn("Run note:", restored.review_summary_label.text())
        self.assertIn("frame 3", restored.review_summary_label.text())
        self.assertEqual(len(restored.current_task.run_history), 1)
        self.assertEqual(restored.current_task.run_history[0].outcome, "partial")
        self.assertTrue(restored.current_task.run_history[0].has_performance_metrics)

    def test_tracking_failure_is_recorded_with_attempted_range_and_pipeline_snapshot(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20) for i in range(5)]
        reader = FailingReader(frames, fail_at=2)
        window.current_task.media_path = "failing.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        with patch("neo_tracker.ui.main_window.QMessageBox.warning") as warning:
            window._run_tracking()
            wait_for_tracking(window)

        warning.assert_called_once()
        self.assertEqual(window.current_task.tracking_outcome, "failed")
        self.assertEqual(len(window.current_task.run_history), 1)
        failed_run = window.current_task.run_history[0]
        self.assertEqual(failed_run.outcome, "failed")
        self.assertEqual(failed_run.start_frame, 0)
        self.assertEqual(failed_run.end_frame, 1)
        self.assertEqual(failed_run.processed_frames, 2)
        self.assertEqual(failed_run.result_count, 2)
        self.assertTrue(failed_run.has_performance_metrics)
        self.assertIn("synthetic decoder failure", failed_run.note)
        self.assertEqual(failed_run.pipeline_config, window.current_task.pipeline.to_config())
        self.assertIn("Failed", window.run_history_list.item(0).text())
        self.assertEqual(window.run_history_list.item(0).background().color().name(), "#fff0f0")

    def test_long_tracking_failure_is_bounded_and_still_records_terminal_run(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        reader = FakeReader([red_dot_frame((50, 70, 3), 12, 20)])
        window.current_task.media_path = "long-failure.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        long_error = "synthetic worker failure " + ("x" * 5000)

        def fail_with_long_message(worker) -> None:
            worker.failed.emit(long_error, 0)

        with (
            patch("neo_tracker.ui.main_window.TrackingWorker.run", new=fail_with_long_message),
            patch("neo_tracker.ui.main_window.QMessageBox.warning") as warning,
        ):
            window._run_tracking()
            wait_for_tracking(window)

        warning.assert_called_once()
        self.assertEqual(window.current_task.tracking_outcome, "failed")
        self.assertLessEqual(len(window.current_task.tracking_note), 4096)
        self.assertIn("[truncated to fit the project tracking-note limit]", window.current_task.tracking_note)
        self.assertEqual(len(window.current_task.run_history), 1)
        self.assertEqual(window.current_task.run_history[0].note, window.current_task.tracking_note)
        self.assertIn("truncated", warning.call_args.args[2])

    def test_full_tracking_requires_explicit_result_replacement_and_clears_after_confirm(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20) for i in range(4)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "replace-results.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        original_results = list(window.current_task.pipeline.results)
        window.current_task.edit_history = [{"type": "manual_correction", "frame_index": 1}]
        window.current_task.tracking_outcome = "complete"

        window._ask_result_replacement = lambda _task: False  # type: ignore[method-assign]
        with patch.object(window, "_start_tracking_job") as start_job:
            window._run_tracking()

        start_job.assert_not_called()
        self.assertTrue(all(current is original for current, original in zip(window.current_task.pipeline.results, original_results)))
        self.assertEqual(window.current_task.edit_history, [{"type": "manual_correction", "frame_index": 1}])
        self.assertIn("Current Results/Edits are unchanged", window.statusBar().currentMessage())

        window._ask_result_replacement = lambda _task: True  # type: ignore[method-assign]
        window._run_tracking()
        wait_for_tracking(window)

        self.assertEqual(len(window.current_task.pipeline.results), len(frames))
        self.assertIsNot(window.current_task.pipeline.results[0], original_results[0])
        self.assertEqual(window.current_task.edit_history, [])
        self.assertEqual(len(window.current_task.run_history), 2)

    def test_zero_frame_full_tracking_failure_restores_previous_result_state_and_analysis(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20) for i in range(4)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "restore-results.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window._run_analysis()
        wait_for_analysis(window)
        previous_analysis_run = window.analysis_controller.current_run
        self.assertIsNotNone(previous_analysis_run)
        previous_results = list(window.current_task.pipeline.results)
        previous_edits = [{"type": "manual_correction", "frame_index": 1}]
        window.current_task.edit_history = [dict(entry) for entry in previous_edits]
        window.current_task.tracking_outcome = "complete"
        window.current_task.tracking_note = "trusted current result state"

        failing_reader = FailingReader(frames, fail_at=0)
        failing_reader.info = MediaInfo(
            **{
                **failing_reader.info.__dict__,
                "source_identity": reader.info.source_identity,
            }
        )
        window._reader_for_task = lambda _task: failing_reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: failing_reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: failing_reader.info
        window._ask_result_replacement = lambda _task: True  # type: ignore[method-assign]
        with patch("neo_tracker.ui.main_window.QMessageBox.warning") as warning:
            window._run_tracking()
            wait_for_tracking(window)

        warning.assert_called_once()
        self.assertTrue(all(current is previous for current, previous in zip(window.current_task.pipeline.results, previous_results)))
        self.assertEqual(window.current_task.edit_history, previous_edits)
        self.assertEqual(window.current_task.tracking_outcome, "complete")
        self.assertEqual(window.current_task.tracking_note, "trusted current result state")
        self.assertIs(window.analysis_controller.current_run, previous_analysis_run)
        self.assertTrue(window.analysis_export_csv_button.isEnabled())
        self.assertEqual(len(window.current_task.run_history), 2)
        failed_run = window.current_task.run_history[-1]
        self.assertEqual(failed_run.outcome, "failed")
        self.assertEqual(failed_run.processed_frames, 0)
        self.assertFalse(failed_run.has_performance_metrics)
        self.assertIn("Previous current Results/Edits were restored", failed_run.note)
        self.assertIn("previous Results/Edits were restored", window.statusBar().currentMessage())
        self.assertTrue(window.run_tracking_button.isEnabled())

    def test_identity_drift_after_progress_restores_complete_pre_run_state(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20) for i in range(4)]
        reader = FakeReader(frames)
        task = window.current_task
        task.media_path = "identity-drift.mp4"
        task.media_info = reader.info
        task.saved_media_info = reader.info
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window._run_analysis()
        wait_for_analysis(window)

        previous_results = list(task.pipeline.results)
        previous_edits = [{"type": "manual_correction", "frame_index": 1}]
        previous_outcome = "complete"
        previous_note = "trusted result state"
        previous_analysis = window.analysis_controller.current_run
        self.assertIsNotNone(previous_analysis)
        task.edit_history = [dict(entry) for entry in previous_edits]
        task.tracking_outcome = previous_outcome
        task.tracking_note = previous_note
        source_identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        task.media_info = MediaInfo(
            **{
                **reader.info.__dict__,
                "source_identity": source_identity,
            }
        )
        task.saved_media_info = task.media_info

        replacement_result = TrackerResult(
            0,
            0.0,
            {"x_px": 60.0, "y_px": 40.0},
            {"x_px": 60.0, "y_px": 40.0},
            0.5,
            "ok",
        )

        def fail_after_progress(worker) -> None:
            worker.pipeline.results = [replacement_result]
            worker.progress.emit(
                TrackingProgress(
                    completed=1,
                    total=4,
                    start_frame=0,
                    elapsed_s=0.1,
                    input_s=0.02,
                    processing_s=0.08,
                )
            )
            worker.failed.emit(
                f"{TRACKING_SOURCE_CHANGED_PREFIX}Media source changed after threaded processing",
                1,
            )

        with (
            patch("neo_tracker.ui.main_window.TrackingWorker.run", new=fail_after_progress),
            patch("neo_tracker.ui.main_window.QMessageBox.warning") as warning,
        ):
            window._start_tracking_job(task, 4, 10.0, mode="full")
            wait_for_tracking(window)

        warning.assert_called_once()
        self.assertTrue(all(current is previous for current, previous in zip(task.pipeline.results, previous_results)))
        self.assertEqual(task.edit_history, previous_edits)
        self.assertEqual(task.tracking_outcome, previous_outcome)
        self.assertEqual(task.tracking_note, previous_note)
        self.assertIs(window.analysis_controller.current_run, previous_analysis)
        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertIn("Previous results were restored", task.media_info.error)
        self.assertIsNotNone(task.pending_media_relink)
        candidate, assessment = task.pending_media_relink
        self.assertIs(candidate, reader.info)
        self.assertEqual(assessment.state, "unverified")
        self.assertTrue(assessment.clear_results)
        self.assertEqual(window._media_relink_candidate_path, task.media_path)
        self.assertEqual(window.media_relink_panel.status_label.text(), "Source identity unverified")
        self.assertEqual(window.tracking_status_label.text(), "Source changed")
        self.assertFalse(window.run_tracking_button.isEnabled())
        self.assertFalse(window.export_tracking_csv_button.isEnabled())
        self.assertFalse(window.export_report_button.isEnabled())
        self.assertFalse(window.correct_point_button.isEnabled())
        self.assertFalse(window.mark_lost_button.isEnabled())
        self.assertFalse(window.jump_to_result_button.isEnabled())
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        self.assertEqual(window.analysis_status_label.text(), "Source review required")
        self.assertFalse(window.preview_label.has_frame())
        self.assertIs(window.sidebar_tabs.currentWidget(), window.media_tab)
        failed_run = task.run_history[-1]
        self.assertEqual(failed_run.outcome, "failed")
        self.assertEqual(failed_run.processed_frames, 1)
        self.assertEqual(failed_run.result_count, len(previous_results))
        self.assertIn("Media source changed during tracking", failed_run.note)
        self.assertIn("were restored", failed_run.note)
        self.assertIn("Media source changed during tracking", window.statusBar().currentMessage())

    def test_default_tracking_job_wires_required_process_isolation(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        task = window.current_task
        task.media_path = "default-isolation.mp4"
        task.media_info = MediaInfo(
            fps=10.0,
            frame_count=1,
            width=16,
            height=12,
            duration_s=0.1,
            available=True,
            source_identity=MediaIdentity("full-sha256-v1", "a" * 64, 128, 128),
        )
        task.saved_media_info = task.media_info
        observed: dict[str, object] = {}

        def capture_wiring(worker) -> None:
            observed["process_isolation"] = worker.process_isolation
            observed["factory"] = worker.isolated_reader_factory
            observed["source_path"] = worker.expected_source_path
            observed["source_identity"] = worker.expected_source_identity
            worker.failed.emit("synthetic wiring stop", 0)

        with (
            patch("neo_tracker.ui.main_window.TrackingWorker.run", new=capture_wiring),
            patch("neo_tracker.ui.main_window.QMessageBox.warning"),
        ):
            window._start_tracking_job(task, 1, 10.0, mode="full")
            wait_for_tracking(window)

        self.assertIs(observed["process_isolation"], True)
        factory = observed["factory"]
        self.assertIsInstance(factory, partial)
        self.assertIs(factory.func, MediaReader)
        self.assertEqual(factory.args, ("default-isolation.mp4",))
        self.assertEqual(observed["source_path"], "default-isolation.mp4")
        self.assertEqual(observed["source_identity"], task.media_info.source_identity)

    def test_review_reports_multiple_color_candidates(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frame = np.maximum(
            red_dot_frame((60, 90, 3), 22, 30, radius=4.0),
            red_dot_frame((60, 90, 3), 68, 30, radius=6.0),
        )
        reader = FakeReader([frame])
        window.current_task.media_path = "two-markers.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()

        window._run_tracking()
        wait_for_tracking(window)

        self.assertEqual(len(window.preview_label.candidate_points), 2)
        self.assertIn("Candidates on frame 0: 2", window.candidate_summary_label.text())
        self.assertIn("selected score", window.candidate_summary_label.text())

    def test_manual_correction_updates_current_result_and_overlay(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(3)]
        reader = FakeReader(frames)
        window.current_task.media_path = "synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window._run_analysis()
        wait_for_analysis(window)
        self.assertTrue(window.analysis_export_csv_button.isEnabled())
        with patch.object(
            window.analysis_controller,
            "invalidate_source_cache",
            wraps=window.analysis_controller.invalidate_source_cache,
        ) as invalidate_source_cache:
            window._manual_point_selected((30.0, 35.0))
        invalidate_source_cache.assert_called_once_with()
        result = window.current_task.pipeline.results[-1]
        self.assertEqual(result.status, "manual")
        self.assertAlmostEqual(result.filtered_state["x_px"], 30.0)
        self.assertAlmostEqual(result.filtered_state["y_px"], 35.0)
        self.assertEqual(window.preview_label.current_tracking_point, (30.0, 35.0))
        self.assertEqual(window.confidence_plot._values[-1][2], "manual")
        self.assertEqual(current_result_row(window), len(frames) - 1)
        self.assertIn("manual", window.review_selection_label.text())
        self.assertIn("x_px 30 px", window.review_selection_detail_label.text())
        self.assertEqual(window.review_selection_label.property("reviewTone"), "manual")
        self.assertFalse(window.analysis_export_csv_button.isEnabled())
        self.assertEqual(window.analysis_status_label.text(), "Needs run")
        self.assertIn("manual correction", window.analysis_result_view.toPlainText())

    def test_rerun_after_current_result_preserves_manual_anchor(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(5)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "synthetic.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)

        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 35.0))
        anchor = window.current_task.pipeline.results[1]
        self.assertEqual(anchor.status, "manual")
        self.assertIn("v_x_px", anchor.debug["filter"]["velocity"])
        window.results_table.selectRow(3)
        QCoreApplication.processEvents()
        window._mark_current_result_lost()
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()

        window._ask_rerun_replacement = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        window._rerun_after_current_result()
        wait_for_tracking(window)
        results = window.current_task.pipeline.results
        self.assertEqual(len(window.current_task.run_history), 2)
        rerun_record = window.current_task.run_history[-1]
        self.assertEqual(rerun_record.mode, "rerun")
        self.assertEqual(rerun_record.outcome, "complete")
        self.assertEqual(rerun_record.start_frame, 2)
        self.assertEqual(rerun_record.end_frame, len(frames) - 1)
        self.assertEqual(rerun_record.processed_frames, len(frames) - 2)
        self.assertIn("#2 Rerun · Complete", window.run_history_list.item(0).text())
        self.assertEqual(window.review_history_tabs.tabText(0), "Runs (2)")
        self.assertEqual(window.review_history_tabs.tabText(1), "Edits (3)")
        self.assertEqual(len(results), len(frames))
        self.assertEqual(results[1].status, "manual")
        self.assertAlmostEqual(results[1].filtered_state["x_px"], 30.0)
        self.assertAlmostEqual(results[1].filtered_state["y_px"], 35.0)
        self.assertEqual([result.frame_index for result in results], list(range(len(frames))))
        self.assertTrue(results[2].debug.get("candidates"))
        self.assertEqual(window.results_model.rowCount(), len(frames))
        self.assertEqual(window.preview_frame_spin.value(), len(frames) - 1)
        self.assertEqual(current_result_row(window), len(frames) - 1)
        self.assertIn(f"Frame {len(frames) - 1}", window.review_selection_label.text())
        self.assertIn(f"Candidates on frame {len(frames) - 1}", window.candidate_summary_label.text())
        prefix_edit, replaced_edit, rerun_edit = window.current_task.edit_history
        self.assertNotIn("superseded_at", prefix_edit)
        self.assertEqual(replaced_edit["superseded_by_rerun_start_frame"], 2)
        self.assertNotIn("superseded_at", rerun_edit)
        window.review_history_tabs.setCurrentIndex(1)
        window.edit_history_panel.show_all()
        self.assertIn("superseded by rerun from frame 2", window.edit_history_list.item(1).text())
        self.assertEqual(window.edit_history_list.item(1).background().color().name(), "#f0f1f3")

    def test_rerun_tail_replacement_cancel_keeps_results_and_edits_unchanged(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(5)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "rerun-cancel.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 35.0))
        window.results_table.selectRow(3)
        QCoreApplication.processEvents()
        window._mark_current_result_lost()
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        previous_results = list(window.current_task.pipeline.results)
        previous_edits = [dict(entry) for entry in window.current_task.edit_history]

        window._ask_rerun_replacement = lambda *_args, **_kwargs: False  # type: ignore[method-assign]
        with patch.object(window, "_start_tracking_job") as start_job:
            window._rerun_after_current_result()

        start_job.assert_not_called()
        self.assertTrue(
            all(current is previous for current, previous in zip(window.current_task.pipeline.results, previous_results))
        )
        self.assertEqual(window.current_task.edit_history, previous_edits)
        self.assertEqual(len(window.current_task.run_history), 1)
        self.assertIn("Current later Results/Edits are unchanged", window.statusBar().currentMessage())

    def test_zero_frame_rerun_failure_restores_tail_without_superseding_edits(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(5)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "rerun-restore.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 35.0))
        window.results_table.selectRow(3)
        QCoreApplication.processEvents()
        window._mark_current_result_lost()
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        previous_results = list(window.current_task.pipeline.results)
        previous_edits = [dict(entry) for entry in window.current_task.edit_history]
        window.current_task.tracking_note = "trusted rerun tail"

        failing_reader = FailingReader(frames, fail_at=2)
        failing_reader.info = MediaInfo(
            **{
                **failing_reader.info.__dict__,
                "source_identity": window.current_task.saved_media_info.source_identity,
            }
        )
        window._reader_for_task = lambda _task: failing_reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: failing_reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: failing_reader.info
        window._ask_rerun_replacement = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        with patch("neo_tracker.ui.main_window.QMessageBox.warning") as warning:
            window._rerun_after_current_result()
            wait_for_tracking(window)

        warning.assert_called_once()
        self.assertTrue(
            all(current is previous for current, previous in zip(window.current_task.pipeline.results, previous_results))
        )
        self.assertEqual(window.current_task.edit_history, previous_edits)
        self.assertTrue(all("superseded_at" not in entry for entry in window.current_task.edit_history))
        self.assertEqual(window.current_task.tracking_outcome, "complete")
        self.assertEqual(window.current_task.tracking_note, "trusted rerun tail")
        self.assertEqual(len(window.current_task.run_history), 2)
        failed_run = window.current_task.run_history[-1]
        self.assertEqual(failed_run.mode, "rerun")
        self.assertEqual(failed_run.outcome, "failed")
        self.assertEqual(failed_run.start_frame, 2)
        self.assertEqual(failed_run.processed_frames, 0)
        self.assertIn("Previous current Results/Edits were restored", failed_run.note)
        self.assertIn("previous Results/Edits were restored", window.statusBar().currentMessage())

    def test_run_history_filters_compares_and_exports_visible_records(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        config = window.current_task.pipeline.to_config()
        window.current_task.media_path = "history-tools.mp4"
        window.current_task.run_history = [
            TrackingRunRecord(
                "2026-07-13T01:00:00Z",
                0.2,
                "full",
                "complete",
                0,
                1,
                2,
                2,
                pipeline_config=config,
            ),
            TrackingRunRecord(
                "2026-07-13T01:05:00Z",
                0.1,
                "rerun",
                "partial",
                2,
                2,
                1,
                3,
                note="early end",
                pipeline_config=config,
            ),
            TrackingRunRecord(
                "2026-07-13T01:10:00Z",
                0.3,
                "full",
                "complete",
                0,
                3,
                4,
                4,
                pipeline_config=config,
            ),
        ]
        window._render_run_history(window.current_task)
        panel = window.run_history_panel
        self.assertEqual(panel.status_label.text(), "Showing 3 of 3 · 0 selected · Select 2 to compare")
        panel.list_widget.item(0).setSelected(True)
        panel.list_widget.item(1).setSelected(True)
        with patch("neo_tracker.ui.main_window.RunHistoryComparisonDialog.exec") as compare_exec:
            panel.compare_button.click()
        compare_exec.assert_called_once()

        panel.outcome_filter.setCurrentIndex(panel.outcome_filter.findData("complete"))
        self.assertEqual(panel.status_label.text(), "Showing 2 of 3 · 0 selected · Select 2 to compare")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "visible-runs.csv"
            with patch(
                "neo_tracker.ui.main_window.QFileDialog.getSaveFileName",
                return_value=(str(path), "CSV files (*.csv)"),
            ):
                panel.export_button.click()
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[1].startswith("1,2026-07-13T01:00:00Z,full,complete,"))
        self.assertTrue(lines[2].startswith("3,2026-07-13T01:10:00Z,full,complete,"))
        self.assertIn("Exported 2 tracking runs", window.statusBar().currentMessage())

    def test_calibrated_wavefront_manual_correction_and_rerun_use_world_units(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        combo = window.preset_combo
        combo.setCurrentIndex(combo.findData("wavefront"))
        frames = [vertical_edge_frame((40, 70, 3), 18 + i * 2) for i in range(5)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "wavefront.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._apply_calibration_rod((0.0, 0.0), (100.0, 0.0), 50.0, "cm")
        window._run_tracking()
        wait_for_tracking(window)
        headers = result_table_headers(window)
        self.assertIn("x_world (cm)", headers)
        self.assertIn("State columns: x_world (cm)", window.review_summary_label.text())

        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 12.0))

        anchor = window.current_task.pipeline.results[1]
        self.assertEqual(anchor.status, "manual")
        self.assertAlmostEqual(anchor.filtered_state["x_world"], 15.0)
        self.assertNotIn("x_px", anchor.filtered_state)
        self.assertIn("v_x_world", anchor.debug["filter"]["velocity"])

        window._ask_rerun_replacement = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        window._rerun_after_current_result()
        wait_for_tracking(window)

        results = window.current_task.pipeline.results
        self.assertEqual(len(results), len(frames))
        self.assertEqual(results[1].status, "manual")
        self.assertIn("x_world", results[2].filtered_state)
        self.assertNotIn("x_px", results[2].filtered_state)

    def test_review_edit_history_records_and_persists_actions(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(5)]
        reader = FakeReader(frames)
        bind_verified_fake_media(self, window, reader, "edit-history.mp4")
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._run_tracking()
        wait_for_tracking(window)

        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 35.0))
        window._ask_rerun_replacement = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
        window._rerun_after_current_result()
        wait_for_tracking(window)
        window.results_table.selectRow(3)
        QCoreApplication.processEvents()
        window._mark_current_result_lost()

        self.assertEqual(current_result_row(window), 3)
        self.assertEqual(window.review_selection_label.property("reviewTone"), "lost")
        lost_background = window.results_model.data(
            window.results_model.index(3, 0),
            Qt.ItemDataRole.BackgroundRole,
        )
        self.assertEqual(lost_background.name(), "#fff0f0")

        history_types = [entry["type"] for entry in window.current_task.edit_history]
        self.assertEqual(history_types, ["manual_correction", "rerun_after", "mark_lost"])
        history_text = "\n".join(window.edit_history_list.item(i).text() for i in range(window.edit_history_list.count()))
        self.assertIn("marked lost", history_text)
        self.assertIn("reran frames", history_text)
        self.assertIn("corrected point", history_text)

        panel = window.edit_history_panel
        panel.event_filter.setCurrentIndex(panel.event_filter.findData("mark_lost"))
        QCoreApplication.processEvents()
        self.assertEqual([selection.sequence for selection in panel.visible_edits], [3])
        panel.list_widget.setCurrentRow(0)
        panel.jump_button.click()
        QCoreApplication.processEvents()
        self.assertEqual(window.current_task.preview_frame_index, 3)
        self.assertEqual(current_result_row(window), 3)

        with tempfile.TemporaryDirectory() as export_dir:
            export_path = Path(export_dir) / "lost-edits.csv"
            with patch(
                "neo_tracker.ui.main_window.QFileDialog.getSaveFileName",
                return_value=(str(export_path), "CSV files (*.csv)"),
            ):
                panel.export_button.click()
            exported = export_path.read_text(encoding="utf-8")
            self.assertIn("3,", exported)
            self.assertIn("mark_lost", exported)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.ntproj"
            window._project_from_window(path).save(path)
            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            restored_types = [entry["type"] for entry in restored.current_task.edit_history]
            self.assertEqual(restored_types, history_types)
            restored_text = "\n".join(
                restored.edit_history_list.item(i).text() for i in range(restored.edit_history_list.count())
            )
            self.assertIn("marked lost", restored_text)

    def test_project_save_and_load_restores_task_results_and_calibration(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(4)]
        reader = FakeReader(frames)
        window.current_task.media_path = "synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._apply_calibration_rod((0.0, 0.0), (100.0, 0.0), 50.0, "cm")
        window._run_tracking()
        wait_for_tracking(window)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "experiment.ntproj"
            project = window._project_from_window(path)
            self.assertEqual(project.tasks[0].media_info["kind"], "video")
            self.assertEqual(project.tasks[0].media_info["frame_count"], len(frames))
            self.assertEqual(project.tasks[0].media_info["width"], frames[0].shape[1])
            self.assertEqual(project.tasks[0].tracking_outcome, "complete")
            self.assertEqual(project.tasks[0].tracking_note, "")
            self.assertEqual(len(project.tasks[0].run_history), 1)
            project.save(path)

            restored = NeoTrackerWindow()
            self.addCleanup(restored.close)
            restored._load_project(path)
            self.assertEqual(restored.project_name_label.text(), "Project: experiment.ntproj")
            self.assertEqual(len(restored.tasks), 1)
            self.assertEqual(restored.current_task.pipeline_key, "color_marker")
            self.assertEqual(len(restored.current_task.pipeline.results), len(frames))
            self.assertEqual(restored.current_task.tracking_outcome, "complete")
            self.assertEqual(len(restored.current_task.run_history), 1)
            self.assertIn("#1 Full · Complete", restored.run_history_list.item(0).text())
            self.assertEqual(restored.tracking_status_label.text(), "Tracked")
            self.assertEqual(restored.tracking_status_label.property("trackingOutcome"), "complete")
            self.assertAlmostEqual(restored.current_task.calibration_rod.unit_per_pixel(), 0.5)
            self.assertIn(
                "Tracking: x_world (cm)",
                [restored.analysis_source_combo.itemText(i) for i in range(restored.analysis_source_combo.count())],
            )

    def test_window_state_can_generate_markdown_report(self) -> None:
        window = NeoTrackerWindow()
        self.addCleanup(close_window_safely, window)
        frames = [red_dot_frame((50, 70, 3), 12 + i * 4, 20 + i) for i in range(3)]
        reader = FakeReader(frames)
        window.current_task.media_path = "synthetic.mp4"
        window.current_task.media_info = reader.info
        window._reader_for_task = lambda _task: reader  # type: ignore[method-assign]
        window._tracking_reader_for_path = lambda _path: reader  # type: ignore[method-assign]
        window.project_controller.media_probe = lambda _path: reader.info
        window._render_task()
        window._apply_calibration_rod((0.0, 0.0), (100.0, 0.0), 50.0, "cm")
        window._run_tracking()
        wait_for_tracking(window)
        window.results_table.selectRow(1)
        QCoreApplication.processEvents()
        window._manual_point_selected((30.0, 35.0))
        text = markdown_report(
            title=f"Neo-Tracker Report - {window.current_task.title()}",
            pipeline=window.current_task.pipeline,
            results=window.current_task.pipeline.results,
            media_path=window.current_task.media_path,
            media_info=window.current_task.media_info,
            roi=window._roi_config_for_task(window.current_task),
            calibration_rod=window._calibration_rod_to_dict(window.current_task.calibration_rod),
            edit_history=[dict(entry) for entry in window.current_task.edit_history],
            run_history=list(window.current_task.run_history),
        )
        self.assertIn("synthetic.mp4", text)
        self.assertIn("Calibration rod", text)
        self.assertIn("unit=cm", text)
        self.assertIn("Average confidence", text)
        self.assertIn("State Units", text)
        self.assertIn("x_world: cm", text)
        self.assertIn("Filter Shift", text)
        self.assertIn("Edit History", text)
        self.assertIn("manual_correction", text)
        self.assertIn("Tracking Run History", text)
        self.assertIn("| 1 |", text)


if __name__ == "__main__":
    unittest.main()
