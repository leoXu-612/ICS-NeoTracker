from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from neo_tracker.core import CoordinateModel, State


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        raise ValueError("axis vector must be non-zero")
    return vector / norm


@dataclass(frozen=True)
class ImageCoordinate(CoordinateModel):
    name: str = "image"

    def image_to_state_space(self, point: tuple[float, float]) -> State:
        return {"x_px": float(point[0]), "y_px": float(point[1])}

    def state_to_image_space(self, state: State) -> tuple[float, float]:
        return float(state["x_px"]), float(state["y_px"])

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name}


@dataclass(frozen=True)
class LinearWorldCoordinate(CoordinateModel):
    origin_px: tuple[float, float]
    x_axis_px: tuple[float, float]
    unit_per_pixel: float = 1.0
    unit: str = "px"
    y_positive: str = "up"
    name: str = "linear_world"

    def __post_init__(self) -> None:
        if self.y_positive not in {"up", "down"}:
            raise ValueError("y_positive must be 'up' or 'down'")

    @classmethod
    def from_calibration_rod(
        cls,
        start_px: tuple[float, float],
        end_px: tuple[float, float],
        real_length: float,
        unit: str,
        y_positive: str = "up",
    ) -> "LinearWorldCoordinate":
        if real_length <= 0:
            raise ValueError("real_length must be positive")
        pixel_length = float(np.linalg.norm(np.asarray(end_px, dtype=float) - np.asarray(start_px, dtype=float)))
        if pixel_length <= 1e-12:
            raise ValueError("calibration rod endpoints must be distinct")
        return cls(
            origin_px=start_px,
            x_axis_px=end_px,
            unit_per_pixel=real_length / pixel_length,
            unit=unit,
            y_positive=y_positive,
        )

    def image_to_state_space(self, point: tuple[float, float]) -> State:
        origin = np.asarray(self.origin_px, dtype=float)
        axis = _unit(np.asarray(self.x_axis_px, dtype=float) - origin)
        y_axis = self._y_axis(axis)
        vector = np.asarray(point, dtype=float) - origin
        return {
            "x_world": float(np.dot(vector, axis) * self.unit_per_pixel),
            "y_world": float(np.dot(vector, y_axis) * self.unit_per_pixel),
        }

    def state_to_image_space(self, state: State) -> tuple[float, float]:
        origin = np.asarray(self.origin_px, dtype=float)
        axis = _unit(np.asarray(self.x_axis_px, dtype=float) - origin)
        y_axis = self._y_axis(axis)
        pixel = origin + (state["x_world"] / self.unit_per_pixel) * axis + (state["y_world"] / self.unit_per_pixel) * y_axis
        return float(pixel[0]), float(pixel[1])

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "origin_px": list(self.origin_px),
            "x_axis_px": list(self.x_axis_px),
            "unit_per_pixel": self.unit_per_pixel,
            "unit": self.unit,
            "y_positive": self.y_positive,
        }

    def _y_axis(self, x_axis: np.ndarray) -> np.ndarray:
        return (
            np.array([x_axis[1], -x_axis[0]])
            if self.y_positive == "up"
            else np.array([-x_axis[1], x_axis[0]])
        )


@dataclass(frozen=True)
class PolarCoordinate(CoordinateModel):
    center_px: tuple[float, float]
    theta_zero_px: tuple[float, float] | None = None
    direction: str = "ccw"
    name: str = "polar"

    def image_to_state_space(self, point: tuple[float, float]) -> State:
        cx, cy = self.center_px
        dx = float(point[0] - cx)
        dy_math = float(cy - point[1])
        theta = np.arctan2(dy_math, dx) - self._theta_zero_angle()
        if self.direction == "cw":
            theta = -theta
        theta = float(theta % (2.0 * np.pi))
        return {"theta": theta, "r": float(np.hypot(dx, dy_math))}

    def state_to_image_space(self, state: State) -> tuple[float, float]:
        theta = float(state.get("theta", state.get("theta_unwrapped", 0.0)))
        if self.direction == "cw":
            theta = -theta
        theta += self._theta_zero_angle()
        radius = float(state.get("r", 0.0))
        cx, cy = self.center_px
        x = cx + radius * np.cos(theta)
        y = cy - radius * np.sin(theta)
        return float(x), float(y)

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "center_px": list(self.center_px),
            "theta_zero_px": list(self.theta_zero_px) if self.theta_zero_px else None,
            "direction": self.direction,
        }

    def _theta_zero_angle(self) -> float:
        if self.theta_zero_px is None:
            return 0.0
        cx, cy = self.center_px
        zx, zy = self.theta_zero_px
        return float(np.arctan2(cy - zy, zx - cx))


