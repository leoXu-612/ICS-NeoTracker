from __future__ import annotations

"""Residual metrics with stable constant-series semantics."""

import math
from dataclasses import dataclass

import numpy as np


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
    scale = max(1.0, float(np.dot(y, y)))
    threshold = np.finfo(np.float64).eps * scale * max(1, count)
    if total_sum_squares <= threshold:
        r_squared = 1.0 if sum_squared_error <= threshold else 0.0
    else:
        r_squared = 1.0 - sum_squared_error / total_sum_squares
    return ResidualMetrics(rmse, r_squared, count, sum_squared_error)


__all__ = ["ResidualMetrics", "residual_metrics"]
