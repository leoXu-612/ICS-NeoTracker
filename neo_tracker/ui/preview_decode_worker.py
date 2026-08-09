from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.media import MediaIdentity
from neo_tracker.ui.isolated_media import (
    IsolatedMediaCancelled,
    PreviewDecoderSession,
    decode_preview_frame_isolated,
)


@dataclass(frozen=True)
class PreviewDecodeRequest:
    owner_token: int
    media_path: str
    frame_index: int
    expected_width: int = 0
    expected_height: int = 0
    expected_identity: MediaIdentity | None = None


@dataclass(frozen=True)
class PreviewDecodeResult:
    request: PreviewDecodeRequest
    bgr_frame: np.ndarray


PreviewDecoder = Callable[..., np.ndarray]


class PreviewDecodeWorker(QObject):
    """Wait for one disposable decoder helper outside the Qt event loop."""

    completed = Signal(object)
    failed = Signal(object, str)
    canceled = Signal(object)

    def __init__(
        self,
        request: PreviewDecodeRequest,
        *,
        session: PreviewDecoderSession | None = None,
        decoder: PreviewDecoder = decode_preview_frame_isolated,
    ) -> None:
        super().__init__()
        self.request = request
        self.session = session
        self.decoder = decoder
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @Slot()
    def run(self) -> None:
        request = self.request
        if self.cancellation_requested:
            self.canceled.emit(request)
            return
        try:
            if self.session is not None:
                frame = self.session.decode(
                    request.frame_index,
                    cancellation_requested=self._cancel_requested,
                )
            else:
                frame = self.decoder(
                    request.media_path,
                    request.frame_index,
                    expected_width=request.expected_width,
                    expected_height=request.expected_height,
                    expected_identity=request.expected_identity,
                    cancellation_requested=self._cancel_requested,
                )
        except IsolatedMediaCancelled:
            self.canceled.emit(request)
            return
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit(request)
            else:
                self.failed.emit(request, str(exc))
            return
        if self.cancellation_requested:
            self.canceled.emit(request)
            return
        self.completed.emit(PreviewDecodeResult(request, frame))


def same_preview_request(first: PreviewDecodeRequest, second: PreviewDecodeRequest) -> bool:
    return bool(
        first.owner_token == second.owner_token
        and first.media_path == second.media_path
        and first.frame_index == second.frame_index
        and first.expected_identity == second.expected_identity
    )
