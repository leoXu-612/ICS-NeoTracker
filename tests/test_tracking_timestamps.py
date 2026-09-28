from __future__ import annotations

import tempfile
import unittest
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from neo_tracker.media import MediaReader
from neo_tracker.presets import color_marker_preset
from neo_tracker.ui.tracking_worker import TrackingWorker


class TimestampReader:
    def __init__(self, times):
        self.times = times
        self.last_frame_time_s = None

    def read_frame(self, index):
        self.last_frame_time_s = self.times[index]
        frame = np.zeros((16, 24, 3), dtype=np.uint8)
        frame[4:8, 5 + index:9 + index, 0] = 255
        return frame

    def close(self):
        self.last_frame_time_s = None


class TimestampCapture:
    def __init__(self, times):
        self.times = times
        self.position = 0

    def isOpened(self):
        return True

    def get(self, key):
        return {
            1: 100.0, 2: len(self.times), 3: 24, 4: 16,
            5: self.position,
            6: self.times[max(0, self.position - 1)] * 1000,
        }.get(key, 0.0)

    def set(self, _key, index):
        self.position = int(index)
        return True

    def grab(self):
        self.position += 1
        return True

    def read(self):
        self.position += 1
        return True, np.zeros((16, 24, 3), dtype=np.uint8)

    def release(self):
        pass


class TrackingTimestampTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_media_reader_captures_the_decoded_frame_timestamp_after_seek(self):
        times = (0.0, 0.013, 0.021, 0.048)
        backend = SimpleNamespace(
            CAP_PROP_FPS=1, CAP_PROP_FRAME_COUNT=2, CAP_PROP_FRAME_WIDTH=3,
            CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_POS_FRAMES=5, CAP_PROP_POS_MSEC=6,
            VideoCapture=lambda _path: TimestampCapture(times),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "neo_tracker.media._load_cv2", return_value=backend,
        ):
            path = Path(directory) / "clock.mov"
            path.write_bytes(b"isolated timestamp fixture")
            with MediaReader(str(path)) as reader:
                for index in (0, 2, 1, 3):
                    reader.read_frame_for_processing(index)
                    self.assertAlmostEqual(reader.last_frame_time_s, times[index])

    def test_variable_frame_times_survive_thread_process_and_prefetch_paths(self):
        for isolated in (False, True):
            for prefetch in (0, 1):
                for times in ((0.0, 0.013, 0.021, 0.048), (None,) * 4):
                    with self.subTest(isolated=isolated, prefetch=prefetch, times=times):
                        pipeline = color_marker_preset()
                        errors = []
                        worker = TrackingWorker(
                            pipeline=pipeline, reader_factory=partial(TimestampReader, times),
                            frame_count=4, fps=100.0, prefetch_frames=prefetch,
                            process_isolation=isolated,
                        )
                        worker.failed.connect(lambda message, _count: errors.append(message), Qt.ConnectionType.DirectConnection)
                        worker.run()
                        self.assertEqual(errors, [])
                        expected = times if times[0] is not None else (0.0, 0.01, 0.02, 0.03)
                        self.assertEqual([row.time_s for row in pipeline.results], list(expected))

    def test_invalid_reader_timestamp_fails_without_publishing_a_result(self):
        for value in (float("nan"), float("inf"), -1.0, True):
            with self.subTest(value=value):
                pipeline = color_marker_preset()
                errors = []
                worker = TrackingWorker(
                    pipeline=pipeline, reader_factory=partial(TimestampReader, (value,)),
                    frame_count=1, fps=100.0,
                )
                worker.failed.connect(lambda message, _count: errors.append(message), Qt.ConnectionType.DirectConnection)
                worker.run()
                self.assertEqual(pipeline.results, [])
                self.assertTrue(errors)
                self.assertIn("timestamp", errors[0])


if __name__ == "__main__":
    unittest.main()
