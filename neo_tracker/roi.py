from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from neo_tracker.core import ROIModel


def _shape_hw(shape: tuple[int, ...]) -> tuple[int, int]:
    if len(shape) < 2:
        raise ValueError("image shape must have at least height and width")
    return int(shape[0]), int(shape[1])


def _clipped_inclusive_bounds(
    bounds: tuple[int, int, int, int],
    height: int,
    width: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bounds
    return max(0, x0), max(0, y0), min(width - 1, x1), min(height - 1, y1)


@dataclass(frozen=True)
class RectangularROI(ROIModel):
    x: float
    y: float
    width: float
    height: float
    name: str = "rectangle"

    @lru_cache(maxsize=8)
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        h, w = _shape_hw(shape)
        mask = np.zeros((h, w), dtype=bool)
        x0, y0, x1, y1 = _clipped_inclusive_bounds(self.bounds(), h, w)
        if x0 <= x1 and y0 <= y1:
            # Evaluate the exact fractional geometry only inside its safe
            # bounds, while retaining the full-size cached mask contract.
            yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
            mask[y0 : y1 + 1, x0 : x1 + 1] = (
                (xx >= self.x)
                & (xx <= self.x + self.width)
                & (yy >= self.y)
                & (yy <= self.y + self.height)
            )
        mask.setflags(write=False)
        return mask

    def contains_point(self, point: tuple[float, float]) -> bool:
        px, py = point
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height

    def bounds(self) -> tuple[int, int, int, int]:
        return (
            int(np.floor(self.x)),
            int(np.floor(self.y)),
            int(np.ceil(self.x + self.width)),
            int(np.ceil(self.y + self.height)),
        )

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class PolygonROI(ROIModel):
    points: tuple[tuple[float, float], ...]
    name: str = "polygon"

    @lru_cache(maxsize=8)
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        h, w = _shape_hw(shape)
        inside = np.zeros((h, w), dtype=bool)
        x0, y0, x1, y1 = _clipped_inclusive_bounds(self.bounds(), h, w)
        if x0 <= x1 and y0 <= y1:
            yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
            local_inside = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=bool)
            points = np.asarray(self.points, dtype=float)
            x_vertices = points[:, 0]
            y_vertices = points[:, 1]
            j = len(points) - 1
            for i in range(len(points)):
                yi = y_vertices[i]
                yj = y_vertices[j]
                xi = x_vertices[i]
                xj = x_vertices[j]
                crosses = ((yi > yy) != (yj > yy)) & (
                    xx < (xj - xi) * (yy - yi) / (yj - yi + 1e-12) + xi
                )
                local_inside ^= crosses
                j = i
            inside[y0 : y1 + 1, x0 : x1 + 1] = local_inside
        inside.setflags(write=False)
        return inside

    def contains_point(self, point: tuple[float, float]) -> bool:
        px, py = point
        inside = False
        j = len(self.points) - 1
        for i, (xi, yi) in enumerate(self.points):
            xj, yj = self.points[j]
            if (yi > py) != (yj > py):
                x_intersect = (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
                if px < x_intersect:
                    inside = not inside
            j = i
        return inside

    def bounds(self) -> tuple[int, int, int, int]:
        points = np.asarray(self.points, dtype=float)
        return (
            int(np.floor(points[:, 0].min())),
            int(np.floor(points[:, 1].min())),
            int(np.ceil(points[:, 0].max())),
            int(np.ceil(points[:, 1].max())),
        )

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "points": [list(point) for point in self.points]}


@dataclass(frozen=True)
class CircularROI(ROIModel):
    center: tuple[float, float]
    radius: float
    name: str = "circle"

    @lru_cache(maxsize=8)
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        h, w = _shape_hw(shape)
        mask = np.zeros((h, w), dtype=bool)
        x0, y0, x1, y1 = _clipped_inclusive_bounds(self.bounds(), h, w)
        if x0 <= x1 and y0 <= y1:
            yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
            cx, cy = self.center
            mask[y0 : y1 + 1, x0 : x1 + 1] = (
                (xx - cx) ** 2 + (yy - cy) ** 2 <= self.radius**2
            )
        mask.setflags(write=False)
        return mask

    def contains_point(self, point: tuple[float, float]) -> bool:
        px, py = point
        cx, cy = self.center
        return (px - cx) ** 2 + (py - cy) ** 2 <= self.radius**2

    def bounds(self) -> tuple[int, int, int, int]:
        cx, cy = self.center
        return (
            int(np.floor(cx - self.radius)),
            int(np.floor(cy - self.radius)),
            int(np.ceil(cx + self.radius)),
            int(np.ceil(cy + self.radius)),
        )

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "center": list(self.center), "radius": self.radius}


