from __future__ import annotations

import time
import unittest
from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.application.task_supervisor import TaskSupervisor
from neo_tracker.application.tracking_coordinator import (
    TrackingCoordinator,
    TrackingRequest,
)
from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaInfo
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.project_controller import DesktopTask
from neo_tracker.ui.tracking_worker import TRACKING_SOURCE_CHANGED_PREFIX, TrackingProgress


class _ControlledWorker(QObject):
    progress = Signal(object)
    completed = Signal(int, bool, bool, str)
    failed = Signal(str, int)

    def __init__(self, start_frame: int) -> None:
        super().__init__()
        self.start_frame = int(start_frame)
        self.release = Event()
        self.cancel_requested = Event()
        self.failure = ""
        self.completed_count = self.start_frame
        self.cancelled = False
        self.ended_early = False
        self.note = ""

    @Slot()
    def run(self) -> None:
        self.release.wait(timeout=1.0)
        if self.failure:
            self.failed.emit(self.failure, self.completed_count)
        else:
            self.completed.emit(
                self.completed_count,
                self.cancelled or self.cancel_requested.is_set(),
                self.ended_early,
                self.note,
            )

    def request_cancel(self) -> None:
        self.cancel_requested.set()
        self.release.set()


def _pump_until(predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    QCoreApplication.processEvents()
    if not predicate():
        raise AssertionError("coordinator did not reach the expected state")


def _result(frame_index: int) -> TrackerResult:
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 10.0,
        state={"x_px": float(frame_index), "y_px": 0.0},
        filtered_state={"x_px": float(frame_index), "y_px": 0.0},
        confidence=1.0,
        status="tracked",
    )


class TrackingCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_coordinator(self) -> tuple[TrackingCoordinator, TaskSupervisor]:
        supervisor = TaskSupervisor()
        coordinator = TrackingCoordinator(
            supervisor,
            worker_factory=lambda request, _job: _ControlledWorker(request.start_frame),
        )
        return coordinator, supervisor

    @staticmethod
    def make_task() -> DesktopTask:
        return DesktopTask(
            media_path="/media/source.mp4",
            pipeline_key="color_marker",
            pipeline=color_marker_preset(),
            media_info=MediaInfo(fps=10.0, frame_count=8, available=True),
        )

    @staticmethod
    def request(
        task: DesktopTask,
        *,
        mode: str = "full",
        start_frame: int = 0,
        prefix: tuple[TrackerResult, ...] = (),
    ) -> TrackingRequest:
        return TrackingRequest(
            task=task,
            frame_count=8,
            fps=10.0,
            mode=mode,
            start_frame=start_frame,
            prefix=prefix,
            anchor_frame=(start_frame - 1 if mode == "rerun" else None),
            previous_analysis_run=None,
            reader_factory=lambda: object(),
            process_isolation=False,
            isolated_reader_factory=None,
        )

    def test_full_replacement_commits_only_after_first_valid_progress(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        task.pipeline.results = [_result(0), _result(1)]
        task.edit_history = [{"type": "manual_correction", "frame_index": 1}]

        self.assertTrue(coordinator.start(self.request(task)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        self.assertEqual(len(task.edit_history), 1)
        coordinator.handle_progressed(
            TrackingProgress(
                completed=1,
                total=8,
                start_frame=0,
                elapsed_s=0.1,
                input_s=0.02,
                processing_s=0.08,
            )
        )

        self.assertEqual(task.edit_history, [])
        self.assertTrue(coordinator.job.result_replacement_committed)
        coordinator.worker.completed_count = 1
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)
        self.assertTrue(supervisor.idle)

    def test_rerun_progress_supersedes_only_tail_edits(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        prefix = (_result(0), _result(1))
        task.pipeline.results = [*prefix, _result(2), _result(3)]
        task.edit_history = [
            {"type": "manual_correction", "frame_index": 1},
            {"type": "mark_lost", "frame_index": 3},
        ]

        self.assertTrue(
            coordinator.start(
                self.request(task, mode="rerun", start_frame=2, prefix=prefix)
            )
        )
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.handle_progressed(
            TrackingProgress(
                completed=3,
                total=8,
                start_frame=2,
                elapsed_s=0.1,
                input_s=0.02,
                processing_s=0.08,
            )
        )

        self.assertNotIn("superseded_at", task.edit_history[0])
        self.assertEqual(task.edit_history[1]["superseded_by_rerun_start_frame"], 2)
        self.assertEqual(coordinator.job.superseded_edit_count, 1)
        coordinator.worker.completed_count = 3
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

    def test_source_change_is_classified_and_delivered_after_thread_stop(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        terminals: list[object] = []
        coordinator.terminal_ready.connect(terminals.append)

        self.assertTrue(coordinator.start(self.request(task)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        worker = coordinator.worker
        worker.completed_count = 1
        worker.failure = f"{TRACKING_SOURCE_CHANGED_PREFIX}changed after checkpoint"
        worker.release.set()
        self.assertEqual(terminals, [])
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(len(terminals), 1)
        self.assertTrue(terminals[0].failed)
        self.assertTrue(terminals[0].source_changed)
        self.assertIn("All newly produced results", terminals[0].completion_note)

    def test_zero_frame_cancel_restores_prior_state_and_rejects_late_progress(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        previous_results = [_result(0), _result(1)]
        previous_edits = [{"type": "manual_correction", "frame_index": 1}]
        task.pipeline.results = list(previous_results)
        task.edit_history = [dict(entry) for entry in previous_edits]
        task.tracking_outcome = "complete"
        task.tracking_note = "trusted"
        progressed: list[object] = []
        coordinator.progressed.connect(lambda _job, progress: progressed.append(progress))

        self.assertTrue(coordinator.start(self.request(task)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        coordinator.handle_completed(0, True, False, "")
        coordinator.handle_progressed(
            TrackingProgress(
                completed=1,
                total=8,
                start_frame=0,
                elapsed_s=0.1,
                input_s=0.02,
                processing_s=0.08,
            )
        )

        self.assertEqual(progressed, [])
        self.assertEqual(task.pipeline.results, previous_results)
        self.assertEqual(task.edit_history, previous_edits)
        self.assertEqual(task.tracking_outcome, "complete")
        self.assertEqual(task.tracking_note, "trusted")
        self.assertTrue(coordinator.job.previous_result_state_restored)
        self.assertEqual(task.run_history[-1].outcome, "canceled")
        self.assertEqual(task.run_history[-1].processed_frames, 0)
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

    def test_active_rerun_cancel_keeps_checkpoint_and_supersedes_tail_edit(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        prefix = (_result(0), _result(1))
        task.pipeline.results = [*prefix, _result(2), _result(3)]
        task.edit_history = [
            {"type": "manual_correction", "frame_index": 1},
            {"type": "mark_lost", "frame_index": 3},
        ]

        self.assertTrue(
            coordinator.start(
                self.request(task, mode="rerun", start_frame=2, prefix=prefix)
            )
        )
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        task.pipeline.results = [*prefix, _result(2)]
        coordinator.handle_progressed(
            TrackingProgress(3, 8, 2, 0.1, 0.02, 0.08)
        )
        coordinator.handle_completed(3, True, False, "checkpoint kept")

        self.assertEqual([result.frame_index for result in task.pipeline.results], [0, 1, 2])
        self.assertEqual(task.run_history[-1].outcome, "canceled")
        self.assertEqual(task.run_history[-1].processed_frames, 1)
        self.assertEqual(task.edit_history[1]["superseded_by_rerun_start_frame"], 2)
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

    def test_active_rerun_failure_discards_partial_tail_but_keeps_history(self) -> None:
        coordinator, _supervisor = self.make_coordinator()
        self.addCleanup(coordinator.close)
        task = self.make_task()
        prefix = (_result(0), _result(1))
        task.pipeline.results = [*prefix, _result(2), _result(3)]
        task.edit_history = [{"type": "mark_lost", "frame_index": 3}]

        self.assertTrue(
            coordinator.start(
                self.request(task, mode="rerun", start_frame=2, prefix=prefix)
            )
        )
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        task.pipeline.results = [*prefix, _result(2)]
        coordinator.handle_progressed(
            TrackingProgress(3, 8, 2, 0.1, 0.02, 0.08)
        )
        coordinator.handle_failed("synthetic rerun failure", 3)

        self.assertEqual(task.pipeline.results, list(prefix))
        self.assertEqual(task.run_history[-1].outcome, "failed")
        self.assertEqual(task.run_history[-1].processed_frames, 1)
        self.assertEqual(task.edit_history[0]["superseded_by_rerun_start_frame"], 2)
        coordinator.worker.release.set()
        _pump_until(lambda: not coordinator.busy)

    def test_close_cancels_worker_and_releases_supervisor_after_terminal(self) -> None:
        coordinator, supervisor = self.make_coordinator()
        task = self.make_task()
        terminals: list[object] = []
        coordinator.terminal_ready.connect(terminals.append)

        self.assertTrue(coordinator.start(self.request(task)))
        _pump_until(lambda: coordinator.thread is not None and coordinator.thread.isRunning())
        supervisor.begin_close()
        coordinator.close()
        self.assertTrue(coordinator.worker.cancel_requested.is_set())
        _pump_until(lambda: not coordinator.busy)

        self.assertEqual(len(terminals), 1)
        self.assertTrue(terminals[0].cancelled)
        self.assertTrue(supervisor.ready_to_close)


if __name__ == "__main__":
    unittest.main()
