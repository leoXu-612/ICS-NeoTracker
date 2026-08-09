from __future__ import annotations

"""Residual metrics with stable constant-series semantics."""

import math
import hashlib
from dataclasses import dataclass

import numpy as np

from .types import FitResult, FitStatus, ProcessingStep, SampleSeries


@dataclass(frozen=True)
class ResidualMetrics:
    rmse: float
    r_squared: float
    sample_count: int
    sum_squared_error: float


def residual_metrics(
    observed: np.ndarray,
    predicted: np.ndarray,
    valid_mask: np.ndarray,
) -> ResidualMetrics:
    actual = np.asarray(observed, dtype=np.float64)
    fitted = np.asarray(predicted, dtype=np.float64)
    mask = np.asarray(valid_mask)
    if actual.ndim != 1 or fitted.ndim != 1 or mask.ndim != 1:
        raise ValueError("observed, predicted, and valid_mask must be one-dimensional")
    if len(actual) != len(fitted) or len(actual) != len(mask):
        raise ValueError("observed, predicted, and valid_mask lengths must match")
    if mask.dtype != np.dtype(bool):
        raise TypeError("valid_mask must contain booleans")
    count = int(np.count_nonzero(mask))
    if count == 0:
        raise ValueError("residual metrics require at least one valid sample")
    y = actual[mask]
    y_hat = fitted[mask]
    if not np.isfinite(y).all() or not np.isfinite(y_hat).all():
        raise ValueError("residual metric samples must be finite")
    residual = y - y_hat
    sum_squared_error = float(np.dot(residual, residual))
    rmse = math.sqrt(sum_squared_error / count)
    centered = y - float(np.mean(y))
    total_sum_squares = float(np.dot(centered, centered))
    maximum_magnitude = float(np.max(np.abs(y)))
    ulp = abs(float(np.spacing(maximum_magnitude)))
    amplitude_tolerance = 8.0 * ulp
    constant = float(np.ptp(y)) <= amplitude_tolerance
    if constant:
        error_tolerance = amplitude_tolerance**2 * count
        r_squared = 1.0 if sum_squared_error <= error_tolerance else 0.0
    else:
        r_squared = 1.0 - sum_squared_error / total_sum_squares
    return ResidualMetrics(rmse, r_squared, count, sum_squared_error)


def residual_series(series: SampleSeries, fit: FitResult) -> SampleSeries:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    if not isinstance(fit, FitResult):
        raise TypeError("fit must be a FitResult")
    if fit.status is not FitStatus.OK:
        raise ValueError("residual series requires a successful fit result")
    if fit.series_id != series.series_id:
        raise ValueError("fit result series_id does not match the source series")
    if fit.source_revision != series.source_revision:
        raise ValueError("fit result source revision is stale")
    if len(fit.residuals) != len(series):
        raise ValueError("fit residuals must remain frame aligned")
    mask = np.asarray(fit.valid_mask & series.valid_mask, dtype=bool)
    values = np.full(len(series), np.nan, dtype=np.float64)
    values[mask] = fit.residuals[mask]
    digest = hashlib.sha256(
        b"\0".join(
            (
                fit.model.value.encode("utf-8"),
                fit.parameters.tobytes(),
                repr((fit.range_start_s, fit.range_end_s)).encode("ascii"),
            )
        )
    ).hexdigest()[:12]
    parameters = {
        "model": fit.model.value,
        "range_start_s": fit.range_start_s,
        "range_end_s": fit.range_end_s,
        "rmse": fit.rmse,
        "r_squared": fit.r_squared,
        "sample_count": fit.sample_count,
    }
    return SampleSeries(
        series_id=f"{series.series_id}:residual:{digest}",
        name=f"Residual {series.name}",
        frame_indices=series.frame_indices,
        time_s=series.time_s,
        values=values,
        valid_mask=mask,
        unit=series.unit,
        source_kind="fit_residual",
        source_revision=series.source_revision,
        processing_chain=series.processing_chain
        + (ProcessingStep("fit_residual", parameters),),
        metadata={"source_series_id": series.series_id, **parameters},
    )


__all__ = ["ResidualMetrics", "residual_metrics", "residual_series"]
