from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from stat import S_ISREG
from threading import Lock
from time import monotonic
from typing import Any, Callable, Protocol
from uuid import uuid4

import numpy as np

from neo_tracker.media import MediaIdentity, MediaInfo, MediaReader, probe_media, probe_media_identity


class CancellationFlag(Protocol):
    def is_set(self) -> bool: ...


class IsolatedMediaError(RuntimeError):
    """Raised when a disposable decoder helper cannot return a valid response."""


class IsolatedMediaTimeout(IsolatedMediaError):
    """Raised when a decoder helper misses its bounded request deadline."""


class IsolatedMediaCancelled(IsolatedMediaError):
    """Raised after cancellation has terminated the decoder helper."""


@dataclass(frozen=True)
class IsolatedMediaLimits:
    probe_timeout_s: float = 8.0
    preview_timeout_s: float = 5.0
    cancel_grace_s: float = 0.20
    kill_grace_s: float = 0.20
    poll_interval_s: float = 0.015
    max_metadata_bytes: int = 256 * 1024
    max_frame_bytes: int = 48 * 1024 * 1024
    max_dimension: int = 16_384
    max_pixels: int = 16_777_216
    max_frame_count: int = 2_147_483_647
    max_path_chars: int = 32_768


DEFAULT_ISOLATED_MEDIA_LIMITS = IsolatedMediaLimits()
_ERROR_TEXT_LIMIT = 8_192
_FRAME_ENVELOPE_BYTES = 64 * 1024
_MAX_DURATION_S = 10.0 * 365.25 * 24.0 * 60.0 * 60.0
_MAX_VIDEO_FPS = 1_000_000.0
_MAX_AUDIO_SAMPLE_RATE = 10_000_000.0
_ProcessTarget = Callable[..., None]
_SourceVersion = tuple[int, int, int, int, int]


def _regular_file_version(path: str) -> _SourceVersion:
    media_path = Path(path)
    try:
        result = media_path.stat()
    except OSError as exc:
        raise IsolatedMediaError(f"Could not inspect media file: {path} ({exc})") from exc
    if not S_ISREG(result.st_mode):
        raise IsolatedMediaError(f"Media source is not a regular file: {path}")
    return (
        int(result.st_dev),
        int(result.st_ino),
        int(result.st_size),
        int(result.st_mtime_ns),
        int(result.st_ctime_ns),
    )


def _source_version_to_wire(version: _SourceVersion) -> list[int]:
    return [int(value) for value in version]


