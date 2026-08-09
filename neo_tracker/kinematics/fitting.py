from __future__ import annotations

"""Stable polynomial regression and optional bounded nonlinear fitting."""

import math
from typing import Any

import numpy as np

from .models import evaluate_model, normalize_sinusoidal_parameters
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


DEFAULT_MAX_EVALUATIONS = 4_000


class _KinematicsUnavailable(RuntimeError):
    pass


def _load_least_squares() -> Any | None:
    try:
        from scipy.optimize import least_squares
    except (ImportError, ModuleNotFoundError):
        return None
    return least_squares


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


def _initial_exponential(time_s: np.ndarray, observed: np.ndarray) -> np.ndarray:
    span = float(time_s[-1] - time_s[0])
    data_range = float(np.ptp(observed))
    scale = max(data_range, max(1.0, abs(float(np.mean(observed)))) * 1e-3)
    offset = float(np.min(observed) - 0.05 * scale)
    first = float(observed[0] - offset)
    last = float(observed[-1] - offset)
    if first == 0.0 or last == 0.0 or first * last <= 0.0:
        rate = -1.0 / span
    else:
        rate = math.log(abs(last / first)) / span
    exponent = rate * float(time_s[0])
    if exponent > 700.0 or exponent < -745.0:
        amplitude = first
    else:
        amplitude = first / math.exp(exponent)
    return np.array([amplitude, rate, offset], dtype=np.float64)


def _initial_sinusoidal(
    time_s: np.ndarray,
    observed: np.ndarray,
    cancellation: CancellationProbe | None,
) -> np.ndarray:
    span = float(time_s[-1] - time_s[0])
    delta = np.diff(time_s)
    cadence = float(np.median(delta))
    minimum_omega = max(2.0 * math.pi / (span * 4.0), np.finfo(np.float64).eps)
    maximum_omega = min(math.pi / cadence, 2.0 * math.pi * 100.0 / span)
    if maximum_omega <= minimum_omega:
        maximum_omega = minimum_omega * 2.0
    candidates = np.linspace(minimum_omega, maximum_omega, 256, dtype=np.float64)
    best_error = math.inf
    observed_origin = float(np.mean(observed))
    centered_observed = observed - observed_origin
    best = np.array(
        [max(float(np.ptp(observed)) * 0.5, 1e-9), candidates[0], 0.0, observed_origin]
    )
    observed_sum = float(np.sum(centered_observed))
    sample_count = float(len(observed))
    for omega in candidates:
        check_cancelled(cancellation, phase="sinusoidal initial estimate")
        sine = np.sin(omega * time_s)
        cosine = np.cos(omega * time_s)
        normal = np.array(
            [
                [np.dot(sine, sine), np.dot(sine, cosine), np.sum(sine)],
                [np.dot(sine, cosine), np.dot(cosine, cosine), np.sum(cosine)],
                [np.sum(sine), np.sum(cosine), sample_count],
            ],
            dtype=np.float64,
        )
        right_hand_side = np.array(
            [
                np.dot(sine, centered_observed),
                np.dot(cosine, centered_observed),
                observed_sum,
            ],
            dtype=np.float64,
        )
        if np.linalg.matrix_rank(normal) != 3:
            continue
        coefficients = np.linalg.solve(normal, right_hand_side)
        fitted = (
            coefficients[0] * sine
            + coefficients[1] * cosine
            + coefficients[2]
        )
        residual = centered_observed - fitted
        error = float(np.dot(residual, residual))
        if error < best_error:
            sine_coefficient, cosine_coefficient, relative_offset = coefficients
            amplitude = math.hypot(float(sine_coefficient), float(cosine_coefficient))
            phase = math.atan2(float(cosine_coefficient), float(sine_coefficient))
            best = np.array(
                [amplitude, omega, phase, observed_origin + float(relative_offset)],
                dtype=np.float64,
            )
            best_error = error
    return best


def _nonlinear_initial_parameters(
    request: FitRequest,
    time_s: np.ndarray,
    observed: np.ndarray,
    cancellation: CancellationProbe | None,
) -> np.ndarray:
    names = fit_parameter_names(request.model)
    unknown = sorted(set(request.initial_parameters) - set(names))
    if unknown:
        raise ValueError(f"unknown initial parameters: {', '.join(unknown)}")
    if all(name in request.initial_parameters for name in names):
        values = np.array([request.initial_parameters[name] for name in names], dtype=np.float64)
    elif request.model is FitModel.EXPONENTIAL:
        values = _initial_exponential(time_s, observed)
    else:
        values = _initial_sinusoidal(time_s, observed, cancellation)
    for index, name in enumerate(names):
        if name in request.initial_parameters:
            values[index] = request.initial_parameters[name]
    if not np.isfinite(values).all():
        raise ValueError("nonlinear initial parameters must be finite")
    return values