@dataclass(frozen=True)
class AnnularROI(ROIModel):
    center: tuple[float, float]
    inner_radius: float
    outer_radius: float
    name: str = "annulus"

    @lru_cache(maxsize=8)
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        h, w = _shape_hw(shape)
        mask = np.zeros((h, w), dtype=bool)
        x0, y0, x1, y1 = _clipped_inclusive_bounds(self.bounds(), h, w)
        if x0 <= x1 and y0 <= y1:
            yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
            cx, cy = self.center
            rr2 = (xx - cx) ** 2 + (yy - cy) ** 2
            mask[y0 : y1 + 1, x0 : x1 + 1] = (
                (rr2 >= self.inner_radius**2) & (rr2 <= self.outer_radius**2)
            )
        mask.setflags(write=False)
        return mask

    def contains_point(self, point: tuple[float, float]) -> bool:
        px, py = point
        cx, cy = self.center
        rr = np.hypot(px - cx, py - cy)
        return self.inner_radius <= rr <= self.outer_radius

    def bounds(self) -> tuple[int, int, int, int]:
        cx, cy = self.center
        return (
            int(np.floor(cx - self.outer_radius)),
            int(np.floor(cy - self.outer_radius)),
            int(np.ceil(cx + self.outer_radius)),
            int(np.ceil(cy + self.outer_radius)),
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "center": list(self.center),
            "inner_radius": self.inner_radius,
            "outer_radius": self.outer_radius,
        }


@dataclass(frozen=True)
class CurveBandROI(ROIModel):
    polyline: tuple[tuple[float, float], ...]
    half_width: float
    name: str = "curve_band"

    @lru_cache(maxsize=8)
    def mask(self, shape: tuple[int, ...]) -> np.ndarray:
        h, w = _shape_hw(shape)
        mask = np.zeros((h, w), dtype=bool)
        points = np.asarray(self.polyline, dtype=float)
        for start, end in zip(points[:-1], points[1:]):
            sx, sy = start
            ex, ey = end
            # A curve band is the union of the padded line-segment capsules.
            # Restrict each distance calculation to its own clipped bounds so
            # a short path does not allocate several full-frame float planes.
            x0 = max(0, int(np.floor(min(sx, ex) - self.half_width)))
            y0 = max(0, int(np.floor(min(sy, ey) - self.half_width)))
            x1 = min(w - 1, int(np.ceil(max(sx, ex) + self.half_width)))
            y1 = min(h - 1, int(np.ceil(max(sy, ey) + self.half_width)))
            if x0 > x1 or y0 > y1:
                continue
            yy, xx = np.ogrid[y0 : y1 + 1, x0 : x1 + 1]
            vx = ex - sx
            vy = ey - sy
            denom = vx * vx + vy * vy + 1e-12
            t = np.clip(((xx - sx) * vx + (yy - sy) * vy) / denom, 0.0, 1.0)
            proj_x = sx + t * vx
            proj_y = sy + t * vy
            local_mask = np.hypot(xx - proj_x, yy - proj_y) <= self.half_width
            target = mask[y0 : y1 + 1, x0 : x1 + 1]
            np.logical_or(target, local_mask, out=target)
        mask.setflags(write=False)
        return mask

    def contains_point(self, point: tuple[float, float]) -> bool:
        px, py = point
        points = np.asarray(self.polyline, dtype=float)
        best = np.inf
        for start, end in zip(points[:-1], points[1:]):
            sx, sy = start
            ex, ey = end
            vx = ex - sx
            vy = ey - sy
            denom = vx * vx + vy * vy + 1e-12
            t = np.clip(((px - sx) * vx + (py - sy) * vy) / denom, 0.0, 1.0)
            best = min(best, float(np.hypot(px - (sx + t * vx), py - (sy + t * vy))))
        return best <= self.half_width

    def bounds(self) -> tuple[int, int, int, int]:
        points = np.asarray(self.polyline, dtype=float)
        return (
            int(np.floor(points[:, 0].min() - self.half_width)),
            int(np.floor(points[:, 1].min() - self.half_width)),
            int(np.ceil(points[:, 0].max() + self.half_width)),
            int(np.ceil(points[:, 1].max() + self.half_width)),
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "polyline": [list(point) for point in self.polyline],
            "half_width": self.half_width,
        }
