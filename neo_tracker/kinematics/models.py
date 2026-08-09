from __future__ import annotations

"""Vectorized model evaluation with finite-output guards."""

import math

import numpy as np

from .types import FitModel


def linear_model(time_s: np.ndarray, slope: float, intercept: float) -> np.ndarray:
    return slope * np.asarray(time_s, dtype=np.float64) + intercept


def quadratic_model(time_s: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    time = np.asarray(time_s, dtype=np.float64)
    return (a * time + b) * time + c


def exponential_model(
    time_s: np.ndarray,
    amplitude: float,
    rate: float,
    offset: float,
) -> np.ndarray:
    time = np.asarray(time_s, dtype=np.float64)
    exponent = rate * time
    if not np.isfinite(exponent).all() or np.any(exponent > 700.0) or np.any(exponent < -745.0):
        raise ValueError("exponential parameters exceed the finite evaluation range")
    with np.errstate(over="ignore", invalid="ignore"):
        values = amplitude * np.exp(exponent) + offset
    if not np.isfinite(values).all():
        raise ValueError("exponential model produced non-finite values")
    return values


def sinusoidal_model(
    time_s: np.ndarray,
    amplitude: float,
    omega: float,
    phase: float,
    offset: float,
) -> np.ndarray:
    time = np.asarray(time_s, dtype=np.float64)
    values = amplitude * np.sin(omega * time + phase) + offset
    if not np.isfinite(values).all():
        raise ValueError("sinusoidal model produced non-finite values")
    return values


def normalize_sinusoidal_parameters(parameters: np.ndarray) -> np.ndarray:
    amplitude, omega, phase, offset = (float(value) for value in parameters)
    if omega < 0.0:
        omega = -omega
        phase = math.pi - phase
    if amplitude < 0.0:
        amplitude = -amplitude
        phase += math.pi
    phase = (phase + math.pi) % (2.0 * math.pi) - math.pi
    return np.array([amplitude, omega, phase, offset], dtype=np.float64)


def evaluate_model(
    model: FitModel | str,
    parameters: np.ndarray,
    time_s: np.ndarray,
) -> np.ndarray:
    fit_model = model if isinstance(model, FitModel) else FitModel(model)
    values = np.asarray(parameters, dtype=np.float64)
    expected = {
        FitModel.LINEAR: 2,
        FitModel.QUADRATIC: 3,
        FitModel.EXPONENTIAL: 3,
        FitModel.SINUSOIDAL: 4,
    }[fit_model]
    if values.ndim != 1 or len(values) != expected or not np.isfinite(values).all():
        raise ValueError(f"{fit_model.value} requires {expected} finite parameters")
    if fit_model is FitModel.LINEAR:
        result = linear_model(time_s, *values)
    elif fit_model is FitModel.QUADRATIC:
        result = quadratic_model(time_s, *values)
    elif fit_model is FitModel.EXPONENTIAL:
        result = exponential_model(time_s, *values)
    else:
        result = sinusoidal_model(time_s, *values)
    if not np.isfinite(result).all():
        raise ValueError(f"{fit_model.value} model produced non-finite values")
    return np.asarray(result, dtype=np.float64)


__all__ = [
    "evaluate_model",
    "exponential_model",
    "linear_model",
    "normalize_sinusoidal_parameters",
    "quadratic_model",
    "sinusoidal_model",
]
