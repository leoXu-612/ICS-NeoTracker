from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.ui.review_response import (
    ReviewResponse,
    ReviewResponseRequest,
    ReviewResponseService,
)


ResponseComputer = Callable[[ReviewResponseRequest], ReviewResponse]


class ReviewResponseWorker(QObject):
    """Recompute one historical response map outside the Qt event loop."""

    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        request: ReviewResponseRequest,
        *,
        computer: ResponseComputer | None = None,
    ) -> None:
        super().__init__()
        self.request = request
        self.computer = computer or ReviewResponseService.compute
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @Slot()
    def run(self) -> None:
        try:
            if self.cancellation_requested:
                self.canceled.emit()
                return
            response = self.computer(self.request)
            if self.cancellation_requested:
                self.canceled.emit()
                return
            self.completed.emit(response)
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
