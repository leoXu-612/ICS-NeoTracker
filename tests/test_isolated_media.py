from __future__ import annotations

import os
import pickle
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker import media
from neo_tracker.media import MediaReader, probe_media_identity
from neo_tracker.ui.isolated_media import (
    IsolatedMediaCancelled,
    IsolatedMediaError,
    IsolatedMediaLimits,
    IsolatedMediaTimeout,
    PreviewDecoderSession,
    decode_preview_frame_isolated,
    probe_media_isolated,
    validate_media_info,
)


def _crash_without_response(connection, *_args) -> None:
    connection.close()
    os._exit(23)


def _block_forever(_connection, *_args) -> None:
    while True:
        time.sleep(1.0)


def _write_unpickle_marker(path: str) -> None:
    Path(path).write_text("unsafe parent unpickle", encoding="utf-8")


class _MaliciousPayload:
    def __init__(self, marker_path: str) -> None:
        self.marker_path = marker_path

    def __reduce__(self):
        return (_write_unpickle_marker, (self.marker_path,))


def _send_malicious_pickle(connection, _operation, _request_id, path, *_args) -> None:
    connection.send_bytes(pickle.dumps(_MaliciousPayload(path + ".unpickled")))
    connection.close()


def _session_send_malicious_pickle(connection, path, *_args) -> None:
    connection.send_bytes(pickle.dumps(_MaliciousPayload(path + ".unpickled")))
    connection.close()


def _send_oversized_session_frame(connection, _path, *_args) -> None:
    from neo_tracker.ui.isolated_media import _decode_response, _send_response

    request, raw = _decode_response(connection.recv_bytes(), IsolatedMediaLimits())
    assert not raw
    source_version = request["source_version"]
    _send_response(
        connection,
        {
            "type": "frame",
            "request_id": request["request_id"],
            "frame_index": request["frame_index"],
            "shape": [4096, 4097, 3],
            "dtype": "uint8",
            "source_version_before": source_version,
            "source_version_after": source_version,
        },
        IsolatedMediaLimits().max_metadata_bytes,
    )
    connection.close()


def _replace_source_before_session_response(connection, path, *_args) -> None:
    from neo_tracker.ui.isolated_media import _decode_response, _send_response

    request, raw = _decode_response(connection.recv_bytes(), IsolatedMediaLimits())
    assert not raw
    source_version = request["source_version"]
    replacement = Path(path + ".replacement")
    replacement.write_bytes(b"replacement media version")
    os.replace(replacement, path)
    _send_response(
        connection,
        {
            "type": "frame",
            "request_id": request["request_id"],
            "frame_index": request["frame_index"],
            "shape": [1, 1, 3],
            "dtype": "uint8",
            "source_version_before": source_version,
            "source_version_after": source_version,
        },
        IsolatedMediaLimits().max_metadata_bytes,
        b"\x00\x00\x00",
    )
    connection.close()


