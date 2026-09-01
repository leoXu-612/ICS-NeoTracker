from __future__ import annotations

from math import isfinite
from numbers import Real
from typing import Any, TypeVar

import numpy as np

from neo_tracker.coordinates import AnnularCoordinate, ImageCoordinate, LinearWorldCoordinate, PathCoordinate, PolarCoordinate
from neo_tracker.core import (
    CoordinateModel,
    MotionModel,
    ObservationModel,
    Optimizer,
    ROIModel,
    StateModel,
    TrackerFilter,
    TrackingPipeline,
)
from neo_tracker.filters import AlphaBetaFilter, ExponentialSmoothingFilter, PassThroughFilter
from neo_tracker.motion import (
    BoundedVelocityPrior,
    ConstantVelocityPrior,
    NoMotionPrior,
    PathMotionPrior,
    PeriodicAngularPrior,
)
from neo_tracker.observations import (
    AnnularRadialFrontObservation,
    BrightnessPeakObservation,
    ColorBlobObservation,
    EdgeFrontObservation,
    TemplateObservation,
    MAX_TEMPLATE_DIMENSION,
    MAX_TEMPLATE_ELEMENTS,
)
from neo_tracker.optimizers import GridSearchOptimizer, ParticleSwarmOptimizer
from neo_tracker.roi import AnnularROI, CircularROI, CurveBandROI, PolygonROI, RectangularROI
from neo_tracker.states import AngleState, ContourState, FrontState, MultiTargetState, ScalarState, XYState


T = TypeVar("T")
MAX_ROI_POINTS = 4096
MAX_DEBUG_HISTORY_RESULTS = 128
MAX_DEBUG_HISTORY_BYTES = 256 * 1024 * 1024


def _finite_values(*values: float) -> bool:
    return all(isfinite(float(value)) for value in values)


def _roi_number(value: object) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError("ROI geometry must be numeric")
    return float(value)


def apply_pipeline_config(pipeline: TrackingPipeline, config: dict[str, Any] | None) -> TrackingPipeline:
    """Apply a serialized pipeline config onto an existing preset pipeline."""

    if not isinstance(config, dict):
        return pipeline
    pipeline.name = str(config.get("name", pipeline.name))
    pipeline.roi = roi_from_config(config.get("roi"), pipeline.roi)
    pipeline.coordinate_model = coordinate_from_config(config.get("coordinate_model"), pipeline.coordinate_model)
    pipeline.observation_model = observation_from_config(config.get("observation_model"), pipeline.observation_model)
    pipeline.state_model = state_from_config(config.get("state_model"), pipeline.state_model)
    pipeline.motion_model = motion_from_config(config.get("motion_model"), pipeline.motion_model)
    pipeline.tracker_filter = filter_from_config(config.get("tracker_filter"), pipeline.tracker_filter)
    if "optimizer" in config:
        pipeline.optimizer = optimizer_from_config(config.get("optimizer"), pipeline.optimizer)
    try:
        min_confidence = float(config["min_confidence"])
        if isfinite(min_confidence) and 0.0 <= min_confidence <= 1.0:
            pipeline.min_confidence = min_confidence
    except Exception:
        pass
    try:
        history_limit = int(config["debug_history_limit"])
        if 0 <= history_limit <= MAX_DEBUG_HISTORY_RESULTS:
            pipeline.debug_history_limit = history_limit
    except Exception:
        pass
    try:
        history_bytes = int(config["debug_history_max_bytes"])
        if 0 <= history_bytes <= MAX_DEBUG_HISTORY_BYTES:
            pipeline.debug_history_max_bytes = history_bytes
    except Exception:
        pass
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        pipeline.metadata.update(metadata)
    if pipeline.results:
        pipeline.rebuild_debug_history()
    return pipeline


