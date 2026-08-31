from __future__ import annotations

"""Cross-object validation and stable unit rules for kinematics contracts."""

import math

import numpy as np

from .types import FitModel, FitRequest, SampleSeries, _numeric_float


class StaleSourceRevisionError(ValueError):
    """Raised when a derived request/result no longer matches its source snapshot."""


def require_current_revision(
    source_revision: str,
    current_revision: str,
    *,
    context: str = "analysis",
) -> None:
    if not source_revision or not current_revision or source_revision != current_revision:
        raise StaleSourceRevisionError(
            f"{context} is stale: source revision {source_revision!r} "
            f"does not match current revision {current_revision!r}"
        )


def derivative_unit(unit: str, order: int) -> str:
    """Derive a display unit without guessing when the source unit is unknown."""

    if not isinstance(unit, str):
        raise TypeError("unit must be a string")
    if isinstance(order, (bool, np.bool_)) or not isinstance(order, (int, np.integer)):
        raise TypeError("derivative order must be an integer")
    order_value = int(order)
    if order_value not in (1, 2):
        raise ValueError("derivative order must be 1 or 2")
    if not unit:
        return ""
    suffixes = (("/s³", 3), ("/s²", 2), ("/s", 1))
    base, existing_order = unit, 0
    for suffix, suffix_order in suffixes:
        if unit.endswith(suffix):
            base = unit[: -len(suffix)]
            existing_order = suffix_order
            break
    total_order = existing_order + order_value
    if total_order == 1:
        return f"{base}/s"
    return f"{base}/s{str(total_order).translate(str.maketrans('23456789', '²³⁴⁵⁶⁷⁸⁹'))}"


def fit_parameter_names(model: FitModel | str) -> tuple[str, ...]:
    try:
        fit_model = model if isinstance(model, FitModel) else FitModel(model)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown fit model {model!r}") from exc
    return {
        FitModel.LINEAR: ("slope", "intercept"),
        FitModel.QUADRATIC: ("a", "b", "c"),
        FitModel.EXPONENTIAL: ("amplitude", "rate", "offset"),
        FitModel.SINUSOIDAL: ("amplitude", "omega", "phase", "offset"),
    }[fit_model]


def fit_parameter_units(model: FitModel | str, series_unit: str) -> tuple[str, ...]:
    fit_model = model if isinstance(model, FitModel) else FitModel(model)
    if not isinstance(series_unit, str):
        raise TypeError("series_unit must be a string")
    if fit_model is FitModel.LINEAR:
        return (derivative_unit(series_unit, 1), series_unit)
    if fit_model is FitModel.QUADRATIC:
        return (derivative_unit(series_unit, 2), derivative_unit(series_unit, 1), series_unit)
    if fit_model is FitModel.EXPONENTIAL:
        return (series_unit, "1/s", series_unit)
    return (series_unit, "rad/s", "rad", series_unit)


def contiguous_valid_segments(valid_mask: np.ndarray) -> tuple[tuple[int, int], ...]:
    mask = np.asarray(valid_mask)
    if mask.ndim != 1 or mask.dtype != np.dtype(bool):
        raise TypeError("valid_mask must be a one-dimensional boolean array")
    if mask.size == 0:
        return ()
    padded = np.concatenate((np.array([False]), mask, np.array([False])))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    return tuple((int(start), int(stop)) for start, stop in changes.reshape(-1, 2))


def cadence_relative_deviation(time_s: np.ndarray, valid_mask: np.ndarray) -> float:
    """Return max relative dt deviation within valid contiguous segments."""

    times = np.asarray(time_s, dtype=np.float64)
    mask = np.asarray(valid_mask)
    if times.ndim != 1 or mask.ndim != 1 or len(times) != len(mask):
        raise ValueError("time_s and valid_mask must be aligned one-dimensional arrays")
    if mask.dtype != np.dtype(bool):
        raise TypeError("valid_mask must contain booleans")
    deviations: list[float] = []
    for start, stop in contiguous_valid_segments(mask):
        if stop - start >= 2:
            segment = times[start:stop]
            if not np.isfinite(segment).all():
                raise ValueError("valid time_s values must be finite")
            delta = np.diff(segment)
            if np.any(delta <= 0.0):
                raise ValueError("valid time_s values must be strictly increasing")
            cadence = float(np.median(delta))
            if not math.isfinite(cadence) or cadence <= 0.0:
                deviations.append(math.inf)
            else:
                deviations.append(float(np.max(np.abs(delta - cadence)) / cadence))
    if not deviations:
        return math.inf
    return max(deviations)


def require_uniform_cadence(
    series: SampleSeries,
    *,
    tolerance: float,
) -> float:
    tolerance_value = _numeric_float(tolerance, "tolerance")
    if not math.isfinite(tolerance_value) or tolerance_value < 0.0 or tolerance_value >= 1.0:
        raise ValueError("tolerance must be finite and in [0, 1)")
    deviation = cadence_relative_deviation(series.time_s, series.valid_mask)
    if not math.isfinite(deviation) or deviation > tolerance_value:
        raise ValueError(
            "savgol_uniform requires uniform cadence; explicit resampling is not "
            f"available (relative deviation={deviation:.6g}, tolerance={tolerance_value:.6g})"
        )
    return deviation


def fit_sample_mask(series: SampleSeries, request: FitRequest) -> np.ndarray:
    """Validate request provenance and return its frame-aligned sample mask."""

    if request.series_id != series.series_id:
        raise ValueError(
            f"fit request series_id {request.series_id!r} does not match {series.series_id!r}"
        )
    require_current_revision(
        request.source_revision,
        series.source_revision,
        context=f"fit request for {series.series_id}",
    )
    mask = (
        np.isfinite(series.time_s)
        & np.isfinite(series.values)
        & (series.time_s >= request.range_start_s)
        & (series.time_s <= request.range_end_s)
    )
    if request.use_valid_only:
        mask &= series.valid_mask
    if not mask.any():
        raise ValueError("fit range contains no eligible samples")
    result = np.array(mask, dtype=bool, copy=True)
    result.setflags(write=False)
    return result
