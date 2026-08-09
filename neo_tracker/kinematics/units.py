from __future__ import annotations

"""Conservative unit inference for tracker state and filter-velocity keys."""

from collections.abc import Mapping

from .validation import derivative_unit, fit_parameter_units


ANGLE_KEYS = frozenset({"theta", "theta_unwrapped"})
ANGULAR_VELOCITY_KEYS = frozenset({"omega", "v_theta", "v_theta_unwrapped"})
PIXEL_KEYS = frozenset({"x_px", "y_px", "r"})


def state_unit(key: str, explicit_units: Mapping[str, str] | None = None) -> str:
    """Return only units justified by the key or an explicit caller mapping."""

    if not isinstance(key, str) or not key:
        raise ValueError("state key must be a non-empty string")
    units = explicit_units or {}
    explicit = units.get(key)
    if explicit is not None:
        if not isinstance(explicit, str):
            raise TypeError(f"unit for {key!r} must be a string")
        return explicit
    if key in ANGLE_KEYS:
        return "rad"
    if key in ANGULAR_VELOCITY_KEYS:
        return "rad/s"
    if key in PIXEL_KEYS or key.endswith("_px"):
        return "px"
    return ""


def velocity_unit(key: str, explicit_units: Mapping[str, str] | None = None) -> str:
    units = explicit_units or {}
    explicit = units.get(key)
    if explicit is not None:
        if not isinstance(explicit, str):
            raise TypeError(f"unit for {key!r} must be a string")
        return explicit
    if key in ANGULAR_VELOCITY_KEYS:
        return "rad/s"
    source_key = key[2:] if key.startswith("v_") else key
    return derivative_unit(state_unit(source_key, units), 1)


__all__ = [
    "ANGLE_KEYS",
    "ANGULAR_VELOCITY_KEYS",
    "PIXEL_KEYS",
    "derivative_unit",
    "fit_parameter_units",
    "state_unit",
    "velocity_unit",
]
