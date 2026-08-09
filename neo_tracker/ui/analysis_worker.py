from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.analysis import AnalysisConfig, SignalSeries, validate_analysis_sample_count
from neo_tracker.ui.analysis_controller import AnalysisController, AnalysisRun, AnalysisSource


SeriesLoader = Callable[[Callable[[], bool]], SignalSeries]
AnalysisRunner = Callable[[AnalysisSource, SignalSeries, AnalysisConfig, int], AnalysisRun]


class AnalysisWorker(QObject):
    """Load and process one signal without blocking the Qt event loop."""

    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()
    stage_changed = Signal(str)

    def __init__(
        self,
        *,
        source: AnalysisSource,
        config: AnalysisConfig,
        owner_token: int,
        series_loader: SeriesLoader,
        runner: AnalysisRunner | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.config = config
        self.owner_token = int(owner_token)
        self.series_loader = series_loader
        self.runner = runner or AnalysisController.compute_run
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
            if self.source.sample_count > 0:
                validate_analysis_sample_count(self.source.sample_count, self.config)
            self.stage_changed.emit("loading")
            series = self.series_loader(self._cancel_requested.is_set)
            if self.cancellation_requested:
                self.canceled.emit()
                return
            self.stage_changed.emit("processing")
            result = self.runner(self.source, series, self.config, self.owner_token)
            if self.cancellation_requested:
                self.canceled.emit()
                return
            self.completed.emit(result)
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
