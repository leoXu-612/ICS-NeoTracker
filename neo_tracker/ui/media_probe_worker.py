from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.media import MediaInfo, probe_media
from neo_tracker.ui.isolated_media import CancellationFlag, probe_media_isolated


MediaProbe = Callable[[str], MediaInfo]


def probe_media_safely(
    path: str,
    media_probe: MediaProbe,
    *,
    cancellation_requested: CancellationFlag | None = None,
) -> MediaInfo:
    """Inspect safely, isolating production video probes from the Python process.

    WAV metadata remains on the existing stdlib ``wave`` path. An explicitly
    injected probe remains an application/test extension point and is not
    claimed to have a process-isolation contract.
    """

    try:
        if media_probe in {probe_media, probe_media_for_ui} and Path(path).suffix.lower() != ".wav":
            return probe_media_isolated(
                path,
                cancellation_requested=cancellation_requested,
            )
        return media_probe(path)
    except Exception as exc:
        kind = "audio" if Path(path).suffix.lower() == ".wav" else "video"
        return MediaInfo(
            kind=kind,
            error=f"Could not inspect media file: {path} ({exc})",
        )


def probe_media_for_ui(path: str) -> MediaInfo:
    """Bounded production probe used by UI/controller call sites."""

    return probe_media_safely(path, probe_media)


class MediaProbeWorker(QObject):
    """Inspect a selected media batch without blocking the Qt event loop."""

    progressed = Signal(int, int, str)
    completed = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        paths: Sequence[str],
        *,
        media_probe: MediaProbe = probe_media,
    ) -> None:
        super().__init__()
        self.paths = tuple(str(path) for path in paths)
        self.media_probe = media_probe
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @Slot()
    def run(self) -> None:
        results: list[tuple[str, MediaInfo]] = []
        total = len(self.paths)
        try:
            for index, path in enumerate(self.paths, start=1):
                if self.cancellation_requested:
                    self.canceled.emit()
                    return
                info = probe_media_safely(
                    path,
                    self.media_probe,
                    cancellation_requested=self._cancel_requested,
                )
                if self.cancellation_requested:
                    self.canceled.emit()
                    return
                results.append((path, info))
                self.progressed.emit(index, total, Path(path).name)
            self.completed.emit(tuple(results))
        except Exception as exc:
            if self.cancellation_requested:
                self.canceled.emit()
            else:
                self.failed.emit(str(exc))
