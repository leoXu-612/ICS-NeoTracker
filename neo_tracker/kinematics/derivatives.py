from __future__ import annotations

"""Gap-aware differentiation on the real frame-aligned time axis."""

import hashlib
import json

import numpy as np

from .protocols import CancellationProbe
from .runtime import check_cancelled
from .types import DerivativeConfig, DerivativeMethod, EdgePolicy, ProcessingStep, SampleSeries
from .validation import contiguous_valid_segments, derivative_unit


def _config_digest(config: DerivativeConfig) -> str:
    payload = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _validate_segment_time(time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    delta = np.diff(time_s)
    if not np.isfinite(time_s).all() or not np.isfinite(delta).all() or np.any(delta <= 0.0):
        raise ValueError("valid segment time_s values must be finite and strictly increasing")
    return time_s, delta


def _first_derivative(
    time_s: np.ndarray,
    values: np.ndarray,
    *,
    edge_policy: EdgePolicy,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(values)
    output = np.full(count, np.nan, dtype=np.float64)
    valid = np.zeros(count, dtype=bool)
    if count < 2:
        return output, valid
    _, delta = _validate_segment_time(time_s)
    if count == 2:
        if edge_policy is EdgePolicy.ONE_SIDED:
            with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                slope = (values[1] - values[0]) / delta[0]
            if not np.isfinite(slope):
                raise ValueError("time spacing is numerically unstable for first derivative")
            output[:] = slope
            valid[:] = True
        return output, valid

    h0 = delta[:-1]
    h1 = delta[1:]
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        c0 = -h1 / (h0 * (h0 + h1))
        c1 = (h1 - h0) / (h0 * h1)
        c2 = h0 / (h1 * (h0 + h1))
        interior = c0 * values[:-2] + c1 * values[1:-1] + c2 * values[2:]
    if not np.isfinite(interior).all():
        raise ValueError("time spacing is numerically unstable for first derivative")
    output[1:-1] = interior
    valid[1:-1] = True

    if edge_policy is EdgePolicy.ONE_SIDED:
        first_h0, first_h1 = delta[0], delta[1]
        last_h0, last_h1 = delta[-2], delta[-1]
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            output[0] = (
                -(2.0 * first_h0 + first_h1) / (first_h0 * (first_h0 + first_h1)) * values[0]
                + (first_h0 + first_h1) / (first_h0 * first_h1) * values[1]
                - first_h0 / (first_h1 * (first_h0 + first_h1)) * values[2]
            )
            output[-1] = (
                last_h1 / (last_h0 * (last_h0 + last_h1)) * values[-3]
                - (last_h0 + last_h1) / (last_h0 * last_h1) * values[-2]
                + (last_h0 + 2.0 * last_h1) / (last_h1 * (last_h0 + last_h1)) * values[-1]
            )
        if not np.isfinite(output[[0, -1]]).all():
            raise ValueError("time spacing is numerically unstable for one-sided derivative")
        valid[[0, -1]] = True
    return output, valid


def _second_derivative(
    time_s: np.ndarray,
    values: np.ndarray,
    *,
    edge_policy: EdgePolicy,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(values)
    output = np.full(count, np.nan, dtype=np.float64)
    valid = np.zeros(count, dtype=bool)
    if count < 3:
        return output, valid
    _, delta = _validate_segment_time(time_s)
    h0 = delta[:-1]
    h1 = delta[1:]
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        c0 = 2.0 / (h0 * (h0 + h1))
        c1 = -2.0 / (h0 * h1)
        c2 = 2.0 / (h1 * (h0 + h1))
        interior = c0 * values[:-2] + c1 * values[1:-1] + c2 * values[2:]
    if not np.isfinite(interior).all():
        raise ValueError("time spacing is numerically unstable for second derivative")
    output[1:-1] = interior
    valid[1:-1] = True
    if edge_policy is EdgePolicy.ONE_SIDED:
        output[0] = interior[0]
        output[-1] = interior[-1]
        valid[[0, -1]] = True
    return output, valid


def _finite_difference(
    series: SampleSeries,
    config: DerivativeConfig,
    *,
    cancellation: CancellationProbe | None,
) -> SampleSeries:
    output = np.full(len(series), np.nan, dtype=np.float64)
    output_mask = np.zeros(len(series), dtype=bool)
    check_cancelled(cancellation, phase="nonuniform derivative")
    for start, stop in contiguous_valid_segments(series.valid_mask):
        check_cancelled(cancellation, phase="nonuniform derivative")
        segment_time = series.time_s[start:stop]
        segment_values = series.values[start:stop]
        if config.order == 1:
            values, mask = _first_derivative(
                segment_time,
                segment_values,
                edge_policy=config.edge_policy,
            )
        else:
            values, mask = _second_derivative(
                segment_time,
                segment_values,
                edge_policy=config.edge_policy,
            )
        output[start:stop] = values
        output_mask[start:stop] = mask
    check_cancelled(cancellation, phase="nonuniform derivative")
    return _derived_series(series, config, output, output_mask)


def _derived_series(
    series: SampleSeries,
    config: DerivativeConfig,
    values: np.ndarray,
    valid_mask: np.ndarray,
) -> SampleSeries:
    label = "d/dt" if config.order == 1 else "d²/dt²"
    return SampleSeries(
        series_id=(
            f"{series.series_id}:derivative:{config.order}:"
            f"{config.method.value}:{_config_digest(config)}"
        ),
        name=f"{label} {series.name}",
        frame_indices=series.frame_indices,
        time_s=series.time_s,
        values=values,
        valid_mask=valid_mask,
        unit=derivative_unit(series.unit, config.order),
        source_kind="derived",
        source_revision=series.source_revision,
        processing_chain=series.processing_chain
        + (ProcessingStep(config.method.value, config.to_dict()),),
        metadata={
            "source_series_id": series.series_id,
            "derivative_order": config.order,
            "method": config.method.value,
            "edge_policy": config.edge_policy.value,
            "gap_policy": config.gap_policy.value,
        },
    )


def derive_series(
    series: SampleSeries,
    config: DerivativeConfig,
    *,
    cancellation: CancellationProbe | None = None,
) -> SampleSeries:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    if not isinstance(config, DerivativeConfig):
        raise TypeError("config must be a DerivativeConfig")
    if config.method is DerivativeMethod.NONUNIFORM_FINITE_DIFFERENCE:
        return _finite_difference(series, config, cancellation=cancellation)
    from .smoothing import savgol_derivative

    return savgol_derivative(series, config, cancellation=cancellation)


class KinematicsDerivativeOperator:
    def derive(
        self,
        series: SampleSeries,
        config: DerivativeConfig,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> SampleSeries:
        return derive_series(series, config, cancellation=cancellation)


__all__ = ["KinematicsDerivativeOperator", "derive_series"]