def roi_from_config(config: Any, fallback: ROIModel | None = None) -> ROIModel:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "ROI config")
    error = validate_roi_config(config)
    if error is not None:
        return _required_fallback(fallback, f"invalid ROI config: {error}")
    try:
        roi_type = str(config.get("type", ""))
        if roi_type == "rectangle":
            return RectangularROI(
                x=float(config["x"]),
                y=float(config["y"]),
                width=float(config["width"]),
                height=float(config["height"]),
            )
        if roi_type == "polygon":
            return PolygonROI(points=_points(config["points"], minimum=3))
        if roi_type == "circle":
            return CircularROI(center=_point(config["center"]), radius=float(config["radius"]))
        if roi_type == "annulus":
            return AnnularROI(
                center=_point(config["center"]),
                inner_radius=float(config["inner_radius"]),
                outer_radius=float(config["outer_radius"]),
            )
        if roi_type == "curve_band":
            return CurveBandROI(polyline=_points(config["polyline"], minimum=2), half_width=float(config["half_width"]))
    except Exception:
        return _required_fallback(fallback, "ROI config")
    return _required_fallback(fallback, f"unsupported ROI type {config.get('type')!r}")


def validate_roi_config(config: object) -> str | None:
    """Return a user-facing error for unsafe ROI geometry, otherwise ``None``."""

    if not isinstance(config, dict):
        return "ROI must be an object"
    roi_type = config.get("type")
    try:
        if roi_type == "rectangle":
            x, y = _roi_number(config["x"]), _roi_number(config["y"])
            width, height = _roi_number(config["width"]), _roi_number(config["height"])
            if not _finite_values(x, y, width, height) or width <= 0.0 or height <= 0.0:
                return "rectangle width and height must be positive finite numbers"
            return None
        if roi_type == "circle":
            center = config["center"]
            cx, cy = _roi_number(center[0]), _roi_number(center[1])  # type: ignore[index]
            radius = _roi_number(config["radius"])
            if not _finite_values(cx, cy, radius) or radius <= 0.0:
                return "circle center and radius must be finite, with radius greater than zero"
            return None
        if roi_type == "annulus":
            center = config["center"]
            cx, cy = _roi_number(center[0]), _roi_number(center[1])  # type: ignore[index]
            inner = _roi_number(config["inner_radius"])
            outer = _roi_number(config["outer_radius"])
            if not _finite_values(cx, cy, inner, outer) or inner <= 0.0 or outer <= inner:
                return "annulus radii must be finite, positive, and ordered"
            return None
        if roi_type == "polygon":
            points = tuple(
                (_roi_number(row[0]), _roi_number(row[1]))  # type: ignore[index]
                for row in config["points"]
            )
            if len(points) < 3 or not all(_finite_values(x, y) for x, y in points):
                return "polygon must contain at least three finite points"
            if len(points) > MAX_ROI_POINTS:
                return f"polygon must not exceed {MAX_ROI_POINTS:,} points"
            return None
        if roi_type == "curve_band":
            points = tuple(
                (_roi_number(row[0]), _roi_number(row[1]))  # type: ignore[index]
                for row in config["polyline"]
            )
            half_width = _roi_number(config["half_width"])
            if (
                len(points) < 2
                or not all(_finite_values(x, y) for x, y in points)
                or not isfinite(half_width)
                or half_width <= 0.0
            ):
                return "curve band must contain two finite points and a positive finite half-width"
            if len(points) > MAX_ROI_POINTS:
                return f"curve band must not exceed {MAX_ROI_POINTS:,} points"
            return None
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "ROI contains missing or non-numeric geometry"
    return f"unsupported ROI type {roi_type!r}"


def coordinate_from_config(config: Any, fallback: CoordinateModel | None = None) -> CoordinateModel:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "coordinate config")
    try:
        coordinate_type = str(config.get("type", ""))
        if coordinate_type == "image":
            return ImageCoordinate()
        if coordinate_type == "linear_world":
            return LinearWorldCoordinate(
                origin_px=_point(config["origin_px"]),
                x_axis_px=_point(config["x_axis_px"]),
                unit_per_pixel=float(config.get("unit_per_pixel", 1.0)),
                unit=str(config.get("unit", "px")),
                y_positive=str(config.get("y_positive", "up")),
            )
        if coordinate_type == "polar":
            return PolarCoordinate(
                center_px=_point(config["center_px"]),
                theta_zero_px=_optional_point(config.get("theta_zero_px")),
                direction=str(config.get("direction", "ccw")),
            )
        if coordinate_type == "annular":
            return AnnularCoordinate(
                center_px=_point(config["center_px"]),
                theta_zero_px=_optional_point(config.get("theta_zero_px")),
                direction=str(config.get("direction", "ccw")),
                inner_radius=float(config.get("inner_radius", 0.0)),
                outer_radius=float(config.get("outer_radius", 1.0)),
                unit_per_pixel=float(config.get("unit_per_pixel", 1.0)),
                unit=str(config.get("unit", "px")),
            )
        if coordinate_type == "path":
            return PathCoordinate(
                polyline=_points(config["polyline"], minimum=2),
                unit_per_pixel=float(config.get("unit_per_pixel", 1.0)),
                unit=str(config.get("unit", "px")),
            )
    except Exception:
        return _required_fallback(fallback, "coordinate config")
    return _required_fallback(fallback, f"unsupported coordinate type {config.get('type')!r}")


