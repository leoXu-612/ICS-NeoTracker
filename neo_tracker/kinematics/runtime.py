from __future__ import annotations

"""Runtime helpers that keep numerical work cancellable and UI-neutral."""

from threading import Event
from typing import Mapping, Protocol, Sequence

from .protocols import CancellationProbe, ProgressReporter
from .types import DerivativeConfig, FitRequest, FitResult, SampleSeries


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


class KinematicsEngineRuntime:
    """Protocol-compatible facade with no UI or Qt dependency."""

    def __init__(
        self,
        *,
        units: Mapping[str, str] | None = None,
        max_fit_evaluations: int = 4_000,
    ) -> None:
        from .fitting import KinematicsFitOperator
        from .series import TrackingSeriesBuilder

        self._series_builder = TrackingSeriesBuilder(units=units)
        self._fit_operator = KinematicsFitOperator(max_evaluations=max_fit_evaluations)

    def build_series(
        self,
        results_snapshot: Sequence[object],
        *,
        source_revision: str,
        cancellation: CancellationProbe | None = None,
        progress: ProgressReporter | None = None,
    ) -> tuple[SampleSeries, ...]:
        return self._series_builder.build_series(
            results_snapshot,
            source_revision=source_revision,
            cancellation=cancellation,
            progress=progress,
        )

    def derive(
        self,
        series: SampleSeries,
        config: DerivativeConfig,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> SampleSeries:
        from .derivatives import derive_series

        return derive_series(series, config, cancellation=cancellation)

    def fit(
        self,
        series: SampleSeries,
        request: FitRequest,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> FitResult:
        return self._fit_operator.fit(series, request, cancellation=cancellation)


__all__ = [
    "CancellationLike",
    "CancellationToken",
    "KinematicsCancelled",
    "KinematicsEngineRuntime",
    "check_cancelled",
    "is_cancelled",
]
