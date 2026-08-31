from __future__ import annotations

"""Explicit segment-aware Savitzky-Golay smoothing and differentiation."""

import hashlib
import json
import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .protocols import CancellationProbe
from .runtime import check_cancelled
from .types import (
    DerivativeConfig,
    DerivativeMethod,
    EdgePolicy,
    ProcessingStep,
    SampleSeries,
    _numeric_float,
)
from .validation import contiguous_valid_segments, derivative_unit, require_uniform_cadence


def _validate_options(
    window_length: int,
    polyorder: int,
    derivative_order: int,
    uniformity_tolerance: float,
    edge_policy: EdgePolicy | str,
) -> tuple[int, int, int, float, EdgePolicy]:
    if isinstance(window_length, bool) or not isinstance(window_length, int):
        raise TypeError("window_length must be an integer")
    if window_length < 3 or window_length % 2 == 0:
        raise ValueError("window_length must be an odd integer of at least 3")
    if isinstance(polyorder, bool) or not isinstance(polyorder, int):
        raise TypeError("polyorder must be an integer")
    if polyorder < derivative_order or polyorder >= window_length:
        raise ValueError("polyorder must cover derivative order and be smaller than window_length")
    if derivative_order not in (0, 1, 2):
        raise ValueError("derivative_order must be 0, 1, or 2")
    tolerance = _numeric_float(uniformity_tolerance, "uniformity_tolerance")
    if not math.isfinite(tolerance) or tolerance < 0.0 or tolerance >= 1.0:
        raise ValueError("uniformity_tolerance must be finite and in [0, 1)")
    try:
        edge = edge_policy if isinstance(edge_policy, EdgePolicy) else EdgePolicy(edge_policy)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown edge policy {edge_policy!r}") from exc
    return window_length, polyorder, derivative_order, tolerance, edge


def _weights(offsets: np.ndarray, polyorder: int, derivative_order: int) -> np.ndarray:
    design = np.vander(offsets, N=polyorder + 1, increasing=True)
    weights = math.factorial(derivative_order) * np.linalg.pinv(design)[derivative_order]
    if not np.isfinite(weights).all():
        raise ValueError("Savitzky-Golay coefficients are numerically unstable")
    return weights


def _segment_cadence(time_s: np.ndarray) -> float:
    delta = np.diff(time_s)
    if delta.size == 0 or not np.isfinite(delta).all() or np.any(delta <= 0.0):
        raise ValueError("Savitzky-Golay segment requires strictly increasing finite time_s")
    cadence = float(np.median(delta))
    if not math.isfinite(cadence) or cadence <= 0.0:
        raise ValueError("Savitzky-Golay cadence is numerically unstable")
    return cadence