def _source_version_from_wire(value: object) -> _SourceVersion:
    if (
        not isinstance(value, list)
        or len(value) != 5
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise IsolatedMediaError("Decoder helper returned invalid source version metadata")
    return (value[0], value[1], value[2], value[3], value[4])


def _validated_path(path: str, limits: IsolatedMediaLimits) -> str:
    normalized = _validated_path_syntax(path, limits)
    _regular_file_version(normalized)
    return normalized


def _validated_path_syntax(path: str, limits: IsolatedMediaLimits) -> str:
    """Validate only bounded request syntax; filesystem I/O belongs to workers."""

    normalized = str(path)
    if not normalized:
        raise IsolatedMediaError("Media path must not be empty")
    if len(normalized) > limits.max_path_chars:
        raise IsolatedMediaError("Media path exceeds the decoder request limit")
    return normalized


def validate_media_info(
    path: str,
    info: object,
    *,
    limits: IsolatedMediaLimits = DEFAULT_ISOLATED_MEDIA_LIMITS,
) -> MediaInfo:
    """Reject malformed or resource-amplifying metadata returned over IPC."""

    if not isinstance(info, MediaInfo):
        raise IsolatedMediaError("Decoder helper returned invalid media metadata")
    if info.kind not in {"video", "audio"}:
        raise IsolatedMediaError(f"Decoder helper returned an invalid media kind for {path}")
    numeric_values = {
        "fps": (info.fps, _MAX_VIDEO_FPS if info.kind == "video" else _MAX_AUDIO_SAMPLE_RATE),
        "duration": (info.duration_s, _MAX_DURATION_S),
        "sample rate": (info.sample_rate_hz, _MAX_AUDIO_SAMPLE_RATE),
    }
    for label, (raw_value, maximum) in numeric_values.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise IsolatedMediaError(f"Decoder helper returned invalid {label} metadata for {path}")
        value = float(raw_value)
        if not isfinite(value) or value < 0.0 or value > maximum:
            raise IsolatedMediaError(f"Decoder helper returned invalid {label} metadata for {path}")
    integer_values = {
        "frame count": (info.frame_count, limits.max_frame_count),
        "width": (info.width, limits.max_dimension),
        "height": (info.height, limits.max_dimension),
        "channels": (info.channels, 1_024),
    }
    validated: dict[str, int] = {}
    for label, (raw_value, maximum) in integer_values.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise IsolatedMediaError(f"Decoder helper returned invalid {label} metadata for {path}")
        value = raw_value
        if value < 0 or value > maximum:
            raise IsolatedMediaError(f"Decoder helper returned invalid {label} metadata for {path}")
        validated[label] = value
    if not isinstance(info.available, bool):
        raise IsolatedMediaError(f"Decoder helper returned invalid availability metadata for {path}")
    if info.kind == "video":
        width = validated["width"]
        height = validated["height"]
        if bool(width) != bool(height):
            raise IsolatedMediaError(f"Decoder helper returned incomplete video dimensions for {path}")
        pixels = width * height
        if pixels > limits.max_pixels or pixels * 3 > limits.max_frame_bytes:
            raise IsolatedMediaError(f"Video dimensions exceed the preview safety limit: {path}")
    elif info.available and (float(info.sample_rate_hz) <= 0.0 or validated["channels"] <= 0):
        raise IsolatedMediaError(f"Decoder helper returned incomplete audio metadata for {path}")
    identity = info.source_identity
    if identity is not None and (
        not isinstance(identity, MediaIdentity)
        or _media_identity_from_wire(identity.to_dict()) != identity
    ):
        raise IsolatedMediaError(f"Decoder helper returned invalid media identity metadata for {path}")
    if not isinstance(info.error, str) or len(info.error) > _ERROR_TEXT_LIMIT:
        raise IsolatedMediaError(f"Decoder helper returned an invalid media error for {path}")
    return info


def _validate_frame(
    path: str,
    frame_index: int,
    shape: object,
    dtype: object,
    frame_bytes: object,
    *,
    expected_width: int,
    expected_height: int,
    limits: IsolatedMediaLimits,
) -> np.ndarray:
    if (
        not isinstance(shape, tuple)
        or len(shape) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in shape)
    ):
        raise IsolatedMediaError("Decoder helper returned an invalid preview frame shape")
    height, width, channels = shape
    if height <= 0 or width <= 0 or channels != 3:
        raise IsolatedMediaError("Decoder helper returned an invalid preview frame shape")
    if height > limits.max_dimension or width > limits.max_dimension:
        raise IsolatedMediaError("Decoder helper returned an oversized preview frame")
    if height * width > limits.max_pixels:
        raise IsolatedMediaError("Decoder helper returned an oversized preview frame")
    if expected_width > 0 and width != expected_width:
        raise IsolatedMediaError(
            f"Decoded frame {frame_index} width does not match probed metadata for {path}"
        )
    if expected_height > 0 and height != expected_height:
        raise IsolatedMediaError(
            f"Decoded frame {frame_index} height does not match probed metadata for {path}"
        )
    if dtype != "uint8" or not isinstance(frame_bytes, bytes):
        raise IsolatedMediaError("Decoder helper returned an invalid preview frame payload")
    expected_bytes = height * width * channels
    if expected_bytes > limits.max_frame_bytes or len(frame_bytes) != expected_bytes:
        raise IsolatedMediaError("Decoder helper returned an invalid preview frame byte count")
    # ``frame_bytes`` owns immutable storage for the lifetime of the array.
    # Avoid a second 6 MiB copy for every 1080p preview frame; Qt makes its own
    # owning image copy before this result can be released.
    return np.frombuffer(frame_bytes, dtype=np.uint8).reshape(shape)


