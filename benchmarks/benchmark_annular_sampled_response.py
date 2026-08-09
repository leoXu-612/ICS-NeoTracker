from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.coordinates import AnnularCoordinate, PolarCoordinate
from neo_tracker.core import FrameContext, ObservationCandidate, ObservationResult
from neo_tracker.observations import AnnularRadialFrontObservation, _fire_response, _intensity
from neo_tracker.roi import AnnularROI


def _sample_indices(
    observation: AnnularRadialFrontObservation,
    roi: AnnularROI,
    coordinate: PolarCoordinate,
    shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    angles = np.linspace(0.0, 2.0 * np.pi, observation.n_angles, endpoint=False)
    radii = np.linspace(roi.inner_radius, roi.outer_radius, observation.n_radii)
    image_angles = -angles if coordinate.direction == "cw" else angles
    image_angles = image_angles + coordinate._theta_zero_angle()
    radius_grid = radii[:, np.newaxis]
    cx, cy = coordinate.center_px
    x = cx + radius_grid * np.cos(image_angles)[np.newaxis, :]
    y = cy - radius_grid * np.sin(image_angles)[np.newaxis, :]
    height, width = shape
    ix = np.clip(np.rint(x), 0, width - 1).astype(np.intp)
    iy = np.clip(np.rint(y), 0, height - 1).astype(np.intp)
    return angles, iy, ix


def _finish_observation(
    samples: np.ndarray,
    angles: np.ndarray,
    roi: AnnularROI,
    coordinate: PolarCoordinate,
    observation: AnnularRadialFrontObservation,
) -> ObservationResult:
    signal = np.mean(samples, axis=0)
    if observation.smoothing > 0:
        signal = observation._smooth_circular(signal, observation.smoothing)
    normalized = signal - signal.min()
    denominator = float(normalized.max())
    if denominator > 1e-12:
        normalized = normalized / denominator
    peak_index = int(np.argmax(normalized))
    score = float(normalized[peak_index])
    candidates: list[ObservationCandidate] = []
    if score >= observation.min_response:
        theta = float(angles[peak_index])
        radius = float(0.5 * (roi.inner_radius + roi.outer_radius))
        point = coordinate.state_to_image_space({"theta": theta, "r": radius})
        candidates.append(
            ObservationCandidate(
                state={"theta": theta, "r": radius},
                score=score,
                image_point=point,
                label=observation.name,
                raw={"signal_index": peak_index, "signal": normalized},
            )
        )
    response_map = np.tile(normalized[np.newaxis, :], (observation.n_radii, 1))
    return ObservationResult(
        candidates=candidates,
        response_map=response_map,
        debug_layers={"polar_samples": samples, "theta_signal": normalized},
    )


def _previous_full_frame_observe(
    frame: np.ndarray,
    roi: AnnularROI,
    coordinate: PolarCoordinate,
    observation: AnnularRadialFrontObservation,
) -> ObservationResult:
    response_image = _fire_response(frame) if observation.response_kind == "fire" else _intensity(frame)
    angles, iy, ix = _sample_indices(observation, roi, coordinate, response_image.shape)
    return _finish_observation(response_image[iy, ix], angles, roi, coordinate, observation)


def _measure(operation, rounds: int) -> tuple[float, int]:
    operation()
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000.0)
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    del result
    tracemalloc.stop()
    return median(samples), int(peak)


def _candidate_summary(result: ObservationResult) -> dict[str, object] | None:
    if not result.candidates:
        return None
    candidate = result.candidates[0]
    return {
        "state": dict(candidate.state),
        "image_point": list(candidate.image_point) if candidate.image_point is not None else None,
        "score": float(candidate.score),
    }