def _nonlinear_bounds(
    request: FitRequest,
    time_s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    names = fit_parameter_names(request.model)
    unknown = sorted(set(request.bounds) - set(names))
    if unknown:
        raise ValueError(f"unknown parameter bounds: {', '.join(unknown)}")
    lower = np.full(len(names), -np.inf, dtype=np.float64)
    upper = np.full(len(names), np.inf, dtype=np.float64)
    if request.model is FitModel.EXPONENTIAL:
        max_abs_time = max(float(np.max(np.abs(time_s))), np.finfo(np.float64).eps)
        rate_index = names.index("rate")
        lower[rate_index] = -700.0 / max_abs_time
        upper[rate_index] = 700.0 / max_abs_time
    else:
        span = float(time_s[-1] - time_s[0])
        cadence = float(np.median(np.diff(time_s)))
        omega_index = names.index("omega")
        amplitude_index = names.index("amplitude")
        lower[amplitude_index] = 0.0
        lower[omega_index] = max(np.finfo(np.float64).eps, 2.0 * math.pi / (span * 20.0))
        upper[omega_index] = math.pi / cadence
    for index, name in enumerate(names):
        if name in request.bounds:
            lower[index], upper[index] = request.bounds[name]
    return lower, upper


def _normalized_sinusoidal_covariance(
    raw_parameters: np.ndarray,
    covariance: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    transform = np.eye(4, dtype=np.float64)
    if raw_parameters[1] < 0.0:
        transform[1, 1] = -1.0
        transform[2, 2] = -1.0
    if raw_parameters[0] < 0.0:
        transform[0, 0] = -1.0
    normalized = normalize_sinusoidal_parameters(raw_parameters)
    return normalized, transform @ covariance @ transform.T


def nonlinear_fit(
    series: SampleSeries,
    request: FitRequest,
    mask: np.ndarray,
    cancellation: CancellationProbe | None,
    *,
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS,
) -> FitResult:
    least_squares = _load_least_squares()
    if least_squares is None:
        raise _KinematicsUnavailable(
            f"{request.model.value} fitting requires the optional science dependency"
        )
    if isinstance(max_evaluations, bool) or not isinstance(max_evaluations, int) or max_evaluations <= 0:
        raise ValueError("max_evaluations must be a positive integer")
    names = fit_parameter_names(request.model)
    parameter_count = len(names)
    sample_count = int(np.count_nonzero(mask))
    if sample_count < parameter_count:
        raise ValueError(
            f"{request.model.value} fit requires at least {parameter_count} eligible samples"
        )
    time_s = series.time_s[mask]
    observed = series.values[mask]
    initial = _nonlinear_initial_parameters(request, time_s, observed, cancellation)
    lower, upper = _nonlinear_bounds(request, time_s)
    if np.any(initial < lower) or np.any(initial > upper):
        raise ValueError("nonlinear initial parameters fall outside safe or requested bounds")

    optimization_initial = np.array(initial, copy=True)
    optimization_lower = np.array(lower, copy=True)
    optimization_upper = np.array(upper, copy=True)
    observed_origin = 0.0
    objective_observed = observed
    if request.model is FitModel.SINUSOIDAL:
        offset_index = names.index("offset")
        observed_origin = float(np.mean(observed))
        objective_observed = observed - observed_origin
        optimization_initial[offset_index] -= observed_origin
        optimization_lower[offset_index] -= observed_origin
        optimization_upper[offset_index] -= observed_origin

    def objective(parameters: np.ndarray) -> np.ndarray:
        check_cancelled(cancellation, phase=f"{request.model.value} fit")
        if request.model is FitModel.SINUSOIDAL:
            amplitude, omega, phase, relative_offset = parameters
            return (
                amplitude * np.sin(omega * time_s + phase)
                + relative_offset
                - objective_observed
            )
        return evaluate_model(request.model, parameters, time_s) - objective_observed

    optimization = least_squares(
        objective,
        optimization_initial,
        bounds=(optimization_lower, optimization_upper),
        max_nfev=max_evaluations,
        method="trf",
    )
    check_cancelled(cancellation, phase=f"{request.model.value} fit")
    if not bool(optimization.success):
        message = str(getattr(optimization, "message", "nonlinear fit did not converge"))
        raise ValueError(f"{request.model.value} fit did not converge: {message}")
    raw_parameters = np.array(optimization.x, dtype=np.float64, copy=True)
    if request.model is FitModel.SINUSOIDAL:
        raw_parameters[names.index("offset")] += observed_origin
    if not np.isfinite(raw_parameters).all():
        raise ValueError("nonlinear optimizer produced non-finite parameters")
    predicted = np.full(len(series), np.nan, dtype=np.float64)
    residuals = np.full(len(series), np.nan, dtype=np.float64)

    covariance = np.full((parameter_count, parameter_count), np.nan, dtype=np.float64)
    jacobian = np.asarray(optimization.jac, dtype=np.float64)
    if sample_count > parameter_count and jacobian.shape == (sample_count, parameter_count):
        rank = int(np.linalg.matrix_rank(jacobian))
        if rank == parameter_count:
            raw_residual = np.asarray(optimization.fun, dtype=np.float64)
            sigma_squared = float(np.dot(raw_residual, raw_residual)) / (
                sample_count - parameter_count
            )
            covariance = sigma_squared * np.linalg.inv(jacobian.T @ jacobian)
            covariance = (covariance + covariance.T) * 0.5
    parameters = raw_parameters
    if request.model is FitModel.SINUSOIDAL:
        parameters, covariance = _normalized_sinusoidal_covariance(raw_parameters, covariance)
    predicted[mask] = evaluate_model(request.model, parameters, time_s)
    residuals[mask] = observed - predicted[mask]
    metrics = residual_metrics(series.values, predicted, mask)
    if np.isfinite(covariance).all():
        diagonal = np.diag(covariance)
        diagonal = np.where((diagonal < 0.0) & (diagonal > -1e-14), 0.0, diagonal)
        standard_errors = np.sqrt(diagonal)
        if not np.isfinite(standard_errors).all():
            covariance[:] = np.nan
            standard_errors = np.full(parameter_count, np.nan, dtype=np.float64)
    else:
        standard_errors = np.full(parameter_count, np.nan, dtype=np.float64)
    return FitResult(
        series_id=series.series_id,
        model=request.model,
        parameter_names=names,
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
    max_evaluations: int,
) -> FitResult:
    try:
        check_cancelled(cancellation, phase="fit")
        mask = fit_sample_mask(series, request)
        if request.model in (FitModel.LINEAR, FitModel.QUADRATIC):
            return _linear_regression(series, request, mask, cancellation=cancellation)
        return nonlinear_fit(
            series,
            request,
            mask,
            cancellation,
            max_evaluations=max_evaluations,
        )
    except KinematicsCancelled as exc:
        return _terminal_result(series, request, FitStatus.CANCELLED, str(exc))
    except StaleSourceRevisionError as exc:
        return _terminal_result(series, request, FitStatus.STALE, str(exc))
    except _KinematicsUnavailable as exc:
        return _terminal_result(series, request, FitStatus.UNAVAILABLE, str(exc))
    except (ArithmeticError, FloatingPointError, TypeError, ValueError, np.linalg.LinAlgError) as exc:
        return _terminal_result(series, request, FitStatus.FAILED, str(exc))


def fit_series(
    series: SampleSeries,
    request: FitRequest,
    *,
    cancellation: CancellationProbe | None = None,
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS,
) -> FitResult:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    if not isinstance(request, FitRequest):
        raise TypeError("request must be a FitRequest")
    return _fit_or_terminal(
        series,
        request,
        cancellation=cancellation,
        max_evaluations=max_evaluations,
    )


class KinematicsFitOperator:
    def __init__(self, *, max_evaluations: int = DEFAULT_MAX_EVALUATIONS) -> None:
        if (
            isinstance(max_evaluations, bool)
            or not isinstance(max_evaluations, int)
            or max_evaluations <= 0
        ):
            raise ValueError("max_evaluations must be a positive integer")
        self._max_evaluations = max_evaluations

    def fit(
        self,
        series: SampleSeries,
        request: FitRequest,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> FitResult:
        return fit_series(
            series,
            request,
            cancellation=cancellation,
            max_evaluations=self._max_evaluations,
        )


__all__ = [
    "DEFAULT_MAX_EVALUATIONS",
    "KinematicsFitOperator",
    "fit_series",
    "nonlinear_fit",
]