def _media_identity_from_wire(data: object) -> MediaIdentity | None:
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {"strategy", "sha256", "size_bytes", "sampled_bytes"}:
        raise IsolatedMediaError("Decoder helper returned invalid media identity metadata")
    if any(isinstance(data[name], bool) or not isinstance(data[name], int) for name in ("size_bytes", "sampled_bytes")):
        raise IsolatedMediaError("Decoder helper returned invalid media identity metadata")
    if not isinstance(data["strategy"], str) or not isinstance(data["sha256"], str):
        raise IsolatedMediaError("Decoder helper returned invalid media identity metadata")
    identity = MediaIdentity.from_dict(data)
    if identity is None:
        raise IsolatedMediaError("Decoder helper returned invalid media identity metadata")
    return identity


def _media_info_to_wire(info: MediaInfo) -> dict[str, object]:
    return {
        "fps": info.fps,
        "frame_count": info.frame_count,
        "width": info.width,
        "height": info.height,
        "duration_s": info.duration_s,
        "available": info.available,
        "kind": info.kind,
        "sample_rate_hz": info.sample_rate_hz,
        "channels": info.channels,
        "error": info.error,
        "source_identity": info.source_identity.to_dict() if info.source_identity is not None else None,
    }


def _media_info_from_wire(data: object) -> MediaInfo:
    names = {
        "fps",
        "frame_count",
        "width",
        "height",
        "duration_s",
        "available",
        "kind",
        "sample_rate_hz",
        "channels",
        "error",
        "source_identity",
    }
    if not isinstance(data, dict) or set(data) != names:
        raise IsolatedMediaError("Decoder helper returned invalid media metadata fields")
    identity = _media_identity_from_wire(data["source_identity"])
    return MediaInfo(
        fps=data["fps"],  # type: ignore[arg-type]
        frame_count=data["frame_count"],  # type: ignore[arg-type]
        width=data["width"],  # type: ignore[arg-type]
        height=data["height"],  # type: ignore[arg-type]
        duration_s=data["duration_s"],  # type: ignore[arg-type]
        available=data["available"],  # type: ignore[arg-type]
        kind=data["kind"],  # type: ignore[arg-type]
        sample_rate_hz=data["sample_rate_hz"],  # type: ignore[arg-type]
        channels=data["channels"],  # type: ignore[arg-type]
        error=data["error"],  # type: ignore[arg-type]
        source_identity=identity,
    )


def _encode_response(header: dict[str, object], raw: bytes = b"") -> bytes:
    encoded_header = json.dumps(
        header,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded_header) > DEFAULT_ISOLATED_MEDIA_LIMITS.max_metadata_bytes:
        raise IsolatedMediaError("Decoder response metadata exceeded IPC limit")
    return len(encoded_header).to_bytes(4, "big", signed=False) + encoded_header + raw


