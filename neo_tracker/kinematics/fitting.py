from __future__ import annotations

"""Stable linear/quadratic regression and bounded nonlinear fit dispatch."""

from collections.abc import Callable

import numpy as np

from .models import evaluate_model
from .protocols import CancellationProbe
from .residuals import residual_metrics
from .runtime import KinematicsCancelled, check_cancelled
from .types import FitModel, FitRequest, FitResult, FitStatus, SampleSeries
from .validation import (
    StaleSourceRevisionError,
    fit_parameter_names,
    fit_parameter_units,
    fit_sample_mask,
)


def _terminal_result(
    series: SampleSeries,
    request: FitRequest,
    status: FitStatus,
    message: str,
) -> FitResult:
    empty = np.array([], dtype=np.float64)
    aligned = np.full(len(series), np.nan, dtype=np.float64)
    return FitResult(
        series_id=series.series_id,
        model=request.model,
        parameter_names=(),
        parameters=empty,
        parameter_units=(),
        standard_errors=empty,
        covariance=np.empty((0, 0), dtype=np.float64),
        predicted=aligned,
        residuals=aligned,
        valid_mask=np.zeros(len(series), dtype=bool),
        rmse=float("nan"),
        r_squared=float("nan"),
        sample_count=0,
        range_start_s=request.range_start_s,
        range_end_s=request.range_end_s,
        source_revision=request.source_revision,
        status=status,
        message=message,
    )


def _design_and_transform(
    model: FitModel,
    time_s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    center = float(np.mean(time_s))
    scale = float(np.max(np.abs(time_s - center)))
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("fit time range is rank deficient")
    normalized = (time_s - center) / scale
    if model is FitModel.LINEAR:
        design = np.column_stack((normalized, np.ones_like(normalized)))
        transform = np.array(
            [[1.0 / scale, 0.0], [-center / scale, 1.0]],
            dtype=np.float64,
        )
    else:
        design = np.column_stack((normalized**2, normalized, np.ones_like(normalized)))
        transform = np.array(
            [
                [1.0 / scale**2, 0.0, 0.0],
                [-2.0 * center / scale**2, 1.0 / scale, 0.0],
                [center**2 / scale**2, -center / scale, 1.0],
            ],
            dtype=np.float64,
        )
    return design, transform


def _linear_regression(
    series: SampleSeries,
    request: FitRequest,
    mask: np.ndarray,
    *,
    cancellation: CancellationProbe | None,
) -> FitResult:
    parameter_names = fit_parameter_names(request.model)
    parameter_count = len(parameter_names)
    sample_count = int(np.count_nonzero(mask))
    if sample_count < parameter_count:
        raise ValueError(
            f"{request.model.value} fit requires at least {parameter_count} eligible samples"
        )
    if request.bounds:
        raise ValueError("bounds are supported only for nonlinear models")
    time_s = series.time_s[mask]
    observed = series.values[mask]
    design, transform = _design_and_transform(request.model, time_s)
    check_cancelled(cancellation, phase=f"{request.model.value} fit")
    scaled_parameters, _, rank, _ = np.linalg.lstsq(design, observed, rcond=None)
    if int(rank) != parameter_count:
        raise ValueError(f"{request.model.value} fit is rank deficient")
    parameters = transform @ scaled_parameters
    if not np.isfinite(parameters).all():
        raise ValueError("fit parameter conversion produced non-finite values")
    predicted = np.full(len(series), np.nan, dtype=np.float64)
    predicted[mask] = evaluate_model(request.model, parameters, time_s)
    residuals = np.full(len(series), np.nan, dtype=np.float64)
    residuals[mask] = series.values[mask] - predicted[mask]
    metrics = residual_metrics(series.values, predicted, mask)

    if sample_count > parameter_count:
        sigma_squared = metrics.sum_squared_error / (sample_count - parameter_count)
        covariance_scaled = sigma_squared * np.linalg.inv(design.T @ design)
        covariance = transform @ covariance_scaled @ transform.T
        covariance = (covariance + covariance.T) * 0.5
        diagonal = np.diag(covariance)
        diagonal = np.where((diagonal < 0.0) & (diagonal > -1e-14), 0.0, diagonal)
        standard_errors = np.sqrt(diagonal)
        if not np.isfinite(covariance).all() or not np.isfinite(standard_errors).all():
            covariance = np.full((parameter_count, parameter_count), np.nan, dtype=np.float64)
            standard_errors = np.full(parameter_count, np.nan, dtype=np.float64)
    else:
        covariance = np.full((parameter_count, parameter_count), np.nan, dtype=np.float64)
        standard_errors = np.full(parameter_count, np.nan, dtype=np.float64)
    check_cancelled(cancellation, phase=f"{request.model.value} fit")
    return FitResult(
        series_id=series.series_id,
        model=request.model,
        parameter_names=parameter_names,
        parameters=parameters,
        parameter_units=fit_parameter_units(request.model, series.unit),
        standard_errors=standard_errors,
        covariance=covariance,
        predicted=predicted,
        residuals=residuals,
        valid_mask=mask,
        rmse=metrics.rmse,
        r_squared=metrics.r_squared,
        sample_count=metrics.sample_count,
        range_start_s=request.range_start_s,
        range_end_s=request.range_end_s,
        source_revision=series.source_revision,
        status=FitStatus.OK,
        message="",
    )


def _fit_or_terminal(
    series: SampleSeries,
    request: FitRequest,
    *,
    cancellation: CancellationProbe | None,
    nonlinear: Callable[[SampleSeries, FitRequest, np.ndarray, CancellationProbe | None], FitResult]
    | None = None,
) -> FitResult:
    try:
        check_cancelled(cancellation, phase="fit")
        mask = fit_sample_mask(series, request)
        if request.model in (FitModel.LINEAR, FitModel.QUADRATIC):
            return _linear_regression(series, request, mask, cancellation=cancellation)
        if nonlinear is None:
            from .nonlinear_fitting import nonlinear_fit

            nonlinear = nonlinear_fit
        return nonlinear(series, request, mask, cancellation)
    except KinematicsCancelled as exc:
        return _terminal_result(series, request, FitStatus.CANCELLED, str(exc))
    except StaleSourceRevisionError as exc:
        return _terminal_result(series, request, FitStatus.STALE, str(exc))
    except (ArithmeticError, FloatingPointError, TypeError, ValueError, np.linalg.LinAlgError) as exc:
        return _terminal_result(series, request, FitStatus.FAILED, str(exc))


def fit_series(
    series: SampleSeries,
    request: FitRequest,
    *,
    cancellation: CancellationProbe | None = None,
) -> FitResult:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    if not isinstance(request, FitRequest):
        raise TypeError("request must be a FitRequest")
    return _fit_or_terminal(series, request, cancellation=cancellation)


class KinematicsFitOperator:
    def fit(
        self,
        series: SampleSeries,
        request: FitRequest,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> FitResult:
        return fit_series(series, request, cancellation=cancellation)


__all__ = ["KinematicsFitOperator", "fit_series"]
