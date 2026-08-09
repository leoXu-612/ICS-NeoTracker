from __future__ import annotations

"""Runtime-neutral interfaces implemented by the v0.3 kinematics engine."""

from typing import Protocol, Sequence, runtime_checkable

from .types import DerivativeConfig, FitRequest, FitResult, SampleSeries


@runtime_checkable
class CancellationProbe(Protocol):
    def is_cancelled(self) -> bool:
        """Return True when the current immutable request should stop."""


@runtime_checkable
class ProgressReporter(Protocol):
    def __call__(self, completed: int, total: int, phase: str) -> None:
        """Receive bounded progress updates; implementations must not mutate inputs."""


@runtime_checkable
class SeriesBuilder(Protocol):
    def build_series(
        self,
        results_snapshot: Sequence[object],
        *,
        source_revision: str,
        cancellation: CancellationProbe | None = None,
        progress: ProgressReporter | None = None,
    ) -> tuple[SampleSeries, ...]:
        """Build frame-aligned base series from an immutable results snapshot."""


@runtime_checkable
class DerivativeOperator(Protocol):
    def derive(
        self,
        series: SampleSeries,
        config: DerivativeConfig,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> SampleSeries:
        """Return a new aligned series without modifying the source."""


@runtime_checkable
class FitOperator(Protocol):
    def fit(
        self,
        series: SampleSeries,
        request: FitRequest,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> FitResult:
        """Fit one immutable request and preserve source revision provenance."""


class KinematicsEngine(SeriesBuilder, DerivativeOperator, FitOperator, Protocol):
    """Combined engine boundary consumed by the Application layer."""