def observation_from_config(config: Any, fallback: ObservationModel | None = None) -> ObservationModel:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "observation config")
    try:
        observation_type = str(config.get("type", ""))
        if observation_type == "color_blob":
            return ColorBlobObservation(
                sample_rgb=_rgb(config.get("sample_rgb", (255.0, 0.0, 0.0))),
                tolerance=float(config.get("tolerance", 0.18)),
                min_response=float(config.get("min_response", 0.35)),
                max_candidates=max(1, int(config.get("max_candidates", 4))),
                min_component_area=max(1, int(config.get("min_component_area", 1))),
            )
        if observation_type == "brightness_peak":
            return BrightnessPeakObservation(
                polarity=str(config.get("polarity", "bright")),
                min_response=float(config.get("min_response", 0.25)),
                percentile_floor=float(config.get("percentile_floor", 80.0)),
                max_candidates=max(1, int(config.get("max_candidates", 4))),
                min_component_area=max(1, int(config.get("min_component_area", 1))),
            )
        if observation_type == "edge_front":
            return EdgeFrontObservation(
                axis=str(config.get("axis", "x")),
                min_response=float(config.get("min_response", 0.2)),
            )
        if observation_type == "template":
            template = config.get("template")
            if template is not None:
                template_array = np.asarray(template)
                if (
                    template_array.ndim not in {2, 3}
                    or any(size < 1 for size in template_array.shape)
                    or any(int(size) > MAX_TEMPLATE_DIMENSION for size in template_array.shape[:2])
                    or int(template_array.size) > MAX_TEMPLATE_ELEMENTS
                ):
                    raise ValueError("template dimensions exceed the safe configuration limit")
                return TemplateObservation(
                    template=np.asarray(template_array, dtype=float),
                    min_score=float(config.get("min_score", 0.65)),
                )
            if isinstance(fallback, TemplateObservation):
                fallback.min_score = float(config.get("min_score", fallback.min_score))
                return fallback
        if observation_type == "annular_radial_front":
            return AnnularRadialFrontObservation(
                n_angles=int(config.get("n_angles", 720)),
                n_radii=int(config.get("n_radii", 24)),
                response_kind=str(config.get("response_kind", "fire")),
                min_response=float(config.get("min_response", 0.15)),
                smoothing=int(config.get("smoothing", 3)),
            )
    except Exception:
        return _required_fallback(fallback, "observation config")
    return _required_fallback(fallback, f"unsupported observation type {config.get('type')!r}")


def state_from_config(config: Any, fallback: StateModel | None = None) -> StateModel:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "state config")
    try:
        state_type = str(config.get("type", ""))
        if state_type == "xy":
            return XYState(unit=str(config.get("unit", "px")))
        if state_type == "scalar":
            return ScalarState(key=str(config.get("key", "s")), unit=str(config.get("unit", "px")))
        if state_type == "angle":
            return AngleState(unit=str(config.get("unit", "rad")), keep_radius=bool(config.get("keep_radius", True)))
        if state_type == "front":
            return FrontState(key=str(config.get("key", "s")), unit=str(config.get("unit", "px")))
        if state_type == "contour":
            return ContourState()
        if state_type == "multi_target":
            return MultiTargetState()
    except Exception:
        return _required_fallback(fallback, "state config")
    return _required_fallback(fallback, f"unsupported state type {config.get('type')!r}")


