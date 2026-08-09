from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from neo_tracker.core import CoordinateModel, ObservationCandidate, State, StateModel


@dataclass(frozen=True)
class XYState(StateModel):
    unit: str = "px"
    name: str = "xy"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        if candidate.image_point is not None:
            x_px, y_px = candidate.image_point
        else:
            x_px = candidate.state["x_px"]
            y_px = candidate.state["y_px"]
        state = {"x_px": float(x_px), "y_px": float(y_px)}
        state.update(coordinate_model.image_to_state_space((float(x_px), float(y_px))))
        return state

    def normalize(self, state: State, previous_state: State | None) -> State:
        return dict(state)

    def units(self) -> dict[str, str]:
        return {"x_px": "px", "y_px": "px", "x_world": self.unit, "y_world": self.unit}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "unit": self.unit}


@dataclass(frozen=True)
class ScalarState(StateModel):
    key: str = "s"
    unit: str = "px"
    name: str = "scalar"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        if self.key in candidate.state:
            return {self.key: float(candidate.state[self.key])}
        if candidate.image_point is None:
            raise ValueError(f"candidate does not contain scalar key {self.key!r}")
        mapped = coordinate_model.image_to_state_space(candidate.image_point)
        if self.key not in mapped:
            raise ValueError(f"coordinate model does not provide scalar key {self.key!r}")
        return {self.key: float(mapped[self.key])}

    def normalize(self, state: State, previous_state: State | None) -> State:
        return dict(state)

    def units(self) -> dict[str, str]:
        return {self.key: self.unit}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "key": self.key, "unit": self.unit}


@dataclass(frozen=True)
class AngleState(StateModel):
    unit: str = "rad"
    keep_radius: bool = True
    name: str = "angle"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        if "theta" in candidate.state:
            theta = float(candidate.state["theta"])
            radius = float(candidate.state.get("r", np.nan))
        elif candidate.image_point is not None:
            mapped = coordinate_model.image_to_state_space(candidate.image_point)
            theta = float(mapped["theta"])
            radius = float(mapped.get("r", np.nan))
        else:
            raise ValueError("angle candidate must contain theta or image_point")
        theta = theta % (2.0 * np.pi)
        state = {"theta": theta, "theta_unwrapped": self._unwrap(theta, previous_state)}
        if self.keep_radius and not np.isnan(radius):
            state["r"] = radius
        return state

    def normalize(self, state: State, previous_state: State | None) -> State:
        theta = float(state.get("theta", state.get("theta_unwrapped", 0.0))) % (2.0 * np.pi)
        normalized = dict(state)
        normalized["theta"] = theta
        normalized["theta_unwrapped"] = self._unwrap(theta, previous_state)
        return normalized

    def units(self) -> dict[str, str]:
        return {"theta": self.unit, "theta_unwrapped": self.unit, "r": "px"}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "unit": self.unit, "keep_radius": self.keep_radius}

    @staticmethod
    def _unwrap(theta: float, previous_state: State | None) -> float:
        if previous_state is None or "theta_unwrapped" not in previous_state:
            return float(theta)
        previous = float(previous_state["theta_unwrapped"])
        return float(theta + 2.0 * np.pi * round((previous - theta) / (2.0 * np.pi)))


@dataclass(frozen=True)
class FrontState(StateModel):
    key: str = "s"
    unit: str = "px"
    name: str = "front"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        if self.key in candidate.state:
            return {self.key: float(candidate.state[self.key])}
        if candidate.image_point is None:
            raise ValueError("front candidate must contain a state key or image point")
        mapped = coordinate_model.image_to_state_space(candidate.image_point)
        return {self.key: float(mapped[self.key])}

    def normalize(self, state: State, previous_state: State | None) -> State:
        return dict(state)

    def units(self) -> dict[str, str]:
        return {self.key: self.unit}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "key": self.key, "unit": self.unit}


@dataclass(frozen=True)
class ContourState(StateModel):
    name: str = "contour"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        return {key: float(value) for key, value in candidate.state.items() if isinstance(value, (int, float))}

    def normalize(self, state: State, previous_state: State | None) -> State:
        return dict(state)

    def units(self) -> dict[str, str]:
        return {"area": "px^2", "x_px": "px", "y_px": "px"}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name}


@dataclass(frozen=True)
class MultiTargetState(StateModel):
    name: str = "multi_target"

    def measurement_from_candidate(
        self,
        candidate: ObservationCandidate,
        coordinate_model: CoordinateModel,
        previous_state: State | None,
    ) -> State:
        return {key: float(value) for key, value in candidate.state.items() if isinstance(value, (int, float))}

    def normalize(self, state: State, previous_state: State | None) -> State:
        return dict(state)

    def units(self) -> dict[str, str]:
        return {"target_count": "count"}

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name}

