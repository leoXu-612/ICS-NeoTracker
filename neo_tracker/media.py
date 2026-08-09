from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from stat import S_ISREG
from typing import Any, Literal
import wave

import numpy as np


_CV2: Any | None = None
_CV2_IMPORT_ERROR: Exception | None = None
MEDIA_IDENTITY_CHUNK_BYTES = 256 * 1024
MEDIA_IDENTITY_FULL_LIMIT_BYTES = MEDIA_IDENTITY_CHUNK_BYTES * 3
_MEDIA_IDENTITY_STRATEGIES = frozenset({"full-sha256-v1", "sampled-sha256-v1"})
MAX_WAV_CHANNELS = 64
MAX_WAV_SAMPLE_WIDTH_BYTES = 4
MAX_WAV_SAMPLE_RATE_HZ = 1_000_000.0
MAX_WAV_FRAMES = 64 * 1024 * 1024
MAX_WAV_DECODE_BYTES = 512 * 1024 * 1024
MAX_WAV_DECODE_BLOCK_BYTES = 16 * 1024 * 1024
_FileVersion = tuple[int, int, int, int, int]


class EndOfMediaError(RuntimeError):
    """Raised when a decoder cannot provide the requested sequential frame."""

    def __init__(self, frame_index: int, path: str) -> None:
        self.frame_index = int(frame_index)
        self.path = str(path)
        super().__init__(f"Media ended or became unreadable at frame {self.frame_index}: {self.path}")


def validate_wav_header(
    channels: int,
    sample_width: int,
    sample_rate: float,
    frame_count: int,
) -> str | None:
    """Return a user-facing error for unsafe WAV metadata, otherwise ``None``."""

    if channels <= 0:
        return "WAV file has no channels"
    if channels > MAX_WAV_CHANNELS:
        return f"WAV channel count {channels} exceeds the supported limit of {MAX_WAV_CHANNELS}"
    if sample_width <= 0 or sample_width > MAX_WAV_SAMPLE_WIDTH_BYTES:
        return (
            f"WAV sample width {sample_width} bytes is outside the supported "
            f"1..{MAX_WAV_SAMPLE_WIDTH_BYTES} range"
        )
    if not isfinite(sample_rate) or sample_rate <= 0.0:
        return "WAV sample rate must be a positive finite number"
    if sample_rate > MAX_WAV_SAMPLE_RATE_HZ:
        return f"WAV sample rate must not exceed {MAX_WAV_SAMPLE_RATE_HZ:,.0f} Hz"
    if frame_count < 0:
        return "WAV frame count must be non-negative"
    if frame_count > MAX_WAV_FRAMES:
        return f"WAV frame count must not exceed {MAX_WAV_FRAMES:,}"
    decode_bytes = frame_count * channels * sample_width
    if decode_bytes > MAX_WAV_DECODE_BYTES:
        return (
            f"WAV decoded size {decode_bytes:,} bytes exceeds the supported "
            f"limit of {MAX_WAV_DECODE_BYTES:,} bytes"
        )
    return None


@dataclass(frozen=True)
class MediaIdentity:
    """Bounded-cost content identity stored with a project media source."""

    strategy: str
    sha256: str
    size_bytes: int
    sampled_bytes: int

    @property
    def complete(self) -> bool:
        return self.strategy == "full-sha256-v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
            "sampled_bytes": int(self.sampled_bytes),
        }

    @classmethod
    def from_dict(cls, data: object) -> MediaIdentity | None:
        if not isinstance(data, dict):
            return None
        strategy = data.get("strategy")
        digest = data.get("sha256")
        if strategy not in _MEDIA_IDENTITY_STRATEGIES or not isinstance(digest, str):
            return None
        normalized_digest = digest.strip().lower()
        if len(normalized_digest) != 64 or any(character not in "0123456789abcdef" for character in normalized_digest):
            return None
        try:
            size_bytes = int(data.get("size_bytes", -1))
            sampled_bytes = int(data.get("sampled_bytes", -1))
        except (TypeError, ValueError, OverflowError):
            return None
        if size_bytes < 0 or sampled_bytes < 0 or sampled_bytes > size_bytes:
            return None
        if strategy == "full-sha256-v1" and (
            size_bytes > MEDIA_IDENTITY_FULL_LIMIT_BYTES or sampled_bytes != size_bytes
        ):
            return None
        if strategy == "sampled-sha256-v1" and (
            size_bytes <= MEDIA_IDENTITY_FULL_LIMIT_BYTES
            or sampled_bytes != MEDIA_IDENTITY_FULL_LIMIT_BYTES
        ):
            return None
        return cls(strategy, normalized_digest, size_bytes, sampled_bytes)


