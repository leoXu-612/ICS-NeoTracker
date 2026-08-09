from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from neo_tracker.coordinates import AnnularCoordinate, ImageCoordinate, PathCoordinate, PolarCoordinate
from neo_tracker.core import TrackingPipeline
from neo_tracker.filters import AlphaBetaFilter, ExponentialSmoothingFilter, PassThroughFilter
from neo_tracker.motion import BoundedVelocityPrior, NoMotionPrior, PathMotionPrior, PeriodicAngularPrior
from neo_tracker.observations import (
    AnnularRadialFrontObservation,
    BrightnessPeakObservation,
    ColorBlobObservation,
    EdgeFrontObservation,
)
from neo_tracker.optimizers import ParticleSwarmOptimizer
from neo_tracker.roi import AnnularROI, CircularROI, CurveBandROI, RectangularROI
from neo_tracker.states import AngleState, FrontState, ScalarState, XYState


@dataclass(frozen=True)
class PresetDescriptor:
    key: str
    title: str
    description: str
    factory: Callable[..., TrackingPipeline]


def color_marker_preset(
    sample_rgb: tuple[float, float, float] = (255.0, 0.0, 0.0),
    roi: RectangularROI | None = None,
    tolerance: float = 0.2,
) -> TrackingPipeline:
    roi = roi or RectangularROI(0.0, 0.0, 1920.0, 1080.0)
    return TrackingPipeline(
        name="Color Marker",
        roi=roi,
        coordinate_model=ImageCoordinate(),
        observation_model=ColorBlobObservation(sample_rgb=sample_rgb, tolerance=tolerance),
        state_model=XYState(),
        motion_model=BoundedVelocityPrior(keys=("x_px", "y_px"), max_speed=600.0),
        tracker_filter=AlphaBetaFilter(keys=("x_px", "y_px"), alpha=0.85, beta=0.2),
        optimizer=ParticleSwarmOptimizer(particles=18, iterations=20),
        metadata={"preset": "color_marker"},
    )


def circular_motion_preset(
    center_px: tuple[float, float] = (320.0, 240.0),
    radius: float = 120.0,
    sample_rgb: tuple[float, float, float] = (255.0, 0.0, 0.0),
    direction: str = "ccw",
) -> TrackingPipeline:
    return TrackingPipeline(
        name="Circular Motion",
        roi=CircularROI(center=center_px, radius=radius + 20.0),
        coordinate_model=PolarCoordinate(center_px=center_px, direction=direction),
        observation_model=ColorBlobObservation(sample_rgb=sample_rgb, tolerance=0.22),
        state_model=AngleState(),
        motion_model=PeriodicAngularPrior(max_angular_speed=30.0, residual_margin=0.45),
        tracker_filter=AlphaBetaFilter(keys=("theta_unwrapped",), alpha=0.85, beta=0.2),
        optimizer=ParticleSwarmOptimizer(particles=18, iterations=20),
        metadata={"preset": "circular_motion", "radius_px": radius},
    )


def travelling_flame_preset(
    center_px: tuple[float, float] = (320.0, 240.0),
    inner_radius: float = 90.0,
    outer_radius: float = 125.0,
    direction: str = "ccw",
    unit_per_pixel: float = 1.0,
    unit: str = "px",
) -> TrackingPipeline:
    return TrackingPipeline(
        name="Travelling Flame",
        roi=AnnularROI(center=center_px, inner_radius=inner_radius, outer_radius=outer_radius),
        coordinate_model=AnnularCoordinate(
            center_px=center_px,
            direction=direction,
            inner_radius=inner_radius,
            outer_radius=outer_radius,
            unit_per_pixel=unit_per_pixel,
            unit=unit,
        ),
        observation_model=AnnularRadialFrontObservation(
            n_angles=720,
            n_radii=24,
            response_kind="fire",
            min_response=0.1,
            smoothing=4,
        ),
        state_model=AngleState(),
        motion_model=PeriodicAngularPrior(max_angular_speed=25.0, residual_margin=0.35),
        tracker_filter=AlphaBetaFilter(keys=("theta_unwrapped",), alpha=0.8, beta=0.18),
        optimizer=ParticleSwarmOptimizer(particles=24, iterations=35),
        metadata={"preset": "travelling_flame"},
    )


