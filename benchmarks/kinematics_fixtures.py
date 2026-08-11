from __future__ import annotations

"""Deterministic analytic fixtures shared by kinematics tests and benchmarks."""

import gc
import hashlib
import os
import resource
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Mapping

import numpy as np

from neo_tracker.core import TrackerResult
from neo_tracker.kinematics.types import SampleSeries


FIXTURE_REVISION = "synthetic:kinematics-v0.3"


class CancelAfterChecks:
    def __init__(self, checks: int) -> None:
        self._remaining = max(1, int(checks))

    def is_cancelled(self) -> bool:
        self._remaining -= 1
        return self._remaining <= 0


def timing_summary(operation, *, iterations: int = 5) -> tuple[object, dict[str, float]]:
    rounds = max(1, int(iterations))
    operation()
    samples: list[float] = []
    result: object = None
    for _ in range(rounds):
        gc.collect()
        started = perf_counter()
        result = operation()
        samples.append((perf_counter() - started) * 1_000.0)
    values = np.asarray(samples, dtype=np.float64)
    return result, {
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def cancellation_latency_ms(operation) -> tuple[float, str]:
    started = perf_counter()
    terminal = "completed"
    try:
        result = operation()
        status = getattr(result, "status", None)
        terminal = getattr(status, "value", str(status or "completed"))
    except BaseException as exc:
        terminal = type(exc).__name__
    return (perf_counter() - started) * 1_000.0, terminal


def digest_arrays(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for value in arrays:
        array = np.ascontiguousarray(value)
        digest.update(array.dtype.str.encode("ascii"))
        digest.update(repr(array.shape).encode("ascii"))
        digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def peak_rss_mb() -> float:
    usage = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    byte_count = usage if sys.platform == "darwin" else usage * 1_024.0
    return byte_count / (1_024.0 * 1_024.0)


def current_rss_mb() -> float:
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(completed.stdout.strip()) / 1_024.0
    status = Path("/proc/self/statm")
    if status.exists():
        resident_pages = int(status.read_text(encoding="ascii").split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1_024.0 * 1_024.0)
    return peak_rss_mb()


@dataclass(frozen=True)
class AnalyticFixture:
    name: str
    frame_indices: np.ndarray
    time_s: np.ndarray
    values: np.ndarray
    valid_mask: np.ndarray
    unit: str
    parameters: Mapping[str, float] = field(default_factory=dict)

    def sample_series(
        self,
        *,
        series_id: str | None = None,
        source_revision: str = FIXTURE_REVISION,
    ) -> SampleSeries:
        return SampleSeries(
            series_id=series_id or f"fixture:{self.name}",
            name=self.name.replace("_", " ").title(),
            frame_indices=self.frame_indices,
            time_s=self.time_s,
            values=self.values,
            valid_mask=self.valid_mask,
            unit=self.unit,
            source_kind="synthetic",
            source_revision=source_revision,
            metadata={"fixture": self.name, "parameters": dict(self.parameters)},
        )

    def tracker_results(
        self,
        *,
        key: str = "x_world",
        velocity_key: str | None = None,
        velocity_values: np.ndarray | None = None,
    ) -> tuple[TrackerResult, ...]:
        if velocity_values is not None and len(velocity_values) != len(self.values):
            raise ValueError("velocity_values must align with fixture values")
        records: list[TrackerResult] = []
        for index, (frame, time_s, value, valid) in enumerate(
            zip(self.frame_indices, self.time_s, self.values, self.valid_mask, strict=True)
        ):
            state = {key: float(value)} if valid and np.isfinite(value) else {}
            velocity: dict[str, float] = {}
            if velocity_key is not None and velocity_values is not None:
                candidate = float(velocity_values[index])
                if valid and np.isfinite(candidate):
                    velocity[velocity_key] = candidate
            records.append(
                TrackerResult(
                    frame_index=int(frame),
                    time_s=float(time_s),
                    state=dict(state),
                    filtered_state=dict(state),
                    confidence=1.0 if valid else 0.0,
                    status="ok" if valid else "lost",
                    debug={"filter": {"velocity": velocity}},
                )
            )
        return tuple(records)


def _fixture(
    name: str,
    time_s: np.ndarray,
    values: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    unit: str = "m",
    parameters: Mapping[str, float] | None = None,
) -> AnalyticFixture:
    times = np.asarray(time_s, dtype=np.float64)
    data = np.asarray(values, dtype=np.float64)
    mask = np.ones(times.size, dtype=bool) if valid_mask is None else np.asarray(valid_mask, dtype=bool)
    return AnalyticFixture(
        name=name,
        frame_indices=np.arange(times.size, dtype=np.int64),
        time_s=times,
        values=data,
        valid_mask=mask,
        unit=unit,
        parameters=dict(parameters or {}),
    )


def uniform_linear(count: int = 201) -> AnalyticFixture:
    slope, intercept = 3.25, -0.75
    time_s = np.arange(count, dtype=np.float64) * 0.02
    return _fixture(
        "uniform_linear",
        time_s,
        slope * time_s + intercept,
        parameters={"slope": slope, "intercept": intercept},
    )


def uniform_quadratic(count: int = 241) -> AnalyticFixture:
    a, b, c = 1.2, -0.4, 2.0
    time_s = np.arange(count, dtype=np.float64) * 0.015
    return _fixture(
        "uniform_quadratic",
        time_s,
        a * time_s**2 + b * time_s + c,
        parameters={"a": a, "b": b, "c": c},
    )


def _vfr_time(count: int, *, base_dt: float = 1.0 / 120.0) -> np.ndarray:
    phase = np.arange(max(count - 1, 0), dtype=np.float64)
    delta = base_dt * (1.0 + 0.22 * np.sin(phase * 0.73) + 0.08 * np.cos(phase * 0.19))
    return np.concatenate((np.array([0.0]), np.cumsum(delta))) if count else np.array([], dtype=np.float64)


def vfr_linear(count: int = 257) -> AnalyticFixture:
    slope, intercept = -2.5, 4.0
    time_s = _vfr_time(count)
    return _fixture(
        "vfr_linear",
        time_s,
        slope * time_s + intercept,
        parameters={"slope": slope, "intercept": intercept},
    )


def vfr_quadratic(count: int = 257) -> AnalyticFixture:
    a, b, c = -0.85, 2.1, 0.3
    time_s = _vfr_time(count)
    return _fixture(
        "vfr_quadratic",
        time_s,
        a * time_s**2 + b * time_s + c,
        parameters={"a": a, "b": b, "c": c},
    )


def sinusoidal(count: int = 720) -> AnalyticFixture:
    amplitude, omega, phase, offset = 2.25, 3.4, 0.45, -0.3
    time_s = np.arange(count, dtype=np.float64) / 120.0
    values = amplitude * np.sin(omega * time_s + phase) + offset
    return _fixture(
        "sinusoidal",
        time_s,
        values,
        parameters={
            "amplitude": amplitude,
            "omega": omega,
            "phase": phase,
            "offset": offset,
        },
    )


def exponential(count: int = 321, *, rate: float = -0.6) -> AnalyticFixture:
    amplitude, offset = 1.7, 0.2
    time_s = np.arange(count, dtype=np.float64) * 0.015
    values = amplitude * np.exp(rate * time_s) + offset
    return _fixture(
        "exponential",
        time_s,
        values,
        parameters={"amplitude": amplitude, "rate": rate, "offset": offset},
    )


def missing_segments(count: int = 121) -> AnalyticFixture:
    base = uniform_quadratic(count)
    mask = np.ones(count, dtype=bool)
    mask[20:27] = False
    mask[61:66] = False
    values = np.array(base.values, copy=True)
    values[~mask] = np.nan
    return _fixture(
        "missing_segments",
        base.time_s,
        values,
        valid_mask=mask,
        parameters=base.parameters,
    )


def outlier_samples(count: int = 401) -> AnalyticFixture:
    base = uniform_linear(count)
    rng = np.random.default_rng(20260810)
    values = base.values + rng.normal(0.0, 0.025, count)
    values[[73, 205, 318]] += np.array([1.5, -1.2, 1.8])
    return _fixture(
        "outlier_samples",
        base.time_s,
        values,
        parameters={**base.parameters, "noise_seed": 20260810.0},
    )


def angular_wrap(count: int = 241) -> AnalyticFixture:
    omega, phase = 2.2, -2.7
    time_s = np.arange(count, dtype=np.float64) / 60.0
    unwrapped = omega * time_s + phase
    wrapped = (unwrapped + np.pi) % (2.0 * np.pi) - np.pi
    return _fixture(
        "angular_wrap",
        time_s,
        wrapped,
        unit="rad",
        parameters={"omega": omega, "phase": phase},
    )


def path_distance(count: int = 301) -> AnalyticFixture:
    speed, start = 0.82, 0.15
    time_s = _vfr_time(count, base_dt=1.0 / 90.0)
    return _fixture(
        "path_distance",
        time_s,
        start + speed * time_s,
        parameters={"speed": speed, "start": start},
    )


def constant_series(count: int = 121) -> AnalyticFixture:
    """Constant target: derivative ~0 and R^2 has a fixed degenerate definition."""
    level = 2.5
    time_s = np.arange(count, dtype=np.float64) * 0.02
    return _fixture(
        "constant_series",
        time_s,
        np.full(count, level, dtype=np.float64),
        parameters={"level": level},
    )


def insufficient_samples(count: int = 2) -> AnalyticFixture:
    """Too few valid samples for a quadratic fit (minimum requirement exceeded)."""
    a, b, c = 1.0, -0.5, 0.25
    time_s = np.arange(count, dtype=np.float64) * 0.1
    return _fixture(
        "insufficient_samples",
        time_s,
        a * time_s**2 + b * time_s + c,
        parameters={"a": a, "b": b, "c": c},
    )


def duplicate_time(count: int = 16) -> AnalyticFixture:
    """Non-strictly-increasing timestamps; builders must reject them."""
    time_s = np.arange(count, dtype=np.float64) * 0.05
    time_s[8] = time_s[7]
    return _fixture(
        "duplicate_time",
        time_s,
        np.arange(count, dtype=np.float64),
        parameters={"duplicate_index": 8.0},
    )


def very_small_dt(count: int = 8) -> AnalyticFixture:
    """Extreme sub-ulp time spacing; numerical paths must fail closed, not emit inf."""
    time_s = np.arange(count, dtype=np.float64) * 1e-200
    return _fixture(
        "very_small_dt",
        time_s,
        time_s**2,
        parameters={"base_dt": 1e-200},
    )


def fixture_catalog() -> dict[str, AnalyticFixture]:
    return {
        fixture.name: fixture
        for fixture in (
            uniform_linear(),
            uniform_quadratic(),
            vfr_linear(),
            vfr_quadratic(),
            sinusoidal(),
            exponential(),
            missing_segments(),
            outlier_samples(),
            angular_wrap(),
            path_distance(),
            constant_series(),
            insufficient_samples(),
            duplicate_time(),
            very_small_dt(),
        )
    }
