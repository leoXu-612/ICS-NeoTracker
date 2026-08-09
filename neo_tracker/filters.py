from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from neo_tracker.core import FilterUpdate, State, TrackerFilter, TrackerResult


@dataclass
class PassThroughFilter(TrackerFilter):
    name: str = "pass_through"

    def update(
        self,
        measurement: State,
        previous_result: TrackerResult | None,
        dt: float,
    ) -> FilterUpdate:
        velocity = _velocity_from_previous(measurement, previous_result, dt)
        return FilterUpdate(state=dict(measurement), debug={"velocity": velocity})

    def reset(self) -> None:
        return None

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name}


@dataclass
class ExponentialSmoothingFilter(TrackerFilter):
    alpha: float = 0.7
    keys: tuple[str, ...] | None = None
    name: str = "exponential_smoothing"

    def update(
        self,
        measurement: State,
        previous_result: TrackerResult | None,
        dt: float,
    ) -> FilterUpdate:
        if previous_result is None:
            velocity = {}
            return FilterUpdate(state=dict(measurement), debug={"velocity": velocity})
        keys = self.keys or tuple(measurement.keys())
        state = dict(measurement)
        for key in keys:
            if key in measurement and key in previous_result.filtered_state:
                state[key] = self.alpha * float(measurement[key]) + (1.0 - self.alpha) * float(previous_result.filtered_state[key])
        velocity = _velocity_from_previous(state, previous_result, dt)
        return FilterUpdate(state=state, debug={"velocity": velocity})

    def reset(self) -> None:
        return None

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "alpha": self.alpha, "keys": list(self.keys) if self.keys else None}


@dataclass
class AlphaBetaFilter(TrackerFilter):
    keys: tuple[str, ...]
    alpha: float = 0.85
    beta: float = 0.25
    velocities: dict[str, float] = field(default_factory=dict)
    name: str = "alpha_beta"

    def update(
        self,
        measurement: State,
        previous_result: TrackerResult | None,
        dt: float,
    ) -> FilterUpdate:
        if previous_result is None:
            state = dict(measurement)
            self.velocities = {f"v_{key}": 0.0 for key in self.keys}
            return FilterUpdate(state=state, debug={"velocity": dict(self.velocities)})
        if dt <= 0.0:
            return FilterUpdate(state=dict(measurement), debug={"velocity": dict(self.velocities)})
        state = dict(measurement)
        for key in self.keys:
            if key not in measurement:
                continue
            velocity_key = f"v_{key}"
            previous_value = float(previous_result.filtered_state.get(key, measurement[key]))
            previous_velocity = float(self.velocities.get(velocity_key, 0.0))
            predicted = previous_value + previous_velocity * dt
            residual = float(measurement[key]) - predicted
            state[key] = predicted + self.alpha * residual
            self.velocities[velocity_key] = previous_velocity + (self.beta * residual / dt)
        return FilterUpdate(state=state, debug={"velocity": dict(self.velocities)})

    def reset(self) -> None:
        self.velocities.clear()

    def prime(self, previous_result: TrackerResult | None) -> None:
        self.reset()
        if previous_result is None:
            return
        velocity = previous_result.debug.get("filter", {}).get("velocity", {})
        if not isinstance(velocity, dict):
            return
        allowed = {f"v_{key}" for key in self.keys}
        self.velocities = {
            str(key): float(value)
            for key, value in velocity.items()
            if str(key) in allowed and isinstance(value, (int, float))
        }

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "keys": list(self.keys),
            "alpha": self.alpha,
            "beta": self.beta,
        }


def _velocity_from_previous(measurement: State, previous_result: TrackerResult | None, dt: float) -> dict[str, float]:
    if previous_result is None or dt <= 0.0:
        return {}
    velocity = {}
    for key, value in measurement.items():
        if key in previous_result.filtered_state and isinstance(value, (int, float)):
            velocity[f"v_{key}"] = float((float(value) - float(previous_result.filtered_state[key])) / dt)
    if "theta_unwrapped" in measurement and "v_theta_unwrapped" in velocity:
        velocity["omega"] = velocity["v_theta_unwrapped"]
    return velocity