def _outside_distractor_check() -> dict[str, object]:
    height, width = 240, 320
    target = np.empty((height, width, 3), dtype=np.uint8)
    target[:] = (30, 25, 20)
    center = (160.0, 120.0)
    roi = AnnularROI(center=center, inner_radius=45.0, outer_radius=65.0)
    coordinate = AnnularCoordinate(
        center_px=center,
        inner_radius=roi.inner_radius,
        outer_radius=roi.outer_radius,
    )
    observation = AnnularRadialFrontObservation(
        n_angles=72,
        n_radii=8,
        response_kind="fire",
        min_response=0.0,
        smoothing=2,
    )
    angles, iy, ix = _sample_indices(observation, roi, coordinate, target.shape[:2])
    del angles
    target[iy[:, 10:18], ix[:, 10:18]] = (180, 80, 10)
    distracted = target.copy()
    distracted[0, 0] = (0, 0, 255)

    previous_target = _previous_full_frame_observe(target, roi, coordinate, observation)
    previous_distracted = _previous_full_frame_observe(distracted, roi, coordinate, observation)
    current_target = observation.observe(target, roi, coordinate, FrameContext(0, 0.0, target))
    current_distracted = observation.observe(
        distracted,
        roi,
        coordinate,
        FrameContext(0, 0.0, distracted),
    )

    return {
        "previous_polar_samples_max_delta": float(
            np.max(
                np.abs(
                    previous_target.debug_layers["polar_samples"]
                    - previous_distracted.debug_layers["polar_samples"]
                ),
                initial=0.0,
            )
        ),
        "previous_theta_signal_max_delta": float(
            np.max(
                np.abs(
                    previous_target.debug_layers["theta_signal"]
                    - previous_distracted.debug_layers["theta_signal"]
                ),
                initial=0.0,
            )
        ),
        "current_polar_samples_max_delta": float(
            np.max(
                np.abs(
                    current_target.debug_layers["polar_samples"]
                    - current_distracted.debug_layers["polar_samples"]
                ),
                initial=0.0,
            )
        ),
        "current_theta_signal_max_delta": float(
            np.max(
                np.abs(
                    current_target.debug_layers["theta_signal"]
                    - current_distracted.debug_layers["theta_signal"]
                ),
                initial=0.0,
            )
        ),
        "previous_candidate": _candidate_summary(previous_target),
        "previous_distracted_candidate": _candidate_summary(previous_distracted),
        "current_candidate": _candidate_summary(current_target),
        "current_distracted_candidate": _candidate_summary(current_distracted),
    }


def benchmark(
    width: int,
    height: int,
    rounds: int,
    response_kind: str,
    seed: int,
) -> dict[str, object]:
    width = int(width)
    height = int(height)
    response_kind = str(response_kind)
    rng = np.random.default_rng(int(seed))
    bgr = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    frame = bgr[:, :, ::-1]
    center = (width * 0.51, height * 0.49)
    scale = min(width, height)
    roi = AnnularROI(center=center, inner_radius=scale * 0.18, outer_radius=scale * 0.24)
    coordinate = AnnularCoordinate(
        center_px=center,
        theta_zero_px=(center[0], center[1] - scale * 0.1),
        direction="cw",
        inner_radius=roi.inner_radius,
        outer_radius=roi.outer_radius,
    )
    observation = AnnularRadialFrontObservation(
        n_angles=720,
        n_radii=24,
        response_kind=response_kind,
        min_response=0.0,
        smoothing=4,
    )
    context = FrameContext(0, 0.0, frame)

    def previous() -> ObservationResult:
        return _previous_full_frame_observe(frame, roi, coordinate, observation)

    def current() -> ObservationResult:
        # Keep this benchmark scoped to direct sample mapping. The separate
        # annular-grid-cache benchmark measures geometry reuse across frames.
        observation._sample_grid_cache_key = None
        observation._sample_grid_cache = None
        return observation.observe(frame, roi, coordinate, context)

    previous_ms, previous_peak = _measure(previous, rounds)
    current_ms, current_peak = _measure(current, rounds)
    previous_result = previous()
    current_result = current()
    previous_theta = previous_result.debug_layers["theta_signal"]
    current_theta = current_result.debug_layers["theta_signal"]

    return {
        "resolution": f"{width}x{height}",
        "rounds": max(3, int(rounds)),
        "response_kind": response_kind,
        "negative_channel_stride": bool(frame.strides[-1] < 0),
        "sample_grid": [observation.n_radii, observation.n_angles],
        "sample_count": int(observation.n_radii * observation.n_angles),
        "frame_pixel_count": int(width * height),
        "sample_to_frame_pixel_ratio": float(
            observation.n_radii * observation.n_angles / (width * height)
        ),
        "measurements": {
            "previous_median_ms": previous_ms,
            "current_median_ms": current_ms,
            "speedup": previous_ms / current_ms if current_ms > 0.0 else 0.0,
            "previous_python_peak_bytes": previous_peak,
            "current_python_peak_bytes": current_peak,
            "python_peak_reduction_percent": (
                100.0 * (previous_peak - current_peak) / previous_peak if previous_peak > 0 else 0.0
            ),
        },
        "tracking_parity": {
            "max_theta_signal_error": float(
                np.max(np.abs(previous_theta - current_theta), initial=0.0)
            ),
            "previous_peak_index": int(np.argmax(previous_theta)),
            "current_peak_index": int(np.argmax(current_theta)),
            "previous_candidate": _candidate_summary(previous_result),
            "current_candidate": _candidate_summary(current_result),
        },
        "outside_annulus_evidence": _outside_distractor_check(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark direct annular sampling against the previous full-frame response path."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--response-kind", choices=("fire", "brightness"), default="fire")
    parser.add_argument("--seed", type=int, default=20260714)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                args.width,
                args.height,
                args.rounds,
                args.response_kind,
                args.seed,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
