from __future__ import annotations

"""Immutable data contracts shared by the v0.3 analysis UI and engine."""

import base64
import binascii
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping, TypeVar

import numpy as np


MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_WIRE_ARRAY_BYTES = 512 * 1024 * 1024


class DerivativeMethod(str, Enum):
    NONUNIFORM_FINITE_DIFFERENCE = "nonuniform_finite_difference"
    SAVGOL_UNIFORM = "savgol_uniform"


class EdgePolicy(str, Enum):
    INVALID = "invalid"
    ONE_SIDED = "one_sided"


class GapPolicy(str, Enum):
    SPLIT = "split"


class FitModel(str, Enum):
    LINEAR = "linear"
    QUADRATIC = "quadratic"
    EXPONENTIAL = "exponential"
    SINUSOIDAL = "sinusoidal"


class FitStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


EnumT = TypeVar("EnumT", bound=Enum)


def _coerce_enum(value: object, enum_type: type[EnumT], name: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"unknown {name} {value!r}; expected one of: {choices}") from exc


def _require_text(
    value: object,
    name: str,
    *,
    allow_empty: bool = False,
    max_length: int = 4096,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return value


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _reject_unknown_keys(
    value: Mapping[str, object],
    allowed: set[str],
    name: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}")


def _freeze_json(
    value: object,
    *,
    name: str,
    depth: int = 0,
    budget: list[int] | None = None,
) -> object:
    """Return a detached, deeply immutable JSON-compatible value."""

    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_JSON_NODES:
        raise ValueError(f"{name} exceeds {MAX_JSON_NODES} JSON nodes")
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"{name} exceeds JSON nesting depth {MAX_JSON_DEPTH}")

    if isinstance(value, np.generic):
        value = value.item()
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{name} keys must be strings")
            frozen[key] = _freeze_json(
                item,
                name=f"{name}.{key}",
                depth=depth + 1,
                budget=budget,
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(
                item,
                name=f"{name}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
            for index, item in enumerate(value)
        )
    raise TypeError(f"{name} contains non-JSON value {type(value).__name__}")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_real_numeric_array(value: np.ndarray, name: str) -> None:
    if value.dtype == np.dtype(bool) or not (
        np.issubdtype(value.dtype, np.integer)
        or np.issubdtype(value.dtype, np.floating)
    ):
        raise TypeError(f"{name} must contain real numbers")


def _readonly_vector(value: object, dtype: np.dtype[Any], name: str) -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    _require_real_numeric_array(source, name)
    result = np.array(source, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


def _frame_indices(value: object) -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != 1:
        raise ValueError("frame_indices must be one-dimensional")
    if source.dtype == np.dtype(bool) or not np.issubdtype(source.dtype, np.integer):
        raise TypeError("frame_indices must contain integers, not coerced numeric values")
    result = np.array(source, dtype=np.int64, copy=True, order="C")
    result.setflags(write=False)
    return result


def _valid_mask(value: object, name: str = "valid_mask") -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if source.dtype != np.dtype(bool):
        raise TypeError(f"{name} must contain booleans")
    result = np.array(source, dtype=bool, copy=True, order="C")
    result.setflags(write=False)
    return result


def _readonly_matrix(value: object, name: str) -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != 2:
        raise ValueError(f"{name} must be two-dimensional")
    _require_real_numeric_array(source, name)
    result = np.array(source, dtype=np.float64, copy=True, order="C")
    result.setflags(write=False)
    return result


def _array_to_wire(value: np.ndarray) -> dict[str, object]:
    contiguous = np.ascontiguousarray(value)
    raw = contiguous.tobytes(order="C")
    return {
        "dtype": contiguous.dtype.str,
        "shape": list(contiguous.shape),
        "data_b64": base64.b64encode(raw).decode("ascii"),
    }


def _array_from_wire(
    value: object,
    *,
    expected_dtype: np.dtype[Any],
    dimensions: int,
    name: str,
) -> np.ndarray:
    data = _require_mapping(value, name)
    _reject_unknown_keys(data, {"dtype", "shape", "data_b64"}, name)
    dtype_value = data.get("dtype")
    shape_value = data.get("shape")
    payload = data.get("data_b64")
    if not isinstance(dtype_value, str):
        raise TypeError(f"{name}.dtype must be a string")
    try:
        dtype = np.dtype(dtype_value)
    except TypeError as exc:
        raise ValueError(f"{name}.dtype is invalid") from exc
    if dtype != np.dtype(expected_dtype):
        raise ValueError(f"{name}.dtype must be {np.dtype(expected_dtype)}")
    if not isinstance(shape_value, list) or len(shape_value) != dimensions:
        raise ValueError(f"{name}.shape must contain {dimensions} dimensions")
    shape: list[int] = []
    item_count = 1
    for index, dimension in enumerate(shape_value):
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 0:
            raise ValueError(f"{name}.shape[{index}] must be a non-negative integer")
        item_count *= dimension
        shape.append(dimension)
    byte_count = item_count * dtype.itemsize
    if byte_count > MAX_WIRE_ARRAY_BYTES:
        raise ValueError(f"{name} exceeds {MAX_WIRE_ARRAY_BYTES} decoded bytes")
    if not isinstance(payload, str):
        raise TypeError(f"{name}.data_b64 must be a string")
    if len(payload) > ((MAX_WIRE_ARRAY_BYTES + 2) // 3) * 4:
        raise ValueError(f"{name}.data_b64 exceeds the wire limit")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{name}.data_b64 is invalid") from exc
    if len(raw) != byte_count:
        raise ValueError(
            f"{name} byte length {len(raw)} does not match shape {tuple(shape)}"
        )
    return np.frombuffer(raw, dtype=dtype).reshape(tuple(shape))


def _string_tuple(value: object, name: str, *, allow_empty_items: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple")
    return tuple(
        _require_text(
            item,
            f"{name}[{index}]",
            allow_empty=allow_empty_items,
            max_length=128,
        )
        for index, item in enumerate(value)
    )


def _float_equal(left: float, right: float) -> bool:
    return left == right or (math.isnan(left) and math.isnan(right))


def _numeric_float(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{name} must be numeric") from exc


def _metric_to_wire(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _metric_from_wire(value: object, name: str) -> float:
    if value is None:
        return math.nan
    try:
        result = _numeric_float(value, name)
    except TypeError as exc:
        raise TypeError(f"{name} must be numeric or null") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} wire value must be finite or null")
    return result


@dataclass(frozen=True)
class ProcessingStep:
    operation: str
    parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", _require_text(self.operation, "operation", max_length=128))
        parameters = _require_mapping(self.parameters, "parameters")
        object.__setattr__(self, "parameters", _freeze_json(parameters, name="parameters"))

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "parameters": _thaw_json(self.parameters),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProcessingStep":
        data = _require_mapping(value, "processing step")
        _reject_unknown_keys(data, {"operation", "parameters"}, "processing step")
        return cls(
            operation=data.get("operation"),  # type: ignore[arg-type]
            parameters=_require_mapping(data.get("parameters", {}), "parameters"),
        )

    def __str__(self) -> str:
        parameters = _thaw_json(self.parameters)
        if not parameters:
            return self.operation
        rendered = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
            for key, value in sorted(parameters.items())
        )
        return f"{self.operation}({rendered})"


@dataclass(frozen=True)
class DerivativeConfig:
    method: DerivativeMethod | str
    order: int
    edge_policy: EdgePolicy | str = EdgePolicy.INVALID
    gap_policy: GapPolicy | str = GapPolicy.SPLIT
    window_length: int = 11
    polyorder: int = 3
    uniformity_tolerance: float = 1e-3

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _coerce_enum(self.method, DerivativeMethod, "derivative method"))
        object.__setattr__(self, "edge_policy", _coerce_enum(self.edge_policy, EdgePolicy, "edge policy"))
        object.__setattr__(self, "gap_policy", _coerce_enum(self.gap_policy, GapPolicy, "gap policy"))
        if isinstance(self.order, (bool, np.bool_)) or not isinstance(
            self.order, (int, np.integer)
        ):
            raise TypeError("derivative order must be an integer")
        order = int(self.order)
        if order not in (1, 2):
            raise ValueError("derivative order must be 1 or 2")
        if isinstance(self.window_length, bool) or not isinstance(self.window_length, int):
            raise TypeError("window_length must be an integer")
        if self.window_length < 3 or self.window_length % 2 == 0:
            raise ValueError("window_length must be an odd integer of at least 3")
        if isinstance(self.polyorder, bool) or not isinstance(self.polyorder, int):
            raise TypeError("polyorder must be an integer")
        if self.polyorder < order or self.polyorder >= self.window_length:
            raise ValueError("polyorder must cover derivative order and be smaller than window_length")
        tolerance = _numeric_float(self.uniformity_tolerance, "uniformity_tolerance")
        if not math.isfinite(tolerance) or tolerance < 0.0 or tolerance >= 1.0:
            raise ValueError("uniformity_tolerance must be finite and in [0, 1)")
        object.__setattr__(self, "order", order)
        object.__setattr__(self, "uniformity_tolerance", tolerance)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method.value,
            "order": self.order,
            "edge_policy": self.edge_policy.value,
            "gap_policy": self.gap_policy.value,
            "window_length": self.window_length,
            "polyorder": self.polyorder,
            "uniformity_tolerance": self.uniformity_tolerance,
        }

    @classmethod
    def from_dict(cls, value: object) -> "DerivativeConfig":
        data = _require_mapping(value, "derivative config")
        allowed = {
            "method",
            "order",
            "edge_policy",
            "gap_policy",
            "window_length",
            "polyorder",
            "uniformity_tolerance",
        }
        _reject_unknown_keys(data, allowed, "derivative config")
        return cls(
            method=data.get("method"),  # type: ignore[arg-type]
            order=data.get("order"),  # type: ignore[arg-type]
            edge_policy=data.get("edge_policy", EdgePolicy.INVALID.value),  # type: ignore[arg-type]
            gap_policy=data.get("gap_policy", GapPolicy.SPLIT.value),  # type: ignore[arg-type]
            window_length=data.get("window_length", 11),  # type: ignore[arg-type]
            polyorder=data.get("polyorder", 3),  # type: ignore[arg-type]
            uniformity_tolerance=data.get("uniformity_tolerance", 1e-3),  # type: ignore[arg-type]
        )


def _finite_parameter_mapping(value: object, name: str) -> Mapping[str, float]:
    source = _require_mapping(value, name)
    result: dict[str, float] = {}
    for key, item in source.items():
        parameter = _require_text(key, f"{name} key", max_length=128)
        number = _numeric_float(item, f"{name}.{parameter}")
        if not math.isfinite(number):
            raise ValueError(f"{name}.{parameter} must be finite")
        result[parameter] = number
    return MappingProxyType(result)


def _bounds_mapping(value: object) -> Mapping[str, tuple[float, float]]:
    source = _require_mapping(value, "bounds")
    result: dict[str, tuple[float, float]] = {}
    for key, item in source.items():
        parameter = _require_text(key, "bounds key", max_length=128)
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise TypeError(f"bounds.{parameter} must be a two-item sequence")
        try:
            lower = _numeric_float(item[0], f"bounds.{parameter}")
            upper = _numeric_float(item[1], f"bounds.{parameter}")
        except TypeError as exc:
            raise TypeError(f"bounds.{parameter} must contain numeric values") from exc
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise ValueError(f"bounds.{parameter} must be finite and strictly increasing")
        result[parameter] = (lower, upper)
    return MappingProxyType(result)


@dataclass(frozen=True)
class FitRequest:
    series_id: str
    model: FitModel | str
    range_start_s: float
    range_end_s: float
    source_revision: str
    initial_parameters: Mapping[str, float] = field(default_factory=dict)
    bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    use_valid_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _require_text(self.series_id, "series_id", max_length=256))
        object.__setattr__(self, "model", _coerce_enum(self.model, FitModel, "fit model"))
        start = _numeric_float(self.range_start_s, "range_start_s")
        end = _numeric_float(self.range_end_s, "range_end_s")
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError("fit range must be finite and range_start_s < range_end_s")
        object.__setattr__(self, "range_start_s", start)
        object.__setattr__(self, "range_end_s", end)
        object.__setattr__(
            self,
            "source_revision",
            _require_text(self.source_revision, "source_revision", max_length=256),
        )
        if not isinstance(self.use_valid_only, bool):
            raise TypeError("use_valid_only must be a boolean")
        initial = _finite_parameter_mapping(self.initial_parameters, "initial_parameters")
        bounds = _bounds_mapping(self.bounds)
        for name, number in initial.items():
            if name in bounds and not bounds[name][0] <= number <= bounds[name][1]:
                raise ValueError(f"initial_parameters.{name} falls outside its bounds")
        object.__setattr__(self, "initial_parameters", initial)
        object.__setattr__(self, "bounds", bounds)

    def to_dict(self) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "model": self.model.value,
            "range_start_s": self.range_start_s,
            "range_end_s": self.range_end_s,
            "source_revision": self.source_revision,
            "initial_parameters": dict(self.initial_parameters),
            "bounds": {name: list(value) for name, value in self.bounds.items()},
            "use_valid_only": self.use_valid_only,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FitRequest":
        data = _require_mapping(value, "fit request")
        allowed = {
            "series_id",
            "model",
            "range_start_s",
            "range_end_s",
            "source_revision",
            "initial_parameters",
            "bounds",
            "use_valid_only",
        }
        _reject_unknown_keys(data, allowed, "fit request")
        return cls(
            series_id=data.get("series_id"),  # type: ignore[arg-type]
            model=data.get("model"),  # type: ignore[arg-type]
            range_start_s=data.get("range_start_s"),  # type: ignore[arg-type]
            range_end_s=data.get("range_end_s"),  # type: ignore[arg-type]
            source_revision=data.get("source_revision"),  # type: ignore[arg-type]
            initial_parameters=_require_mapping(
                data.get("initial_parameters", {}), "initial_parameters"
            ),
            bounds=_require_mapping(data.get("bounds", {}), "bounds"),
            use_valid_only=data.get("use_valid_only", True),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, eq=False)
class SampleSeries:
    series_id: str
    name: str
    frame_indices: np.ndarray
    time_s: np.ndarray
    values: np.ndarray
    valid_mask: np.ndarray
    unit: str
    source_kind: str
    source_revision: str
    processing_chain: tuple[ProcessingStep, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    __hash__ = None

    @property
    def is_derived(self) -> bool:
        return self.source_kind.strip().lower() in {
            "derived",
            "fit",
            "fit_residual",
            "residual",
        }

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _require_text(self.series_id, "series_id", max_length=256))
        object.__setattr__(self, "name", _require_text(self.name, "name", max_length=512))
        object.__setattr__(self, "unit", _require_text(self.unit, "unit", allow_empty=True, max_length=64))
        object.__setattr__(
            self,
            "source_kind",
            _require_text(self.source_kind, "source_kind", max_length=128),
        )
        object.__setattr__(
            self,
            "source_revision",
            _require_text(self.source_revision, "source_revision", max_length=256),
        )

        frames = _frame_indices(self.frame_indices)
        times = _readonly_vector(self.time_s, np.dtype(np.float64), "time_s")
        values = _readonly_vector(self.values, np.dtype(np.float64), "values")
        mask = _valid_mask(self.valid_mask)
        lengths = {len(frames), len(times), len(values), len(mask)}
        if len(lengths) != 1:
            raise ValueError("frame_indices, time_s, values, and valid_mask lengths must match")
        if frames.size and (np.any(frames < 0) or np.any(np.diff(frames) <= 0)):
            raise ValueError("frame_indices must be non-negative and strictly increasing")
        if mask.any():
            valid_times = times[mask]
            valid_values = values[mask]
            if not np.isfinite(valid_times).all():
                raise ValueError("valid time_s values must be finite")
            if valid_times.size > 1 and np.any(np.diff(valid_times) <= 0.0):
                raise ValueError("valid time_s values must be strictly increasing")
            if not np.isfinite(valid_values).all():
                raise ValueError("values marked valid must be finite")
        if np.isinf(times).any() or np.isinf(values).any():
            raise ValueError("time_s and values may use NaN for invalid samples, not infinity")

        chain = tuple(self.processing_chain)
        if any(not isinstance(step, ProcessingStep) for step in chain):
            raise TypeError("processing_chain must contain ProcessingStep values")
        metadata = _require_mapping(self.metadata, "metadata")
        object.__setattr__(self, "frame_indices", frames)
        object.__setattr__(self, "time_s", times)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "valid_mask", mask)
        object.__setattr__(self, "processing_chain", chain)
        object.__setattr__(self, "metadata", _freeze_json(metadata, name="metadata"))

    def __len__(self) -> int:
        return int(self.values.size)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SampleSeries):
            return NotImplemented
        return (
            self.series_id == other.series_id
            and self.name == other.name
            and np.array_equal(self.frame_indices, other.frame_indices)
            and np.array_equal(self.time_s, other.time_s, equal_nan=True)
            and np.array_equal(self.values, other.values, equal_nan=True)
            and np.array_equal(self.valid_mask, other.valid_mask)
            and self.unit == other.unit
            and self.source_kind == other.source_kind
            and self.source_revision == other.source_revision
            and self.processing_chain == other.processing_chain
            and self.metadata == other.metadata
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "name": self.name,
            "frame_indices": _array_to_wire(self.frame_indices),
            "time_s": _array_to_wire(self.time_s),
            "values": _array_to_wire(self.values),
            "valid_mask": _array_to_wire(self.valid_mask),
            "unit": self.unit,
            "source_kind": self.source_kind,
            "source_revision": self.source_revision,
            "processing_chain": [step.to_dict() for step in self.processing_chain],
            "metadata": _thaw_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "SampleSeries":
        data = _require_mapping(value, "sample series")
        allowed = {
            "series_id",
            "name",
            "frame_indices",
            "time_s",
            "values",
            "valid_mask",
            "unit",
            "source_kind",
            "source_revision",
            "processing_chain",
            "metadata",
        }
        _reject_unknown_keys(data, allowed, "sample series")
        chain_data = data.get("processing_chain", [])
        if not isinstance(chain_data, list):
            raise TypeError("processing_chain must be a list")
        return cls(
            series_id=data.get("series_id"),  # type: ignore[arg-type]
            name=data.get("name"),  # type: ignore[arg-type]
            frame_indices=_array_from_wire(
                data.get("frame_indices"),
                expected_dtype=np.dtype(np.int64),
                dimensions=1,
                name="frame_indices",
            ),
            time_s=_array_from_wire(
                data.get("time_s"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="time_s",
            ),
            values=_array_from_wire(
                data.get("values"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="values",
            ),
            valid_mask=_array_from_wire(
                data.get("valid_mask"),
                expected_dtype=np.dtype(bool),
                dimensions=1,
                name="valid_mask",
            ),
            unit=data.get("unit"),  # type: ignore[arg-type]
            source_kind=data.get("source_kind"),  # type: ignore[arg-type]
            source_revision=data.get("source_revision"),  # type: ignore[arg-type]
            processing_chain=tuple(ProcessingStep.from_dict(item) for item in chain_data),
            metadata=_require_mapping(data.get("metadata", {}), "metadata"),
        )


@dataclass(frozen=True, eq=False)
class FitResult:
    series_id: str
    model: FitModel | str
    parameter_names: tuple[str, ...]
    parameters: np.ndarray
    parameter_units: tuple[str, ...]
    standard_errors: np.ndarray
    covariance: np.ndarray
    predicted: np.ndarray
    residuals: np.ndarray
    valid_mask: np.ndarray
    rmse: float
    r_squared: float
    sample_count: int
    range_start_s: float
    range_end_s: float
    source_revision: str
    status: FitStatus | str
    message: str = ""

    __hash__ = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _require_text(self.series_id, "series_id", max_length=256))
        object.__setattr__(self, "model", _coerce_enum(self.model, FitModel, "fit model"))
        object.__setattr__(self, "status", _coerce_enum(self.status, FitStatus, "fit status"))
        object.__setattr__(
            self,
            "source_revision",
            _require_text(self.source_revision, "source_revision", max_length=256),
        )
        object.__setattr__(
            self,
            "message",
            _require_text(self.message, "message", allow_empty=True, max_length=4096),
        )
        names = _string_tuple(self.parameter_names, "parameter_names")
        units = _string_tuple(self.parameter_units, "parameter_units", allow_empty_items=True)
        if len(set(names)) != len(names):
            raise ValueError("parameter_names must be unique")
        if len(names) != len(units):
            raise ValueError("parameter_names and parameter_units lengths must match")

        parameters = _readonly_vector(self.parameters, np.dtype(np.float64), "parameters")
        errors = _readonly_vector(self.standard_errors, np.dtype(np.float64), "standard_errors")
        covariance = _readonly_matrix(self.covariance, "covariance")
        predicted = _readonly_vector(self.predicted, np.dtype(np.float64), "predicted")
        residuals = _readonly_vector(self.residuals, np.dtype(np.float64), "residuals")
        mask = _valid_mask(self.valid_mask, "fit valid_mask")
        parameter_count = len(names)
        if len(parameters) != parameter_count or len(errors) != parameter_count:
            raise ValueError("parameter arrays must align with parameter_names")
        if covariance.shape != (parameter_count, parameter_count):
            raise ValueError("covariance must be square and align with parameter_names")
        if len(predicted) != len(residuals) or len(predicted) != len(mask):
            raise ValueError("predicted, residuals, and valid_mask lengths must match")
        if not np.isfinite(parameters).all():
            raise ValueError("fit parameters must be finite")
        if np.isinf(errors).any() or np.isinf(covariance).any():
            raise ValueError("fit uncertainty arrays may use NaN for unavailable values, not infinity")
        if np.isinf(predicted).any() or np.isinf(residuals).any():
            raise ValueError("fit value arrays may use NaN outside the fit mask, not infinity")
        finite_errors = errors[np.isfinite(errors)]
        if finite_errors.size and np.any(finite_errors < 0.0):
            raise ValueError("finite standard errors must be non-negative")
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int):
            raise TypeError("sample_count must be an integer")
        if self.sample_count < 0 or self.sample_count != int(np.count_nonzero(mask)):
            raise ValueError("sample_count must equal the number of true fit-mask entries")
        start = _numeric_float(self.range_start_s, "range_start_s")
        end = _numeric_float(self.range_end_s, "range_end_s")
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise ValueError("fit result range must be finite and strictly increasing")
        rmse = _numeric_float(self.rmse, "rmse")
        r_squared = _numeric_float(self.r_squared, "r_squared")
        if self.status is FitStatus.OK:
            if parameter_count == 0 or self.sample_count == 0:
                raise ValueError("successful fits require parameters and samples")
            if not math.isfinite(rmse) or rmse < 0.0 or not math.isfinite(r_squared):
                raise ValueError("successful fit metrics must be finite and rmse non-negative")
            if not np.isfinite(predicted[mask]).all() or not np.isfinite(residuals[mask]).all():
                raise ValueError("successful fit values marked valid must be finite")
        else:
            if math.isinf(rmse) or math.isinf(r_squared):
                raise ValueError("failed fit metrics may be NaN for unavailable values, not infinity")
            if math.isfinite(rmse) and rmse < 0.0:
                raise ValueError("finite rmse must be non-negative")

        object.__setattr__(self, "parameter_names", names)
        object.__setattr__(self, "parameter_units", units)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "standard_errors", errors)
        object.__setattr__(self, "covariance", covariance)
        object.__setattr__(self, "predicted", predicted)
        object.__setattr__(self, "residuals", residuals)
        object.__setattr__(self, "valid_mask", mask)
        object.__setattr__(self, "range_start_s", start)
        object.__setattr__(self, "range_end_s", end)
        object.__setattr__(self, "rmse", rmse)
        object.__setattr__(self, "r_squared", r_squared)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FitResult):
            return NotImplemented
        return (
            self.series_id == other.series_id
            and self.model == other.model
            and self.parameter_names == other.parameter_names
            and np.array_equal(self.parameters, other.parameters, equal_nan=True)
            and self.parameter_units == other.parameter_units
            and np.array_equal(self.standard_errors, other.standard_errors, equal_nan=True)
            and np.array_equal(self.covariance, other.covariance, equal_nan=True)
            and np.array_equal(self.predicted, other.predicted, equal_nan=True)
            and np.array_equal(self.residuals, other.residuals, equal_nan=True)
            and np.array_equal(self.valid_mask, other.valid_mask)
            and _float_equal(self.rmse, other.rmse)
            and _float_equal(self.r_squared, other.r_squared)
            and self.sample_count == other.sample_count
            and self.range_start_s == other.range_start_s
            and self.range_end_s == other.range_end_s
            and self.source_revision == other.source_revision
            and self.status == other.status
            and self.message == other.message
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "model": self.model.value,
            "parameter_names": list(self.parameter_names),
            "parameters": _array_to_wire(self.parameters),
            "parameter_units": list(self.parameter_units),
            "standard_errors": _array_to_wire(self.standard_errors),
            "covariance": _array_to_wire(self.covariance),
            "predicted": _array_to_wire(self.predicted),
            "residuals": _array_to_wire(self.residuals),
            "valid_mask": _array_to_wire(self.valid_mask),
            "rmse": _metric_to_wire(self.rmse),
            "r_squared": _metric_to_wire(self.r_squared),
            "sample_count": self.sample_count,
            "range_start_s": self.range_start_s,
            "range_end_s": self.range_end_s,
            "source_revision": self.source_revision,
            "status": self.status.value,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: object) -> "FitResult":
        data = _require_mapping(value, "fit result")
        allowed = {
            "series_id",
            "model",
            "parameter_names",
            "parameters",
            "parameter_units",
            "standard_errors",
            "covariance",
            "predicted",
            "residuals",
            "valid_mask",
            "rmse",
            "r_squared",
            "sample_count",
            "range_start_s",
            "range_end_s",
            "source_revision",
            "status",
            "message",
        }
        _reject_unknown_keys(data, allowed, "fit result")
        return cls(
            series_id=data.get("series_id"),  # type: ignore[arg-type]
            model=data.get("model"),  # type: ignore[arg-type]
            parameter_names=_string_tuple(data.get("parameter_names"), "parameter_names"),
            parameters=_array_from_wire(
                data.get("parameters"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="parameters",
            ),
            parameter_units=_string_tuple(
                data.get("parameter_units"), "parameter_units", allow_empty_items=True
            ),
            standard_errors=_array_from_wire(
                data.get("standard_errors"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="standard_errors",
            ),
            covariance=_array_from_wire(
                data.get("covariance"),
                expected_dtype=np.dtype(np.float64),
                dimensions=2,
                name="covariance",
            ),
            predicted=_array_from_wire(
                data.get("predicted"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="predicted",
            ),
            residuals=_array_from_wire(
                data.get("residuals"),
                expected_dtype=np.dtype(np.float64),
                dimensions=1,
                name="residuals",
            ),
            valid_mask=_array_from_wire(
                data.get("valid_mask"),
                expected_dtype=np.dtype(bool),
                dimensions=1,
                name="fit valid_mask",
            ),
            rmse=_metric_from_wire(data.get("rmse"), "rmse"),
            r_squared=_metric_from_wire(data.get("r_squared"), "r_squared"),
            sample_count=data.get("sample_count"),  # type: ignore[arg-type]
            range_start_s=data.get("range_start_s"),  # type: ignore[arg-type]
            range_end_s=data.get("range_end_s"),  # type: ignore[arg-type]
            source_revision=data.get("source_revision"),  # type: ignore[arg-type]
            status=data.get("status"),  # type: ignore[arg-type]
            message=data.get("message", ""),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, eq=False)
class KinematicsBundle:
    base_series: tuple[SampleSeries, ...]
    derived_series: tuple[SampleSeries, ...]
    fit_results: tuple[FitResult, ...]
    source_revision: str

    __hash__ = None

    def __post_init__(self) -> None:
        revision = _require_text(self.source_revision, "source_revision", max_length=256)
        base = tuple(self.base_series)
        derived = tuple(self.derived_series)
        fits = tuple(self.fit_results)
        if any(not isinstance(series, SampleSeries) for series in base + derived):
            raise TypeError("bundle series must contain SampleSeries values")
        if any(not isinstance(result, FitResult) for result in fits):
            raise TypeError("fit_results must contain FitResult values")
        identifiers = [series.series_id for series in base + derived]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("series_id values must be unique within a bundle")
        if any(series.source_revision != revision for series in base + derived):
            raise ValueError("bundle contains stale series revisions")
        known_ids = set(identifiers)
        if any(result.source_revision != revision for result in fits):
            raise ValueError("bundle contains stale fit-result revisions")
        if any(result.series_id not in known_ids for result in fits):
            raise ValueError("fit_results must reference a series in the bundle")
        object.__setattr__(self, "base_series", base)
        object.__setattr__(self, "derived_series", derived)
        object.__setattr__(self, "fit_results", fits)
        object.__setattr__(self, "source_revision", revision)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KinematicsBundle):
            return NotImplemented
        return (
            self.base_series == other.base_series
            and self.derived_series == other.derived_series
            and self.fit_results == other.fit_results
            and self.source_revision == other.source_revision
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "base_series": [series.to_dict() for series in self.base_series],
            "derived_series": [series.to_dict() for series in self.derived_series],
            "fit_results": [result.to_dict() for result in self.fit_results],
            "source_revision": self.source_revision,
        }

    @classmethod
    def from_dict(cls, value: object) -> "KinematicsBundle":
        data = _require_mapping(value, "kinematics bundle")
        _reject_unknown_keys(
            data,
            {"base_series", "derived_series", "fit_results", "source_revision"},
            "kinematics bundle",
        )
        base = data.get("base_series")
        derived = data.get("derived_series")
        fits = data.get("fit_results")
        if not isinstance(base, list) or not isinstance(derived, list) or not isinstance(fits, list):
            raise TypeError("bundle series and fit_results must be lists")
        return cls(
            base_series=tuple(SampleSeries.from_dict(item) for item in base),
            derived_series=tuple(SampleSeries.from_dict(item) for item in derived),
            fit_results=tuple(FitResult.from_dict(item) for item in fits),
            source_revision=data.get("source_revision"),  # type: ignore[arg-type]
        )
