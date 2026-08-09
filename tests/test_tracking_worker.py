from __future__ import annotations

import os
import threading
import time
import unittest
from functools import partial
from pathlib import Path
from pickle import dumps as actual_pickle_dumps
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt

from neo_tracker.media import probe_media_identity
from neo_tracker.presets import color_marker_preset
from neo_tracker.roi import RectangularROI
from neo_tracker.ui.tracking_worker import (
    TRACKING_SOURCE_CHANGED_PREFIX,
    PrefetchedFrameStream,
    TrackingProgress,
    TrackingWorker,
)


class _Filter:
    def prime(self, _result) -> None:
        return None


class _Pipeline:
    def __init__(self) -> None:
        self.results: list[object] = []
        self.tracker_filter = _Filter()
        self.debug_history_max_bytes = 1024

    def reset(self) -> None:
        self.results.clear()

    def process_frame(self, _frame, frame_index: int, _time_s: float) -> None:
        time.sleep(0.0005)
        self.results.append(frame_index)

    def debug_history_usage(self) -> tuple[int, int]:
        return len(self.results) * 100, len(self.results)


class _ProcessingReader:
    def __init__(self) -> None:
        self.public_reads = 0
        self.processing_reads = 0
        self.closed = False

    def read_frame(self, _frame_index: int) -> np.ndarray:
        self.public_reads += 1
        return np.zeros((4, 5, 3), dtype=np.uint8)

    def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
        self.processing_reads += 1
        time.sleep(0.0005)
        return np.zeros((4, 5, 3), dtype=np.uint8)

    def close(self) -> None:
        self.closed = True


class _FailingProcessingReader(_ProcessingReader):
    def __init__(self, fail_at: int) -> None:
        super().__init__()
        self.fail_at = int(fail_at)

    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        if frame_index >= self.fail_at:
            raise RuntimeError(f"synthetic input failure at frame {frame_index}")
        return super().read_frame_for_processing(frame_index)


class _CloseFailingProcessingReader(_ProcessingReader):
    def close(self) -> None:
        self.closed = True
        raise RuntimeError("synthetic close failure")


class _ReadAndCloseFailingProcessingReader(_CloseFailingProcessingReader):
    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        self.processing_reads += 1
        raise RuntimeError(f"synthetic input failure at frame {frame_index}")


class _SlowPipeline(_Pipeline):
    def process_frame(self, _frame, frame_index: int, _time_s: float) -> None:
        time.sleep(0.012)
        self.results.append(frame_index)


class _SlowProcessingReader(_ProcessingReader):
    def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
        self.processing_reads += 1
        time.sleep(0.01)
        return np.zeros((4, 5, 3), dtype=np.uint8)


class _FastPipeline(_Pipeline):
    def process_frame(self, _frame, frame_index: int, _time_s: float) -> None:
        self.results.append(frame_index)


class _FastProcessingReader(_ProcessingReader):
    def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
        self.processing_reads += 1
        return np.zeros((4, 5, 3), dtype=np.uint8)


class _GatedCloseProcessingReader(_FastProcessingReader):
    def __init__(self, close_started: Event, release_close: Event) -> None:
        super().__init__()
        self.close_started = close_started
        self.release_close = release_close

    def close(self) -> None:
        self.close_started.set()
        self.release_close.wait(timeout=2.0)
        super().close()


class _SpawnProcessingReader:
    """Module-level reader so multiprocessing spawn can import it by name."""

    def read_frame(self, frame_index: int) -> np.ndarray:
        return self.read_frame_for_processing(frame_index)

    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        frame = np.zeros((12, 16, 3), dtype=np.uint8)
        frame[3:7, 4 + frame_index : 7 + frame_index, 0] = 255
        return frame

    def close(self) -> None:
        return None


class _CheckpointThenHangReader(_SpawnProcessingReader):
    def __init__(self, marker_path: str, completed_before_hang: int) -> None:
        self.marker_path = str(marker_path)
        self.completed_before_hang = int(completed_before_hang)

    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        if frame_index >= self.completed_before_hang:
            Path(self.marker_path).touch()
            Event().wait()
            raise AssertionError("unreachable")
        return super().read_frame_for_processing(frame_index)


class _CheckpointThenReplaceAndHangReader(_SpawnProcessingReader):
    def __init__(self, marker_path: str, source_path: str) -> None:
        self.marker_path = str(marker_path)
        self.source_path = str(source_path)

    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        if frame_index >= 1:
            Path(self.source_path).write_bytes(b"replacement source")
            Path(self.marker_path).touch()
            Event().wait()
            raise AssertionError("unreachable")
        return super().read_frame_for_processing(frame_index)


class _FileGatedCloseReader(_SpawnProcessingReader):
    def __init__(self, close_started_path: str, release_close_path: str) -> None:
        self.close_started_path = str(close_started_path)
        self.release_close_path = str(release_close_path)

    def close(self) -> None:
        Path(self.close_started_path).touch()
        deadline = time.monotonic() + 5.0
        while not Path(self.release_close_path).exists() and time.monotonic() < deadline:
            time.sleep(0.005)