def motion_from_config(config: Any, fallback: MotionModel | None = None) -> MotionModel:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "motion config")
    try:
        motion_type = str(config.get("type", ""))
        if motion_type == "none":
            return NoMotionPrior()
        if motion_type == "bounded_velocity":
            return BoundedVelocityPrior(
                keys=_strings(config.get("keys"), ("x_px", "y_px")),
                max_speed=float(config.get("max_speed", 250.0)),
                margin=float(config.get("margin", 8.0)),
            )
        if motion_type == "constant_velocity":
            return ConstantVelocityPrior(
                keys=_strings(config.get("keys"), ("x_px", "y_px")),
                max_residual=float(config.get("max_residual", 40.0)),
            )
        if motion_type == "periodic_angular":
            return PeriodicAngularPrior(
                key=str(config.get("key", "theta_unwrapped")),
                max_angular_speed=float(config.get("max_angular_speed", 20.0)),
                residual_margin=float(config.get("residual_margin", 0.25)),
                direction=str(config.get("direction", "either")),
            )
        if motion_type == "path_motion":
            return PathMotionPrior(
                key=str(config.get("key", "s")),
                max_speed=float(config.get("max_speed", 250.0)),
                margin=float(config.get("margin", 8.0)),
            )
    except Exception:
        return _required_fallback(fallback, "motion config")
    return _required_fallback(fallback, f"unsupported motion type {config.get('type')!r}")


def filter_from_config(config: Any, fallback: TrackerFilter | None = None) -> TrackerFilter:
    if not isinstance(config, dict):
        return _required_fallback(fallback, "filter config")
    try:
        filter_type = str(config.get("type", ""))
        if filter_type == "pass_through":
            return PassThroughFilter()
        if filter_type == "exponential_smoothing":
            keys = config.get("keys")
            return ExponentialSmoothingFilter(
                alpha=float(config.get("alpha", 0.7)),
                keys=_optional_strings(keys),
            )
        if filter_type == "alpha_beta":
            return AlphaBetaFilter(
                keys=_strings(config.get("keys"), ("x_px", "y_px")),
                alpha=float(config.get("alpha", 0.85)),
                beta=float(config.get("beta", 0.25)),
            )
    except Exception:
        return _required_fallback(fallback, "filter config")
    return _required_fallback(fallback, f"unsupported filter type {config.get('type')!r}")


def optimizer_from_config(config: Any, fallback: Optimizer | None = None) -> Optimizer | None:
    if config is None:
        return None
    if not isinstance(config, dict):
        return fallback
    try:
        optimizer_type = str(config.get("type", ""))
        if optimizer_type == "grid_search":
            return GridSearchOptimizer(
                samples_per_axis=int(config.get("samples_per_axis", 5)),
                maximize=bool(config.get("maximize", True)),
            )
        if optimizer_type == "particle_swarm":
            seed = config.get("seed", 7)
            return ParticleSwarmOptimizer(
                particles=int(config.get("particles", 24)),
                iterations=int(config.get("iterations", 40)),
                inertia=float(config.get("inertia", 0.65)),
                cognitive=float(config.get("cognitive", 1.4)),
                social=float(config.get("social", 1.4)),
                seed=None if seed is None else int(seed),
                maximize=bool(config.get("maximize", True)),
            )
    except Exception:
        return fallback
    return fallback


def _point(value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("point must contain two numbers")
    return float(value[0]), float(value[1])


def _optional_point(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    return _point(value)


def _points(value: Any, minimum: int) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)) or len(value) < minimum:
        raise ValueError(f"expected at least {minimum} points")
    if len(value) > MAX_ROI_POINTS:
        raise ValueError(f"point collection must not exceed {MAX_ROI_POINTS:,} points")
    return tuple(_point(point) for point in value)


def _rgb(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError("RGB sample must contain three numbers")
    return float(value[0]), float(value[1]), float(value[2])


def _strings(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    parsed = _optional_strings(value)
    return parsed if parsed else fallback


def _optional_strings(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a string list")
    return tuple(str(item) for item in value)


def _required_fallback(fallback: T | None, label: str) -> T:
    if fallback is None:
        raise ValueError(f"Cannot build {label} without a fallback")
    return fallback
