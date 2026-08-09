from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from neo_tracker.core import TrackerResult, TrackingPipeline
from neo_tracker.roi import AnnularROI, CircularROI, CurveBandROI, PolygonROI, RectangularROI


@dataclass(frozen=True)
class OverlayPrimitive:
    kind: str
    points: list[tuple[float, float]]
    label: str = ""
    style: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def roi_overlay(pipeline: TrackingPipeline) -> list[OverlayPrimitive]:
    roi = pipeline.roi
    if isinstance(roi, RectangularROI):
        x0, y0, x1, y1 = roi.bounds()
        return [
            OverlayPrimitive(
                kind="polyline",
                points=[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
                label="ROI",
            )
        ]
    if isinstance(roi, PolygonROI):
        return [OverlayPrimitive(kind="polygon", points=list(roi.points), label="ROI")]
    if isinstance(roi, CircularROI):
        return [
            OverlayPrimitive(
                kind="circle",
                points=[roi.center],
                label="ROI",
                metadata={"radius": roi.radius},
            )
        ]
    if isinstance(roi, AnnularROI):
        return [
            OverlayPrimitive(
                kind="annulus",
                points=[roi.center],
                label="Annular ROI",
                metadata={"inner_radius": roi.inner_radius, "outer_radius": roi.outer_radius},
            )
        ]
    if isinstance(roi, CurveBandROI):
        return [
            OverlayPrimitive(
                kind="polyline",
                points=list(roi.polyline),
                label="Curve ROI",
                metadata={"half_width": roi.half_width},
            )
        ]
    return []


def result_overlay(result: TrackerResult) -> list[OverlayPrimitive]:
    overlays: list[OverlayPrimitive] = []
    if result.observation and result.observation.image_point:
        overlays.append(
            OverlayPrimitive(
                kind="point",
                points=[result.observation.image_point],
                label=f"observation {result.confidence:.2f}",
                style={"role": "measurement"},
            )
        )
    if "x_px" in result.filtered_state and "y_px" in result.filtered_state:
        overlays.append(
            OverlayPrimitive(
                kind="point",
                points=[(result.filtered_state["x_px"], result.filtered_state["y_px"])],
                label="filtered",
                style={"role": "filtered"},
            )
        )
    return overlays


def trajectory_series(results: list[TrackerResult], keys: tuple[str, ...]) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {"time_s": [result.time_s for result in results]}
    for key in keys:
        series[key] = [float(result.filtered_state.get(key, np.nan)) for result in results]
    return series


def mapping_probe(pipeline: TrackingPipeline, point_px: tuple[float, float]) -> dict[str, Any]:
    mapped = pipeline.coordinate_model.image_to_state_space(point_px)
    roundtrip = pipeline.coordinate_model.state_to_image_space(mapped)
    return {
        "pixel": {"x": point_px[0], "y": point_px[1]},
        "state_space": mapped,
        "roundtrip_pixel": {"x": roundtrip[0], "y": roundtrip[1]},
    }


def annular_theta_time_heatmap(results: list[TrackerResult]) -> np.ndarray:
    maps = []
    for result in results:
        debug_layers = result.debug.get("debug_layers")
        theta_signal = debug_layers.get("theta_signal") if isinstance(debug_layers, dict) else None
        if isinstance(theta_signal, (np.ndarray, list, tuple)):
            try:
                signal = np.asarray(theta_signal, dtype=float)
            except (TypeError, ValueError):
                signal = np.empty((0, 0), dtype=float)
            if signal.ndim == 1:
                maps.append(signal)
                continue
        response = result.debug.get("response_map")
        if isinstance(response, (np.ndarray, list, tuple)):
            try:
                response_array = np.asarray(response, dtype=float)
            except (TypeError, ValueError):
                continue
            if response_array.ndim >= 2:
                maps.append(response_array.mean(axis=0))
    if not maps:
        return np.empty((0, 0), dtype=float)
    return np.vstack(maps)
