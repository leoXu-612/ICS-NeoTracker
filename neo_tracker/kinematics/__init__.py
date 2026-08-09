"""Public contracts for frame-aligned kinematics analysis."""

from .protocols import (
    CancellationProbe,
    DerivativeOperator,
    FitOperator,
    KinematicsEngine,
    ProgressReporter,
    SeriesBuilder,
)
from .derivatives import derive_series
from .export import export_csv, export_markdown, export_npz
from .fitting import KinematicsFitOperator, fit_series
from .residuals import residual_series
from .runtime import (
    CancellationToken,
    KinematicsCancelled,
    KinematicsEngineRuntime,
)
from .series import (
    TrackingResultSnapshot,
    TrackingSeriesBuilder,
    snapshot_tracker_results,
)
from .smoothing import smooth_series
from .types import (
    DerivativeConfig,
    DerivativeMethod,
    EdgePolicy,
    FitModel,
    FitRequest,
    FitResult,
    FitStatus,
    GapPolicy,
    KinematicsBundle,
    ProcessingStep,
    SampleSeries,
)
from .validation import (
    StaleSourceRevisionError,
    cadence_relative_deviation,
    contiguous_valid_segments,
    derivative_unit,
    fit_parameter_names,
    fit_parameter_units,
    fit_sample_mask,
    require_current_revision,
    require_uniform_cadence,
)

__all__ = [
    "CancellationProbe",
    "CancellationToken",
    "DerivativeConfig",
    "DerivativeMethod",
    "DerivativeOperator",
    "EdgePolicy",
    "FitModel",
    "FitOperator",
    "FitRequest",
    "FitResult",
    "FitStatus",
    "GapPolicy",
    "KinematicsBundle",
    "KinematicsCancelled",
    "KinematicsEngine",
    "KinematicsEngineRuntime",
    "KinematicsFitOperator",
    "ProcessingStep",
    "ProgressReporter",
    "SampleSeries",
    "SeriesBuilder",
    "StaleSourceRevisionError",
    "cadence_relative_deviation",
    "contiguous_valid_segments",
    "derivative_unit",
    "fit_parameter_names",
    "fit_parameter_units",
    "fit_sample_mask",
    "fit_series",
    "derive_series",
    "export_csv",
    "export_markdown",
    "export_npz",
    "residual_series",
    "require_current_revision",
    "require_uniform_cadence",
    "smooth_series",
    "snapshot_tracker_results",
    "TrackingResultSnapshot",
    "TrackingSeriesBuilder",
]