def _filter_segment(
    time_s: np.ndarray,
    values: np.ndarray,
    *,
    window_length: int,
    polyorder: int,
    derivative_order: int,
    edge_policy: EdgePolicy,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(values)
    output = np.full(count, np.nan, dtype=np.float64)
    valid = np.zeros(count, dtype=bool)
    if count < window_length:
        return output, valid
    cadence = _segment_cadence(time_s)
    half = window_length // 2
    centered_offsets = np.arange(-half, half + 1, dtype=np.float64) * cadence
    centered_weights = _weights(centered_offsets, polyorder, derivative_order)
    windows = sliding_window_view(values, window_length)
    with np.errstate(over="ignore", invalid="ignore"):
        centered = windows @ centered_weights
    if not np.isfinite(centered).all():
        raise ValueError("Savitzky-Golay output is numerically unstable")
    output[half : count - half] = centered
    valid[half : count - half] = True

    if edge_policy is EdgePolicy.ONE_SIDED:
        positions = np.arange(window_length, dtype=np.float64)
        first_window = values[:window_length]
        last_window = values[-window_length:]
        for index in range(half):
            first_offsets = (positions - float(index)) * cadence
            last_target = window_length - half + index
            last_offsets = (positions - float(last_target)) * cadence
            output[index] = float(first_window @ _weights(first_offsets, polyorder, derivative_order))
            output[count - half + index] = float(
                last_window @ _weights(last_offsets, polyorder, derivative_order)
            )
        if not np.isfinite(output).all():
            raise ValueError("one-sided Savitzky-Golay output is numerically unstable")
        valid[:] = True
    return output, valid


def _operation_digest(parameters: dict[str, object]) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _apply_savgol(
    series: SampleSeries,
    *,
    window_length: int,
    polyorder: int,
    derivative_order: int,
    uniformity_tolerance: float,
    edge_policy: EdgePolicy | str,
    cancellation: CancellationProbe | None,
) -> SampleSeries:
    window_length, polyorder, derivative_order, tolerance, edge = _validate_options(
        window_length,
        polyorder,
        derivative_order,
        uniformity_tolerance,
        edge_policy,
    )
    check_cancelled(cancellation, phase="Savitzky-Golay")
    segments = contiguous_valid_segments(series.valid_mask)
    if any(stop - start >= window_length for start, stop in segments):
        require_uniform_cadence(series, tolerance=tolerance)
    output = np.full(len(series), np.nan, dtype=np.float64)
    output_mask = np.zeros(len(series), dtype=bool)
    for start, stop in segments:
        check_cancelled(cancellation, phase="Savitzky-Golay")
        values, mask = _filter_segment(
            series.time_s[start:stop],
            series.values[start:stop],
            window_length=window_length,
            polyorder=polyorder,
            derivative_order=derivative_order,
            edge_policy=edge,
        )
        output[start:stop] = values
        output_mask[start:stop] = mask
    check_cancelled(cancellation, phase="Savitzky-Golay")
    parameters: dict[str, object] = {
        "window_length": window_length,
        "polyorder": polyorder,
        "derivative_order": derivative_order,
        "uniformity_tolerance": tolerance,
        "edge_policy": edge.value,
        "gap_policy": "split",
    }
    label = "Smoothed" if derivative_order == 0 else ("d/dt" if derivative_order == 1 else "d²/dt²")
    return SampleSeries(
        series_id=f"{series.series_id}:savgol:{_operation_digest(parameters)}",
        name=f"{label} {series.name}",
        frame_indices=series.frame_indices,
        time_s=series.time_s,
        values=output,
        valid_mask=output_mask,
        unit=derivative_unit(series.unit, derivative_order) if derivative_order else series.unit,
        source_kind="derived",
        source_revision=series.source_revision,
        processing_chain=series.processing_chain
        + (ProcessingStep("savgol_uniform", parameters),),
        metadata={"source_series_id": series.series_id, **parameters},
    )


def smooth_series(
    series: SampleSeries,
    *,
    window_length: int = 11,
    polyorder: int = 3,
    uniformity_tolerance: float = 1e-3,
    edge_policy: EdgePolicy | str = EdgePolicy.INVALID,
    cancellation: CancellationProbe | None = None,
) -> SampleSeries:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    return _apply_savgol(
        series,
        window_length=window_length,
        polyorder=polyorder,
        derivative_order=0,
        uniformity_tolerance=uniformity_tolerance,
        edge_policy=edge_policy,
        cancellation=cancellation,
    )


def savgol_derivative(
    series: SampleSeries,
    config: DerivativeConfig,
    *,
    cancellation: CancellationProbe | None = None,
) -> SampleSeries:
    if config.method is not DerivativeMethod.SAVGOL_UNIFORM:
        raise ValueError("savgol_derivative requires method=savgol_uniform")
    return _apply_savgol(
        series,
        window_length=config.window_length,
        polyorder=config.polyorder,
        derivative_order=config.order,
        uniformity_tolerance=config.uniformity_tolerance,
        edge_policy=config.edge_policy,
        cancellation=cancellation,
    )


__all__ = ["savgol_derivative", "smooth_series"]
