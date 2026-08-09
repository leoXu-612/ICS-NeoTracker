from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from neo_tracker.coordinates import PolarCoordinate
from neo_tracker.core import (
    CoordinateModel,
    FrameContext,
    ObservationCandidate,
    ObservationModel,
    ObservationResult,
    ROIModel,
)
from neo_tracker.roi import AnnularROI


_OBSERVATION_CV2: Any | None = None
_OBSERVATION_CV2_IMPORT_ERROR: Exception | None = None
_COMPONENT_CV2_RUNTIME_ERROR: Exception | None = None
_TEMPLATE_CV2_RUNTIME_ERROR: Exception | None = None
_COMPONENT_NUMPY_VECTOR_MIN_RUNS = 512
_COMPONENT_CV2_SPARSE_WEIGHT_RATIO = 0.25
_COMPONENT_CV2_LOCAL_BBOX_RATIO = 0.25
_COMPONENT_CV2_LOCAL_COMPONENT_LIMIT = 256
_COLOR_RESPONSE_LOCAL_BBOX_RATIO = 0.25
_COLOR_RESPONSE_COARSE_DIVISOR = 8
MAX_COMPONENT_CANDIDATES = 12
MAX_COMPONENT_AREA_THRESHOLD = 100_000
MAX_TEMPLATE_ELEMENTS = 4_194_304
MAX_TEMPLATE_DIMENSION = 4096
MAX_ANNULAR_ANGLES = 16_384
MAX_ANNULAR_RADII = 4_096
MAX_ANNULAR_SAMPLE_POINTS = 1_048_576
MAX_ANNULAR_SMOOTHING = 4_096


def _load_observation_cv2() -> Any | None:
    """Load the optional observation backend without making core import it eagerly."""
    global _OBSERVATION_CV2, _OBSERVATION_CV2_IMPORT_ERROR
    if _OBSERVATION_CV2 is not None:
        return _OBSERVATION_CV2
    if _OBSERVATION_CV2_IMPORT_ERROR is not None:
        return None
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on the local optional backend
        _OBSERVATION_CV2_IMPORT_ERROR = exc
        return None
    _OBSERVATION_CV2 = cv2
    return _OBSERVATION_CV2


def _load_component_cv2() -> Any | None:
    if _COMPONENT_CV2_RUNTIME_ERROR is not None:
        return None
    return _load_observation_cv2()


def _load_template_cv2() -> Any | None:
    if _TEMPLATE_CV2_RUNTIME_ERROR is not None:
        return None
    return _load_observation_cv2()


def _as_float01(frame: np.ndarray) -> np.ndarray:
    source = np.asarray(frame)
    if source.dtype == np.uint8:
        arr = source.astype(np.float32)
        arr *= np.float32(1.0 / 255.0)
        return arr
    arr = np.asarray(source, dtype=np.float32)
    if arr.size and arr.max() > 1.0:
        arr = arr / 255.0
    return np.clip(arr, 0.0, 1.0)


def _color_distance_response(
    frame: np.ndarray,
    sample_rgb: tuple[float, float, float],
    tolerance: float,
) -> np.ndarray:
    """Return the normalized RGB-distance response with a low-copy uint8 path.

    Decoded video frames are uint8. Compact color targets use a conservative
    RGB-box bound before evaluating the exact Euclidean formula in one local
    image box. Broad/dense colors retain the full-plane path. Both reuse one
    distance plane and one scratch plane; non-uint8 inputs retain the original
    normalization semantics.
    """
    rgb = np.asarray(frame)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("ColorBlobObservation requires an RGB frame")

    sample = np.asarray(sample_rgb, dtype=np.float32)
    if sample.max() > 1.0:
        sample = sample / np.float32(255.0)
    normalized_tolerance = tolerance / 255.0 if tolerance > 1.0 else tolerance

    if rgb.dtype != np.uint8:
        arr = _as_float01(rgb)
        distance = np.linalg.norm(arr[:, :, :3] - sample[:3], axis=2) / np.float32(np.sqrt(3.0))
        return np.clip(1.0 - distance / max(normalized_tolerance, 1e-12), 0.0, 1.0)

    sample_u8_scale = sample[:3] * np.float32(255.0)
    scale = np.float32(1.0 / (255.0 * np.sqrt(3.0) * max(normalized_tolerance, 1e-12)))
    local_response, x, y = _uint8_color_distance_response_window(
        rgb,
        sample_u8_scale,
        scale,
    )
    if x == 0 and y == 0 and local_response.shape == rgb.shape[:2]:
        return local_response
    return _full_response_from_window(local_response, rgb.shape, x, y)


def _uint8_color_distance_plane(
    rgb: np.ndarray,
    sample_u8_scale: np.ndarray,
    scale: np.float32,
) -> np.ndarray:
    """Evaluate the established RGB-distance formula on one uint8 plane."""

    distance_squared = rgb[:, :, 0].astype(np.float32)
    distance_squared -= sample_u8_scale[0]
    np.square(distance_squared, out=distance_squared)

    scratch = rgb[:, :, 1].astype(np.float32)
    scratch -= sample_u8_scale[1]
    np.square(scratch, out=scratch)
    distance_squared += scratch

    scratch[:] = rgb[:, :, 2]
    scratch -= sample_u8_scale[2]
    np.square(scratch, out=scratch)
    distance_squared += scratch

    np.sqrt(distance_squared, out=distance_squared)
    distance_squared *= scale
    np.subtract(np.float32(1.0), distance_squared, out=distance_squared)
    np.clip(distance_squared, 0.0, 1.0, out=distance_squared)
    return distance_squared


def _uint8_color_distance_response_window(
    rgb: np.ndarray,
    sample_u8_scale: np.ndarray,
    scale: np.float32,
) -> tuple[np.ndarray, int, int]:
    """Return the exact uint8 response in its smallest safe color box.

    Public observation calls expand this window back to the established full
    response plane. Background tracking can retain the local values and their
    placement metadata so connected-components work does not rescan an unused
    full-frame zero plane for a compact marker.
    """

    local_bbox = _color_response_local_bbox(rgb, sample_u8_scale, scale)
    if local_bbox is None:
        return _uint8_color_distance_plane(rgb, sample_u8_scale, scale), 0, 0
    x, y, width, height = local_bbox
    if width <= 0 or height <= 0:
        return np.zeros((0, 0), dtype=np.float32), int(x), int(y)
    return (
        _uint8_color_distance_plane(
            rgb[y : y + height, x : x + width],
            sample_u8_scale,
            scale,
        ),
        int(x),
        int(y),
    )