@dataclass(frozen=True)
class AnnularCoordinate(PolarCoordinate):
    inner_radius: float = 0.0
    outer_radius: float = 1.0
    unit_per_pixel: float = 1.0
    unit: str = "px"
    name: str = "annular"

    def image_to_state_space(self, point: tuple[float, float]) -> State:
        state = super().image_to_state_space(point)
        state["s"] = float(state["theta"] * self.mean_radius_px() * self.unit_per_pixel)
        return state

    def state_to_image_space(self, state: State) -> tuple[float, float]:
        if "s" in state and "theta" not in state and "theta_unwrapped" not in state:
            theta = float(state["s"] / (self.mean_radius_px() * self.unit_per_pixel))
            state = {**state, "theta": theta}
        if "r" not in state:
            state = {**state, "r": self.mean_radius_px()}
        return super().state_to_image_space(state)

    def mean_radius_px(self) -> float:
        return 0.5 * (self.inner_radius + self.outer_radius)

    def theta_to_arc_length(self, theta: float) -> float:
        return float(theta * self.mean_radius_px() * self.unit_per_pixel)

    def to_config(self) -> dict[str, Any]:
        base = super().to_config()
        base.update(
            {
                "type": self.name,
                "inner_radius": self.inner_radius,
                "outer_radius": self.outer_radius,
                "unit_per_pixel": self.unit_per_pixel,
                "unit": self.unit,
            }
        )
        return base


@dataclass(frozen=True)
class PathCoordinate(CoordinateModel):
    polyline: tuple[tuple[float, float], ...]
    unit_per_pixel: float = 1.0
    unit: str = "px"
    name: str = "path"

    def image_to_state_space(self, point: tuple[float, float]) -> State:
        px, py = point
        points = np.asarray(self.polyline, dtype=float)
        best_distance = np.inf
        best_s_px = 0.0
        best_offset = 0.0
        accumulated = 0.0
        for start, end in zip(points[:-1], points[1:]):
            segment = end - start
            length = float(np.linalg.norm(segment))
            if length <= 1e-12:
                continue
            t = float(np.clip(np.dot(np.asarray([px, py]) - start, segment) / (length**2), 0.0, 1.0))
            projection = start + t * segment
            distance_vector = np.asarray([px, py]) - projection
            distance = float(np.linalg.norm(distance_vector))
            if distance < best_distance:
                tangent = segment / length
                normal = np.array([-tangent[1], tangent[0]])
                best_distance = distance
                best_s_px = accumulated + t * length
                best_offset = float(np.dot(distance_vector, normal))
            accumulated += length
        return {"s": best_s_px * self.unit_per_pixel, "offset": best_offset * self.unit_per_pixel}

    def state_to_image_space(self, state: State) -> tuple[float, float]:
        target_s_px = float(state["s"] / self.unit_per_pixel)
        offset_px = float(state.get("offset", 0.0) / self.unit_per_pixel)
        points = np.asarray(self.polyline, dtype=float)
        accumulated = 0.0
        for start, end in zip(points[:-1], points[1:]):
            segment = end - start
            length = float(np.linalg.norm(segment))
            if length <= 1e-12:
                continue
            if accumulated + length >= target_s_px:
                t = (target_s_px - accumulated) / length
                tangent = segment / length
                normal = np.array([-tangent[1], tangent[0]])
                point = start + t * segment + offset_px * normal
                return float(point[0]), float(point[1])
            accumulated += length
        return float(points[-1, 0]), float(points[-1, 1])

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "polyline": [list(point) for point in self.polyline],
            "unit_per_pixel": self.unit_per_pixel,
            "unit": self.unit,
        }