class IsolatedMediaTests(unittest.TestCase):
    @staticmethod
    def _source_file(directory: str) -> Path:
        path = Path(directory) / "source.mp4"
        path.write_bytes(b"synthetic decoder boundary source")
        return path

    def test_helper_crash_and_eof_become_bounded_error(self) -> None:
        limits = IsolatedMediaLimits(
            probe_timeout_s=1.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            started = time.monotonic()
            with self.assertRaisesRegex(IsolatedMediaError, "exited|closed IPC"):
                probe_media_isolated(
                    str(path),
                    limits=limits,
                    process_target=_crash_without_response,
                )
        self.assertLess(time.monotonic() - started, 0.8)

    def test_parent_rejects_malicious_pickle_without_executing_it(self) -> None:
        limits = IsolatedMediaLimits(
            probe_timeout_s=1.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            marker = Path(str(path) + ".unpickled")
            with self.assertRaisesRegex(IsolatedMediaError, "IPC envelope|header length"):
                probe_media_isolated(
                    str(path),
                    limits=limits,
                    process_target=_send_malicious_pickle,
                )
            self.assertFalse(marker.exists(), "parent process executed helper-controlled pickle")

    def test_metadata_limits_reject_fractional_integer_bool_and_extreme_rates(self) -> None:
        base = dict(
            fps=30.0,
            frame_count=10,
            width=4,
            height=3,
            duration_s=1.0,
            available=True,
        )
        invalid = (
            media.MediaInfo(**{**base, "frame_count": 1.5}),
            media.MediaInfo(**{**base, "width": True}),
            media.MediaInfo(**{**base, "fps": 1_000_001.0}),
            media.MediaInfo(**{**base, "duration_s": 400_000_000.0}),
        )
        for info in invalid:
            with self.subTest(info=info):
                with self.assertRaises(IsolatedMediaError):
                    validate_media_info("source.mp4", info)

    def test_4k_working_set_limit_rejects_oversize_before_numpy_allocation(self) -> None:
        limits = IsolatedMediaLimits()
        self.assertEqual(limits.max_pixels, 16_777_216)
        self.assertEqual(limits.max_frame_bytes, 48 * 1024 * 1024)
        supported = media.MediaInfo(
            fps=30.0,
            frame_count=1,
            width=4096,
            height=4096,
            duration_s=1.0 / 30.0,
            available=True,
        )
        self.assertIs(validate_media_info("4k-square.mp4", supported), supported)
        oversized = media.MediaInfo(
            **{**supported.__dict__, "width": 4097}
        )
        with self.assertRaisesRegex(IsolatedMediaError, "safety limit"):
            validate_media_info("oversized.mp4", oversized)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            session = PreviewDecoderSession(
                str(path),
                process_target=_send_oversized_session_frame,
            )
            with patch(
                "neo_tracker.ui.isolated_media.np.frombuffer",
                side_effect=AssertionError("oversized response reached NumPy allocation"),
            ) as frombuffer:
                with self.assertRaisesRegex(IsolatedMediaError, "oversized"):
                    session.decode(0)
                frombuffer.assert_not_called()
            session.close()

    def test_session_constructor_performs_no_filesystem_io(self) -> None:
        with patch(
            "neo_tracker.ui.isolated_media._regular_file_version",
            side_effect=AssertionError("constructor performed filesystem I/O"),
        ):
            session = PreviewDecoderSession("/local/path/checked-by-worker.mp4")
        session.close()

    def test_parent_rejects_replaced_source_before_stale_frame_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            session = PreviewDecoderSession(
                str(path),
                expected_width=1,
                expected_height=1,
                process_target=_replace_source_before_session_response,
            )
            with patch(
                "neo_tracker.ui.isolated_media.np.frombuffer",
                side_effect=AssertionError("stale response reached frame construction"),
            ) as frombuffer:
                with self.assertRaisesRegex(IsolatedMediaError, "changed before.*committed"):
                    session.decode(0)
                frombuffer.assert_not_called()
            self.assertFalse(session.is_alive)
            session.close()

    def test_blocked_decoder_times_out_and_is_terminated(self) -> None:
        limits = IsolatedMediaLimits(
            preview_timeout_s=0.08,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            started = time.monotonic()
            with self.assertRaises(IsolatedMediaTimeout):
                decode_preview_frame_isolated(
                    str(path),
                    0,
                    limits=limits,
                    process_target=_block_forever,
                )
        self.assertLess(time.monotonic() - started, 0.8)

    def test_cancel_terminates_a_blocked_decoder_in_bounded_time(self) -> None:
        limits = IsolatedMediaLimits(
            preview_timeout_s=2.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        cancel_requested = threading.Event()
        timer = threading.Timer(0.08, cancel_requested.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            started = time.monotonic()
            with self.assertRaises(IsolatedMediaCancelled):
                decode_preview_frame_isolated(
                    str(path),
                    0,
                    cancellation_requested=cancel_requested,
                    limits=limits,
                    process_target=_block_forever,
                )
        self.assertLess(time.monotonic() - started, 0.8)

    def test_session_cancel_terminates_blocked_decoder_and_drops_session(self) -> None:
        limits = IsolatedMediaLimits(
            preview_timeout_s=2.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        cancel_requested = threading.Event()
        timer = threading.Timer(0.08, cancel_requested.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            session = PreviewDecoderSession(
                str(path),
                limits=limits,
                process_target=_block_forever,
            )
            self.addCleanup(session.close)
            started = time.monotonic()
            with self.assertRaises(IsolatedMediaCancelled):
                session.decode(0, cancellation_requested=cancel_requested)
            self.assertFalse(session.is_alive)
        self.assertLess(time.monotonic() - started, 0.8)

    def test_session_crash_and_timeout_drop_the_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            for target, expected_error in (
                (_crash_without_response, IsolatedMediaError),
                (_block_forever, IsolatedMediaTimeout),
            ):
                limits = IsolatedMediaLimits(
                    preview_timeout_s=0.08,
                    cancel_grace_s=0.02,
                    kill_grace_s=0.02,
                    poll_interval_s=0.005,
                )
                session = PreviewDecoderSession(
                    str(path),
                    limits=limits,
                    process_target=target,
                )
                with self.subTest(target=target.__name__):
                    with self.assertRaises(expected_error):
                        session.decode(0)
                    self.assertFalse(session.is_alive)
                session.close()

    def test_session_rejects_malicious_pickle_without_executing_it(self) -> None:
        limits = IsolatedMediaLimits(
            preview_timeout_s=1.0,
            cancel_grace_s=0.02,
            kill_grace_s=0.02,
            poll_interval_s=0.005,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._source_file(tmpdir)
            marker = Path(str(path) + ".unpickled")
            session = PreviewDecoderSession(
                str(path),
                limits=limits,
                process_target=_session_send_malicious_pickle,
            )
            self.addCleanup(session.close)
            with self.assertRaisesRegex(IsolatedMediaError, "IPC envelope|header length"):
                session.decode(0)
            self.assertFalse(marker.exists(), "parent process executed session-controlled pickle")

    @unittest.skipUnless(media.has_media_backend(), "OpenCV is required for synthetic video decode")
    def test_synthetic_video_returns_the_exact_requested_frame(self) -> None:
        cv2 = media._load_cv2()
        self.assertIsNotNone(cv2)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "exact-frame.avi"
            writer = cv2.VideoWriter(  # type: ignore[union-attr]
                str(path),
                cv2.VideoWriter_fourcc(*"MJPG"),  # type: ignore[union-attr]
                12.0,
                (48, 32),
            )
            if not writer.isOpened():
                self.skipTest("OpenCV MJPG writer is unavailable")
            try:
                for index in range(4):
                    frame = np.zeros((32, 48, 3), dtype=np.uint8)
                    frame[:, :, 0] = 15 + index * 40
                    frame[4 + index : 13 + index, 8:28, 1] = 220
                    frame[18:27, 20 + index : 34 + index, 2] = 180
                    writer.write(frame)
            finally:
                writer.release()

            identity = probe_media_identity(path)
            self.assertIsNotNone(identity)
            with MediaReader(str(path)) as reader:
                expected = reader.read_frame_for_display(2)
            isolated = decode_preview_frame_isolated(
                str(path),
                2,
                expected_width=48,
                expected_height=32,
                expected_identity=identity,
            )

        self.assertTrue(isolated.flags.c_contiguous)
        self.assertEqual(isolated.dtype, np.uint8)
        self.assertTrue(np.array_equal(isolated, expected))

    @unittest.skipUnless(media.has_media_backend(), "OpenCV is required for persistent video decode")
    def test_session_reuses_one_process_for_sequential_and_random_exact_frames(self) -> None:
        cv2 = media._load_cv2()
        self.assertIsNotNone(cv2)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-frames.avi"
            writer = cv2.VideoWriter(  # type: ignore[union-attr]
                str(path),
                cv2.VideoWriter_fourcc(*"MJPG"),  # type: ignore[union-attr]
                12.0,
                (48, 32),
            )
            if not writer.isOpened():
                self.skipTest("OpenCV MJPG writer is unavailable")
            try:
                for index in range(5):
                    frame = np.zeros((32, 48, 3), dtype=np.uint8)
                    frame[:, :, 0] = 15 + index * 35
                    frame[3 + index : 12 + index, 7:30, 1] = 230
                    frame[18:28, 18 + index : 34 + index, 2] = 170
                    writer.write(frame)
            finally:
                writer.release()

            identity = probe_media_identity(path)
            self.assertIsNotNone(identity)
            with MediaReader(str(path)) as reader:
                expected = {
                    index: reader.read_frame_for_display(index)
                    for index in (0, 1, 2, 4)
                }
            with PreviewDecoderSession(
                str(path),
                expected_width=48,
                expected_height=32,
                expected_identity=identity,
            ) as session:
                actual = [session.decode(index) for index in (0, 1, 2, 1, 4)]
                self.assertEqual(session.start_count, 1)
                session.terminate()
                restarted = session.decode(2)
                self.assertEqual(session.start_count, 2)

        for index, frame in zip((0, 1, 2, 1, 4), actual):
            self.assertTrue(np.array_equal(frame, expected[index]))
        self.assertTrue(np.array_equal(restarted, expected[2]))


if __name__ == "__main__":
    unittest.main()