@dataclass(frozen=True)
class MediaInfo:
    fps: float = 0.0
    frame_count: int = 0
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    available: bool = False
    kind: str = "video"
    sample_rate_hz: float = 0.0
    channels: int = 0
    sample_width_bytes: int = 0
    error: str = ""
    source_identity: MediaIdentity | None = None


def _media_file_version(path: Path) -> _FileVersion:
    """Return fields that change when a source is replaced or rewritten."""

    stat_result = path.stat()
    if not S_ISREG(stat_result.st_mode):
        raise OSError(f"Media source is not a regular file: {path}")
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
        int(stat_result.st_ctime_ns),
    )


def _stable_media_identity(path: Path, expected_version: _FileVersion) -> tuple[MediaIdentity | None, str]:
    """Bind decoder metadata and content identity to one file version."""

    identity = probe_media_identity(path)
    try:
        current_version = _media_file_version(path)
    except OSError:
        current_version = None
    if identity is None or current_version != expected_version:
        return (
            None,
            f"Media file changed or could not be verified while it was inspected; retry: {path}",
        )
    return identity, ""


def probe_media_identity(path: str | Path) -> MediaIdentity | None:
    """Hash a small source fully or three bounded samples from a large source.

    The sampled strategy reads at most 768 KiB, independent of source size. It
    improves accidental replacement detection without claiming full-file
    cryptographic verification for large media.
    """

    media_path = Path(path)
    try:
        before = media_path.stat()
        size_bytes = int(before.st_size)
        with media_path.open("rb") as source:
            if size_bytes <= MEDIA_IDENTITY_FULL_LIMIT_BYTES:
                digest = sha256()
                sampled_bytes = 0
                while True:
                    block = source.read(MEDIA_IDENTITY_CHUNK_BYTES)
                    if not block:
                        break
                    digest.update(block)
                    sampled_bytes += len(block)
                strategy = "full-sha256-v1"
            else:
                digest = sha256()
                digest.update(b"neo-tracker-sampled-sha256-v1\0")
                digest.update(size_bytes.to_bytes(16, "big", signed=False))
                positions = (
                    0,
                    (size_bytes - MEDIA_IDENTITY_CHUNK_BYTES) // 2,
                    size_bytes - MEDIA_IDENTITY_CHUNK_BYTES,
                )
                sampled_bytes = 0
                for position in positions:
                    source.seek(position)
                    block = source.read(MEDIA_IDENTITY_CHUNK_BYTES)
                    if len(block) != MEDIA_IDENTITY_CHUNK_BYTES:
                        return None
                    digest.update(position.to_bytes(16, "big", signed=False))
                    digest.update(block)
                    sampled_bytes += len(block)
                strategy = "sampled-sha256-v1"
        after = media_path.stat()
    except (OSError, OverflowError):
        return None
    if (
        int(after.st_size) != size_bytes
        or int(after.st_mtime_ns) != int(before.st_mtime_ns)
        or int(after.st_ino) != int(before.st_ino)
    ):
        return None
    return MediaIdentity(strategy, digest.hexdigest(), size_bytes, sampled_bytes)


def has_media_backend() -> bool:
    return _load_cv2() is not None