def _exit_tracking_process() -> None:
    """Simulate a native decoder crash that bypasses Python cleanup."""

    os._exit(7)


class TrackingWorkerTests(unittest.TestCase):
    def test_isolated_full_and_rerun_pickle_child_input_once_and_match_reference(self) -> None:
        def result_signature(pipeline) -> list[tuple[object, ...]]:
            return [
                (
                    result.frame_index,
                    result.time_s,
                    result.state,
                    result.filtered_state,
                    result.confidence,
                    result.status,
                )
                for result in pipeline.results
            ]

        reference = color_marker_preset(
            roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
            tolerance=0.08,
        )
        TrackingWorker(
            pipeline=reference,
            reader_factory=_SpawnProcessingReader,
            frame_count=4,
            fps=25.0,
            process_isolation=False,
        ).run()
        expected = result_signature(reference)

        for mode in ("full", "rerun"):
            with self.subTest(mode=mode):
                pipeline = color_marker_preset(
                    roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
                    tolerance=0.08,
                )
                start_frame = 0
                prefix = None
                if mode == "rerun":
                    TrackingWorker(
                        pipeline=pipeline,
                        reader_factory=_SpawnProcessingReader,
                        frame_count=2,
                        fps=25.0,
                        process_isolation=False,
                    ).run()
                    start_frame = 2
                    prefix = list(pipeline.results)
                worker = TrackingWorker(
                    pipeline=pipeline,
                    reader_factory=_SpawnProcessingReader,
                    isolated_reader_factory=_SpawnProcessingReader,
                    frame_count=4,
                    fps=25.0,
                    start_frame=start_frame,
                    prefix=prefix,
                    process_isolation=True,
                )
                with patch(
                    "neo_tracker.ui.tracking_worker.pickle_dumps",
                    wraps=actual_pickle_dumps,
                ) as serialize_child_input:
                    worker.run()

                self.assertEqual(serialize_child_input.call_count, 1)
                self.assertIsNone(worker._isolated_child_input)
                self.assertEqual(result_signature(pipeline), expected)

    def test_isolated_child_exit_without_terminal_emits_failure_and_returns(self) -> None:
        factory = partial(_exit_tracking_process)
        failures: list[tuple[str, int]] = []
        completions: list[tuple[int, bool, bool, str]] = []
        worker = TrackingWorker(
            pipeline=color_marker_preset(),
            reader_factory=factory,  # type: ignore[arg-type]
            isolated_reader_factory=factory,  # type: ignore[arg-type]
            frame_count=1,
            fps=25.0,
            process_isolation=True,
            prefetch_frames=0,
        )
        worker.failed.connect(
            lambda message, completed: failures.append((message, completed)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.completed.connect(
            lambda *args: completions.append(args),
            Qt.ConnectionType.DirectConnection,
        )
        thread = Thread(target=worker.run, daemon=True)

        thread.start()
        thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive(), "EOF from a crashed child must not spin forever")
        self.assertEqual(completions, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("without a terminal result", failures[0][0])
        self.assertIn("exit code 7", failures[0][0])
        self.assertEqual(failures[0][1], 0)

    def test_isolated_setup_exception_is_converted_to_one_failure_signal(self) -> None:
        failures: list[tuple[str, int]] = []
        completions: list[tuple[int, bool, bool, str]] = []
        worker = TrackingWorker(
            pipeline=color_marker_preset(),
            reader_factory=_SpawnProcessingReader,
            isolated_reader_factory=_SpawnProcessingReader,
            frame_count=1,
            fps=25.0,
            process_isolation=True,
        )
        worker.failed.connect(
            lambda message, completed: failures.append((message, completed)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.completed.connect(
            lambda *args: completions.append(args),
            Qt.ConnectionType.DirectConnection,
        )

        with patch(
            "neo_tracker.ui.tracking_worker.get_context",
            side_effect=OSError("synthetic multiprocessing resource exhaustion"),
        ):
            worker.run()

        self.assertEqual(completions, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("tracking worker failed unexpectedly", failures[0][0])
        self.assertIn("resource exhaustion", failures[0][0])
        self.assertEqual(failures[0][1], 0)

    def test_precancelled_isolated_worker_skips_pickle_capability_check(self) -> None:
        pipeline = color_marker_preset()
        pipeline.metadata["not_spawn_serializable"] = lambda: None
        failures: list[tuple[str, int]] = []
        completions: list[tuple[int, bool, bool, str]] = []
        worker = TrackingWorker(
            pipeline=pipeline,
            reader_factory=_SpawnProcessingReader,
            isolated_reader_factory=_SpawnProcessingReader,
            frame_count=1,
            fps=25.0,
            process_isolation=True,
        )
        worker.failed.connect(
            lambda message, completed: failures.append((message, completed)),
            Qt.ConnectionType.DirectConnection,
        )
        worker.completed.connect(
            lambda *args: completions.append(args),
            Qt.ConnectionType.DirectConnection,
        )

        worker.request_cancel()
        worker.run()

        self.assertEqual(failures, [])
        self.assertEqual(completions, [(0, True, False, "")])
        self.assertEqual(worker.process_isolation_fallback_reason, "")

    def test_threaded_cancel_during_close_keeps_completed_outcome(self) -> None:
        for prefetch_frames in (0, 1):
            with self.subTest(prefetch_frames=prefetch_frames):
                close_started = Event()
                release_close = Event()
                reader = _GatedCloseProcessingReader(close_started, release_close)
                pipeline = _FastPipeline()
                completions: list[tuple[int, bool, bool, str]] = []
                worker = TrackingWorker(
                    pipeline=pipeline,  # type: ignore[arg-type]
                    reader_factory=lambda: reader,
                    frame_count=2,
                    fps=25.0,
                    prefetch_frames=prefetch_frames,
                )
                worker.completed.connect(
                    lambda *args: completions.append(args),
                    Qt.ConnectionType.DirectConnection,
                )
                thread = Thread(target=worker.run)
                thread.start()
                self.assertTrue(close_started.wait(timeout=1.0))
                deadline = time.monotonic() + 1.0
                while len(pipeline.results) < 2 and time.monotonic() < deadline:
                    time.sleep(0.001)
                self.assertEqual(len(pipeline.results), 2)

                worker.request_cancel()
                release_close.set()
                thread.join(timeout=2.0)

                self.assertFalse(thread.is_alive())
                self.assertEqual(completions, [(2, False, False, "")])

    def test_isolated_cancel_during_close_keeps_completed_outcome(self) -> None:
        with TemporaryDirectory() as directory:
            close_started_path = str(Path(directory) / "close-started")
            release_close_path = str(Path(directory) / "release-close")
            factory = partial(
                _FileGatedCloseReader,
                close_started_path,
                release_close_path,
            )
            pipeline = color_marker_preset(
                roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
                tolerance=0.08,
            )
            completions: list[tuple[int, bool, bool, str]] = []
            failures: list[tuple[str, int]] = []
            worker = TrackingWorker(
                pipeline=pipeline,
                reader_factory=factory,
                isolated_reader_factory=factory,
                frame_count=2,
                fps=25.0,
                process_isolation=True,
                cancel_grace_s=1.0,
                checkpoint_frames=1,
            )
            worker.completed.connect(
                lambda *args: completions.append(args),
                Qt.ConnectionType.DirectConnection,
            )
            worker.failed.connect(
                lambda message, completed: failures.append((message, completed)),
                Qt.ConnectionType.DirectConnection,
            )
            thread = Thread(target=worker.run)
            thread.start()
            deadline = time.monotonic() + 5.0
            while not Path(close_started_path).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(Path(close_started_path).exists())
            while len(pipeline.results) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(pipeline.results), 2)

            worker.request_cancel()
            Path(release_close_path).touch()
            thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(completions, [(2, False, False, "")])

    def test_forced_isolated_cancel_after_all_frames_keeps_completed_outcome(self) -> None:
        with TemporaryDirectory() as directory:
            close_started_path = str(Path(directory) / "close-started")
            release_close_path = str(Path(directory) / "release-close")
            factory = partial(
                _FileGatedCloseReader,
                close_started_path,
                release_close_path,
            )
            pipeline = color_marker_preset(
                roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
                tolerance=0.08,
            )
            completions: list[tuple[int, bool, bool, str]] = []
            failures: list[tuple[str, int]] = []
            worker = TrackingWorker(
                pipeline=pipeline,
                reader_factory=factory,
                isolated_reader_factory=factory,
                frame_count=2,
                fps=25.0,
                process_isolation=True,
                cancel_grace_s=0.05,
                kill_grace_s=0.10,
                checkpoint_frames=1,
            )
            worker.completed.connect(
                lambda *args: completions.append(args),
                Qt.ConnectionType.DirectConnection,
            )
            worker.failed.connect(
                lambda message, completed: failures.append((message, completed)),
                Qt.ConnectionType.DirectConnection,
            )
            thread = Thread(target=worker.run)
            thread.start()
            deadline = time.monotonic() + 5.0
            while not Path(close_started_path).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(Path(close_started_path).exists())
            while len(pipeline.results) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(pipeline.results), 2)

            cancel_started = time.monotonic()
            worker.request_cancel()
            thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - cancel_started, 1.0)
            self.assertEqual(failures, [])
            self.assertEqual(len(completions), 1)
            self.assertEqual(completions[0][:3], (2, False, False))
            self.assertIn("completed all 2 frames", completions[0][3])
            self.assertIn("unresponsive decoder subprocess", completions[0][3])

    def test_builtin_pipeline_runs_in_spawn_process_and_returns_results(self) -> None:
        pipeline = color_marker_preset(
            roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
            tolerance=0.08,
        )
        progress: list[TrackingProgress] = []
        completions: list[tuple[int, bool, bool, str]] = []
        failures: list[tuple[str, int]] = []
        worker = TrackingWorker(
            pipeline=pipeline,
            reader_factory=_SpawnProcessingReader,
            isolated_reader_factory=_SpawnProcessingReader,
            frame_count=4,
            fps=25.0,
            process_isolation=True,
            checkpoint_frames=2,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)
        worker.completed.connect(
            lambda *args: completions.append(args),
            Qt.ConnectionType.DirectConnection,
        )
        worker.failed.connect(
            lambda message, completed: failures.append((message, completed)),
            Qt.ConnectionType.DirectConnection,
        )

        worker.run()

        self.assertTrue(worker.process_isolation_used)
        self.assertEqual(failures, [])
        self.assertEqual(completions, [(4, False, False, "")])
        self.assertEqual([result.frame_index for result in pipeline.results], [0, 1, 2, 3])
        self.assertTrue(progress)
        self.assertTrue(all(sample.process_isolated for sample in progress))

    def test_isolated_cancel_terminates_hung_decoder_and_keeps_checkpoints(self) -> None:
        with TemporaryDirectory() as directory:
            marker_path = str(Path(directory) / "decoder-hung")
            pipeline = color_marker_preset(
                roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
                tolerance=0.08,
            )
            completions: list[tuple[int, bool, bool, str]] = []
            failures: list[tuple[str, int]] = []
            reader_factory = partial(_CheckpointThenHangReader, marker_path, 2)
            worker = TrackingWorker(
                pipeline=pipeline,
                reader_factory=reader_factory,
                isolated_reader_factory=reader_factory,
                frame_count=8,
                fps=25.0,
                process_isolation=True,
                prefetch_frames=1,
                checkpoint_frames=1,
                cancel_grace_s=0.05,
                kill_grace_s=0.10,
            )
            worker.completed.connect(
                lambda *args: completions.append(args),
                Qt.ConnectionType.DirectConnection,
            )
            worker.failed.connect(
                lambda message, completed: failures.append((message, completed)),
                Qt.ConnectionType.DirectConnection,
            )
            thread = Thread(target=worker.run)
            thread.start()
            deadline = time.monotonic() + 5.0
            while not Path(marker_path).exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(Path(marker_path).exists(), "spawned decoder did not reach the synthetic hang")

            cancel_started = time.monotonic()
            worker.request_cancel()
            thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - cancel_started, 1.0)
            self.assertTrue(worker.process_isolation_used)
            self.assertEqual(failures, [])
            self.assertEqual(len(completions), 1)
            self.assertEqual(completions[0][:3], (2, True, False))
            self.assertIn("unresponsive decoder subprocess", completions[0][3])
            self.assertEqual([result.frame_index for result in pipeline.results], [0, 1])

    def test_required_process_isolation_fails_closed_for_third_party_pipeline(self) -> None:
        pipeline = _FastPipeline()
        reader = _FastProcessingReader()
        failures: list[tuple[str, int]] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            isolated_reader_factory=_SpawnProcessingReader,
            frame_count=2,
            fps=25.0,
            process_isolation=True,
        )
        worker.failed.connect(
            lambda message, completed: failures.append((message, completed)),
            Qt.ConnectionType.DirectConnection,
        )

        worker.run()

        self.assertFalse(worker.process_isolation_used)
        self.assertIn("third-party pipeline", worker.process_isolation_fallback_reason)
        self.assertEqual(pipeline.results, [])
        self.assertFalse(reader.closed)
        self.assertEqual(len(failures), 1)
        self.assertIn("process isolation is required but unavailable", failures[0][0])
        self.assertEqual(failures[0][1], 0)

    def test_explicit_thread_execution_remains_available_for_third_party_pipeline(self) -> None:
        pipeline = _FastPipeline()
        reader = _FastProcessingReader()
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=2,
            fps=25.0,
            process_isolation=False,
        )

        worker.run()

        self.assertFalse(worker.process_isolation_used)
        self.assertEqual(pipeline.results, [0, 1])
        self.assertTrue(reader.closed)

    def test_threaded_tracking_detects_identity_drift_after_processing(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.bin"
            source_path.write_bytes(b"original source")
            expected_identity = probe_media_identity(source_path)
            self.assertIsNotNone(expected_identity)

            class ReplacingReader(_FastProcessingReader):
                def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
                    frame = super().read_frame_for_processing(frame_index)
                    source_path.write_bytes(b"replacement source")
                    return frame

            failures: list[tuple[str, int]] = []
            completions: list[tuple[int, bool, bool, str]] = []
            worker = TrackingWorker(
                pipeline=_FastPipeline(),  # type: ignore[arg-type]
                reader_factory=ReplacingReader,
                frame_count=1,
                fps=25.0,
                prefetch_frames=0,
                expected_source_path=str(source_path),
                expected_source_identity=expected_identity,
            )
            worker.failed.connect(
                lambda message, completed: failures.append((message, completed)),
                Qt.ConnectionType.DirectConnection,
            )
            worker.completed.connect(
                lambda *args: completions.append(args),
                Qt.ConnectionType.DirectConnection,
            )

            worker.run()

            self.assertEqual(completions, [])
            self.assertEqual(len(failures), 1)
            self.assertTrue(failures[0][0].startswith(TRACKING_SOURCE_CHANGED_PREFIX))
            self.assertEqual(failures[0][1], 1)

    def test_forced_isolated_cancel_reprobes_and_detects_identity_drift(self) -> None:
        with TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.bin"
            marker_path = Path(directory) / "decoder-hung"
            source_path.write_bytes(b"original source")
            expected_identity = probe_media_identity(source_path)
            self.assertIsNotNone(expected_identity)
            pipeline = color_marker_preset(
                roi=RectangularROI(0.0, 0.0, 16.0, 12.0),
                tolerance=0.08,
            )
            reader_factory = partial(
                _CheckpointThenReplaceAndHangReader,
                str(marker_path),
                str(source_path),
            )
            failures: list[tuple[str, int]] = []
            completions: list[tuple[int, bool, bool, str]] = []
            worker = TrackingWorker(
                pipeline=pipeline,
                reader_factory=reader_factory,
                isolated_reader_factory=reader_factory,
                frame_count=4,
                fps=25.0,
                process_isolation=True,
                prefetch_frames=0,
                checkpoint_frames=1,
                cancel_grace_s=0.05,
                kill_grace_s=0.10,
                expected_source_path=str(source_path),
                expected_source_identity=expected_identity,
            )
            worker.failed.connect(
                lambda message, completed: failures.append((message, completed)),
                Qt.ConnectionType.DirectConnection,
            )
            worker.completed.connect(
                lambda *args: completions.append(args),
                Qt.ConnectionType.DirectConnection,
            )
            thread = Thread(target=worker.run)
            thread.start()
            deadline = time.monotonic() + 5.0
            while not marker_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker_path.exists())

            worker.request_cancel()
            thread.join(timeout=2.0)

            self.assertFalse(thread.is_alive())
            self.assertEqual(completions, [])
            self.assertEqual(len(failures), 1)
            self.assertTrue(failures[0][0].startswith(TRACKING_SOURCE_CHANGED_PREFIX))
            self.assertEqual(failures[0][1], 1)

    def test_worker_rejects_invalid_fps_before_reset_or_reader_creation(self) -> None:
        for invalid_fps in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(fps=invalid_fps):
                class ResetCountingPipeline(_Pipeline):
                    def __init__(self) -> None:
                        super().__init__()
                        self.reset_calls = 0

                    def reset(self) -> None:
                        self.reset_calls += 1
                        super().reset()

                pipeline = ResetCountingPipeline()
                reader_creations: list[bool] = []
                failures: list[tuple[str, int]] = []
                completions: list[tuple[int, bool, bool, str]] = []
                worker = TrackingWorker(
                    pipeline=pipeline,  # type: ignore[arg-type]
                    reader_factory=lambda: reader_creations.append(True),  # type: ignore[arg-type,return-value]
                    frame_count=3,
                    fps=invalid_fps,
                )
                worker.failed.connect(
                    lambda message, completed: failures.append((message, completed)),
                    Qt.ConnectionType.DirectConnection,
                )
                worker.completed.connect(
                    lambda *args: completions.append(args),
                    Qt.ConnectionType.DirectConnection,
                )

                worker.run()

                self.assertEqual(pipeline.reset_calls, 0)
                self.assertEqual(pipeline.results, [])
                self.assertEqual(reader_creations, [])
                self.assertEqual(completions, [])
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0][1], 0)
                self.assertIn("finite and positive", failures[0][0])

    def test_precancelled_worker_does_not_reset_results_or_create_reader(self) -> None:
        for prefetch_frames in (0, 1):
            with self.subTest(prefetch_frames=prefetch_frames):
                class ResetCountingPipeline(_Pipeline):
                    def __init__(self) -> None:
                        super().__init__()
                        self.results = ["existing"]
                        self.reset_calls = 0

                    def reset(self) -> None:
                        self.reset_calls += 1
                        super().reset()

                pipeline = ResetCountingPipeline()
                reader_creations: list[bool] = []
                completions: list[tuple[int, bool, bool, str]] = []
                failures: list[tuple[str, int]] = []
                worker = TrackingWorker(
                    pipeline=pipeline,  # type: ignore[arg-type]
                    reader_factory=lambda: reader_creations.append(True),  # type: ignore[arg-type,return-value]
                    frame_count=3,
                    fps=25.0,
                    prefetch_frames=prefetch_frames,
                )
                worker.completed.connect(
                    lambda *args: completions.append(args),
                    Qt.ConnectionType.DirectConnection,
                )
                worker.failed.connect(
                    lambda message, completed: failures.append((message, completed)),
                    Qt.ConnectionType.DirectConnection,
                )

                worker.request_cancel()
                worker.run()

                self.assertEqual(pipeline.reset_calls, 0)
                self.assertEqual(pipeline.results, ["existing"])
                self.assertEqual(reader_creations, [])
                self.assertEqual(completions, [(0, True, False, "")])
                self.assertEqual(failures, [])

    def test_worker_throttles_fast_progress_but_keeps_first_and_terminal_samples(self) -> None:
        pipeline = _FastPipeline()
        reader = _FastProcessingReader()
        progress: list[TrackingProgress] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=72,
            fps=30.0,
            prefetch_frames=0,
            progress_interval_s=3600.0,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)

        worker.run()

        self.assertEqual([sample.completed for sample in progress], [1, 72])
        self.assertEqual(progress[-1].processed_frames, 72)
        self.assertEqual(pipeline.results, list(range(72)))
        self.assertTrue(reader.closed)

    def test_zero_progress_interval_preserves_stride_updates_for_benchmarks(self) -> None:
        pipeline = _FastPipeline()
        reader = _FastProcessingReader()
        progress: list[TrackingProgress] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=72,
            fps=30.0,
            prefetch_frames=0,
            progress_interval_s=0.0,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)

        worker.run()

        self.assertEqual(len(progress), 72)
        self.assertEqual(progress[0].completed, 1)
        self.assertEqual(progress[-1].completed, 72)

    def test_prefetch_capacity_counts_the_inflight_decode(self) -> None:
        reader = _ProcessingReader()
        cancel_requested = Event()
        stream = PrefetchedFrameStream(
            reader_factory=lambda: reader,
            start_frame=0,
            frame_count=10,
            cancel_requested=cancel_requested,
            prefetch_frames=1,
        )
        stream.start()
        try:
            deadline = time.monotonic() + 1.0
            while reader.processing_reads < 1 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(reader.processing_reads, 1)
            time.sleep(0.03)
            self.assertEqual(
                reader.processing_reads,
                1,
                "a one-frame prefetch must not decode a second retained frame",
            )

            prefetched = stream.read(0)
            self.assertIsNotNone(prefetched)
            deadline = time.monotonic() + 1.0
            while reader.processing_reads < 2 and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(reader.processing_reads, 2)
        finally:
            cancel_requested.set()
            stream.close()
        self.assertTrue(reader.closed)

    def test_prefetch_cancel_after_slot_reservation_does_not_start_decode(self) -> None:
        slot_reserved = Event()
        continue_after_cancel = Event()
        cancel_requested = Event()
        reader_creations: list[bool] = []

        class GatedReserveStream(PrefetchedFrameStream):
            def _reserve_slot(self) -> bool:
                reserved = super()._reserve_slot()
                if reserved:
                    slot_reserved.set()
                    continue_after_cancel.wait(timeout=1.0)
                return reserved

        stream = GatedReserveStream(
            reader_factory=lambda: reader_creations.append(True),  # type: ignore[arg-type,return-value]
            start_frame=0,
            frame_count=1,
            cancel_requested=cancel_requested,
            prefetch_frames=1,
        )
        stream.start()
        self.assertTrue(slot_reserved.wait(timeout=1.0))

        cancel_requested.set()
        continue_after_cancel.set()
        stream.close()

        self.assertEqual(reader_creations, [])

    def test_worker_keeps_roi_local_responses_for_bounded_review_history(self) -> None:
        frame = np.zeros((80, 110, 3), dtype=np.uint8)
        frame[43:50, 72:81, 0] = 255

        class FixedFrameReader:
            def __init__(self) -> None:
                self.closed = False

            def read_frame(self, _frame_index: int) -> np.ndarray:
                return frame

            def read_frame_for_processing(self, _frame_index: int) -> np.ndarray:
                return frame

            def close(self) -> None:
                self.closed = True

        pipeline = color_marker_preset(
            roi=RectangularROI(60.0, 35.0, 35.0, 25.0),
            tolerance=0.08,
        )
        progress: list[TrackingProgress] = []
        worker = TrackingWorker(
            pipeline=pipeline,
            reader_factory=FixedFrameReader,
            frame_count=3,
            fps=25.0,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)

        worker.run()

        stored = pipeline.results[-1].debug["response_map"]
        self.assertIsInstance(stored, np.ndarray)
        self.assertEqual(pipeline.results[-1].debug["response_frame_shape"], frame.shape[:2])
        self.assertLess(stored.nbytes, frame.shape[0] * frame.shape[1] * np.dtype(np.float32).itemsize)
        self.assertEqual(progress[-1].retained_debug_bytes, stored.nbytes * 3)

    def test_worker_uses_processing_reader_and_reports_stage_metrics(self) -> None:
        pipeline = _Pipeline()
        reader = _ProcessingReader()
        progress: list[TrackingProgress] = []
        completed: list[tuple[int, bool, bool, str]] = []
        failures: list[tuple[str, int]] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=5,
            fps=25.0,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)
        worker.completed.connect(
            lambda *args: completed.append(args),
            Qt.ConnectionType.DirectConnection,
        )
        worker.failed.connect(
            lambda message, count: failures.append((message, count)),
            Qt.ConnectionType.DirectConnection,
        )

        worker.run()

        self.assertEqual(reader.processing_reads, 5)
        self.assertEqual(reader.public_reads, 0)
        self.assertTrue(reader.closed)
        self.assertEqual(failures, [])
        self.assertEqual(completed, [(5, False, False, "")])
        self.assertTrue(progress)
        final = progress[-1]
        self.assertEqual(final.completed, 5)
        self.assertEqual(final.total, 5)
        self.assertEqual(final.processed_frames, 5)
        self.assertGreater(final.input_ms_per_frame, 0.0)
        self.assertGreater(final.processing_ms_per_frame, 0.0)
        self.assertGreater(final.throughput_fps, 0.0)
        self.assertEqual(final.eta_s, 0.0)
        self.assertEqual(final.retained_debug_bytes, 500)
        self.assertEqual(final.retained_debug_frames, 5)
        self.assertEqual(final.peak_retained_debug_bytes, 500)
        self.assertEqual(final.debug_history_max_bytes, 1024)
        self.assertEqual(final.prefetch_frames, 1)

    def test_terminal_callbacks_observe_reader_closed_for_serial_and_prefetch(self) -> None:
        for prefetch_frames in (0, 1):
            with self.subTest(prefetch_frames=prefetch_frames):
                pipeline = _FastPipeline()
                reader = _FastProcessingReader()
                terminal_events: list[tuple[str, bool]] = []
                worker = TrackingWorker(
                    pipeline=pipeline,  # type: ignore[arg-type]
                    reader_factory=lambda: reader,
                    frame_count=2,
                    fps=25.0,
                    prefetch_frames=prefetch_frames,
                )
                worker.completed.connect(
                    lambda *_args: terminal_events.append(("completed", reader.closed)),
                    Qt.ConnectionType.DirectConnection,
                )
                worker.failed.connect(
                    lambda *_args: terminal_events.append(("failed", reader.closed)),
                    Qt.ConnectionType.DirectConnection,
                )

                worker.run()

                self.assertEqual(terminal_events, [("completed", True)])

    def test_reader_close_failure_emits_only_failed_for_serial_and_prefetch(self) -> None:
        thread_errors: list[tuple[str, str]] = []
        original_excepthook = threading.excepthook
        threading.excepthook = lambda args: thread_errors.append(
            (args.exc_type.__name__, str(args.exc_value))
        )
        try:
            for prefetch_frames in (0, 1):
                with self.subTest(prefetch_frames=prefetch_frames):
                    pipeline = _FastPipeline()
                    reader = _CloseFailingProcessingReader()
                    completed: list[tuple[int, bool, bool, str]] = []
                    failures: list[tuple[str, int, bool]] = []
                    worker = TrackingWorker(
                        pipeline=pipeline,  # type: ignore[arg-type]
                        reader_factory=lambda: reader,
                        frame_count=1,
                        fps=25.0,
                        prefetch_frames=prefetch_frames,
                    )
                    worker.completed.connect(
                        lambda *args: completed.append(args),
                        Qt.ConnectionType.DirectConnection,
                    )
                    worker.failed.connect(
                        lambda message, count: failures.append((message, count, reader.closed)),
                        Qt.ConnectionType.DirectConnection,
                    )

                    worker.run()

                    self.assertEqual(completed, [])
                    self.assertEqual(len(failures), 1)
                    self.assertIn("tracking input cleanup failed", failures[0][0])
                    self.assertIn("synthetic close failure", failures[0][0])
                    self.assertEqual(failures[0][1:], (1, True))
        finally:
            threading.excepthook = original_excepthook
        self.assertEqual(thread_errors, [])

    def test_input_and_close_failures_are_both_reported_for_serial_and_prefetch(self) -> None:
        thread_errors: list[tuple[str, str]] = []
        original_excepthook = threading.excepthook
        threading.excepthook = lambda args: thread_errors.append(
            (args.exc_type.__name__, str(args.exc_value))
        )
        try:
            for prefetch_frames in (0, 1):
                with self.subTest(prefetch_frames=prefetch_frames):
                    pipeline = _FastPipeline()
                    reader = _ReadAndCloseFailingProcessingReader()
                    completed: list[tuple[int, bool, bool, str]] = []
                    failures: list[tuple[str, int, bool]] = []
                    worker = TrackingWorker(
                        pipeline=pipeline,  # type: ignore[arg-type]
                        reader_factory=lambda: reader,
                        frame_count=1,
                        fps=25.0,
                        prefetch_frames=prefetch_frames,
                    )
                    worker.completed.connect(
                        lambda *args: completed.append(args),
                        Qt.ConnectionType.DirectConnection,
                    )
                    worker.failed.connect(
                        lambda message, count: failures.append((message, count, reader.closed)),
                        Qt.ConnectionType.DirectConnection,
                    )

                    worker.run()

                    self.assertEqual(completed, [])
                    self.assertEqual(len(failures), 1)
                    self.assertIn("synthetic input failure at frame 0", failures[0][0])
                    self.assertIn("tracking input cleanup also failed", failures[0][0])
                    self.assertIn("synthetic close failure", failures[0][0])
                    self.assertEqual(failures[0][1:], (0, True))
        finally:
            threading.excepthook = original_excepthook
        self.assertEqual(thread_errors, [])

    def test_worker_overlaps_bounded_input_prefetch_and_cancels_cleanly(self) -> None:
        pipeline = _SlowPipeline()
        reader = _SlowProcessingReader()
        progress: list[TrackingProgress] = []
        completed: list[tuple[int, bool, bool, str]] = []
        failures: list[tuple[str, int]] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=20,
            fps=25.0,
        )
        worker.progress.connect(progress.append, Qt.ConnectionType.DirectConnection)
        worker.completed.connect(
            lambda *args: completed.append(args),
            Qt.ConnectionType.DirectConnection,
        )
        worker.failed.connect(
            lambda message, count: failures.append((message, count)),
            Qt.ConnectionType.DirectConnection,
        )
        thread = Thread(target=worker.run)

        thread.start()
        deadline = time.monotonic() + 1.0
        while reader.processing_reads < 4 and time.monotonic() < deadline:
            time.sleep(0.001)
        worker.request_cancel()
        thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(reader.closed)
        self.assertEqual(failures, [])
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0][1])
        self.assertLess(completed[0][0], 20)
        self.assertTrue(progress)
        self.assertEqual(progress[-1].prefetch_frames, 1)
        self.assertGreater(progress[-1].stage_overlap_ms_per_frame, 0.0)

    def test_worker_emits_terminal_metrics_before_failure_between_progress_strides(self) -> None:
        pipeline = _Pipeline()
        reader = _FailingProcessingReader(fail_at=7)
        events: list[tuple[str, object]] = []
        worker = TrackingWorker(
            pipeline=pipeline,  # type: ignore[arg-type]
            reader_factory=lambda: reader,
            frame_count=1000,
            fps=25.0,
        )
        worker.progress.connect(lambda progress: events.append(("progress", progress)))
        worker.failed.connect(lambda message, completed: events.append(("failed", (message, completed))))

        worker.run()

        self.assertEqual([kind for kind, _value in events], ["progress", "failed"])
        terminal = events[0][1]
        self.assertIsInstance(terminal, TrackingProgress)
        self.assertEqual(terminal.completed, 7)
        self.assertGreater(terminal.input_s, 0.0)
        self.assertGreater(terminal.processing_s, 0.0)
        self.assertEqual(events[1][1][1], 7)
        self.assertTrue(reader.closed)

    def test_isolated_results_batch_count_is_bounded(self) -> None:
        producer = color_marker_preset()
        TrackingWorker(
            pipeline=producer,
            reader_factory=_SpawnProcessingReader,
            frame_count=17,
            fps=25.0,
            process_isolation=False,
        ).run()
        batch = list(producer.results)
        self.assertEqual(len(batch), 17)

        worker = TrackingWorker(
            pipeline=color_marker_preset(),
            reader_factory=_SpawnProcessingReader,
            frame_count=1,
            fps=25.0,
            process_isolation=True,
            checkpoint_frames=16,
        )
        with self.assertRaisesRegex(RuntimeError, "batch"):
            worker._accept_isolated_results(batch)

    def test_isolated_message_encode_rejects_oversized_payload(self) -> None:
        from neo_tracker.ui.tracking_worker import (
            TRACKING_IPC_MAX_MESSAGE_BYTES,
            _encode_isolated_message,
        )

        self.assertGreater(TRACKING_IPC_MAX_MESSAGE_BYTES, 0)
        with self.assertRaisesRegex(RuntimeError, "IPC message exceeded"):
            _encode_isolated_message({"payload": b"x" * 8192}, limit=4096)

    def test_isolated_receive_rejects_oversized_frame(self) -> None:
        from multiprocessing import get_context

        from neo_tracker.ui.tracking_worker import _receive_isolated_message

        context = get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        try:
            send.send_bytes(actual_pickle_dumps({"kind": "progress", "value": 1}))
            message = _receive_isolated_message(receive, limit=4096)
            self.assertEqual(message["value"], 1)
            send.send_bytes(actual_pickle_dumps({"kind": "results", "payload": b"x" * 8192}))
            with self.assertRaises(OSError):
                _receive_isolated_message(receive, limit=4096)
        finally:
            receive.close()
            send.close()

    def test_isolated_terminal_validation_rejects_malformed(self) -> None:
        from neo_tracker.ui.tracking_worker import _validate_isolated_terminal

        good = _validate_isolated_terminal(("terminal", 5, False, True, "note", "", {}))
        self.assertEqual(good[1], 5)
        for bad in (
            ("terminal", 5),
            ("terminal", 5, False, True, "note", "", "not-a-dict"),
            "junk",
            None,
        ):
            with self.assertRaises(ValueError):
                _validate_isolated_terminal(bad)


if __name__ == "__main__":
    unittest.main()