def _decode_response(payload: bytes, limits: IsolatedMediaLimits) -> tuple[dict[str, object], bytes]:
    if len(payload) < 4:
        raise IsolatedMediaError("Decoder helper returned an invalid IPC envelope")
    header_size = int.from_bytes(payload[:4], "big", signed=False)
    if header_size <= 0 or header_size > limits.max_metadata_bytes or 4 + header_size > len(payload):
        raise IsolatedMediaError("Decoder helper returned an invalid IPC header length")
    try:
        header = json.loads(payload[4 : 4 + header_size].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IsolatedMediaError("Decoder helper returned invalid JSON metadata") from exc
    if not isinstance(header, dict):
        raise IsolatedMediaError("Decoder helper returned invalid JSON metadata")
    return header, payload[4 + header_size :]


def _send_response(
    connection: Connection,
    header: dict[str, object],
    maximum: int,
    raw: bytes = b"",
) -> None:
    payload = _encode_response(header, raw)
    if len(payload) > maximum:
        payload = _encode_response(
            {
                "type": "error",
                "request_id": str(header.get("request_id", "")),
                "detail": "decoder response exceeded IPC limit",
            }
        )
    connection.send_bytes(payload)


def _isolated_media_process(
    connection: Connection,
    operation: str,
    request_id: str,
    path: str,
    frame_index: int,
    expected_width: int,
    expected_height: int,
    expected_identity: MediaIdentity | None,
    limits: IsolatedMediaLimits,
) -> None:
    """OpenCV boundary. A native fault here cannot terminate the GUI process."""

    response_limit = limits.max_metadata_bytes
    reader: MediaReader | None = None
    try:
        before = _regular_file_version(path)
        if expected_identity is not None and probe_media_identity(path) != expected_identity:
            raise RuntimeError(f"Media source changed before preview decode: {path}")
        if operation == "probe":
            result = probe_media(path)
            response = {
                "type": "probe",
                "request_id": request_id,
                "media_info": _media_info_to_wire(result),
            }
            raw = b""
        elif operation == "frame":
            reader = MediaReader(path)
            frame = np.ascontiguousarray(reader.read_frame_for_display(frame_index))
            if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
                raise RuntimeError("decoder returned a non-uint8 three-channel frame")
            height, width, _channels = frame.shape
            if expected_width > 0 and width != expected_width:
                raise RuntimeError("decoded frame width does not match probed metadata")
            if expected_height > 0 and height != expected_height:
                raise RuntimeError("decoded frame height does not match probed metadata")
            if frame.nbytes > limits.max_frame_bytes or height * width > limits.max_pixels:
                raise RuntimeError("decoded preview frame exceeds the safety limit")
            response = {
                "type": "frame",
                "request_id": request_id,
                "frame_index": int(frame_index),
                "shape": [int(value) for value in frame.shape],
                "dtype": "uint8",
            }
            raw = frame.tobytes(order="C")
            response_limit = limits.max_frame_bytes + _FRAME_ENVELOPE_BYTES
        else:
            raise ValueError(f"unknown isolated media operation: {operation}")
        after = _regular_file_version(path)
        if after != before:
            raise RuntimeError(f"Media source changed while it was decoded: {path}")
        if expected_identity is not None and probe_media_identity(path) != expected_identity:
            raise RuntimeError(f"Media source changed while it was decoded: {path}")
        _send_response(connection, response, response_limit, raw)
    except BaseException as exc:
        try:
            detail = f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_LIMIT]
            _send_response(
                connection,
                {"type": "error", "request_id": request_id, "detail": detail},
                limits.max_metadata_bytes,
            )
        except BaseException:
            pass
    finally:
        try:
            if reader is not None:
                reader.close()
        finally:
            connection.close()