def probe_media(path: str) -> MediaInfo:
    media_path = Path(path)
    if not media_path.exists():
        return MediaInfo(error=f"Media file does not exist: {path}")
    if media_path.suffix.lower() == ".wav":
        return probe_wav_media(path)
    try:
        source_version = _media_file_version(media_path)
    except OSError as exc:
        return MediaInfo(error=f"Could not inspect media file: {path} ({exc})")
    cv2 = _load_cv2()
    if cv2 is None:
        return MediaInfo(error=_backend_error_message())

    capture = cv2.VideoCapture(str(media_path))
    if not capture.isOpened():
        return MediaInfo(error=f"Could not open media file: {path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    source_identity, identity_error = _stable_media_identity(media_path, source_version)
    if identity_error:
        return MediaInfo(error=identity_error)
    duration_s = float(frame_count / fps) if fps > 0.0 and frame_count > 0 else 0.0
    return MediaInfo(
        fps=fps,
        frame_count=frame_count,
        width=width,
        height=height,
        duration_s=duration_s,
        available=True,
        kind="video",
        source_identity=source_identity,
    )


def video_info(path: str) -> MediaInfo:
    info = probe_media(path)
    if info.available and info.kind != "video":
        return MediaInfo(error=f"Expected a video file, got {info.kind}: {path}")
    return info


def probe_wav_media(path: str) -> MediaInfo:
    media_path = Path(path)
    if not media_path.exists():
        return MediaInfo(kind="audio", error=f"Media file does not exist: {path}")
    try:
        source_version = _media_file_version(media_path)
    except OSError as exc:
        return MediaInfo(kind="audio", error=f"Could not inspect WAV file: {path} ({exc})")
    try:
        with wave.open(str(media_path), "rb") as wav:
            channels = int(wav.getnchannels())
            sample_width = int(wav.getsampwidth())
            sample_rate = float(wav.getframerate())
            frame_count = int(wav.getnframes())
    except Exception as exc:
        return MediaInfo(kind="audio", error=f"Could not open WAV file: {path} ({exc})")
    validation_error = validate_wav_header(channels, sample_width, sample_rate, frame_count)
    if validation_error is not None:
        return MediaInfo(kind="audio", error=f"{validation_error}: {path}")
    source_identity, identity_error = _stable_media_identity(media_path, source_version)
    if identity_error:
        return MediaInfo(kind="audio", error=identity_error)
    duration_s = float(frame_count / sample_rate) if sample_rate > 0.0 and frame_count > 0 else 0.0
    return MediaInfo(
        fps=sample_rate,
        frame_count=frame_count,
        duration_s=duration_s,
        available=True,
        kind="audio",
        sample_rate_hz=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        source_identity=source_identity,
    )


class MediaReader:
    """Small stateful video reader for preview playback.

    OpenCV random seeking is noticeably slower and less reliable on some
    compressed streams than sequential reads. The UI keeps one reader per task
    and asks it for adjacent frames during playback.
    """

    def __init__(self, path: str) -> None:
        self.path = str(path)
        media_path = Path(self.path)
        if not media_path.exists():
            raise FileNotFoundError(f"Media file does not exist: {self.path}")
        try:
            source_version = _media_file_version(media_path)
        except OSError as exc:
            raise RuntimeError(f"Could not inspect media file: {self.path} ({exc})") from exc
        if media_path.suffix.lower() == ".wav":
            raise RuntimeError("MediaReader supports video files only. Use the signal processing workflow for WAV audio.")
        self._cv2 = _load_cv2()
        if self._cv2 is None:
            raise RuntimeError(_backend_error_message())
        self._capture = self._cv2.VideoCapture(str(media_path))
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise RuntimeError(f"Could not open media file: {self.path}")
        try:
            self._verify_source_unchanged(source_version)
        except RuntimeError:
            self._capture.release()
            self._capture = None
            raise
        self._source_version = source_version

        fps = float(self._capture.get(self._cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(self._capture.get(self._cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(self._capture.get(self._cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self._capture.get(self._cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_s = float(frame_count / fps) if fps > 0.0 and frame_count > 0 else 0.0
        self.info = MediaInfo(
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
            duration_s=duration_s,
            available=True,
            kind="video",
        )
        self._next_frame_index = int(self._capture.get(self._cv2.CAP_PROP_POS_FRAMES) or 0)

    def close(self) -> None:
        capture = getattr(self, "_capture", None)
        if capture is not None:
            capture.release()
        self._capture = None
        self._next_frame_index = 0

    def read_frame(self, frame_index: int) -> np.ndarray:
        """Decode one RGB frame with a contiguous result for public callers."""
        return self._read_frame(frame_index, color_mode="rgb")

    def read_frame_for_processing(self, frame_index: int) -> np.ndarray:
        """Decode one RGB frame without an extra full-frame color copy.

        The returned channel-reversed view is safe for the NumPy tracking
        pipeline and Review recomputation. Public callers continue to use
        ``read_frame`` so its contiguous-array contract remains unchanged.
        """
        return self._read_frame(
            frame_index,
            color_mode="rgb-view",
            clamp_to_reported_length=False,
        )

    def read_frame_for_display(self, frame_index: int) -> np.ndarray:
        """Decode one contiguous BGR frame for Qt's native BGR888 display path.

        OpenCV already owns the decoded frame in this layout. Preview callers
        can hand it to ``QImage.Format_BGR888`` and keep the existing owning
        Qt copy without first allocating a second full-frame RGB array. Code
        that inspects pixel colors must continue to use ``read_frame`` or a
        channel-reversed view of this result.
        """

        return self._read_frame(frame_index, color_mode="bgr")

    def _read_frame(
        self,
        frame_index: int,
        *,
        color_mode: Literal["rgb", "rgb-view", "bgr"],
        clamp_to_reported_length: bool = True,
    ) -> np.ndarray:
        if self._capture is None:
            raise RuntimeError(f"Media reader is closed: {self.path}")
        self._verify_source_unchanged(self._source_version)
        index = self._clamp_frame(frame_index) if clamp_to_reported_length else max(0, int(frame_index))
        if index < self._next_frame_index or index - self._next_frame_index > 12:
            self._seek_or_recover(index)
        elif not self._grab_to(index):
            self._seek_or_recover(index)

        ok, frame = self._capture.read()
        if not ok or frame is None:
            self._seek_or_recover(index)
            ok, frame = self._capture.read()
            if not ok or frame is None:
                raise EndOfMediaError(index, self.path)
        self._next_frame_index = index + 1
        if color_mode == "bgr":
            return frame if frame.flags.c_contiguous else np.ascontiguousarray(frame)
        if color_mode == "rgb":
            return self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        return frame[:, :, ::-1]

    def _seek_or_recover(self, index: int) -> None:
        """Position the next decode exactly, never trusting a rejected seek."""

        if self._capture is None:
            raise RuntimeError(f"Media reader is closed: {self.path}")
        seek_succeeded = bool(self._capture.set(self._cv2.CAP_PROP_POS_FRAMES, index))
        landed_at = self._reported_frame_position() if seek_succeeded else None
        if landed_at is not None and 0 <= landed_at <= index:
            self._next_frame_index = landed_at
            if self._grab_to(index):
                return

        self._reopen_capture()
        if not self._grab_to(index):
            raise EndOfMediaError(index, self.path)

    def _verify_source_unchanged(self, expected_version: _FileVersion) -> None:
        """Fail closed when the media path no longer matches the opened source."""

        try:
            current_version = _media_file_version(Path(self.path))
        except OSError as exc:
            raise RuntimeError(
                f"Media source changed or became unreadable while it was open: {self.path} ({exc})"
            ) from exc
        if current_version != expected_version:
            raise RuntimeError(
                f"Media source changed while it was open; reopen or retry with the new file: {self.path}"
            )

    def _reported_frame_position(self) -> int | None:
        if self._capture is None:
            return None
        try:
            position = float(self._capture.get(self._cv2.CAP_PROP_POS_FRAMES))
        except Exception:
            return None
        if not np.isfinite(position) or position < 0.0:
            return None
        rounded = int(round(position))
        if abs(position - rounded) > 1e-6:
            return None
        return rounded

    def _grab_to(self, index: int) -> bool:
        if self._capture is None:
            return False
        while self._next_frame_index < index:
            if not self._capture.grab():
                return False
            self._next_frame_index += 1
        return self._next_frame_index == index

    def _reopen_capture(self) -> None:
        self._verify_source_unchanged(self._source_version)
        capture = self._capture
        if capture is not None:
            capture.release()
        replacement = self._cv2.VideoCapture(self.path)
        if not replacement.isOpened():
            replacement.release()
            self._capture = None
            self._next_frame_index = 0
            raise RuntimeError(f"Could not reopen media file after a failed seek: {self.path}")
        try:
            self._verify_source_unchanged(self._source_version)
        except RuntimeError:
            replacement.release()
            self._capture = None
            self._next_frame_index = 0
            raise
        self._capture = replacement
        self._next_frame_index = 0

    def _clamp_frame(self, frame_index: int) -> int:
        index = max(0, int(frame_index))
        if self.info.frame_count > 0:
            index = min(index, self.info.frame_count - 1)
        return index

    def __enter__(self) -> "MediaReader":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def read_video_frame(path: str, frame_index: int) -> np.ndarray:
    info = probe_media(path)
    if not info.available:
        raise RuntimeError(info.error or f"Could not open media file: {path}")
    if info.kind != "video":
        raise RuntimeError(f"read_video_frame requires a video file, got {info.kind}.")
    with MediaReader(path) as reader:
        return reader.read_frame(frame_index)


def read_frame(path: str, frame_index: int) -> np.ndarray:
    return read_video_frame(path, frame_index)


def _load_cv2() -> Any | None:
    global _CV2, _CV2_IMPORT_ERROR
    if _CV2 is not None:
        return _CV2
    if _CV2_IMPORT_ERROR is not None:
        return None
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception as exc:
        _CV2_IMPORT_ERROR = exc
        return None
    _CV2 = cv2
    return _CV2


def _backend_error_message() -> str:
    detail = f" ({_CV2_IMPORT_ERROR})" if _CV2_IMPORT_ERROR else ""
    return "OpenCV media backend is not installed. Install the media extra or opencv-python to enable video preview." + detail
