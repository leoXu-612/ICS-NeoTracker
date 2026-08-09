from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from neo_tracker.core import MotionModel, State, TrackerResult


def _distance_for_keys(a: State, b: State, keys: tuple[str, ...]) -> float:
    return float(np.sqrt(sum((float(a[key]) - float(b[key])) ** 2 for key in keys if key in a and key in b)))


@dataclass(frozen=True)
class NoMotionPrior(MotionModel):
    name: str = "none"

    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        return None

    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        return 1.0

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name}


@dataclass(frozen=True)
class BoundedVelocityPrior(MotionModel):
    keys: tuple[str, ...] = ("x_px", "y_px")
    max_speed: float = 250.0
    margin: float = 8.0
    name: str = "bounded_velocity"

    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        if previous_result is None:
            return None
        velocity = previous_result.debug.get("filter", {}).get("velocity", {})
        if not velocity:
            return dict(previous_result.filtered_state)
        prediction = dict(previous_result.filtered_state)
        for key in self.keys:
            velocity_key = f"v_{key}"
            if key in prediction and velocity_key in velocity:
                prediction[key] = float(prediction[key]) + float(velocity[velocity_key]) * dt
        return prediction

    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        if prediction is None or dt <= 0.0:
            return 1.0
        distance = _distance_for_keys(measurement, prediction, self.keys)
        allowed = self.max_speed * dt + self.margin
        if distance <= allowed:
            return 1.0
        return float(np.exp(-((distance - allowed) / max(allowed, 1e-12)) ** 2))

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "keys": list(self.keys),
            "max_speed": self.max_speed,
            "margin": self.margin,
        }


@dataclass(frozen=True)
class ConstantVelocityPrior(MotionModel):
    keys: tuple[str, ...] = ("x_px", "y_px")
    max_residual: float = 40.0
    name: str = "constant_velocity"

    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        if previous_result is None:
            return None
        velocity = previous_result.debug.get("filter", {}).get("velocity", {})
        prediction = dict(previous_result.filtered_state)
        for key in self.keys:
            velocity_key = f"v_{key}"
            if velocity_key in velocity and key in prediction:
                prediction[key] = float(prediction[key]) + float(velocity[velocity_key]) * dt
        return prediction

    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        if prediction is None:
            return 1.0
        residual = _distance_for_keys(measurement, prediction, self.keys)
        return float(np.exp(-(residual / max(self.max_residual, 1e-12)) ** 2))

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "keys": list(self.keys), "max_residual": self.max_residual}


@dataclass(frozen=True)
class PeriodicAngularPrior(MotionModel):
    key: str = "theta_unwrapped"
    max_angular_speed: float = 20.0
    residual_margin: float = 0.25
    direction: str = "either"
    name: str = "periodic_angular"

    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        if previous_result is None:
            return None
        prediction = dict(previous_result.filtered_state)
        velocity = previous_result.debug.get("filter", {}).get("velocity", {})
        omega = float(velocity.get(f"v_{self.key}", velocity.get("omega", 0.0)))
        if self.key in prediction:
            prediction[self.key] = float(prediction[self.key]) + omega * dt
            prediction["theta"] = float(prediction[self.key] % (2.0 * np.pi))
        return prediction

    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        if prediction is None or self.key not in measurement or self.key not in prediction:
            return 1.0
        residual = abs(float(measurement[self.key]) - float(prediction[self.key]))
        allowed = self.max_angular_speed * dt + self.residual_margin
        direction_score = self._direction_score(measurement, prediction)
        if residual <= allowed:
            return direction_score
        return float(direction_score * np.exp(-((residual - allowed) / max(allowed, 1e-12)) ** 2))

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "key": self.key,
            "max_angular_speed": self.max_angular_speed,
            "residual_margin": self.residual_margin,
            "direction": self.direction,
        }

    def _direction_score(self, measurement: State, prediction: State) -> float:
        if self.direction == "either":
            return 1.0
        delta = float(measurement[self.key]) - float(prediction[self.key])
        if self.direction == "increasing":
            return 1.0 if delta >= -self.residual_margin else 0.25
        if self.direction == "decreasing":
            return 1.0 if delta <= self.residual_margin else 0.25
        return 1.0


@dataclass(frozen=True)
class PathMotionPrior(MotionModel):
    key: str = "s"
    max_speed: float = 250.0
    margin: float = 8.0
    name: str = "path_motion"

    def predict(self, previous_result: TrackerResult | None, dt: float) -> State | None:
        if previous_result is None:
            return None
        prediction = dict(previous_result.filtered_state)
        velocity = previous_result.debug.get("filter", {}).get("velocity", {})
        velocity_key = f"v_{self.key}"
        if velocity_key in velocity and self.key in prediction:
            prediction[self.key] = float(prediction[self.key]) + float(velocity[velocity_key]) * dt
        return prediction

    def score(self, measurement: State, prediction: State | None, dt: float) -> float:
        if prediction is None or self.key not in prediction or self.key not in measurement:
            return 1.0
        distance = abs(float(measurement[self.key]) - float(prediction[self.key]))
        allowed = self.max_speed * dt + self.margin
        if distance <= allowed:
            return 1.0
        return float(np.exp(-((distance - allowed) / max(allowed, 1e-12)) ** 2))

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "key": self.key, "max_speed": self.max_speed, "margin": self.margin}