def _isolated_preview_session_process(
    connection: Connection,
    path: str,
    expected_width: int,
    expected_height: int,
    expected_identity: MediaIdentity | None,
    limits: IsolatedMediaLimits,
) -> None:
    """Own one reusable MediaReader until the parent ends this session."""

    reader: MediaReader | None = None
    try:
        session_version = _regular_file_version(path)
        if expected_identity is not None and probe_media_identity(path) != expected_identity:
            raise RuntimeError(f"Media source changed before preview session startup: {path}")
        while True:
            try:
                payload = connection.recv_bytes(maxlength=limits.max_metadata_bytes)
            except (EOFError, OSError):
                return
            request_id = ""
            try:
                request, raw = _decode_response(payload, limits)
                request_id_value = request.get("request_id")
                if not isinstance(request_id_value, str) or len(request_id_value) != 32:
                    raise ValueError("invalid preview request id")
                request_id = request_id_value
                frame_index = request.get("frame_index")
                requested_version = _source_version_from_wire(request.get("source_version"))
                if (
                    set(request) != {"type", "request_id", "frame_index", "source_version"}
                    or request.get("type") != "frame-request"
                    or raw
                    or isinstance(frame_index, bool)
                    or not isinstance(frame_index, int)
                    or frame_index < 0
                    or frame_index > limits.max_frame_count
                ):
                    raise ValueError("invalid preview frame request")
                before = _regular_file_version(path)
                if before != session_version or before != requested_version:
                    raise RuntimeError(f"Media source changed during the preview session: {path}")
                if expected_identity is not None and probe_media_identity(path) != expected_identity:
                    raise RuntimeError(f"Media source changed during the preview session: {path}")
                if reader is None:
                    reader = MediaReader(path)
                frame = np.ascontiguousarray(reader.read_frame_for_display(frame_index))
                if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
                    raise RuntimeError("decoder returned a non-uint8 three-channel frame")
                height, width, _channels = frame.shape
                if expected_width > 0 and width != expected_width:
                    raise RuntimeError("decoded frame width does not match probed metadata")
                if expected_height > 0 and height != expected_height:
                    raise RuntimeError("decoded frame height does not match probed metadata")
                if frame.nbytes > limits.max_frame_bytes or height * width > limits.max_pixels:
                    raise RuntimeError("decoded preview frame exceeds the safety limit")
                after = _regular_file_version(path)
                if after != session_version:
                    raise RuntimeError(f"Media source changed while it was decoded: {path}")
                _send_response(
                    connection,
                    {
                        "type": "frame",
                        "request_id": request_id,
                        "frame_index": frame_index,
                        "shape": [int(value) for value in frame.shape],
                        "dtype": "uint8",
                        "source_version_before": _source_version_to_wire(before),
                        "source_version_after": _source_version_to_wire(after),
                    },
                    limits.max_frame_bytes + _FRAME_ENVELOPE_BYTES,
                    frame.tobytes(order="C"),
                )
            except BaseException as exc:
                try:
                    detail = f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_LIMIT]
                    _send_response(
                        connection,
                        {"type": "error", "request_id": request_id, "detail": detail},
                        limits.max_metadata_bytes,
                    )
                except BaseException:
                    return
    except BaseException:
        # Startup failures occur before a request id exists. Closing the pipe
        # gives the parent a typed crash/EOF failure without trusting stderr.
        return
    finally:
        try:
            if reader is not None:
                reader.close()
        finally:
            connection.close()


def _stop_process(process: Any, *, limits: IsolatedMediaLimits) -> None:
    if process is None:
        return
    try:
        process.join(timeout=max(0.0, float(limits.cancel_grace_s)))
        if process.is_alive():
            process.terminate()
            process.join(timeout=max(0.0, float(limits.cancel_grace_s)))
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
            else:
                process.terminate()
            process.join(timeout=max(0.01, float(limits.kill_grace_s)))
    finally:
        try:
            if not process.is_alive():
                process.close()
        except (OSError, ValueError):
            pass


def _terminate_process_now(process: Any, *, limits: IsolatedMediaLimits) -> None:
    """Terminate first; used when a session decoder is hung or superseded."""

    if process is None:
        return
    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=max(0.0, float(limits.cancel_grace_s)))
        else:
            process.join(timeout=0.0)
        if process.is_alive():
            kill = getattr(process, "kill", None)
            if callable(kill):
                kill()
            else:
                process.terminate()
            process.join(timeout=max(0.01, float(limits.kill_grace_s)))
    finally:
        try:
            if not process.is_alive():
                process.close()
        except (OSError, ValueError):
            pass


def _frame_from_response(
    response: dict[str, object],
    raw: bytes,
    *,
    request_id: str,
    path: str,
    frame_index: int,
    expected_width: int,
    expected_height: int,
    expected_identity: MediaIdentity | None,
    parent_version_before: _SourceVersion,
    limits: IsolatedMediaLimits,
) -> np.ndarray:
    if response.get("request_id") != request_id:
        raise IsolatedMediaError("Decoder helper returned a mismatched IPC response")
    if response.get("type") == "error":
        detail = response.get("detail")
        if set(response) != {"type", "request_id", "detail"} or not isinstance(detail, str):
            raise IsolatedMediaError("Decoder helper returned an invalid error response")
        raise IsolatedMediaError(detail[:_ERROR_TEXT_LIMIT])
    returned_index = response.get("frame_index")
    if (
        response.get("type") != "frame"
        or isinstance(returned_index, bool)
        or not isinstance(returned_index, int)
        or returned_index != int(frame_index)
        or set(response)
        != {
            "type",
            "request_id",
            "frame_index",
            "shape",
            "dtype",
            "source_version_before",
            "source_version_after",
        }
    ):
        raise IsolatedMediaError("Decoder helper returned an invalid preview response")
    child_version_before = _source_version_from_wire(response.get("source_version_before"))
    child_version_after = _source_version_from_wire(response.get("source_version_after"))
    parent_version_after = _regular_file_version(path)
    if not (
        parent_version_before
        == child_version_before
        == child_version_after
        == parent_version_after
    ):
        raise IsolatedMediaError(
            f"Media source changed before the decoded preview frame could be committed: {path}"
        )
    if expected_identity is not None and probe_media_identity(path) != expected_identity:
        raise IsolatedMediaError(
            f"Media source identity changed before the decoded preview frame could be committed: {path}"
        )
    return _validate_frame(
        path,
        int(frame_index),
        tuple(response.get("shape", ())) if isinstance(response.get("shape"), list) else response.get("shape"),
        response.get("dtype"),
        raw,
        expected_width=expected_width,
        expected_height=expected_height,
        limits=limits,
    )


