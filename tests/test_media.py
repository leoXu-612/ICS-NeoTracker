from __future__ import annotations

import os
import tempfile
import unittest
import wave
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker import media


class MediaLayerTests(unittest.TestCase):
    def test_import_is_safe_without_eager_cv2_dependency(self) -> None:
        self.assertTrue(hasattr(media, "probe_media"))
        self.assertTrue(hasattr(media, "read_video_frame"))

    def test_missing_backend_returns_readable_probe_error(self) -> None:
        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = None
            media._CV2_IMPORT_ERROR = ModuleNotFoundError("No module named 'cv2'")
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_media = Path(tmpdir) / "clip.mp4"
                fake_media.write_bytes(b"not a real video")
                info = media.probe_media(str(fake_media))
            self.assertFalse(info.available)
            self.assertIn("OpenCV", info.error)
            self.assertFalse(media.has_media_backend())
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_reports_missing_backend_without_crashing(self) -> None:
        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = None
            media._CV2_IMPORT_ERROR = ModuleNotFoundError("No module named 'cv2'")
            with tempfile.TemporaryDirectory() as tmpdir:
                fake_media = Path(tmpdir) / "clip.mp4"
                fake_media.write_bytes(b"not a real video")
                with self.assertRaisesRegex(RuntimeError, "OpenCV"):
                    media.MediaReader(str(fake_media))
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_specialized_reads_preserve_processing_display_and_public_color_contracts(self) -> None:
        class FakeCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 2.0, 4: 2.0, 5: 1.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                padded = np.asarray([[[10, 20, 30, 0], [40, 50, 60, 0]]], dtype=np.uint8)
                frame = padded[:, :, :3]
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self) -> None:
                self.cvt_color_calls = 0

            def VideoCapture(self, _path: str) -> FakeCapture:
                return FakeCapture()

            def cvtColor(self, frame: np.ndarray, _code: int) -> np.ndarray:
                self.cvt_color_calls += 1
                return np.ascontiguousarray(frame[:, :, ::-1])

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        backend = FakeCV2()
        try:
            media._CV2 = backend
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "clip.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    processing = reader.read_frame_for_processing(0)
                    public = reader.read_frame(1)
                    display = reader.read_frame_for_display(1)

            self.assertEqual(processing[0, 0].tolist(), [30, 20, 10])
            self.assertFalse(processing.flags.c_contiguous)
            self.assertEqual(public[0, 0].tolist(), [30, 20, 10])
            self.assertTrue(public.flags.c_contiguous)
            self.assertEqual(display[0, 0].tolist(), [10, 20, 30])
            self.assertTrue(display.flags.c_contiguous)
            self.assertEqual(backend.cvt_color_calls, 1)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_missing_file_returns_media_error_before_backend_error(self) -> None:
        info = media.probe_media("/definitely/not/a/file.mp4")
        self.assertFalse(info.available)
        self.assertIn("does not exist", info.error)

    def test_small_media_identity_is_full_sha256_and_roundtrips(self) -> None:
        data = b"neo-tracker-source\x00" * 1000
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "small.bin"
            path.write_bytes(data)

            identity = media.probe_media_identity(path)

        self.assertIsNotNone(identity)
        self.assertTrue(identity.complete)
        self.assertEqual(identity.strategy, "full-sha256-v1")
        self.assertEqual(identity.sha256, sha256(data).hexdigest())
        self.assertEqual(identity.size_bytes, len(data))
        self.assertEqual(identity.sampled_bytes, len(data))
        self.assertEqual(media.MediaIdentity.from_dict(identity.to_dict()), identity)
        self.assertIsNone(
            media.MediaIdentity.from_dict(
                {**identity.to_dict(), "size_bytes": True, "sampled_bytes": True}
            )
        )
        self.assertIsNone(media.MediaIdentity.from_dict({**identity.to_dict(), "complete": False}))
        self.assertIsNone(media.MediaIdentity.from_dict({"strategy": "full-sha256-v1", "sha256": "bad"}))
        impossible_sampled = {
            "strategy": "sampled-sha256-v1",
            "sha256": "0" * 64,
            "size_bytes": 1,
            "sampled_bytes": 1,
        }
        impossible_full = {
            "strategy": "full-sha256-v1",
            "sha256": "0" * 64,
            "size_bytes": media.MEDIA_IDENTITY_FULL_LIMIT_BYTES + 1,
            "sampled_bytes": media.MEDIA_IDENTITY_FULL_LIMIT_BYTES + 1,
        }
        self.assertIsNone(media.MediaIdentity.from_dict(impossible_sampled))
        self.assertIsNone(media.MediaIdentity.from_dict(impossible_full))

    def test_large_media_identity_reads_bounded_samples_and_detects_sample_change(self) -> None:
        size_bytes = media.MEDIA_IDENTITY_FULL_LIMIT_BYTES + media.MEDIA_IDENTITY_CHUNK_BYTES * 2
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large.bin"
            with path.open("wb") as source:
                source.truncate(size_bytes)

            original = media.probe_media_identity(path)
            middle_position = (size_bytes - media.MEDIA_IDENTITY_CHUNK_BYTES) // 2
            with path.open("r+b") as source:
                source.seek(middle_position + 17)
                source.write(b"changed")
            changed = media.probe_media_identity(path)

        self.assertIsNotNone(original)
        self.assertIsNotNone(changed)
        self.assertFalse(original.complete)
        self.assertEqual(original.strategy, "sampled-sha256-v1")
        self.assertEqual(original.sampled_bytes, media.MEDIA_IDENTITY_FULL_LIMIT_BYTES)
        self.assertEqual(original.size_bytes, size_bytes)
        self.assertNotEqual(changed.sha256, original.sha256)

    def test_sampled_identity_equal_when_change_is_outside_sample_windows(self) -> None:
        size_bytes = media.MEDIA_IDENTITY_FULL_LIMIT_BYTES + media.MEDIA_IDENTITY_CHUNK_BYTES * 8
        payload = (b"neo-tracker-source\x00" * (size_bytes // 17 + 1))[:size_bytes]
        with tempfile.TemporaryDirectory() as tmpdir:
            path_a = Path(tmpdir) / "a.bin"
            path_b = Path(tmpdir) / "b.bin"
            path_a.write_bytes(payload)
            path_b.write_bytes(payload)
            with path_b.open("r+b") as source:
                source.seek(600 * 1024)
                source.write(b"modified")
            identity_a = media.probe_media_identity(path_a)
            identity_b = media.probe_media_identity(path_b)

        self.assertIsNotNone(identity_a)
        self.assertIsNotNone(identity_b)
        self.assertFalse(identity_a.complete)
        self.assertEqual(identity_a, identity_b)
        self.assertEqual(identity_b.strategy, "sampled-sha256-v1")

    def _raw_wav_bytes(
        self,
        *,
        channels: int,
        sample_width: int,
        sample_rate: int,
        frame_count: int,
    ) -> bytes:
        data_size = frame_count * channels * sample_width
        block_align = channels * sample_width
        return (
            b"RIFF"
            + ((36 + data_size) % (2**32)).to_bytes(4, "little")
            + b"WAVE"
            + b"fmt "
            + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")
            + (channels % (2**16)).to_bytes(2, "little")
            + (sample_rate % (2**32)).to_bytes(4, "little")
            + ((sample_rate * block_align) % (2**32)).to_bytes(4, "little")
            + (block_align % (2**16)).to_bytes(2, "little")
            + ((sample_width * 8) % (2**16)).to_bytes(2, "little")
            + b"data"
            + (data_size % (2**32)).to_bytes(4, "little")
        )

    def test_wav_probe_rejects_extreme_channel_or_size_metadata(self) -> None:
        extreme_headers = [
            {"channels": 65535, "sample_width": 2, "sample_rate": 44100, "frame_count": 0},
            {"channels": 2, "sample_width": 8, "sample_rate": 44100, "frame_count": 0},
            {"channels": 2, "sample_width": 2, "sample_rate": 0, "frame_count": 0},
            {"channels": 2, "sample_width": 2, "sample_rate": 44100, "frame_count": 2**29},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for index, header_values in enumerate(extreme_headers):
                with self.subTest(header=header_values):
                    path = Path(tmpdir) / f"extreme-{index}.wav"
                    path.write_bytes(self._raw_wav_bytes(**header_values))
                    info = media.probe_wav_media(path)
                    self.assertFalse(info.available)
                    self.assertIn("WAV", info.error)

    def test_probe_media_reads_wav_without_video_backend(self) -> None:
        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = None
            media._CV2_IMPORT_ERROR = ModuleNotFoundError("No module named 'cv2'")
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "tone.wav"
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(2)
                    wav.setsampwidth(2)
                    wav.setframerate(8000)
                    wav.writeframes(b"\x00\x00" * 2 * 400)

                info = media.probe_media(str(path))

            self.assertTrue(info.available)
            self.assertEqual(info.kind, "audio")
            self.assertEqual(info.sample_rate_hz, 8000)
            self.assertEqual(info.channels, 2)
            self.assertEqual(info.frame_count, 400)
            self.assertAlmostEqual(info.duration_s, 0.05)
            self.assertIsNotNone(info.source_identity)
            self.assertTrue(info.source_identity.complete)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_wav_probe_rejects_metadata_digest_from_different_file_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "changing.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\x00\x00" * 100)
            real_probe_identity = media.probe_media_identity

            def digest_then_change(source_path: str | Path) -> media.MediaIdentity | None:
                identity = real_probe_identity(source_path)
                with Path(source_path).open("ab") as source:
                    source.write(b"changed-after-metadata")
                return identity

            with patch.object(media, "probe_media_identity", side_effect=digest_then_change):
                info = media.probe_wav_media(str(path))

        self.assertFalse(info.available)
        self.assertEqual(info.kind, "audio")
        self.assertIn("changed", info.error)
        self.assertIn("retry", info.error)

    def test_video_probe_rejects_metadata_digest_from_different_file_versions(self) -> None:
        class FakeCapture:
            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: 30.0, 2: 12.0, 3: 640.0, 4: 360.0}.get(prop, 0.0)

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_FPS = 1
            CAP_PROP_FRAME_COUNT = 2
            CAP_PROP_FRAME_WIDTH = 3
            CAP_PROP_FRAME_HEIGHT = 4

            def VideoCapture(self, _path: str) -> FakeCapture:
                return FakeCapture()

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "changing.mp4"
                path.write_bytes(b"original video payload")
                real_probe_identity = media.probe_media_identity

                def digest_then_replace(source_path: str | Path) -> media.MediaIdentity | None:
                    identity = real_probe_identity(source_path)
                    Path(source_path).write_bytes(b"replacement video payload with another size")
                    return identity

                with patch.object(media, "probe_media_identity", side_effect=digest_then_replace):
                    info = media.probe_media(str(path))
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

        self.assertFalse(info.available)
        self.assertIn("changed", info.error)
        self.assertIn("retry", info.error)

    def test_video_info_rejects_wav_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tone.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\x00\x00" * 20)

            info = media.video_info(str(path))

        self.assertFalse(info.available)
        self.assertIn("Expected a video file", info.error)

    def test_video_reader_rejects_wav_audio_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tone.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\x00\x00" * 20)

            with self.assertRaisesRegex(RuntimeError, "video file"):
                media.read_video_frame(str(path), 0)
            with self.assertRaisesRegex(RuntimeError, "video files only"):
                media.MediaReader(str(path))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO probe requires POSIX mkfifo")
    def test_media_probe_and_reader_reject_fifo_without_opening_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blocked.mp4"
            os.mkfifo(path)

            info = media.probe_media(str(path))
            self.assertFalse(info.available)
            self.assertIn("not a regular file", info.error)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                media.MediaReader(str(path))

    def test_media_reader_seeks_to_target_when_sequential_grab_fails(self) -> None:
        class FakeCapture:
            def __init__(self) -> None:
                self.position = 0
                self.grab_calls = 0
                self.set_calls: list[tuple[int, float]] = []

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                values = {
                    1: float(self.position),
                    2: 30.0,
                    3: 20.0,
                    4: 8.0,
                    5: 6.0,
                }
                return values.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                self.set_calls.append((prop, value))
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.grab_calls += 1
                if self.grab_calls == 1:
                    return False
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self, capture: FakeCapture) -> None:
                self.capture = capture

            def VideoCapture(self, _path: str) -> FakeCapture:
                return self.capture

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        capture = FakeCapture()
        try:
            media._CV2 = FakeCV2(capture)
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "clip.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    frame = reader.read_frame(3)
            self.assertEqual(int(frame[0, 0, 0]), 3)
            self.assertIn((FakeCV2.CAP_PROP_POS_FRAMES, 3), capture.set_calls)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_recovers_when_backend_rejects_seek(self) -> None:
        class RejectingCapture:
            def __init__(self) -> None:
                self.position = 0
                self.grab_calls = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 40.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, _prop: int, _value: float) -> bool:
                return False

            def grab(self) -> bool:
                self.grab_calls += 1
                if self.position >= 40:
                    return False
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self) -> None:
                self.captures: list[RejectingCapture] = []

            def VideoCapture(self, _path: str) -> RejectingCapture:
                capture = RejectingCapture()
                self.captures.append(capture)
                return capture

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        backend = FakeCV2()
        try:
            media._CV2 = backend
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "reject-seek.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    frame = reader.read_frame(20)

            self.assertEqual(int(frame[0, 0, 0]), 20)
            self.assertEqual(len(backend.captures), 2)
            self.assertEqual(backend.captures[-1].grab_calls, 20)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_advances_from_inaccurate_seek_landing(self) -> None:
        class InaccurateCapture:
            def __init__(self) -> None:
                self.position = 0
                self.grab_calls = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 40.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = max(0, int(value) - 2)
                return True

            def grab(self) -> bool:
                self.grab_calls += 1
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self, capture: InaccurateCapture) -> None:
                self.capture = capture

            def VideoCapture(self, _path: str) -> InaccurateCapture:
                return self.capture

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        capture = InaccurateCapture()
        try:
            media._CV2 = FakeCV2(capture)
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "inaccurate-seek.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    frame = reader.read_frame(20)

            self.assertEqual(int(frame[0, 0, 0]), 20)
            self.assertEqual(capture.grab_calls, 2)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_raises_typed_end_error_when_decode_cannot_continue(self) -> None:
        class FailedCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 20.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, None]:
                return False, None

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self, capture: FailedCapture) -> None:
                self.capture = capture

            def VideoCapture(self, _path: str) -> FailedCapture:
                return self.capture

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2(FailedCapture())
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "truncated.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    with self.assertRaises(media.EndOfMediaError) as raised:
                        reader.read_frame(3)
            self.assertEqual(raised.exception.frame_index, 3)
            self.assertIn("ended or became unreadable", str(raised.exception))
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_processing_reader_does_not_repeat_last_frame_past_reported_length(self) -> None:
        class FiniteCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 3.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                if self.position >= 3:
                    return False
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray | None]:
                if self.position >= 3:
                    return False, None
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def VideoCapture(self, _path: str) -> FiniteCapture:
                return FiniteCapture()

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "finite.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as preview_reader:
                    preview = preview_reader.read_frame(9)
                with media.MediaReader(str(path)) as processing_reader:
                    with self.assertRaises(media.EndOfMediaError) as raised:
                        processing_reader.read_frame_for_processing(3)

            self.assertEqual(int(preview[0, 0, 0]), 2)
            self.assertEqual(raised.exception.frame_index, 3)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_rejects_source_replaced_between_open_and_read(self) -> None:
        class ReplacingCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 20.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def VideoCapture(self, _path: str) -> ReplacingCapture:
                return ReplacingCapture()

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                path = root / "clip.mp4"
                path.write_bytes(b"original payload")
                replacement = root / "replacement.mp4"
                replacement.write_bytes(b"a completely different payload")
                with media.MediaReader(str(path)) as reader:
                    os.replace(replacement, path)
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        reader.read_frame(0)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_rejects_source_rewritten_in_place_between_reads(self) -> None:
        class RewriteCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 20.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def VideoCapture(self, _path: str) -> RewriteCapture:
                return RewriteCapture()

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "clip.mp4"
                path.write_bytes(b"original payload")
                with media.MediaReader(str(path)) as reader:
                    path.write_bytes(b"rewritten in place with a longer body")
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        reader.read_frame(0)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_reopen_after_source_replacement_fails_closed(self) -> None:
        class RejectingCapture:
            def __init__(self) -> None:
                self.position = 0
                self.grab_calls = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 40.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, _prop: int, _value: float) -> bool:
                return False

            def grab(self) -> bool:
                self.grab_calls += 1
                if self.position >= 40:
                    return False
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self) -> None:
                self.captures: list[RejectingCapture] = []

            def VideoCapture(self, _path: str) -> RejectingCapture:
                capture = RejectingCapture()
                self.captures.append(capture)
                return capture

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                path = root / "clip.mp4"
                path.write_bytes(b"original payload")
                replacement = root / "replacement.mp4"
                replacement.write_bytes(b"a completely different payload")
                with media.MediaReader(str(path)) as reader:
                    os.replace(replacement, path)
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        reader.read_frame(20)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_rejects_source_change_during_capture_open(self) -> None:
        class SwapCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 20.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def VideoCapture(self, _path: str) -> SwapCapture:
                return SwapCapture()

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        versions = iter([(1, 2, 3, 4, 5), (6, 7, 8, 9, 10)])
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "clip.mp4"
                path.write_bytes(b"fake")
                with patch.object(
                    media,
                    "_media_file_version",
                    side_effect=lambda _path: next(versions),
                ):
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        media.MediaReader(str(path))
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink requires platform support")
    def test_media_reader_follows_regular_file_symlink_and_detects_target_swap(self) -> None:
        class SymlinkCapture:
            def __init__(self) -> None:
                self.position = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 20.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray]:
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def VideoCapture(self, _path: str) -> SymlinkCapture:
                return SymlinkCapture()

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                original = root / "original.mp4"
                original.write_bytes(b"original payload")
                replacement = root / "replacement.mp4"
                replacement.write_bytes(b"a completely different payload")
                link = root / "clip.mp4"
                os.symlink(str(original), str(link))
                with media.MediaReader(str(link)) as reader:
                    first = reader.read_frame(0)
                    self.assertIsNotNone(first)
                    os.replace(replacement, original)
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        reader.read_frame(1)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error

    def test_media_reader_raises_end_error_when_reported_count_exceeds_actual_vfr(self) -> None:
        class VfrCapture:
            def __init__(self) -> None:
                self.position = 0
                self.grab_calls = 0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                return {1: float(self.position), 2: 30.0, 3: 40.0, 4: 8.0, 5: 6.0}.get(prop, 0.0)

            def set(self, prop: int, value: float) -> bool:
                if prop == 1:
                    self.position = int(value)
                return True

            def grab(self) -> bool:
                self.grab_calls += 1
                if self.position >= 21:
                    return False
                self.position += 1
                return True

            def read(self) -> tuple[bool, np.ndarray | None]:
                if self.position >= 21:
                    return False, None
                frame = np.full((2, 3, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame

            def release(self) -> None:
                return None

        class FakeCV2:
            CAP_PROP_POS_FRAMES = 1
            CAP_PROP_FPS = 2
            CAP_PROP_FRAME_COUNT = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            COLOR_BGR2RGB = 6

            def __init__(self) -> None:
                self.captures: list[VfrCapture] = []

            def VideoCapture(self, _path: str) -> VfrCapture:
                capture = VfrCapture()
                self.captures.append(capture)
                return capture

            @staticmethod
            def cvtColor(frame: np.ndarray, _code: int) -> np.ndarray:
                return frame

        old_cv2 = media._CV2
        old_error = media._CV2_IMPORT_ERROR
        try:
            media._CV2 = FakeCV2()
            media._CV2_IMPORT_ERROR = None
            with tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "vfr.mp4"
                path.write_bytes(b"fake")
                with media.MediaReader(str(path)) as reader:
                    with self.assertRaises(media.EndOfMediaError) as raised:
                        reader.read_frame_for_processing(30)
            self.assertEqual(raised.exception.frame_index, 30)
        finally:
            media._CV2 = old_cv2
            media._CV2_IMPORT_ERROR = old_error


if __name__ == "__main__":
    unittest.main()
