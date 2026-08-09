from __future__ import annotations

"""Runtime helpers that keep numerical work cancellable and UI-neutral."""

from threading import Event
from typing import Protocol


class CancellationLike(Protocol):
    def is_cancelled(self) -> bool:
        """Return whether the current operation should stop."""


class KinematicsCancelled(RuntimeError):
    """Raised before a cancelled operation can publish a partial result."""


class CancellationToken:
    """Small thread-safe cancellation probe for engine and benchmark callers."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def is_cancelled(cancellation: CancellationLike | None) -> bool:
    return bool(cancellation is not None and cancellation.is_cancelled())


def check_cancelled(cancellation: CancellationLike | None, *, phase: str = "kinematics") -> None:
    if is_cancelled(cancellation):
        raise KinematicsCancelled(f"{phase} cancelled")