class PreviewDecoderSession:
    """Reusable spawn-owned MediaReader for one immutable local file.

    Preview sources are intentionally restricted to local regular files. URLs,
    FIFOs, devices, and other potentially blocking path types are rejected by
    the worker before decoder startup.
    """

    def __init__(
        self,
        path: str,
        *,
        expected_width: int = 0,
        expected_height: int = 0,
        expected_identity: MediaIdentity | None = None,
        limits: IsolatedMediaLimits = DEFAULT_ISOLATED_MEDIA_LIMITS,
        process_target: _ProcessTarget = _isolated_preview_session_process,
    ) -> None:
        # Construction runs on the GUI thread. Keep it I/O-free; the first
        # worker-owned ``decode`` performs regular-file and identity checks.
        self.path = _validated_path_syntax(path, limits)
        self.expected_width = max(0, int(expected_width))
        self.expected_height = max(0, int(expected_height))
        self.expected_identity = expected_identity
        self.limits = limits
        self.process_target = process_target
        self._connection: Connection | None = None
        self._process: Any | None = None
        self._lock = Lock()
        self.start_count = 0

    def matches(
        self,
        path: str,
        *,
        expected_width: int,
        expected_height: int,
        expected_identity: MediaIdentity | None,
    ) -> bool:
        return bool(
            self.path == str(path)
            and self.expected_width == max(0, int(expected_width))
            and self.expected_height == max(0, int(expected_height))
            and self.expected_identity == expected_identity
        )

    @property
    def is_alive(self) -> bool:
        process = self._process
        return bool(process is not None and process.is_alive())

    def _ensure_started(self) -> None:
        if self.is_alive and self._connection is not None:
            return
        self._terminate_locked()
        context = get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=self.process_target,
            args=(
                child_connection,
                self.path,
                self.expected_width,
                self.expected_height,
                self.expected_identity,
                self.limits,
            ),
            name="neo-tracker-preview-session",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            parent_connection.close()
            child_connection.close()
            raise
        child_connection.close()
        self._connection = parent_connection
        self._process = process
        self.start_count += 1

    def decode(
        self,
        frame_index: int,
        *,
        cancellation_requested: CancellationFlag | None = None,
    ) -> np.ndarray:
        index = max(0, int(frame_index))
        request_id = uuid4().hex
        deadline = monotonic() + max(0.01, float(self.limits.preview_timeout_s))
        with self._lock:
            try:
                if cancellation_requested is not None and cancellation_requested.is_set():
                    raise IsolatedMediaCancelled("Preview decoder request was canceled")
                parent_version_before = _regular_file_version(self.path)
                if (
                    self.expected_identity is not None
                    and probe_media_identity(self.path) != self.expected_identity
                ):
                    raise IsolatedMediaError(
                        f"Media source identity changed before preview decode: {self.path}"
                    )
                self._ensure_started()
                connection = self._connection
                process = self._process
                if connection is None or process is None:
                    raise IsolatedMediaError("Preview decoder session did not start")
                request_payload = _encode_response(
                    {
                        "type": "frame-request",
                        "request_id": request_id,
                        "frame_index": index,
                        "source_version": _source_version_to_wire(parent_version_before),
                    }
                )
                if len(request_payload) > self.limits.max_metadata_bytes:
                    raise IsolatedMediaError("Preview request exceeded the IPC metadata limit")
                try:
                    connection.send_bytes(request_payload)
                except (BrokenPipeError, EOFError, OSError) as exc:
                    raise IsolatedMediaError(f"Preview decoder session IPC failed: {exc}") from exc
                maximum = self.limits.max_frame_bytes + _FRAME_ENVELOPE_BYTES
                while True:
                    if cancellation_requested is not None and cancellation_requested.is_set():
                        raise IsolatedMediaCancelled("Preview decoder request was canceled")
                    remaining = deadline - monotonic()
                    if remaining <= 0.0:
                        raise IsolatedMediaTimeout(
                            f"Preview decoder timed out after {self.limits.preview_timeout_s:.2f} seconds: "
                            f"{self.path}"
                        )
                    try:
                        ready = connection.poll(min(self.limits.poll_interval_s, remaining))
                    except (EOFError, OSError) as exc:
                        raise IsolatedMediaError(f"Preview decoder session IPC failed: {exc}") from exc
                    if ready:
                        try:
                            payload = connection.recv_bytes(maxlength=maximum)
                        except (EOFError, OSError) as exc:
                            raise IsolatedMediaError(
                                "Preview decoder closed IPC or exceeded the response size limit"
                            ) from exc
                        response, raw = _decode_response(payload, self.limits)
                        return _frame_from_response(
                            response,
                            raw,
                            request_id=request_id,
                            path=self.path,
                            frame_index=index,
                            expected_width=self.expected_width,
                            expected_height=self.expected_height,
                            expected_identity=self.expected_identity,
                            parent_version_before=parent_version_before,
                            limits=self.limits,
                        )
                    if not process.is_alive():
                        if connection.poll(0.0):
                            continue
                        raise IsolatedMediaError(
                            "Preview decoder exited before returning a result "
                            f"(exit code {process.exitcode})"
                        )
            except BaseException:
                # Any protocol, decoder, timeout, or cancellation failure makes
                # the reader state untrustworthy. The next request gets a new
                # process and a new MediaReader.
                self._terminate_locked()
                raise

    def _terminate_locked(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        _terminate_process_now(process, limits=self.limits)

    def terminate(self) -> None:
        with self._lock:
            self._terminate_locked()

    close = terminate

    def __enter__(self) -> "PreviewDecoderSession":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _run_isolated_request(
    operation: str,
    path: str,
    *,
    frame_index: int = 0,
    expected_width: int = 0,
    expected_height: int = 0,
    expected_identity: MediaIdentity | None = None,
    timeout_s: float,
    cancellation_requested: CancellationFlag | None = None,
    limits: IsolatedMediaLimits = DEFAULT_ISOLATED_MEDIA_LIMITS,
    process_target: _ProcessTarget = _isolated_media_process,
) -> tuple[dict[str, object], bytes]:
    normalized_path = _validated_path(path, limits)
    request_id = uuid4().hex
    context = get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=process_target,
        args=(
            send_connection,
            operation,
            request_id,
            normalized_path,
            int(frame_index),
            int(expected_width),
            int(expected_height),
            expected_identity,
            limits,
        ),
        name=f"neo-tracker-{operation}-decoder",
        daemon=True,
    )
    started = False
    deadline = monotonic() + max(0.01, float(timeout_s))
    try:
        process.start()
        started = True
        send_connection.close()
        maximum = (
            limits.max_metadata_bytes
            if operation == "probe"
            else limits.max_frame_bytes + _FRAME_ENVELOPE_BYTES
        )
        while True:
            if cancellation_requested is not None and cancellation_requested.is_set():
                raise IsolatedMediaCancelled(f"{operation.capitalize()} decoder request was canceled")
            remaining = deadline - monotonic()
            if remaining <= 0.0:
                raise IsolatedMediaTimeout(
                    f"{operation.capitalize()} decoder timed out after {float(timeout_s):.2f} seconds: {path}"
                )
            try:
                ready = receive_connection.poll(min(limits.poll_interval_s, remaining))
            except (EOFError, OSError) as exc:
                raise IsolatedMediaError(f"Decoder helper IPC failed: {exc}") from exc
            if ready:
                try:
                    payload = receive_connection.recv_bytes(maxlength=maximum)
                except (EOFError, OSError) as exc:
                    raise IsolatedMediaError(
                        "Decoder helper closed IPC or exceeded the response size limit"
                    ) from exc
                response, raw = _decode_response(payload, limits)
                if response.get("request_id") != request_id:
                    raise IsolatedMediaError("Decoder helper returned a mismatched IPC response")
                return response, raw
            if not process.is_alive():
                if receive_connection.poll(0.0):
                    continue
                exit_code = process.exitcode
                raise IsolatedMediaError(
                    f"Decoder helper exited before returning a result (exit code {exit_code})"
                )
    finally:
        try:
            receive_connection.close()
        finally:
            if not started:
                send_connection.close()
            if started:
                _stop_process(process, limits=limits)