def _color_response_local_bbox(
    rgb: np.ndarray,
    sample_u8_scale: np.ndarray,
    scale: np.float32,
) -> tuple[int, int, int, int] | None:
    """Return a conservative compact search box, or ``None`` for full-plane work.

    OpenCV can inspect standard interleaved RGB input and MediaReader's
    channel-reversed RGB view without copying the full frame: the latter is
    viewed again as its contiguous BGR owner and uses reversed bounds. A tiny
    coarse sample avoids adding a full mask pass to obviously dense frames.
    """

    ratio = max(0.0, float(_COLOR_RESPONSE_LOCAL_BBOX_RATIO))
    if ratio <= 0.0 or rgb.size == 0 or rgb.dtype != np.uint8:
        return None
    if not np.all(np.isfinite(sample_u8_scale)) or not np.isfinite(scale) or scale <= 0.0:
        return None
    cv2 = _load_observation_cv2()
    if cv2 is None:
        return None

    rgb3 = rgb[:, :, :3]
    itemsize = int(rgb3.dtype.itemsize)
    probe: np.ndarray | None = None
    reverse_bounds = False
    standard_pixel_stride = 3 * itemsize
    if (
        rgb3.strides[0] > 0
        and rgb3.strides[1] == standard_pixel_stride
        and rgb3.strides[2] == itemsize
    ):
        probe = rgb3
    elif (
        rgb3.strides[0] > 0
        and rgb3.strides[1] == standard_pixel_stride
        and rgb3.strides[2] == -itemsize
    ):
        candidate = rgb3[:, :, 2::-1]
        if candidate.strides[2] == itemsize:
            probe = candidate
            reverse_bounds = True
    if probe is None:
        return None

    # The exact response is positive only inside this per-channel superset.
    # One extra code value covers float32 multiplication/rounding at the zero
    # boundary; pixels admitted by that margin are still evaluated exactly.
    radius = float(np.float32(1.0) / scale) + 1.0
    lower = [
        int(value)
        for value in np.clip(np.floor(sample_u8_scale - radius), 0.0, 255.0)
    ]
    upper = [
        int(value)
        for value in np.clip(np.ceil(sample_u8_scale + radius), 0.0, 255.0)
    ]
    if reverse_bounds:
        lower.reverse()
        upper.reverse()
    lower_bound = tuple(lower)
    upper_bound = tuple(upper)

    try:
        divisor = max(1, int(_COLOR_RESPONSE_COARSE_DIVISOR))
        row_step = max(1, probe.shape[0] // divisor)
        column_step = max(1, probe.shape[1] // divisor)
        coarse = probe[row_step // 2 :: row_step, column_step // 2 :: column_step]
        coarse_mask = cv2.inRange(coarse, lower_bound, upper_bound)
        if int(cv2.countNonZero(coarse_mask)) > coarse_mask.size * ratio:
            return None

        color_box_mask = cv2.inRange(probe, lower_bound, upper_bound)
        x, y, width, height = (int(value) for value in cv2.boundingRect(color_box_mask))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return 0, 0, 0, 0
    if width * height > rgb.shape[0] * rgb.shape[1] * ratio:
        return None
    return x, y, width, height


def _intensity(frame: np.ndarray) -> np.ndarray:
    source = np.asarray(frame)
    if source.ndim == 2:
        return _as_float01(source)
    if source.shape[2] == 1:
        return _as_float01(source[:, :, 0])
    if source.dtype == np.uint8:
        intensity = source[:, :, 0].astype(np.float32)
        intensity *= np.float32(0.299 / 255.0)
        scratch = source[:, :, 1].astype(np.float32)
        scratch *= np.float32(0.587 / 255.0)
        intensity += scratch
        scratch[:] = source[:, :, 2]
        scratch *= np.float32(0.114 / 255.0)
        intensity += scratch
        return intensity
    arr = _as_float01(source)
    return 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]


def _absolute_axis_gradient(image: np.ndarray, axis: int) -> np.ndarray:
    """Return NumPy-compatible first differences without a second axis buffer."""
    if image.ndim != 2 or axis not in {0, 1}:
        raise ValueError("edge front gradient requires a two-dimensional image and axis 0 or 1")
    if image.shape[axis] < 2:
        raise ValueError("edge front gradient axis must contain at least two pixels")

    response = np.empty_like(image)
    if axis == 1:
        np.subtract(image[:, 1], image[:, 0], out=response[:, 0])
        np.subtract(image[:, -1], image[:, -2], out=response[:, -1])
        if image.shape[1] > 2:
            np.subtract(image[:, 2:], image[:, :-2], out=response[:, 1:-1])
            response[:, 1:-1] *= np.float32(0.5)
    else:
        np.subtract(image[1, :], image[0, :], out=response[0, :])
        np.subtract(image[-1, :], image[-2, :], out=response[-1, :])
        if image.shape[0] > 2:
            np.subtract(image[2:, :], image[:-2, :], out=response[1:-1, :])
            response[1:-1, :] *= np.float32(0.5)
    np.abs(response, out=response)
    return response


def _fire_response(frame: np.ndarray) -> np.ndarray:
    source = np.asarray(frame)
    if source.ndim == 2:
        return _as_float01(source)
    if source.dtype == np.uint8:
        # Fuse the brightness and chromatic terms before touching the frame.
        # The previous implementation first built intensity and then scanned
        # the same RGB channels again to add the fire-specific weights.  The
        # combined coefficients preserve that mapping while halving the
        # channel conversions/passes on decoded uint8 video.
        response = source[:, :, 0].astype(np.float32)
        response *= np.float32((0.45 * 0.299 + 0.35) / 255.0)
        scratch = source[:, :, 1].astype(np.float32)
        scratch *= np.float32((0.45 * 0.587 + 0.25) / 255.0)
        response += scratch
        scratch[:] = source[:, :, 2]
        scratch *= np.float32((0.45 * 0.114 - 0.25) / 255.0)
        response += scratch
    else:
        arr = _as_float01(source)
        red = arr[:, :, 0]
        green = arr[:, :, 1]
        blue = arr[:, :, 2]
        brightness = _intensity(arr)
        response = 0.45 * brightness + 0.35 * red + 0.25 * green - 0.25 * blue
    response -= response.min()
    # The minimum is exactly zero after the in-place shift above.  Scanning
    # the full response a second time only repeats an H×W reduction on every
    # frame, which is particularly visible for 4K acquisition.
    denom = response.max()
    if denom <= 1e-12:
        response.fill(0.0)
        return response
    response /= denom
    return response


def _annular_sample_response(
    frame: np.ndarray,
    iy: np.ndarray,
    ix: np.ndarray,
    response_kind: str,
    *,
    linear_indices: np.ndarray | None = None,
) -> np.ndarray:
    """Compute an annular response only at the pixels the detector samples."""
    source = np.asarray(frame)
    if source.dtype == np.uint8 and linear_indices is not None:
        sample_shape = tuple(int(size) for size in np.asarray(iy).shape)
        flattened_indices = np.asarray(linear_indices, dtype=np.intp).reshape(-1)

        def sampled_plane(channel: int | None = None) -> np.ndarray | None:
            plane = source if channel is None else source[:, :, channel]
            flattened = plane.reshape(-1)
            if plane.size and not np.shares_memory(flattened, plane):
                return None
            return flattened[flattened_indices].reshape(sample_shape)

        if source.ndim == 2:
            sampled = sampled_plane()
            if sampled is not None:
                return _intensity(sampled)
        elif source.ndim == 3 and source.shape[2] == 1:
            sampled = sampled_plane(0)
            if sampled is not None:
                return _intensity(sampled)
        elif source.ndim == 3 and source.shape[2] >= 3:
            red = sampled_plane(0)
            green = sampled_plane(1)
            blue = sampled_plane(2)
            if red is not None and green is not None and blue is not None:
                response = red.astype(np.float32)
                scratch = green.astype(np.float32)
                if response_kind == "fire":
                    response *= np.float32((0.45 * 0.299 + 0.35) / 255.0)
                    scratch *= np.float32((0.45 * 0.587 + 0.25) / 255.0)
                    response += scratch
                    scratch[:] = blue
                    scratch *= np.float32((0.45 * 0.114 - 0.25) / 255.0)
                    response += scratch
                    response -= response.min()
                    denominator = response.max()
                    if denominator <= 1e-12:
                        response.fill(0.0)
                    else:
                        response /= denominator
                    return response
                response *= np.float32(0.299 / 255.0)
                scratch *= np.float32(0.587 / 255.0)
                response += scratch
                scratch[:] = blue
                scratch *= np.float32(0.114 / 255.0)
                response += scratch
                return response

    sampled = source[iy, ix]
    if source.dtype != np.uint8:
        sampled = np.asarray(sampled, dtype=np.float32)
        if source.size and source.max() > 1.0:
            sampled /= np.float32(255.0)
        np.clip(sampled, 0.0, 1.0, out=sampled)
    if response_kind == "fire":
        return _fire_response(sampled)
    return _intensity(sampled)


def _component_threshold(response: np.ndarray, threshold: float) -> float:
    value = float(threshold)
    if not np.isfinite(value):
        raise ValueError("component threshold must be finite")
    dtype = np.asarray(response).dtype
    epsilon = np.finfo(dtype).eps if np.issubdtype(dtype, np.floating) else np.finfo(np.float32).eps
    return max(value, float(epsilon))


def _component_centroids_numpy(
    response: np.ndarray,
    threshold: float,
    *,
    max_candidates: int,
    min_area: int,
) -> list[dict[str, Any]]:
    """Return weighted centroids for 8-connected thresholded response regions."""
    if response.ndim != 2:
        raise ValueError("component extraction requires a two-dimensional response")
    threshold = _component_threshold(response, threshold)
    active = np.asarray(response >= threshold, dtype=bool)
    active_count = int(np.count_nonzero(active))
    if active_count == 0:
        return []

    if active_count == active.size:
        area = int(active.size)
        response64 = np.asarray(response, dtype=np.float64)
        weight = float(response64.sum())
        if area < max(1, int(min_area)) or weight <= 1e-12:
            return []
        row_weights = response64.sum(axis=1)
        column_weights = response64.sum(axis=0)
        return [
            {
                "x": float(np.dot(np.arange(response.shape[1]), column_weights)) / weight,
                "y": float(np.dot(np.arange(response.shape[0]), row_weights)) / weight,
                "score": float(np.clip(weight / area, 0.0, 1.0)),
                "area": area,
                "peak_response": float(response64.max(initial=0.0)),
                "bbox": [0, 0, response.shape[1] - 1, response.shape[0] - 1],
            }
        ]

    runs: list[tuple[int, int, int, int]] = []
    parents: list[int] = []
    previous_runs: list[tuple[int, int, int, int]] = []

    def find(label: int) -> int:
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for y, row in enumerate(active):
        padded = np.pad(row.astype(np.int8, copy=False), (1, 1))
        transitions = np.diff(padded)
        starts = np.flatnonzero(transitions == 1)
        ends = np.flatnonzero(transitions == -1) - 1
        current_runs: list[tuple[int, int, int, int]] = []
        previous_index = 0
        for start, end in zip(starts.tolist(), ends.tolist()):
            label = len(parents)
            parents.append(label)
            run = (y, int(start), int(end), label)
            current_runs.append(run)
            runs.append(run)
            while previous_index < len(previous_runs) and previous_runs[previous_index][2] < start - 1:
                previous_index += 1
            overlap_index = previous_index
            while overlap_index < len(previous_runs) and previous_runs[overlap_index][1] <= end + 1:
                union(label, previous_runs[overlap_index][3])
                overlap_index += 1
        previous_runs = current_runs

    if len(runs) > _COMPONENT_NUMPY_VECTOR_MIN_RUNS:
        run_array = np.asarray(runs, dtype=np.int64)
        run_y, run_start, run_end, run_label = run_array.T
        roots = np.fromiter(
            (find(int(label)) for label in run_label),
            dtype=np.int64,
            count=len(run_label),
        )
        run_lengths = run_end - run_start + 1
        pixel_roots = np.repeat(roots, run_lengths)
        pixel_y, pixel_x = np.nonzero(active)
        pixel_values = np.asarray(response[active], dtype=np.float64)
        label_count = len(parents)

        areas = np.bincount(roots, weights=run_lengths, minlength=label_count).astype(np.int64)
        weights = np.bincount(pixel_roots, weights=pixel_values, minlength=label_count)
        x_weights = np.bincount(pixel_roots, weights=pixel_x * pixel_values, minlength=label_count)
        y_weights = np.bincount(pixel_roots, weights=pixel_y * pixel_values, minlength=label_count)
        compact_run_starts = np.concatenate(([0], np.cumsum(run_lengths[:-1])))
        run_peaks = np.maximum.reduceat(pixel_values, compact_run_starts)
        peaks = np.zeros(label_count, dtype=np.float64)
        np.maximum.at(peaks, roots, run_peaks)

        x_min = np.full(label_count, response.shape[1], dtype=np.int64)
        x_max = np.full(label_count, -1, dtype=np.int64)
        y_min = np.full(label_count, response.shape[0], dtype=np.int64)
        y_max = np.full(label_count, -1, dtype=np.int64)
        np.minimum.at(x_min, roots, run_start)
        np.maximum.at(x_max, roots, run_end)
        np.minimum.at(y_min, roots, run_y)
        np.maximum.at(y_max, roots, run_y)

        minimum_area = max(1, int(min_area))
        valid_roots = np.flatnonzero((areas >= minimum_area) & (weights > 1e-12))
        if valid_roots.size == 0:
            return []
        scores = np.clip(weights[valid_roots] / areas[valid_roots], 0.0, 1.0)
        order = np.lexsort(
            (
                x_min[valid_roots],
                y_min[valid_roots],
                -areas[valid_roots],
                -scores,
            )
        )
        candidates: list[dict[str, Any]] = []
        for ranked_index in order[: max(1, int(max_candidates))]:
            root = int(valid_roots[ranked_index])
            weight = float(weights[root])
            candidates.append(
                {
                    "x": float(x_weights[root]) / weight,
                    "y": float(y_weights[root]) / weight,
                    "score": float(scores[ranked_index]),
                    "area": int(areas[root]),
                    "peak_response": float(peaks[root]),
                    "bbox": [
                        int(x_min[root]),
                        int(y_min[root]),
                        int(x_max[root]),
                        int(y_max[root]),
                    ],
                }
            )
        return candidates

    components: dict[int, dict[str, Any]] = {}
    for y, start, end, label in runs:
        root = find(label)
        values = np.asarray(response[y, start : end + 1], dtype=float)
        x_values = np.arange(start, end + 1, dtype=float)
        weight = float(values.sum())
        stats = components.setdefault(
            root,
            {
                "area": 0,
                "weight": 0.0,
                "x_weight": 0.0,
                "y_weight": 0.0,
                "peak_response": 0.0,
                "x_min": start,
                "x_max": end,
                "y_min": y,
                "y_max": y,
            },
        )
        stats["area"] += end - start + 1
        stats["weight"] += weight
        stats["x_weight"] += float(np.dot(x_values, values))
        stats["y_weight"] += float(y * weight)
        stats["peak_response"] = max(float(stats["peak_response"]), float(values.max(initial=0.0)))
        stats["x_min"] = min(int(stats["x_min"]), start)
        stats["x_max"] = max(int(stats["x_max"]), end)
        stats["y_min"] = min(int(stats["y_min"]), y)
        stats["y_max"] = max(int(stats["y_max"]), y)

    candidates: list[dict[str, Any]] = []
    for stats in components.values():
        area = int(stats["area"])
        weight = float(stats["weight"])
        if area < max(1, int(min_area)) or weight <= 1e-12:
            continue
        candidates.append(
            {
                "x": float(stats["x_weight"]) / weight,
                "y": float(stats["y_weight"]) / weight,
                "score": float(np.clip(weight / area, 0.0, 1.0)),
                "area": area,
                "peak_response": float(stats["peak_response"]),
                "bbox": [
                    int(stats["x_min"]),
                    int(stats["y_min"]),
                    int(stats["x_max"]),
                    int(stats["y_max"]),
                ],
            }
        )
    candidates.sort(
        key=lambda item: (
            -float(item["score"]),
            -int(item["area"]),
            int(item["bbox"][1]),
            int(item["bbox"][0]),
        )
    )
    return candidates[: max(1, int(max_candidates))]


def _weighted_component_candidate_cv2(
    response: np.ndarray,
    labels: np.ndarray,
    stats: np.ndarray,
    centroids: np.ndarray,
    label: int,
    *,
    cv2: Any,
) -> dict[str, Any] | None:
    """Measure one labelled component inside its bounding box."""

    area = int(stats[label, cv2.CC_STAT_AREA])
    left = int(stats[label, cv2.CC_STAT_LEFT])
    top = int(stats[label, cv2.CC_STAT_TOP])
    width = int(stats[label, cv2.CC_STAT_WIDTH])
    height = int(stats[label, cv2.CC_STAT_HEIGHT])
    local_labels = labels[top : top + height, left : left + width]
    local_values = response[top : top + height, left : left + width]
    if area == width * height:
        component_values = local_values.reshape(-1)
        uniform_response = bool(np.all(component_values == component_values[0]))
        local_mask = None
    else:
        local_mask = local_labels == label
        component_values = local_values[local_mask]
        uniform_response = bool(np.all(component_values == component_values[0]))
    weight = float(np.sum(component_values, dtype=np.float64))
    if weight <= 1e-12:
        return None
    if uniform_response:
        x = float(centroids[label, 0])
        y = float(centroids[label, 1])
    else:
        if local_mask is None:
            y_indices, x_indices = np.indices((height, width), dtype=np.float64)
            y_indices = y_indices.reshape(-1)
            x_indices = x_indices.reshape(-1)
        else:
            y_indices, x_indices = np.nonzero(local_mask)
        x = left + float(np.dot(x_indices, component_values)) / weight
        y = top + float(np.dot(y_indices, component_values)) / weight
    return {
        "x": x,
        "y": y,
        "score": float(np.clip(weight / area, 0.0, 1.0)),
        "area": area,
        "peak_response": float(component_values.max(initial=0.0)),
        "bbox": [left, top, left + width - 1, top + height - 1],
    }


def _component_centroids_cv2(
    response: np.ndarray,
    threshold: float,
    *,
    max_candidates: int,
    min_area: int,
    cv2: Any,
) -> list[dict[str, Any]]:
    """OpenCV-backed equivalent of :func:`_component_centroids_numpy`."""
    if response.ndim != 2:
        raise ValueError("component extraction requires a two-dimensional response")
    threshold = _component_threshold(response, threshold)
    try:
        active = cv2.compare(np.asarray(response), threshold, cv2.CMP_GE)
        active_count = int(cv2.countNonZero(active))
    except Exception:
        # OpenCV cannot compare a few uncommon NumPy dtypes (for example
        # float16 or int64). Preserve the established generic input path.
        active = np.asarray(response >= threshold, dtype=np.uint8)
        active_count = int(np.count_nonzero(active))
    if active_count == 0:
        return []

    if active_count == active.size:
        area = int(active.size)
        weight = float(np.sum(response, dtype=np.float64))
        if area < max(1, int(min_area)) or weight <= 1e-12:
            return []
        row_weights = np.sum(response, axis=1, dtype=np.float64)
        column_weights = np.sum(response, axis=0, dtype=np.float64)
        return [
            {
                "x": float(np.dot(np.arange(response.shape[1]), column_weights)) / weight,
                "y": float(np.dot(np.arange(response.shape[0]), row_weights)) / weight,
                "score": float(np.clip(weight / area, 0.0, 1.0)),
                "area": area,
                "peak_response": float(np.max(response, initial=0.0)),
                "bbox": [0, 0, response.shape[1] - 1, response.shape[0] - 1],
            }
        ]

    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        active,
        connectivity=8,
        ltype=cv2.CV_32S,
    )
    if component_count <= 1:
        return []

    minimum_area = max(1, int(min_area))
    maximum_candidates = max(1, int(max_candidates))
    component_total = component_count - 1
    bounding_box_pixels = sum(
        int(stats[label, cv2.CC_STAT_WIDTH]) * int(stats[label, cv2.CC_STAT_HEIGHT])
        for label in range(1, component_count)
    )
    local_bbox_limit = int(
        active.size * max(0.0, float(_COMPONENT_CV2_LOCAL_BBOX_RATIO))
    )
    if (
        component_total <= max(0, int(_COMPONENT_CV2_LOCAL_COMPONENT_LIMIT))
        and bounding_box_pixels <= local_bbox_limit
    ):
        # Typical scientific targets occupy a few compact regions. Measuring
        # those boxes avoids a full response/label scan while retaining the
        # same weighted centroid and deterministic ranking semantics.
        local_ranked: list[tuple[float, int, int, int, dict[str, Any]]] = []
        for label in range(1, component_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < minimum_area:
                continue
            candidate = _weighted_component_candidate_cv2(
                response,
                labels,
                stats,
                centroids,
                label,
                cv2=cv2,
            )
            if candidate is None:
                continue
            left = int(stats[label, cv2.CC_STAT_LEFT])
            top = int(stats[label, cv2.CC_STAT_TOP])
            local_ranked.append(
                (float(candidate["score"]), area, top, left, candidate)
            )
        local_ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
        return [item[4] for item in local_ranked[:maximum_candidates]]

    flat_labels = labels.reshape(-1)
    flat_response = response.reshape(-1)
    sparse_limit = max(1, int(active.size * _COMPONENT_CV2_SPARSE_WEIGHT_RATIO))
    if active_count <= sparse_limit:
        active_indices = np.flatnonzero(active)
        weight_labels = flat_labels[active_indices]
        weight_values = flat_response[active_indices]
        candidate_response = response
    else:
        candidate_response = np.asarray(response, dtype=np.float64)
        weight_labels = flat_labels
        weight_values = candidate_response.reshape(-1)
    weights = np.bincount(weight_labels, weights=weight_values, minlength=component_count)
    ranked: list[tuple[float, int, int, int, int]] = []
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        weight = float(weights[label])
        if area < minimum_area or weight <= 1e-12:
            continue
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        ranked.append((float(np.clip(weight / area, 0.0, 1.0)), area, top, left, label))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    candidates: list[dict[str, Any]] = []
    for score, _area, _top, _left, label in ranked[:maximum_candidates]:
        candidate = _weighted_component_candidate_cv2(
            candidate_response,
            labels,
            stats,
            centroids,
            label,
            cv2=cv2,
        )
        if candidate is None:
            continue
        candidate["score"] = score
        candidates.append(candidate)
    return candidates


def _component_centroids(
    response: np.ndarray,
    threshold: float,
    *,
    max_candidates: int,
    min_area: int,
) -> list[dict[str, Any]]:
    """Return weighted centroids for 8-connected thresholded response regions."""
    global _COMPONENT_CV2_RUNTIME_ERROR
    if response.ndim != 2:
        raise ValueError("component extraction requires a two-dimensional response")
    cv2 = _load_component_cv2()
    if cv2 is not None:
        try:
            return _component_centroids_cv2(
                response,
                threshold,
                max_candidates=max_candidates,
                min_area=min_area,
                cv2=cv2,
            )
        except Exception as exc:  # pragma: no cover - backend-specific failure path
            _COMPONENT_CV2_RUNTIME_ERROR = exc
    return _component_centroids_numpy(
        response,
        threshold,
        max_candidates=max_candidates,
        min_area=min_area,
    )


def _candidate_from_point(
    x: float,
    y: float,
    score: float,
    label: str,
    raw: dict[str, Any] | None = None,
) -> ObservationCandidate:
    return ObservationCandidate(
        state={"x_px": float(x), "y_px": float(y)},
        score=float(np.clip(score, 0.0, 1.0)),
        image_point=(float(x), float(y)),
        label=label,
        raw=raw or {},
    )


def _roi_window(
    frame: np.ndarray,
    roi: ROIModel,
) -> tuple[np.ndarray, int, int, int, int] | None:
    """Return the clipped inclusive ROI bounds and cached full-frame mask."""
    height, width = np.asarray(frame).shape[:2]
    x0, y0, x1, y1 = roi.bounds()
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(width - 1, int(x1))
    y1 = min(height - 1, int(y1))
    if width <= 0 or height <= 0 or x0 > x1 or y0 > y1:
        return None
    return roi.mask(frame.shape), x0, y0, x1 + 1, y1 + 1


def _offset_components(
    components: list[dict[str, Any]],
    x_offset: int,
    y_offset: int,
) -> list[dict[str, Any]]:
    """Translate component coordinates from an ROI window to the full frame."""
    if x_offset == 0 and y_offset == 0:
        return components
    translated: list[dict[str, Any]] = []
    for component in components:
        item = dict(component)
        item["x"] = float(item["x"]) + x_offset
        item["y"] = float(item["y"]) + y_offset
        left, top, right, bottom = item["bbox"]
        item["bbox"] = [
            int(left) + x_offset,
            int(top) + y_offset,
            int(right) + x_offset,
            int(bottom) + y_offset,
        ]
        translated.append(item)
    return translated


def _full_response_from_window(
    local_response: np.ndarray,
    frame_shape: tuple[int, ...],
    x0: int,
    y0: int,
) -> np.ndarray:
    response = np.zeros(frame_shape[:2], dtype=local_response.dtype)
    height, width = local_response.shape
    response[y0 : y0 + height, x0 : x0 + width] = local_response
    return response


def _stored_response_from_window(
    local_response: np.ndarray,
    frame_shape: tuple[int, ...],
    x0: int,
    y0: int,
    context: FrameContext,
) -> tuple[np.ndarray, tuple[int, int] | None, tuple[int, int] | None]:
    """Keep an ROI-local response for tracking or materialize the public map.

    Direct observation calls retain the established full-frame response
    contract.  Background tracking can keep the exact local values plus their
    placement metadata, avoiding an otherwise unused full-frame zero plane on
    every frame.  Review materializes that plane only when the user asks for
    the response overlay.
    """

    if context.compact_response_map:
        height, width = frame_shape[:2]
        return local_response, (int(x0), int(y0)), (int(height), int(width))
    return _full_response_from_window(local_response, frame_shape, x0, y0), None, None


@dataclass
class ColorBlobObservation(ObservationModel):
    sample_rgb: tuple[float, float, float]
    tolerance: float = 0.18
    min_response: float = 0.35
    max_candidates: int = 4
    min_component_area: int = 1
    name: str = "color_blob"

    def __post_init__(self) -> None:
        self.max_candidates = int(self.max_candidates)
        self.min_component_area = int(self.min_component_area)
        if not 1 <= self.max_candidates <= MAX_COMPONENT_CANDIDATES:
            raise ValueError(
                f"color blob max_candidates must be between 1 and {MAX_COMPONENT_CANDIDATES}"
            )
        if not 1 <= self.min_component_area <= MAX_COMPONENT_AREA_THRESHOLD:
            raise ValueError(
                "color blob min_component_area must be between 1 and "
                f"{MAX_COMPONENT_AREA_THRESHOLD:,}"
            )

    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        source = np.asarray(frame)
        if source.ndim != 3 or source.shape[2] < 3:
            raise ValueError("ColorBlobObservation requires an RGB frame")
        window = _roi_window(source, roi)
        if window is None:
            response, origin, frame_shape = _stored_response_from_window(
                np.zeros((0, 0), dtype=np.float32),
                source.shape,
                0,
                0,
                context,
            )
            return ObservationResult(
                candidates=[],
                response_map=response,
                response_origin=origin,
                response_frame_shape=frame_shape,
            )
        roi_mask, x0, y0, x1, y1 = window
        full_window = x0 == 0 and y0 == 0 and x1 == source.shape[1] and y1 == source.shape[0]
        response_x0 = x0
        response_y0 = y0
        stores_local_response = not full_window
        if full_window:
            if context.compact_response_map and source.dtype == np.uint8:
                sample = np.asarray(self.sample_rgb, dtype=np.float32)
                if sample.max() > 1.0:
                    sample = sample / np.float32(255.0)
                normalized_tolerance = (
                    self.tolerance / 255.0
                    if self.tolerance > 1.0
                    else self.tolerance
                )
                sample_u8_scale = sample[:3] * np.float32(255.0)
                scale = np.float32(
                    1.0
                    / (
                        255.0
                        * np.sqrt(3.0)
                        * max(normalized_tolerance, 1e-12)
                    )
                )
                component_response, response_x0, response_y0 = (
                    _uint8_color_distance_response_window(
                        source,
                        sample_u8_scale,
                        scale,
                    )
                )
                response_height, response_width = component_response.shape
                if response_width > 0 and response_height > 0:
                    np.multiply(
                        component_response,
                        roi_mask[
                            response_y0 : response_y0 + response_height,
                            response_x0 : response_x0 + response_width,
                        ],
                        out=component_response,
                    )
                stores_local_response = bool(
                    response_x0 != 0
                    or response_y0 != 0
                    or component_response.shape != source.shape[:2]
                )
            else:
                component_response = _color_distance_response(
                    source,
                    self.sample_rgb,
                    self.tolerance,
                )
                np.multiply(component_response, roi_mask, out=component_response)
        else:
            local_mask = roi_mask[y0:y1, x0:x1]
            if not np.any(local_mask):
                response, origin, frame_shape = _stored_response_from_window(
                    np.zeros((0, 0), dtype=np.float32),
                    source.shape,
                    0,
                    0,
                    context,
                )
                return ObservationResult(
                    candidates=[],
                    response_map=response,
                    response_origin=origin,
                    response_frame_shape=frame_shape,
                )
            component_response = _color_distance_response(
                source[y0:y1, x0:x1],
                self.sample_rgb,
                self.tolerance,
            )
            np.multiply(component_response, local_mask, out=component_response)
        components = (
            _component_centroids(
                component_response,
                self.min_response,
                max_candidates=self.max_candidates,
                min_area=self.min_component_area,
            )
            if component_response.size
            else []
        )
        response_origin = None
        response_frame_shape = None
        if stores_local_response:
            components = _offset_components(components, response_x0, response_y0)
            response, response_origin, response_frame_shape = _stored_response_from_window(
                component_response,
                source.shape,
                response_x0,
                response_y0,
                context,
            )
        else:
            response = component_response
        candidates = [
            _candidate_from_point(
                component["x"],
                component["y"],
                component["score"],
                self.name,
                raw={
                    "component_rank": rank,
                    "component_area": component["area"],
                    "peak_response": component["peak_response"],
                    "bbox": component["bbox"],
                },
            )
            for rank, component in enumerate(components, start=1)
        ]
        return ObservationResult(
            candidates=candidates,
            response_map=response,
            response_origin=response_origin,
            response_frame_shape=response_frame_shape,
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "sample_rgb": list(self.sample_rgb),
            "tolerance": self.tolerance,
            "min_response": self.min_response,
            "max_candidates": max(1, int(self.max_candidates)),
            "min_component_area": max(1, int(self.min_component_area)),
        }


@dataclass
class BrightnessPeakObservation(ObservationModel):
    polarity: str = "bright"
    min_response: float = 0.25
    percentile_floor: float = 80.0
    max_candidates: int = 4
    min_component_area: int = 1
    name: str = "brightness_peak"

    def __post_init__(self) -> None:
        self.max_candidates = int(self.max_candidates)
        self.min_component_area = int(self.min_component_area)
        if not 1 <= self.max_candidates <= MAX_COMPONENT_CANDIDATES:
            raise ValueError(
                f"brightness peak max_candidates must be between 1 and {MAX_COMPONENT_CANDIDATES}"
            )
        if not 1 <= self.min_component_area <= MAX_COMPONENT_AREA_THRESHOLD:
            raise ValueError(
                "brightness peak min_component_area must be between 1 and "
                f"{MAX_COMPONENT_AREA_THRESHOLD:,}"
            )

    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        source = np.asarray(frame)
        window = _roi_window(source, roi)
        if window is None:
            response, origin, frame_shape = _stored_response_from_window(
                np.zeros((0, 0), dtype=np.float32),
                source.shape,
                0,
                0,
                context,
            )
            return ObservationResult(
                candidates=[],
                response_map=response,
                response_origin=origin,
                response_frame_shape=frame_shape,
            )
        roi_mask, x0, y0, x1, y1 = window
        full_window = x0 == 0 and y0 == 0 and x1 == source.shape[1] and y1 == source.shape[0]
        local_mask = roi_mask if full_window else roi_mask[y0:y1, x0:x1]
        if not np.any(local_mask):
            response, origin, frame_shape = _stored_response_from_window(
                np.zeros((0, 0), dtype=np.float32),
                source.shape,
                0,
                0,
                context,
            )
            return ObservationResult(
                candidates=[],
                response_map=response,
                response_origin=origin,
                response_frame_shape=frame_shape,
            )
        response_source = source if full_window else source[y0:y1, x0:x1]
        component_response = _intensity(response_source)
        if self.polarity == "dark":
            np.subtract(np.float32(1.0), component_response, out=component_response)
        floor = float(np.percentile(component_response[local_mask], self.percentile_floor))
        np.subtract(component_response, floor, out=component_response)
        np.divide(component_response, max(1.0 - floor, 1e-12), out=component_response)
        np.clip(component_response, 0.0, 1.0, out=component_response)
        np.multiply(component_response, local_mask, out=component_response)
        components = _component_centroids(
            component_response,
            self.min_response,
            max_candidates=self.max_candidates,
            min_area=self.min_component_area,
        )
        response_origin = None
        response_frame_shape = None
        if full_window:
            response = component_response
        else:
            components = _offset_components(components, x0, y0)
            response, response_origin, response_frame_shape = _stored_response_from_window(
                component_response,
                source.shape,
                x0,
                y0,
                context,
            )
        candidates = [
            _candidate_from_point(
                component["x"],
                component["y"],
                component["score"],
                self.name,
                raw={
                    "component_rank": rank,
                    "component_area": component["area"],
                    "peak_response": component["peak_response"],
                    "bbox": component["bbox"],
                },
            )
            for rank, component in enumerate(components, start=1)
        ]
        return ObservationResult(
            candidates=candidates,
            response_map=response,
            response_origin=response_origin,
            response_frame_shape=response_frame_shape,
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "polarity": self.polarity,
            "min_response": self.min_response,
            "percentile_floor": self.percentile_floor,
            "max_candidates": max(1, int(self.max_candidates)),
            "min_component_area": max(1, int(self.min_component_area)),
        }


@dataclass
class EdgeFrontObservation(ObservationModel):
    axis: str = "x"
    min_response: float = 0.2
    name: str = "edge_front"

    def __post_init__(self) -> None:
        self.axis = str(self.axis).strip().lower()
        if self.axis not in {"x", "y"}:
            raise ValueError("edge front axis must be 'x' or 'y'")

    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        source = np.asarray(frame)
        gradient_axis = 1 if self.axis == "x" else 0
        if source.ndim < 2 or source.shape[gradient_axis] < 2:
            raise ValueError("edge front gradient axis must contain at least two pixels")

        window = _roi_window(source, roi)
        if window is None:
            response, origin, frame_shape = _stored_response_from_window(
                np.zeros((0, 0), dtype=np.float32),
                source.shape,
                0,
                0,
                context,
            )
            return ObservationResult(
                candidates=[],
                response_map=response,
                response_origin=origin,
                response_frame_shape=frame_shape,
            )

        roi_mask, x0, y0, x1, y1 = window
        # Preserve NumPy's central-difference values at an interior ROI edge
        # with a one-pixel halo along the selected gradient axis.  The
        # orthogonal axis has no dependency outside the ROI bounds.
        source_x0 = max(0, x0 - 1) if gradient_axis == 1 else x0
        source_x1 = min(source.shape[1], x1 + 1) if gradient_axis == 1 else x1
        source_y0 = max(0, y0 - 1) if gradient_axis == 0 else y0
        source_y1 = min(source.shape[0], y1 + 1) if gradient_axis == 0 else y1
        image = _intensity(source[source_y0:source_y1, source_x0:source_x1])
        expanded_response = _absolute_axis_gradient(image, gradient_axis)
        response_slice = expanded_response[
            y0 - source_y0 : y1 - source_y0,
            x0 - source_x0 : x1 - source_x0,
        ]
        if (
            response_slice.shape == expanded_response.shape
            and response_slice.strides == expanded_response.strides
        ):
            local_response = expanded_response
        else:
            # Do not retain the one-pixel halo through a view base when this
            # local response enters the bounded tracking history.
            local_response = np.array(response_slice, copy=True)
        np.multiply(local_response, roi_mask[y0:y1, x0:x1], out=local_response)
        # Normalize against evidence inside the applied ROI.  A stronger edge
        # elsewhere in the frame must not suppress the selected experiment
        # region or change its confidence.
        max_value = float(local_response.max(initial=0.0))
        if max_value > 1e-12:
            local_response *= np.float32(1.0 / max_value)
        flat_index = int(np.argmax(local_response))
        local_y, local_x = np.unravel_index(flat_index, local_response.shape)
        y = y0 + local_y
        x = x0 + local_x
        score = float(local_response[local_y, local_x])
        candidates = []
        if score >= self.min_response:
            if self.axis == "x":
                weights = local_response[:, local_x]
                orthogonal_positions = np.arange(y0, y1, dtype=np.float64)
                total_weight = float(np.sum(weights, dtype=np.float64))
                if total_weight > 1e-12:
                    y = float(np.dot(weights, orthogonal_positions)) / total_weight
            else:
                weights = local_response[local_y, :]
                orthogonal_positions = np.arange(x0, x1, dtype=np.float64)
                total_weight = float(np.sum(weights, dtype=np.float64))
                if total_weight > 1e-12:
                    x = float(np.dot(weights, orthogonal_positions)) / total_weight
            candidates.append(_candidate_from_point(float(x), float(y), score, self.name))

        full_window = x0 == 0 and y0 == 0 and x1 == source.shape[1] and y1 == source.shape[0]
        response_origin = None
        response_frame_shape = None
        if full_window:
            response = local_response
        else:
            response, response_origin, response_frame_shape = _stored_response_from_window(
                local_response,
                source.shape,
                x0,
                y0,
                context,
            )
        return ObservationResult(
            candidates=candidates,
            response_map=response,
            response_origin=response_origin,
            response_frame_shape=response_frame_shape,
        )

    def to_config(self) -> dict[str, Any]:
        return {"type": self.name, "axis": self.axis, "min_response": self.min_response}


_TEMPLATE_NUMPY_WORKSPACE_BYTES = 64 * 1024 * 1024
_TEMPLATE_NUMPY_DIRECT_OPERATIONS = 50_000


def _next_fast_len(size: int) -> int:
    """Return a nearby FFT length composed only of small prime factors."""
    candidate = max(1, int(size))
    while True:
        remainder = candidate
        for factor in (2, 3, 5, 7):
            while remainder % factor == 0:
                remainder //= factor
        if remainder == 1:
            return candidate
        candidate += 1


def _valid_window_sum(
    source: np.ndarray,
    window_height: int,
    window_width: int,
    *,
    square: bool = False,
) -> np.ndarray:
    """Compute all valid window sums with one bounded integral-image buffer."""
    integral = np.zeros((source.shape[0] + 1, source.shape[1] + 1), dtype=np.float64)
    if square:
        np.multiply(source, source, out=integral[1:, 1:], casting="unsafe")
    else:
        integral[1:, 1:] = source
    np.cumsum(integral, axis=0, out=integral)
    np.cumsum(integral, axis=1, out=integral)
    return (
        integral[window_height:, window_width:]
        - integral[:-window_height, window_width:]
        - integral[window_height:, :-window_width]
        + integral[:-window_height, :-window_width]
    )


def _template_fft_chunk_shape(
    region_shape: tuple[int, int],
    template_shape: tuple[int, int],
    output_height: int,
    output_width: int,
    workspace_bytes: int,
) -> tuple[int, tuple[int, int]]:
    """Choose the largest output-row chunk fitting the FFT soft workspace budget."""
    _, region_width = region_shape
    template_height, template_width = template_shape
    fft_width = _next_fast_len(region_width + template_width - 1)

    def estimate(rows: int) -> tuple[int, int]:
        fft_height = _next_fast_len(rows + 2 * template_height - 2)
        spectrum_bytes = fft_height * (fft_width // 2 + 1) * np.dtype(np.complex128).itemsize
        fft_peak = 2 * spectrum_bytes + fft_height * fft_width * np.dtype(np.float64).itemsize
        integral_bytes = (rows + template_height) * (region_width + 1) * np.dtype(np.float64).itemsize
        statistic_bytes = (
            spectrum_bytes
            + integral_bytes
            + 5 * rows * output_width * np.dtype(np.float64).itemsize
        )
        return max(fft_peak, statistic_bytes), fft_height

    budget = max(1, int(workspace_bytes))
    low, high = 1, max(1, int(output_height))
    while low < high:
        middle = (low + high + 1) // 2
        if estimate(middle)[0] <= budget:
            low = middle
        else:
            high = middle - 1
    rows = low
    return rows, (estimate(rows)[1], fft_width)


def _mapped_ncc_scores(
    numerator: np.ndarray,
    patch_variance: np.ndarray,
    template_energy: float,
) -> np.ndarray:
    """Map zero-mean NCC to [0, 1], treating zero-variance patches as neutral."""
    np.maximum(patch_variance, 0.0, out=patch_variance)
    np.sqrt(patch_variance, out=patch_variance)
    patch_variance *= float(template_energy)
    valid = patch_variance > 1e-12
    np.divide(numerator, patch_variance, out=numerator, where=valid)
    numerator[~valid] = 0.0
    numerator += 1.0
    numerator *= 0.5
    np.clip(numerator, 0.0, 1.0, out=numerator)
    return numerator.astype(np.float32, copy=False)


def _template_scores_numpy_direct(
    region: np.ndarray,
    template_norm: np.ndarray,
    template_energy: float,
    *,
    workspace_bytes: int,
) -> np.ndarray:
    """Compute exact sliding-window NCC directly for small search problems."""
    th, tw = template_norm.shape
    output_height = region.shape[0] - th + 1
    output_width = region.shape[1] - tw + 1
    if output_height <= 0 or output_width <= 0:
        return np.zeros((0, 0), dtype=np.float32)
    template_pixels = th * tw
    logical_row_bytes = max(1, output_width * template_pixels * region.dtype.itemsize)
    rows_per_chunk = max(1, min(output_height, int(workspace_bytes) // logical_row_bytes))
    scores = np.empty((output_height, output_width), dtype=np.float32)
    for start in range(0, output_height, rows_per_chunk):
        end = min(output_height, start + rows_per_chunk)
        source = region[start : end + th - 1]
        windows = np.lib.stride_tricks.sliding_window_view(source, (th, tw))
        patch_sum = windows.sum(axis=(-2, -1), dtype=np.float64)
        patch_square_sum = np.einsum(
            "ijmn,ijmn->ij",
            windows,
            windows,
            dtype=np.float64,
            optimize=True,
        )
        patch_variance = np.maximum(
            patch_square_sum - (patch_sum**2) / template_pixels,
            0.0,
        )
        numerator = np.einsum(
            "ijmn,mn->ij",
            windows,
            template_norm,
            dtype=np.float64,
            optimize=True,
        )
        scores[start:end] = _mapped_ncc_scores(numerator, patch_variance, template_energy)
    return scores


def _template_scores_numpy_fft(
    region: np.ndarray,
    template_norm: np.ndarray,
    template_energy: float,
    *,
    workspace_bytes: int,
    kernel_spectrum: np.ndarray | None = None,
) -> np.ndarray:
    """Compute bounded zero-mean NCC using block FFT correlation and integral sums."""
    th, tw = template_norm.shape
    output_height = region.shape[0] - th + 1
    output_width = region.shape[1] - tw + 1
    if output_height <= 0 or output_width <= 0:
        return np.zeros((0, 0), dtype=np.float32)
    if template_energy <= 1e-12:
        return np.full((output_height, output_width), 0.5, dtype=np.float32)

    rows_per_chunk, fft_shape = _template_fft_chunk_shape(
        region.shape,
        template_norm.shape,
        output_height,
        output_width,
        workspace_bytes,
    )
    expected_spectrum_shape = (fft_shape[0], fft_shape[1] // 2 + 1)
    if kernel_spectrum is None:
        kernel_spectrum = np.fft.rfftn(
            template_norm[::-1, ::-1],
            s=fft_shape,
            axes=(0, 1),
        )
    elif kernel_spectrum.shape != expected_spectrum_shape:
        raise ValueError(
            f"template FFT spectrum shape {kernel_spectrum.shape} does not match {expected_spectrum_shape}"
        )
    scores = np.empty((output_height, output_width), dtype=np.float32)
    template_pixels = th * tw
    for start in range(0, output_height, rows_per_chunk):
        chunk_rows = min(rows_per_chunk, output_height - start)
        source = region[start : start + chunk_rows + th - 1]
        source_spectrum = np.fft.rfftn(source, s=fft_shape, axes=(0, 1))
        source_spectrum *= kernel_spectrum
        convolution = np.fft.irfftn(source_spectrum, s=fft_shape, axes=(0, 1))
        numerator = np.array(
            convolution[
                th - 1 : th - 1 + chunk_rows,
                tw - 1 : tw - 1 + output_width,
            ],
            copy=True,
        )
        del source_spectrum, convolution

        patch_sum = _valid_window_sum(source, th, tw)
        patch_variance = _valid_window_sum(source, th, tw, square=True)
        np.square(patch_sum, out=patch_sum)
        patch_sum /= template_pixels
        patch_variance -= patch_sum
        scores[start : start + chunk_rows] = _mapped_ncc_scores(
            numerator,
            patch_variance,
            template_energy,
        )
    return scores


def _template_scores_numpy(
    region: np.ndarray,
    template_norm: np.ndarray,
    template_energy: float,
    *,
    workspace_bytes: int = _TEMPLATE_NUMPY_WORKSPACE_BYTES,
) -> np.ndarray:
    """Compute mapped zero-mean NCC with a small-direct/bounded-FFT dispatcher."""
    th, tw = template_norm.shape
    output_height = region.shape[0] - th + 1
    output_width = region.shape[1] - tw + 1
    if output_height <= 0 or output_width <= 0:
        return np.zeros((0, 0), dtype=np.float32)
    operations = output_height * output_width * th * tw
    if operations <= _TEMPLATE_NUMPY_DIRECT_OPERATIONS or int(workspace_bytes) < 1024 * 1024:
        return _template_scores_numpy_direct(
            region,
            template_norm,
            template_energy,
            workspace_bytes=workspace_bytes,
        )
    return _template_scores_numpy_fft(
        region,
        template_norm,
        template_energy,
        workspace_bytes=workspace_bytes,
    )


def _template_scores_cv2(
    region: np.ndarray,
    template: np.ndarray,
    *,
    cv2: Any,
) -> np.ndarray:
    """Compute scores equivalent to the NumPy NCC mapping with OpenCV."""
    raw_scores = cv2.matchTemplate(
        np.ascontiguousarray(region, dtype=np.float32),
        np.ascontiguousarray(template, dtype=np.float32),
        cv2.TM_CCOEFF_NORMED,
    )
    raw_scores = np.nan_to_num(raw_scores, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.clip((raw_scores + 1.0) * 0.5, 0.0, 1.0).astype(np.float32, copy=False)


@dataclass
class TemplateObservation(ObservationModel):
    template: np.ndarray
    min_score: float = 0.65
    name: str = "template"
    _template_cache_source: np.ndarray | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _template_cache: tuple[np.ndarray, np.ndarray, float] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _template_cache_generation: int = field(
        default=0,
        init=False,
        repr=False,
        compare=False,
    )
    _template_fft_cache_key: tuple[int, int, int] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _template_fft_cache: np.ndarray | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        template = np.asarray(self.template)
        if template.ndim not in {2, 3} or any(size < 1 for size in template.shape):
            raise ValueError("template must be a non-empty 2D image or 3D color image")
        if any(int(size) > MAX_TEMPLATE_DIMENSION for size in template.shape[:2]):
            raise ValueError(
                f"template dimensions must not exceed {MAX_TEMPLATE_DIMENSION} pixels"
            )
        if int(template.size) > MAX_TEMPLATE_ELEMENTS:
            raise ValueError(
                f"template must not exceed {MAX_TEMPLATE_ELEMENTS:,} numeric elements"
            )

    def _cached_template_preprocessing(self) -> tuple[np.ndarray, np.ndarray, float]:
        """Reuse immutable template statistics while detecting public-array edits.

        ``template`` remains a public configuration field, so callers can
        replace it or edit it in place.  A read-only content snapshot keeps
        those mutations observable without hashing or copying the template on
        every frame.  The cached intensity and zero-mean planes are never
        exposed as writable arrays.
        """
        source = np.asarray(self.template)
        cached_source = self._template_cache_source
        cache_matches = bool(
            cached_source is not None
            and cached_source.shape == source.shape
            and cached_source.dtype == source.dtype
            and np.array_equal(cached_source, source, equal_nan=True)
        )
        if cache_matches and self._template_cache is not None:
            return self._template_cache

        template = np.ascontiguousarray(_intensity(source), dtype=np.float32)
        template_norm = np.ascontiguousarray(template - template.mean(), dtype=np.float32)
        template_energy = float(
            np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
        )
        source_snapshot = np.array(source, copy=True, order="K")
        for values in (source_snapshot, template, template_norm):
            values.setflags(write=False)

        self._template_cache_source = source_snapshot
        self._template_cache = (template, template_norm, template_energy)
        self._template_cache_generation += 1
        self._template_fft_cache_key = None
        self._template_fft_cache = None
        return self._template_cache

    def _cached_template_fft_spectrum(
        self,
        template_norm: np.ndarray,
        fft_shape: tuple[int, int],
    ) -> np.ndarray:
        cache_key = (
            int(self._template_cache_generation),
            int(fft_shape[0]),
            int(fft_shape[1]),
        )
        if cache_key == self._template_fft_cache_key and self._template_fft_cache is not None:
            return self._template_fft_cache
        spectrum = np.fft.rfftn(
            template_norm[::-1, ::-1],
            s=fft_shape,
            axes=(0, 1),
        )
        spectrum.setflags(write=False)
        self._template_fft_cache_key = cache_key
        self._template_fft_cache = spectrum
        return spectrum

    def _template_scores_numpy_cached(
        self,
        region: np.ndarray,
        template_norm: np.ndarray,
        template_energy: float,
        *,
        workspace_bytes: int = _TEMPLATE_NUMPY_WORKSPACE_BYTES,
    ) -> np.ndarray:
        """Dispatch NumPy NCC while reusing the static block-FFT kernel."""
        th, tw = template_norm.shape
        output_height = region.shape[0] - th + 1
        output_width = region.shape[1] - tw + 1
        if output_height <= 0 or output_width <= 0:
            return np.zeros((0, 0), dtype=np.float32)
        operations = output_height * output_width * th * tw
        if operations <= _TEMPLATE_NUMPY_DIRECT_OPERATIONS or int(workspace_bytes) < 1024 * 1024:
            return _template_scores_numpy_direct(
                region,
                template_norm,
                template_energy,
                workspace_bytes=workspace_bytes,
            )
        _rows_per_chunk, fft_shape = _template_fft_chunk_shape(
            region.shape,
            template_norm.shape,
            output_height,
            output_width,
            workspace_bytes,
        )
        kernel_spectrum = self._cached_template_fft_spectrum(template_norm, fft_shape)
        return _template_scores_numpy_fft(
            region,
            template_norm,
            template_energy,
            workspace_bytes=workspace_bytes,
            kernel_spectrum=kernel_spectrum,
        )

    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        global _TEMPLATE_CV2_RUNTIME_ERROR
        source = np.asarray(frame)
        template, template_norm, template_energy = self._cached_template_preprocessing()
        th, tw = template.shape
        h, w = source.shape[:2]
        if th > h or tw > w:
            return ObservationResult(candidates=[])
        roi_mask = roi.mask(frame.shape)
        x0, y0, x1, y1 = roi.bounds()
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(w - tw, x1)
        y1 = min(h - th, y1)
        if x0 > x1 or y0 > y1:
            response, origin, frame_shape = _stored_response_from_window(
                np.zeros((0, 0), dtype=np.float32),
                source.shape,
                0,
                0,
                context,
            )
            return ObservationResult(
                candidates=[],
                response_map=response,
                response_origin=origin,
                response_frame_shape=frame_shape,
            )

        if source.dtype == np.uint8:
            # Decoded video uses uint8, whose intensity conversion is local and
            # independent per pixel.  Convert only the search window plus the
            # template halo instead of scanning the complete source frame.
            region = _intensity(source[y0 : y1 + th, x0 : x1 + tw])
        else:
            # Preserve the existing full-frame max-based normalization for
            # plugin/custom dtypes whose numeric range is not known in advance.
            image = _intensity(source)
            region = image[y0 : y1 + th, x0 : x1 + tw]
        output_shape = (y1 - y0 + 1, x1 - x0 + 1)
        if template_energy <= 1e-12:
            scores = np.full(output_shape, 0.5, dtype=np.float32)
        else:
            cv2 = _load_template_cv2()
            if cv2 is not None:
                try:
                    scores = _template_scores_cv2(region, template, cv2=cv2)
                except Exception as exc:  # pragma: no cover - backend-specific failure path
                    _TEMPLATE_CV2_RUNTIME_ERROR = exc
                    scores = self._template_scores_numpy_cached(region, template_norm, template_energy)
            else:
                scores = self._template_scores_numpy_cached(region, template_norm, template_energy)

        center_y = np.arange(y0, y1 + 1, dtype=np.intp) + th // 2
        center_x = np.arange(x0, x1 + 1, dtype=np.intp) + tw // 2
        valid = roi_mask[np.ix_(center_y, center_x)]
        np.multiply(scores, valid, out=scores)
        response_origin = None
        response_frame_shape = None
        if context.compact_response_map:
            response = scores
            response_origin = (int(center_x[0]), int(center_y[0]))
            response_frame_shape = (int(h), int(w))
            score = float(scores.max(initial=0.0))
            if score > 0.0:
                local_y, local_x = np.unravel_index(int(np.argmax(scores)), scores.shape)
                y = int(center_y[local_y])
                x = int(center_x[local_x])
            else:
                # A full zero response has its first maximum at image origin.
                y = 0
                x = 0
        else:
            response = np.zeros((h, w), dtype=np.float32)
            response[np.ix_(center_y, center_x)] = scores
            y, x = np.unravel_index(int(np.argmax(response)), response.shape)
            score = float(response[y, x])
        candidates = []
        if score >= self.min_score:
            candidates.append(_candidate_from_point(float(x), float(y), score, self.name))
        return ObservationResult(
            candidates=candidates,
            response_map=response,
            response_origin=response_origin,
            response_frame_shape=response_frame_shape,
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "template_shape": list(self.template.shape),
            "template": np.asarray(self.template).tolist(),
            "min_score": self.min_score,
        }


@dataclass(frozen=True)
class ObservationBackendInfo:
    label: str
    state: str
    detail: str


def observation_backend_info(observation: ObservationModel) -> ObservationBackendInfo:
    """Describe the active observation compute path for UI and diagnostics."""
    if isinstance(observation, (ColorBlobObservation, BrightnessPeakObservation)):
        if _load_component_cv2() is not None:
            return ObservationBackendInfo(
                "OpenCV components",
                "accelerated",
                "Connected components use the OpenCV acceleration path.",
            )
        detail = "Connected components use the portable NumPy fallback; dense noisy masks can be slower."
        if _COMPONENT_CV2_RUNTIME_ERROR is not None:
            detail += f" OpenCV was disabled after a runtime error: {_COMPONENT_CV2_RUNTIME_ERROR}"
        else:
            detail += " Install the media extra to enable OpenCV acceleration."
        return ObservationBackendInfo("NumPy components", "fallback", detail)

    if isinstance(observation, TemplateObservation):
        if _load_template_cv2() is not None:
            return ObservationBackendInfo(
                "OpenCV cached NCC",
                "accelerated",
                "Template intensity and zero-mean statistics are reused until the template changes; "
                "matching uses OpenCV TM_CCOEFF_NORMED.",
            )
        detail = (
            "Template intensity and zero-mean statistics are reused until the template changes. "
            "Small searches use direct NumPy NCC; larger searches use bounded block FFT NCC with a cached kernel."
        )
        if _TEMPLATE_CV2_RUNTIME_ERROR is not None:
            detail += f" OpenCV was disabled after a runtime error: {_TEMPLATE_CV2_RUNTIME_ERROR}"
        else:
            detail += " Install the media extra to enable the faster OpenCV path."
        return ObservationBackendInfo("NumPy cached NCC", "optimized", detail)

    if isinstance(observation, EdgeFrontObservation):
        axis_label = "horizontal" if observation.axis == "x" else "vertical"
        return ObservationBackendInfo(
            "NumPy ROI gradient",
            "optimized",
            f"The {axis_label} single-axis gradient and response normalization are limited to the applied ROI.",
        )

    if isinstance(observation, AnnularRadialFrontObservation):
        sample_count = max(0, int(observation.n_angles)) * max(0, int(observation.n_radii))
        if observation.response_kind == "fire":
            return ObservationBackendInfo(
                "NumPy cached linear fire",
                "optimized",
                f"Cached linear indices gather RGB channels at {sample_count:,} annular sample points before "
                "fire response and angular normalization. Indices are reused while frame size, ROI, and "
                "mapping are unchanged.",
            )
        return ObservationBackendInfo(
            "NumPy cached linear intensity",
            "optimized",
            f"Cached linear indices gather RGB channels at {sample_count:,} annular sample points before "
            "intensity response and angular normalization. Indices are reused while frame size, ROI, and "
            "mapping are unchanged.",
        )

    return ObservationBackendInfo(
        "NumPy vectorized",
        "native",
        "This observation model uses its native vectorized NumPy implementation.",
    )


@dataclass
class AnnularRadialFrontObservation(ObservationModel):
    n_angles: int = 720
    n_radii: int = 24
    response_kind: str = "fire"
    min_response: float = 0.15
    smoothing: int = 3
    name: str = "annular_radial_front"
    _sample_grid_cache_key: tuple[Any, ...] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _sample_grid_cache: tuple[np.ndarray, np.ndarray, np.ndarray] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _sample_linear_indices_cache: np.ndarray | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        for value, label in (
            (self.n_angles, "n_angles"),
            (self.n_radii, "n_radii"),
            (self.smoothing, "smoothing"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise ValueError(f"annular {label} must be an integer")
        self.n_angles = int(self.n_angles)
        self.n_radii = int(self.n_radii)
        self.smoothing = int(self.smoothing)
        if not 1 <= self.n_angles <= MAX_ANNULAR_ANGLES:
            raise ValueError(
                f"annular n_angles must be between 1 and {MAX_ANNULAR_ANGLES:,}"
            )
        if not 1 <= self.n_radii <= MAX_ANNULAR_RADII:
            raise ValueError(
                f"annular n_radii must be between 1 and {MAX_ANNULAR_RADII:,}"
            )
        if self.n_angles * self.n_radii > MAX_ANNULAR_SAMPLE_POINTS:
            raise ValueError(
                "annular sample grid must not exceed "
                f"{MAX_ANNULAR_SAMPLE_POINTS:,} points"
            )
        smoothing_limit = min(MAX_ANNULAR_SMOOTHING, self.n_angles // 2)
        if not 0 <= self.smoothing <= smoothing_limit:
            raise ValueError(
                f"annular smoothing must be between 0 and {smoothing_limit:,}"
            )

    def observe(
        self,
        frame: np.ndarray,
        roi: ROIModel,
        coordinate_model: CoordinateModel,
        context: FrameContext,
    ) -> ObservationResult:
        if not isinstance(roi, AnnularROI):
            raise ValueError("AnnularRadialFrontObservation requires AnnularROI")
        source = np.asarray(frame)
        if source.ndim < 2 or source.shape[0] < 1 or source.shape[1] < 1:
            raise ValueError("AnnularRadialFrontObservation requires a non-empty image frame")
        h, w = source.shape[:2]
        if isinstance(coordinate_model, PolarCoordinate):
            angles, iy, ix = self._cached_polar_sample_grid(roi, coordinate_model, (h, w))
            linear_indices = self._sample_linear_indices_cache
        else:
            self._sample_grid_cache_key = None
            self._sample_grid_cache = None
            self._sample_linear_indices_cache = None
            angles = np.linspace(0.0, 2.0 * np.pi, self.n_angles, endpoint=False)
            radii = np.linspace(roi.inner_radius, roi.outer_radius, self.n_radii)
            ix = np.empty((self.n_radii, self.n_angles), dtype=np.intp)
            iy = np.empty((self.n_radii, self.n_angles), dtype=np.intp)
            for angle_index, theta in enumerate(angles):
                for radius_index, radius in enumerate(radii):
                    x, y = coordinate_model.state_to_image_space({"theta": float(theta), "r": float(radius)})
                    ix[radius_index, angle_index] = int(np.clip(round(x), 0, w - 1))
                    iy[radius_index, angle_index] = int(np.clip(round(y), 0, h - 1))
            linear_indices = iy * np.intp(w) + ix
        samples = _annular_sample_response(
            source,
            iy,
            ix,
            self.response_kind,
            linear_indices=linear_indices,
        )
        signal = np.mean(samples, axis=0)
        if self.smoothing > 0:
            signal = self._smooth_circular(signal, self.smoothing)
        normalized = signal - signal.min()
        denom = float(normalized.max())
        if denom > 1e-12:
            normalized = normalized / denom
        peak_index = int(np.argmax(normalized))
        score = float(normalized[peak_index])
        candidates = []
        if score >= self.min_response:
            theta = float(angles[peak_index])
            radius = float(0.5 * (roi.inner_radius + roi.outer_radius))
            x, y = coordinate_model.state_to_image_space({"theta": theta, "r": radius})
            candidates.append(
                ObservationCandidate(
                    state={"theta": theta, "r": radius},
                    score=score,
                    image_point=(x, y),
                    label=self.name,
                    raw={"signal_index": peak_index, "signal": normalized},
                )
            )
        return ObservationResult(
            candidates=candidates,
            response_map=None,
            debug_layers={"polar_samples": samples, "theta_signal": normalized},
        )

    def _cached_polar_sample_grid(
        self,
        roi: AnnularROI,
        coordinate_model: PolarCoordinate,
        frame_shape: tuple[int, int],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        height, width = (int(frame_shape[0]), int(frame_shape[1]))
        cache_key = (
            height,
            width,
            int(self.n_angles),
            int(self.n_radii),
            float(roi.inner_radius),
            float(roi.outer_radius),
            float(coordinate_model.center_px[0]),
            float(coordinate_model.center_px[1]),
            str(coordinate_model.direction),
            float(coordinate_model._theta_zero_angle()),
        )
        if cache_key == self._sample_grid_cache_key and self._sample_grid_cache is not None:
            if self._sample_linear_indices_cache is None:
                _, cached_iy, cached_ix = self._sample_grid_cache
                self._sample_linear_indices_cache = cached_iy * np.intp(width) + cached_ix
                self._sample_linear_indices_cache.setflags(write=False)
            return self._sample_grid_cache

        angles = np.linspace(0.0, 2.0 * np.pi, self.n_angles, endpoint=False)
        radii = np.linspace(roi.inner_radius, roi.outer_radius, self.n_radii)
        image_angles = -angles if coordinate_model.direction == "cw" else angles
        image_angles = image_angles + coordinate_model._theta_zero_angle()
        radius_grid = radii[:, np.newaxis]
        cx, cy = coordinate_model.center_px
        x = cx + radius_grid * np.cos(image_angles)[np.newaxis, :]
        y = cy - radius_grid * np.sin(image_angles)[np.newaxis, :]
        ix = np.clip(np.rint(x), 0, width - 1).astype(np.intp)
        iy = np.clip(np.rint(y), 0, height - 1).astype(np.intp)
        linear_indices = iy * np.intp(width) + ix
        for values in (angles, iy, ix, linear_indices):
            values.setflags(write=False)
        self._sample_grid_cache_key = cache_key
        self._sample_grid_cache = (angles, iy, ix)
        self._sample_linear_indices_cache = linear_indices
        return self._sample_grid_cache

    def to_config(self) -> dict[str, Any]:
        return {
            "type": self.name,
            "n_angles": self.n_angles,
            "n_radii": self.n_radii,
            "response_kind": self.response_kind,
            "min_response": self.min_response,
            "smoothing": self.smoothing,
        }

    @staticmethod
    def _smooth_circular(signal: np.ndarray, radius: int) -> np.ndarray:
        if radius <= 0:
            return signal
        width = 2 * radius + 1
        padded = np.concatenate([signal[-radius:], signal, signal[:radius]])
        cumulative = np.empty(padded.size + 1, dtype=np.float64)
        cumulative[0] = 0.0
        np.cumsum(padded, dtype=np.float64, out=cumulative[1:])
        return (cumulative[width:] - cumulative[:-width]) / width
