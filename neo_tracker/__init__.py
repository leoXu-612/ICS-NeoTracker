"""Neo-Tracker adaptive physics tracking toolkit."""

from neo_tracker.core import (
    FilterUpdate,
    FrameContext,
    ObservationCandidate,
    ObservationResult,
    TrackerResult,
    TrackingPipeline,
)
from neo_tracker.analysis import (
    AnalysisConfig,
    FFTResult,
    STFTResult,
    SignalSeries,
    compute_fft,
    compute_stft,
    wav_signal_series,
)
from neo_tracker.presets import (
    circular_motion_preset,
    color_marker_preset,
    path_motion_preset,
    travelling_flame_preset,
    wavefront_preset,
)

__all__ = [
    "FilterUpdate",
    "FrameContext",
    "ObservationCandidate",
    "ObservationResult",
    "TrackerResult",
    "TrackingPipeline",
    "AnalysisConfig",
    "FFTResult",
    "STFTResult",
    "SignalSeries",
    "compute_fft",
    "compute_stft",
    "wav_signal_series",
    "circular_motion_preset",
    "color_marker_preset",
    "path_motion_preset",
    "travelling_flame_preset",
    "wavefront_preset",
]