def wavefront_preset(
    roi: RectangularROI | None = None,
    axis: str = "x",
) -> TrackingPipeline:
    key = "x_px" if axis == "x" else "y_px"
    return TrackingPipeline(
        name="Wave / Interface Front",
        roi=roi or RectangularROI(0.0, 0.0, 640.0, 480.0),
        coordinate_model=ImageCoordinate(),
        observation_model=EdgeFrontObservation(axis=axis),
        state_model=ScalarState(key=key, unit="px"),
        motion_model=BoundedVelocityPrior(keys=(key,), max_speed=500.0),
        tracker_filter=ExponentialSmoothingFilter(alpha=0.7, keys=(key,)),
        optimizer=ParticleSwarmOptimizer(particles=18, iterations=20),
        metadata={"preset": "wavefront", "axis": axis},
    )


def path_motion_preset(
    polyline: tuple[tuple[float, float], ...] = ((60.0, 240.0), (250.0, 240.0), (420.0, 180.0)),
    half_width: float = 24.0,
) -> TrackingPipeline:
    return TrackingPipeline(
        name="Path Motion",
        roi=CurveBandROI(polyline=polyline, half_width=half_width),
        coordinate_model=PathCoordinate(polyline=polyline),
        observation_model=BrightnessPeakObservation(polarity="bright", min_response=0.2),
        state_model=FrontState(key="s", unit="px"),
        motion_model=PathMotionPrior(key="s", max_speed=500.0),
        tracker_filter=AlphaBetaFilter(keys=("s",), alpha=0.85, beta=0.2),
        optimizer=ParticleSwarmOptimizer(particles=18, iterations=20),
        metadata={"preset": "path_motion"},
    )


def default_preset_registry() -> dict[str, PresetDescriptor]:
    return {
        "color_marker": PresetDescriptor(
            key="color_marker",
            title="Color Marker",
            description="HSV/RGB-style marker tracking in a rectangular or polygon ROI.",
            factory=color_marker_preset,
        ),
        "circular_motion": PresetDescriptor(
            key="circular_motion",
            title="Circular Motion",
            description="Color/bright marker tracking with polar state and phase unwrapping.",
            factory=circular_motion_preset,
        ),
        "travelling_flame": PresetDescriptor(
            key="travelling_flame",
            title="Travelling Flame",
            description="Annular ROI with cached linear fire sampling, polar unwrap, and angular front state.",
            factory=travelling_flame_preset,
        ),
        "wavefront": PresetDescriptor(
            key="wavefront",
            title="Wave / Interface Front",
            description="Single-axis edge tracking normalized inside the applied ROI.",
            factory=wavefront_preset,
        ),
        "path_motion": PresetDescriptor(
            key="path_motion",
            title="Path Motion",
            description="Curve-band ROI with path arc-length state.",
            factory=path_motion_preset,
        ),
    }


def synthetic_ring_frame(
    shape: tuple[int, int, int],
    center_px: tuple[float, float],
    radius: float,
    theta: float,
    angular_width: float = 0.12,
    color: tuple[float, float, float] = (255.0, 170.0, 20.0),
) -> np.ndarray:
    h, w = shape[:2]
    yy, xx = np.indices((h, w))
    cx, cy = center_px
    rr = np.hypot(xx - cx, yy - cy)
    angle = np.arctan2(cy - yy, xx - cx) % (2.0 * np.pi)
    delta = np.angle(np.exp(1j * (angle - theta)))
    radial = np.exp(-((rr - radius) / max(radius * 0.08, 1.0)) ** 2)
    angular = np.exp(-(delta / angular_width) ** 2)
    signal = radial * angular
    frame = np.zeros(shape, dtype=np.uint8)
    for channel, value in enumerate(color):
        frame[:, :, channel] = np.clip(signal * value, 0, 255).astype(np.uint8)
    return frame