def probe_media_isolated(
    path: str,
    *,
    cancellation_requested: CancellationFlag | None = None,
    limits: IsolatedMediaLimits = DEFAULT_ISOLATED_MEDIA_LIMITS,
    process_target: _ProcessTarget = _isolated_media_process,
) -> MediaInfo:
    response, raw = _run_isolated_request(
        "probe",
        path,
        timeout_s=limits.probe_timeout_s,
        cancellation_requested=cancellation_requested,
        limits=limits,
        process_target=process_target,
    )
    if response.get("type") == "error":
        detail = response.get("detail")
        if set(response) != {"type", "request_id", "detail"} or not isinstance(detail, str):
            raise IsolatedMediaError("Decoder helper returned an invalid error response")
        raise IsolatedMediaError(detail[:_ERROR_TEXT_LIMIT])
    if (
        response.get("type") != "probe"
        or set(response) != {"type", "request_id", "media_info"}
        or raw
    ):
        raise IsolatedMediaError("Decoder helper returned an invalid probe response")
    return validate_media_info(
        path,
        _media_info_from_wire(response.get("media_info")),
        limits=limits,
    )


def decode_preview_frame_isolated(
    path: str,
    frame_index: int,
    *,
    expected_width: int = 0,
    expected_height: int = 0,
    expected_identity: MediaIdentity | None = None,
    cancellation_requested: CancellationFlag | None = None,
    limits: IsolatedMediaLimits = DEFAULT_ISOLATED_MEDIA_LIMITS,
    process_target: _ProcessTarget = _isolated_media_process,
) -> np.ndarray:
    response, raw = _run_isolated_request(
        "frame",
        path,
        frame_index=max(0, int(frame_index)),
        expected_width=max(0, int(expected_width)),
        expected_height=max(0, int(expected_height)),
        expected_identity=expected_identity,
        timeout_s=limits.preview_timeout_s,
        cancellation_requested=cancellation_requested,
        limits=limits,
        process_target=process_target,
    )
    if response.get("type") == "error":
        detail = response.get("detail")
        if set(response) != {"type", "request_id", "detail"} or not isinstance(detail, str):
            raise IsolatedMediaError("Decoder helper returned an invalid error response")
        raise IsolatedMediaError(detail[:_ERROR_TEXT_LIMIT])
    returned_index = response.get("frame_index")
    if (
        response.get("type") != "frame"
        or isinstance(returned_index, bool)
        or not isinstance(returned_index, int)
        or returned_index != int(frame_index)
        or set(response) != {"type", "request_id", "frame_index", "shape", "dtype"}
    ):
        raise IsolatedMediaError("Decoder helper returned an invalid preview response")
    return _validate_frame(
        path,
        int(frame_index),
        tuple(response.get("shape", ())) if isinstance(response.get("shape"), list) else response.get("shape"),
        response.get("dtype"),
        raw,
        expected_width=max(0, int(expected_width)),
        expected_height=max(0, int(expected_height)),
        limits=limits,
    )
