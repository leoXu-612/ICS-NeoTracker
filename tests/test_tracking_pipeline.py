from __future__ import annotations

import math
import unittest
from unittest.mock import patch

import numpy as np

from neo_tracker import observations as observation_module
from neo_tracker.coordinates import AnnularCoordinate, ImageCoordinate
from neo_tracker.core import FrameContext, TrackerResult
from neo_tracker.filters import AlphaBetaFilter
from neo_tracker.motion import ConstantVelocityPrior
from neo_tracker.observations import (
    AnnularRadialFrontObservation,
    BrightnessPeakObservation,
    ColorBlobObservation,
    EdgeFrontObservation,
    TemplateObservation,
)
from neo_tracker.presets import circular_motion_preset, color_marker_preset, synthetic_ring_frame, travelling_flame_preset
from neo_tracker.roi import AnnularROI, RectangularROI
from neo_tracker.visualization import annular_theta_time_heatmap, mapping_probe, trajectory_series


def red_dot_frame(shape: tuple[int, int, int], x: float, y: float, radius: float = 4.0) -> np.ndarray:
    h, w = shape[:2]
    yy, xx = np.indices((h, w))
    mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius**2
    frame = np.zeros(shape, dtype=np.uint8)
    frame[mask, 0] = 255
    return frame


def red_dots_frame(
    shape: tuple[int, int, int],
    dots: list[tuple[float, float, float]],
) -> np.ndarray:
    frame = np.zeros(shape, dtype=np.uint8)
    for x, y, radius in dots:
        frame = np.maximum(frame, red_dot_frame(shape, x, y, radius))
    return frame


class TrackingPipelineTests(unittest.TestCase):
    def test_color_and_brightness_roi_responses_match_copy_based_formulas(self) -> None:
        rng = np.random.default_rng(20260718)
        frame = rng.integers(0, 256, size=(43, 67, 3), dtype=np.uint8)
        roi = RectangularROI(7, 5, 48, 30)
        roi_mask = roi.mask(frame.shape)
        coordinate = ImageCoordinate()
        context = FrameContext(0, 0.0, frame)

        color = ColorBlobObservation(sample_rgb=(214.0, 37.0, 126.0), tolerance=0.31)
        expected_color = np.where(
            roi_mask,
            observation_module._color_distance_response(frame, color.sample_rgb, color.tolerance),
            0.0,
        )
        actual_color = color.observe(frame, roi, coordinate, context).response_map
        np.testing.assert_array_equal(actual_color, expected_color)
        self.assertEqual(actual_color.dtype, np.float32)

        for polarity in ("bright", "dark"):
            brightness = BrightnessPeakObservation(polarity=polarity, percentile_floor=80.0)
            expected_brightness = observation_module._intensity(frame)
            if polarity == "dark":
                expected_brightness = 1.0 - expected_brightness
            floor = float(np.percentile(expected_brightness[roi_mask], brightness.percentile_floor))
            expected_brightness = np.clip(
                (expected_brightness - floor) / max(1.0 - floor, 1e-12),
                0.0,
                1.0,
            )
            expected_brightness = np.where(roi_mask, expected_brightness, 0.0)

            actual_brightness = brightness.observe(frame, roi, coordinate, context).response_map

            np.testing.assert_array_equal(actual_brightness, expected_brightness)
            self.assertEqual(actual_brightness.dtype, np.float32)

    def test_roi_windowed_color_and_brightness_keep_global_candidate_metadata(self) -> None:
        frame = np.zeros((80, 110, 3), dtype=np.uint8)
        frame[43:50, 72:81, 0] = 255
        roi = RectangularROI(60.0, 35.0, 35.0, 25.0)
        context = FrameContext(0, 0.0, frame)

        for observation in (
            ColorBlobObservation(
                sample_rgb=(255.0, 0.0, 0.0),
                tolerance=0.08,
                min_response=0.35,
            ),
            BrightnessPeakObservation(min_response=0.25),
        ):
            result = observation.observe(frame, roi, ImageCoordinate(), context)
            candidate = result.candidates[0]

            self.assertAlmostEqual(candidate.image_point[0], 76.0)
            self.assertAlmostEqual(candidate.image_point[1], 46.0)
            self.assertEqual(candidate.raw["bbox"], [72, 43, 80, 49])
            self.assertEqual(candidate.raw["component_area"], 63)
            self.assertEqual(result.response_map.shape, frame.shape[:2])
            self.assertTrue(np.all(result.response_map[:35] == 0.0))

    def test_roi_windowed_observations_can_store_exact_local_review_responses(self) -> None:
        rng = np.random.default_rng(20260714)
        frame = rng.integers(0, 256, size=(80, 110, 3), dtype=np.uint8)
        frame[43:50, 72:81, 0] = 255
        roi = RectangularROI(60.0, 35.0, 35.0, 25.0)
        coordinate = ImageCoordinate()

        for observation in (
            ColorBlobObservation(
                sample_rgb=(255.0, 0.0, 0.0),
                tolerance=0.08,
                min_response=0.35,
            ),
            BrightnessPeakObservation(min_response=0.25),
        ):
            with self.subTest(observation=observation.name):
                full = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))
                compact = observation.observe(
                    frame,
                    roi,
                    coordinate,
                    FrameContext(0, 0.0, frame, compact_response_map=True),
                )

                self.assertIsNotNone(compact.response_origin)
                self.assertEqual(compact.response_frame_shape, frame.shape[:2])
                self.assertLess(compact.response_map.nbytes, full.response_map.nbytes)
                x0, y0 = compact.response_origin
                height, width = compact.response_map.shape
                np.testing.assert_array_equal(
                    compact.response_map,
                    full.response_map[y0 : y0 + height, x0 : x0 + width],
                )
                self.assertEqual(
                    [candidate.image_point for candidate in compact.candidates],
                    [candidate.image_point for candidate in full.candidates],
                )

    def test_full_frame_color_tracking_stores_exact_compact_marker_window(self) -> None:
        cv2 = observation_module._load_observation_cv2()
        if cv2 is None:
            self.skipTest("OpenCV is not installed")
        bgr = np.full((96, 128, 3), (224, 224, 224), dtype=np.uint8)
        bgr[32:44, 71:86] = (0, 0, 255)
        observation = ColorBlobObservation(
            sample_rgb=(255.0, 0.0, 0.0),
            tolerance=0.08,
            min_response=0.35,
        )
        roi = RectangularROI(0.0, 0.0, 128.0, 96.0)
        coordinate = ImageCoordinate()

        for frame in (bgr[:, :, ::-1], np.ascontiguousarray(bgr[:, :, ::-1])):
            with self.subTest(channel_stride=frame.strides[2]):
                full = observation.observe(
                    frame,
                    roi,
                    coordinate,
                    FrameContext(0, 0.0, frame),
                )
                compact = observation.observe(
                    frame,
                    roi,
                    coordinate,
                    FrameContext(0, 0.0, frame, compact_response_map=True),
                )

                self.assertIsNotNone(compact.response_origin)
                self.assertEqual(compact.response_frame_shape, frame.shape[:2])
                self.assertLess(compact.response_map.nbytes, full.response_map.nbytes)
                reconstructed = np.zeros(frame.shape[:2], dtype=np.float32)
                x0, y0 = compact.response_origin
                height, width = compact.response_map.shape
                reconstructed[y0 : y0 + height, x0 : x0 + width] = compact.response_map
                np.testing.assert_array_equal(reconstructed, full.response_map)
                self.assertEqual(
                    [candidate.image_point for candidate in compact.candidates],
                    [candidate.image_point for candidate in full.candidates],
                )
                self.assertEqual(
                    [candidate.raw for candidate in compact.candidates],
                    [candidate.raw for candidate in full.candidates],
                )

        dense_rgb = np.full((96, 128, 3), (255, 0, 0), dtype=np.uint8)
        dense = observation.observe(
            dense_rgb,
            roi,
            coordinate,
            FrameContext(0, 0.0, dense_rgb, compact_response_map=True),
        )
        self.assertIsNone(dense.response_origin)
        self.assertEqual(dense.response_map.shape, dense_rgb.shape[:2])

    def test_edge_front_response_matches_roi_normalized_axis_gradient(self) -> None:
        rng = np.random.default_rng(20260717)
        frame = rng.integers(0, 256, size=(43, 67, 3), dtype=np.uint8)
        roi = RectangularROI(7, 5, 48, 30)
        coordinate = ImageCoordinate()
        context = FrameContext(0, 0.0, frame)

        for axis, gradient_index in (("x", 1), ("y", 0)):
            image = observation_module._intensity(frame)
            legacy_gradients = np.gradient(image)
            expected = np.abs(legacy_gradients[gradient_index])
            expected = np.where(roi.mask(frame.shape), expected, 0.0)
            if float(expected.max()) > 1e-12:
                expected = expected / expected.max()
            expected_y, expected_x = np.unravel_index(int(np.argmax(expected)), expected.shape)

            result = EdgeFrontObservation(axis=axis).observe(frame, roi, coordinate, context)

            np.testing.assert_allclose(result.response_map, expected, rtol=0.0, atol=1e-7)
            self.assertEqual(result.response_map.dtype, np.float32)
            self.assertEqual(len(result.candidates), 1)
            candidate = result.candidates[0]
            candidate_x = candidate.state["x_px"]
            candidate_y = candidate.state["y_px"]
            if axis == "x":
                weights = expected[:, expected_x]
                expected_orthogonal = float(
                    np.dot(weights, np.arange(expected.shape[0], dtype=np.float64))
                    / np.sum(weights, dtype=np.float64)
                )
                self.assertEqual(candidate_x, float(expected_x))
                self.assertAlmostEqual(candidate_y, expected_orthogonal, places=6)
            else:
                weights = expected[expected_y, :]
                expected_orthogonal = float(
                    np.dot(weights, np.arange(expected.shape[1], dtype=np.float64))
                    / np.sum(weights, dtype=np.float64)
                )
                self.assertAlmostEqual(candidate_x, expected_orthogonal, places=6)
                self.assertEqual(candidate_y, float(expected_y))

    def test_edge_front_roi_normalization_ignores_stronger_outside_edges(self) -> None:
        cases = (
            (
                "x",
                RectangularROI(60, 10, 40, 60),
                (slice(None), slice(76, None), slice(None)),
                (slice(None), slice(20, 30), slice(None)),
                (75.0, 40.0),
            ),
            (
                "y",
                RectangularROI(10, 40, 80, 30),
                (slice(56, None), slice(None), slice(None)),
                (slice(10, 20), slice(None), slice(None)),
                (50.0, 55.0),
            ),
        )
        for axis, roi, target_slice, distractor_slice, expected_point in cases:
            with self.subTest(axis=axis):
                target_only = np.zeros((80, 120, 3), dtype=np.uint8)
                target_only[target_slice] = 40
                with_distractor = target_only.copy()
                with_distractor[distractor_slice] = 255
                observation = EdgeFrontObservation(axis=axis)

                baseline = observation.observe(
                    target_only,
                    roi,
                    ImageCoordinate(),
                    FrameContext(0, 0.0, target_only),
                )
                actual = observation.observe(
                    with_distractor,
                    roi,
                    ImageCoordinate(),
                    FrameContext(0, 0.0, with_distractor),
                )

                np.testing.assert_array_equal(actual.response_map, baseline.response_map)
                self.assertEqual(len(actual.candidates), 1)
                self.assertEqual(actual.candidates[0].image_point, expected_point)
                self.assertEqual(actual.candidates[0].score, 1.0)

    def test_edge_front_tracking_stores_exact_roi_local_response(self) -> None:
        rng = np.random.default_rng(2026071403)
        frame = rng.integers(0, 256, size=(81, 123, 3), dtype=np.uint8)
        roi = RectangularROI(19.0, 13.0, 54.0, 38.0)
        coordinate = ImageCoordinate()

        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                observation = EdgeFrontObservation(axis=axis)
                full = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))
                compact = observation.observe(
                    frame,
                    roi,
                    coordinate,
                    FrameContext(0, 0.0, frame, compact_response_map=True),
                )

                self.assertIsNotNone(compact.response_origin)
                self.assertEqual(compact.response_frame_shape, frame.shape[:2])
                self.assertLess(compact.response_map.nbytes, full.response_map.nbytes)
                reconstructed = np.zeros(frame.shape[:2], dtype=compact.response_map.dtype)
                x0, y0 = compact.response_origin
                height, width = compact.response_map.shape
                reconstructed[y0 : y0 + height, x0 : x0 + width] = compact.response_map
                np.testing.assert_array_equal(reconstructed, full.response_map)
                self.assertEqual(compact.candidates[0].image_point, full.candidates[0].image_point)
                self.assertEqual(compact.candidates[0].score, full.candidates[0].score)

    def test_edge_front_places_uniform_front_marker_at_roi_center(self) -> None:
        roi = RectangularROI(5, 7, 60, 20)

        vertical = np.zeros((40, 70, 3), dtype=np.uint8)
        vertical[:, 30:, :] = 255
        vertical_result = EdgeFrontObservation(axis="x").observe(
            vertical,
            roi,
            ImageCoordinate(),
            FrameContext(0, 0.0, vertical),
        )
        self.assertEqual(vertical_result.candidates[0].state, {"x_px": 29.0, "y_px": 17.0})

        horizontal = np.zeros((40, 70, 3), dtype=np.uint8)
        horizontal[20:, :, :] = 255
        horizontal_result = EdgeFrontObservation(axis="y").observe(
            horizontal,
            roi,
            ImageCoordinate(),
            FrameContext(0, 0.0, horizontal),
        )
        self.assertEqual(horizontal_result.candidates[0].state, {"x_px": 35.0, "y_px": 19.0})

    def test_edge_front_rejects_invalid_axis(self) -> None:
        with self.assertRaisesRegex(ValueError, "axis must be 'x' or 'y'"):
            EdgeFrontObservation(axis="depth")

    def test_uint8_color_response_matches_reference_normalization(self) -> None:
        rng = np.random.default_rng(20260714)
        frame = rng.integers(0, 256, size=(47, 63, 3), dtype=np.uint8)
        sample = (214.0, 37.0, 126.0)
        tolerance = 0.31

        arr = observation_module._as_float01(frame)
        sample_arr = np.asarray(sample, dtype=np.float32) / 255.0
        distance = np.linalg.norm(arr[:, :, :3] - sample_arr, axis=2) / np.float32(np.sqrt(3.0))
        expected = np.clip(1.0 - distance / tolerance, 0.0, 1.0)
        actual = observation_module._color_distance_response(frame, sample, tolerance)

        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-6)
        self.assertEqual(actual.dtype, np.float32)

    def test_uint8_color_response_local_bbox_matches_full_plane_and_dense_fallback(self) -> None:
        cv2 = observation_module._load_observation_cv2()
        if cv2 is None:
            self.skipTest("OpenCV is not installed")
        sample = (210.0, 40.0, 80.0)
        tolerance = 0.18
        bgr = np.full((96, 128, 3), (224, 224, 224), dtype=np.uint8)
        bgr[32:44, 71:86] = (80, 40, 210)
        negative_stride_rgb = bgr[:, :, ::-1]
        contiguous_rgb = np.ascontiguousarray(negative_stride_rgb)
        sample_array = np.asarray(sample, dtype=np.float32) / np.float32(255.0)
        sample_u8_scale = sample_array * np.float32(255.0)
        scale = np.float32(1.0 / (255.0 * np.sqrt(3.0) * tolerance))

        for frame in (negative_stride_rgb, contiguous_rgb):
            bbox = observation_module._color_response_local_bbox(
                frame,
                sample_u8_scale,
                scale,
            )
            self.assertIsNotNone(bbox)
            self.assertLess(bbox[2] * bbox[3], frame.shape[0] * frame.shape[1] // 4)
            expected = observation_module._uint8_color_distance_plane(
                frame,
                sample_u8_scale,
                scale,
            )
            actual = observation_module._color_distance_response(frame, sample, tolerance)
            np.testing.assert_array_equal(actual, expected)

        unsupported_stride_rgb = contiguous_rgb[:, ::2, :]
        self.assertIsNone(
            observation_module._color_response_local_bbox(
                unsupported_stride_rgb,
                sample_u8_scale,
                scale,
            )
        )
        np.testing.assert_array_equal(
            observation_module._color_distance_response(
                unsupported_stride_rgb,
                sample,
                tolerance,
            ),
            observation_module._uint8_color_distance_plane(
                unsupported_stride_rgb,
                sample_u8_scale,
                scale,
            ),
        )

        no_match_rgb = np.full((96, 128, 3), (224, 224, 224), dtype=np.uint8)
        self.assertEqual(
            observation_module._color_response_local_bbox(
                no_match_rgb,
                sample_u8_scale,
                scale,
            ),
            (0, 0, 0, 0),
        )
        self.assertFalse(
            np.any(observation_module._color_distance_response(no_match_rgb, sample, tolerance))
        )

        dense_bgr = np.full((96, 128, 3), (80, 40, 210), dtype=np.uint8)
        dense_rgb = dense_bgr[:, :, ::-1]
        self.assertIsNone(
            observation_module._color_response_local_bbox(
                dense_rgb,
                sample_u8_scale,
                scale,
            )
        )
        np.testing.assert_array_equal(
            observation_module._color_distance_response(dense_rgb, sample, tolerance),
            observation_module._uint8_color_distance_plane(
                dense_rgb,
                sample_u8_scale,
                scale,
            ),
        )

        class BrokenCV2:
            @staticmethod
            def inRange(*_args):  # noqa: N802
                raise RuntimeError("synthetic OpenCV failure")

        with patch.object(observation_module, "_load_observation_cv2", return_value=BrokenCV2()):
            self.assertIsNone(
                observation_module._color_response_local_bbox(
                    negative_stride_rgb,
                    sample_u8_scale,
                    scale,
                )
            )
            np.testing.assert_array_equal(
                observation_module._color_distance_response(
                    negative_stride_rgb,
                    sample,
                    tolerance,
                ),
                observation_module._uint8_color_distance_plane(
                    negative_stride_rgb,
                    sample_u8_scale,
                    scale,
                ),
            )

    def test_uint8_negative_stride_intensity_matches_reference_normalization(self) -> None:
        rng = np.random.default_rng(20260715)
        bgr = rng.integers(0, 256, size=(47, 63, 3), dtype=np.uint8)
        frame = bgr[:, :, ::-1]
        self.assertLess(frame.strides[-1], 0)
        normalized = np.asarray(frame, dtype=np.float32) / np.float32(255.0)
        expected = (
            np.float32(0.299) * normalized[:, :, 0]
            + np.float32(0.587) * normalized[:, :, 1]
            + np.float32(0.114) * normalized[:, :, 2]
        )

        actual = observation_module._intensity(frame)

        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=2e-7)
        self.assertEqual(actual.dtype, np.float32)
        self.assertTrue(actual.flags.c_contiguous)

    def test_uint8_negative_stride_fire_response_matches_reference(self) -> None:
        rng = np.random.default_rng(20260716)
        frame = rng.integers(0, 256, size=(41, 59, 3), dtype=np.uint8)[:, :, ::-1]
        arr = np.asarray(frame, dtype=np.float32) / np.float32(255.0)
        brightness = (
            np.float32(0.299) * arr[:, :, 0]
            + np.float32(0.587) * arr[:, :, 1]
            + np.float32(0.114) * arr[:, :, 2]
        )
        expected = (
            np.float32(0.45) * brightness
            + np.float32(0.35) * arr[:, :, 0]
            + np.float32(0.25) * arr[:, :, 1]
            - np.float32(0.25) * arr[:, :, 2]
        )
        expected -= expected.min()
        expected /= expected.max() - expected.min()

        actual = observation_module._fire_response(frame)

        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=3e-7)
        self.assertEqual(actual.dtype, np.float32)

    def test_constant_uint8_fire_response_reuses_zeroed_response(self) -> None:
        frame = np.full((41, 59, 3), 127, dtype=np.uint8)[:, :, ::-1]

        actual = observation_module._fire_response(frame)

        self.assertEqual(actual.dtype, np.float32)
        self.assertEqual(actual.shape, frame.shape[:2])
        self.assertTrue(actual.flags.c_contiguous)
        self.assertTrue(np.array_equal(actual, np.zeros(frame.shape[:2], dtype=np.float32)))

    def test_annular_backend_describes_cached_fire_and_intensity_grids(self) -> None:
        fire = observation_module.observation_backend_info(
            AnnularRadialFrontObservation(response_kind="fire")
        )
        intensity = observation_module.observation_backend_info(
            AnnularRadialFrontObservation(response_kind="brightness")
        )

        self.assertEqual(fire.label, "NumPy cached linear fire")
        self.assertEqual(fire.state, "optimized")
        self.assertIn("17,280 annular sample points", fire.detail)
        self.assertIn("Cached linear indices gather RGB channels", fire.detail)
        self.assertIn("Indices are reused", fire.detail)
        self.assertEqual(intensity.label, "NumPy cached linear intensity")
        self.assertEqual(intensity.state, "optimized")
        self.assertIn("17,280 annular sample points", intensity.detail)
        self.assertIn("Cached linear indices gather RGB channels", intensity.detail)
        self.assertIn("Indices are reused", intensity.detail)

    def test_component_centroid_backends_have_candidate_level_parity(self) -> None:
        cv2 = observation_module._load_component_cv2()
        if cv2 is None:
            self.skipTest("OpenCV is not installed")
        response = np.zeros((18, 24), dtype=np.float32)
        response[1:4, 1:5] = np.asarray(
            [
                [0.55, 0.60, 0.65, 0.70],
                [0.60, 0.70, 0.80, 0.90],
                [0.55, 0.65, 0.75, 0.85],
            ],
            dtype=np.float32,
        )
        response[7:10, 14:17] = 0.92
        response[13, 4] = 0.78
        response[14, 5] = 0.82  # Diagonal pixels belong to one 8-connected component.

        expected = observation_module._component_centroids_numpy(
            response,
            0.5,
            max_candidates=3,
            min_area=2,
        )
        actual = observation_module._component_centroids_cv2(
            response,
            0.5,
            max_candidates=3,
            min_area=2,
            cv2=cv2,
        )

        self.assertEqual(len(actual), len(expected))
        for cv_candidate, numpy_candidate in zip(actual, expected):
            self.assertEqual(cv_candidate["area"], numpy_candidate["area"])
            self.assertEqual(cv_candidate["bbox"], numpy_candidate["bbox"])
            for key in ("x", "y", "score", "peak_response"):
                self.assertAlmostEqual(cv_candidate[key], numpy_candidate[key], places=7)

        dense_response = np.linspace(0.55, 0.95, 35, dtype=np.float32).reshape(5, 7)
        dense_expected = observation_module._component_centroids_numpy(
            dense_response,
            0.5,
            max_candidates=1,
            min_area=1,
        )
        dense_actual = observation_module._component_centroids_cv2(
            dense_response,
            0.5,
            max_candidates=1,
            min_area=1,
            cv2=cv2,
        )
        self.assertEqual(dense_actual[0]["bbox"], dense_expected[0]["bbox"])
        for key in ("x", "y", "score", "peak_response"):
            self.assertAlmostEqual(dense_actual[0][key], dense_expected[0][key], places=7)

    def test_component_centroids_fall_back_when_opencv_is_unavailable(self) -> None:
        response = np.zeros((8, 9), dtype=np.float32)
        response[2:5, 3:7] = 0.8
        expected = observation_module._component_centroids_numpy(
            response,
            0.5,
            max_candidates=2,
            min_area=1,
        )

        original_loader = observation_module._load_component_cv2
        original_runtime_error = observation_module._COMPONENT_CV2_RUNTIME_ERROR
        try:
            observation_module._load_component_cv2 = lambda: None
            actual = observation_module._component_centroids(
                response,
                0.5,
                max_candidates=2,
                min_area=1,
            )
            observation_module._load_component_cv2 = lambda: object()
            observation_module._COMPONENT_CV2_RUNTIME_ERROR = None
            backend_failure_result = observation_module._component_centroids(
                response,
                0.5,
                max_candidates=2,
                min_area=1,
            )
            self.assertIsNotNone(observation_module._COMPONENT_CV2_RUNTIME_ERROR)
            observation_module._load_component_cv2 = original_loader
            self.assertIsNone(observation_module._load_component_cv2())
        finally:
            observation_module._load_component_cv2 = original_loader
            observation_module._COMPONENT_CV2_RUNTIME_ERROR = original_runtime_error

        self.assertEqual(actual, expected)
        self.assertEqual(backend_failure_result, expected)

    def test_opencv_component_sparse_and_full_weight_paths_match(self) -> None:
        cv2 = observation_module._load_component_cv2()
        if cv2 is None:
            self.skipTest("OpenCV is not installed")
        response = np.zeros((24, 32), dtype=np.float32)
        response[:12, :] = np.linspace(0.51, 0.99, 12 * 32, dtype=np.float32).reshape(12, 32)
        original_ratio = observation_module._COMPONENT_CV2_SPARSE_WEIGHT_RATIO
        original_local_ratio = observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO
        try:
            observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = 0.0
            observation_module._COMPONENT_CV2_SPARSE_WEIGHT_RATIO = 0.75
            sparse = observation_module._component_centroids_cv2(
                response,
                0.5,
                max_candidates=2,
                min_area=1,
                cv2=cv2,
            )
            observation_module._COMPONENT_CV2_SPARSE_WEIGHT_RATIO = 0.25
            full = observation_module._component_centroids_cv2(
                response,
                0.5,
                max_candidates=2,
                min_area=1,
                cv2=cv2,
            )
        finally:
            observation_module._COMPONENT_CV2_SPARSE_WEIGHT_RATIO = original_ratio
            observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = original_local_ratio

        self.assertEqual(sparse, full)

    def test_opencv_component_local_and_global_weight_paths_match(self) -> None:
        cv2 = observation_module._load_component_cv2()
        if cv2 is None:
            self.skipTest("OpenCV is not installed")
        response = np.zeros((96, 128), dtype=np.float32)
        response[4:16, 8:24] = np.linspace(
            0.51,
            0.98,
            12 * 16,
            dtype=np.float32,
        ).reshape(12, 16)
        response[55:64, 76:90] = 0.84
        response[80:84, 14:20] = np.asarray(
            [
                [0.65, 0.72, 0.0, 0.0, 0.0, 0.0],
                [0.68, 0.81, 0.88, 0.0, 0.0, 0.0],
                [0.0, 0.75, 0.91, 0.78, 0.0, 0.0],
                [0.0, 0.0, 0.73, 0.82, 0.76, 0.69],
            ],
            dtype=np.float32,
        )
        original_ratio = observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO
        try:
            observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = 1.0
            local = observation_module._component_centroids_cv2(
                response,
                0.5,
                max_candidates=4,
                min_area=1,
                cv2=cv2,
            )
            observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = 0.0
            global_scan = observation_module._component_centroids_cv2(
                response,
                0.5,
                max_candidates=4,
                min_area=1,
                cv2=cv2,
            )
        finally:
            observation_module._COMPONENT_CV2_LOCAL_BBOX_RATIO = original_ratio

        self.assertEqual(len(local), len(global_scan))
        for local_candidate, global_candidate in zip(local, global_scan):
            self.assertEqual(local_candidate["area"], global_candidate["area"])
            self.assertEqual(local_candidate["bbox"], global_candidate["bbox"])
            for key in ("x", "y", "score", "peak_response"):
                self.assertAlmostEqual(local_candidate[key], global_candidate[key], places=12)

    def test_vectorized_numpy_components_match_small_run_aggregator(self) -> None:
        rng = np.random.default_rng(404)
        response = np.zeros((96, 128), dtype=np.float32)
        isolated_y, isolated_x = np.mgrid[1:95:3, 1:127:3]
        response[isolated_y, isolated_x] = rng.random(isolated_y.shape, dtype=np.float32) * 0.4 + 0.6
        original_threshold = observation_module._COMPONENT_NUMPY_VECTOR_MIN_RUNS
        try:
            observation_module._COMPONENT_NUMPY_VECTOR_MIN_RUNS = response.size
            expected = observation_module._component_centroids_numpy(
                response,
                0.5,
                max_candidates=8,
                min_area=1,
            )
            observation_module._COMPONENT_NUMPY_VECTOR_MIN_RUNS = original_threshold
            actual = observation_module._component_centroids_numpy(
                response,
                0.5,
                max_candidates=8,
                min_area=1,
            )
        finally:
            observation_module._COMPONENT_NUMPY_VECTOR_MIN_RUNS = original_threshold

        self.assertEqual(len(actual), len(expected))
        for vector_candidate, legacy_candidate in zip(actual, expected):
            self.assertEqual(vector_candidate["area"], legacy_candidate["area"])
            self.assertEqual(vector_candidate["bbox"], legacy_candidate["bbox"])
            for key in ("x", "y", "score", "peak_response"):
                self.assertAlmostEqual(vector_candidate[key], legacy_candidate[key], places=7)

    def test_component_threshold_supports_integer_responses_and_rejects_nonfinite_values(self) -> None:
        response = np.zeros((8, 9), dtype=np.uint8)
        response[2:5, 3:7] = 255

        candidates = observation_module._component_centroids_numpy(
            response,
            1,
            max_candidates=2,
            min_area=1,
        )

        self.assertEqual(candidates[0]["area"], 12)
        self.assertEqual(candidates[0]["bbox"], [3, 2, 6, 4])
        with self.assertRaisesRegex(ValueError, "threshold must be finite"):
            observation_module._component_centroids_numpy(
                response,
                float("nan"),
                max_candidates=2,
                min_area=1,
            )

    def test_color_marker_tracks_synthetic_red_dot(self) -> None:
        positions = [(20 + i * 3, 40 + i * 2) for i in range(8)]
        frames = [red_dot_frame((120, 160, 3), x, y) for x, y in positions]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 160, 120), tolerance=0.08)
        results = pipeline.run(frames, fps=30.0)
        for result, (expected_x, expected_y) in zip(results, positions):
            self.assertAlmostEqual(result.state["x_px"], expected_x, delta=0.35)
            self.assertAlmostEqual(result.state["y_px"], expected_y, delta=0.35)
            self.assertEqual(result.status, "ok")
            candidates = result.debug.get("candidates")
            self.assertIsInstance(candidates, list)
            self.assertTrue(candidates)
            self.assertTrue(any(candidate["selected"] for candidate in candidates))
            selected = next(candidate for candidate in candidates if candidate["selected"])
            self.assertAlmostEqual(selected["image_point"][0], expected_x, delta=0.35)
            self.assertAlmostEqual(selected["image_point"][1], expected_y, delta=0.35)
            self.assertIn("motion_score", selected)
            self.assertIn("combined_score", selected)

    def test_color_marker_separates_blobs_and_motion_prior_selects_target(self) -> None:
        frames = [
            red_dots_frame((90, 100, 3), [(20.0, 40.0, 4.0)]),
            red_dots_frame((90, 100, 3), [(30.0, 40.0, 4.0)]),
            red_dots_frame((90, 100, 3), [(40.0, 40.0, 4.0), (76.0, 40.0, 7.0)]),
        ]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 100, 90), tolerance=0.08)
        pipeline.motion_model = ConstantVelocityPrior(keys=("x_px", "y_px"), max_residual=7.0)

        results = pipeline.run(frames, fps=10.0)

        final = results[-1]
        candidates = final.debug["candidates"]
        self.assertEqual(len(candidates), 2)
        selected = next(candidate for candidate in candidates if candidate["selected"])
        alternative = next(candidate for candidate in candidates if not candidate["selected"])
        self.assertAlmostEqual(selected["image_point"][0], 40.0, delta=0.35)
        self.assertAlmostEqual(alternative["image_point"][0], 76.0, delta=0.35)
        self.assertAlmostEqual(final.state["x_px"], 40.0, delta=0.35)
        self.assertGreater(selected["motion_score"], alternative["motion_score"])

    def test_color_marker_filters_small_components_and_limits_candidates(self) -> None:
        frame = red_dots_frame(
            (80, 100, 3),
            [(15.0, 30.0, 1.0), (40.0, 30.0, 3.0), (70.0, 30.0, 5.0)],
        )
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 100, 80), tolerance=0.08)
        observation = pipeline.observation_model
        observation.min_component_area = 8
        observation.max_candidates = 1

        result = pipeline.run([frame], fps=30.0)[0]

        self.assertEqual(len(result.debug["candidates"]), 1)
        self.assertAlmostEqual(result.observation.image_point[0], 70.0, delta=0.35)

    def test_brightness_observation_returns_separate_region_candidates(self) -> None:
        frame = np.zeros((70, 100, 3), dtype=np.uint8)
        yy, xx = np.indices(frame.shape[:2])
        for x, radius in ((25.0, 4.0), (74.0, 6.0)):
            frame[(xx - x) ** 2 + (yy - 35.0) ** 2 <= radius**2] = 255
        observation = BrightnessPeakObservation(max_candidates=4, min_component_area=4)
        context = FrameContext(0, 0.0, frame, None, None)

        observed = observation.observe(
            frame,
            RectangularROI(0, 0, 100, 70),
            ImageCoordinate(),
            context,
        )

        self.assertEqual(len(observed.candidates), 2)
        xs = sorted(candidate.image_point[0] for candidate in observed.candidates)
        np.testing.assert_allclose(xs, [25.0, 74.0], atol=0.35)

    def test_circular_motion_unwraps_phase_across_zero(self) -> None:
        center = (80.0, 70.0)
        radius = 35.0
        thetas = [5.75 + i * 0.22 for i in range(8)]
        frames = [
            red_dot_frame(
                (150, 170, 3),
                center[0] + radius * math.cos(theta),
                center[1] - radius * math.sin(theta),
                radius=3.0,
            )
            for theta in thetas
        ]
        pipeline = circular_motion_preset(center_px=center, radius=radius)
        results = pipeline.run(frames, fps=24.0)
        unwrapped = [result.state["theta_unwrapped"] for result in results]
        diffs = np.diff(unwrapped)
        self.assertTrue(np.all(diffs > 0.05), unwrapped)
        self.assertGreater(unwrapped[-1], 2.0 * math.pi)

    def test_travelling_flame_tracks_annular_front_across_period_boundary(self) -> None:
        center = (96.0, 96.0)
        radius = 45.0
        thetas = [5.8 + i * 0.18 for i in range(9)]
        frames = [
            synthetic_ring_frame((192, 192, 3), center, radius, theta, angular_width=0.08)
            for theta in thetas
        ]
        pipeline = travelling_flame_preset(center_px=center, inner_radius=38.0, outer_radius=52.0)
        results = pipeline.run(frames, fps=30.0)
        unwrapped = [result.state["theta_unwrapped"] for result in results]
        self.assertTrue(np.all(np.diff(unwrapped) > 0.04), unwrapped)
        self.assertGreater(unwrapped[-1], 2.0 * math.pi)
        heatmap = annular_theta_time_heatmap(results)
        self.assertEqual(heatmap.shape[0], len(frames))
        self.assertGreater(heatmap.shape[1], 100)

    def test_visualization_helpers_return_mapping_and_series(self) -> None:
        frame = red_dot_frame((80, 90, 3), 25, 30)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)
        results = pipeline.run([frame], fps=30.0)
        probe = mapping_probe(pipeline, (25, 30))
        self.assertEqual(probe["state_space"]["x_px"], 25)
        series = trajectory_series(results, ("x_px", "y_px"))
        self.assertEqual(series["x_px"][0], results[0].filtered_state["x_px"])

    def test_filter_can_prime_from_existing_result_before_segment_rerun(self) -> None:
        frames = [red_dot_frame((80, 90, 3), 20 + i * 5, 30 + i * 2) for i in range(5)]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)
        results = pipeline.run(frames, fps=30.0)
        anchor = results[2]
        velocity = anchor.debug["filter"]["velocity"]
        self.assertIn("v_x_px", velocity)

        pipeline.tracker_filter.reset()
        self.assertEqual(pipeline.tracker_filter.velocities, {})
        pipeline.tracker_filter.prime(anchor)
        self.assertAlmostEqual(pipeline.tracker_filter.velocities["v_x_px"], velocity["v_x_px"])
        self.assertAlmostEqual(pipeline.tracker_filter.velocities["v_y_px"], velocity["v_y_px"])

    def test_run_without_reset_continues_frame_indices_and_timestamps(self) -> None:
        frames = [red_dot_frame((80, 90, 3), 20 + i * 4, 30) for i in range(4)]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)

        pipeline.run(frames[:2], fps=10.0)
        results = pipeline.run(frames[2:], fps=10.0, reset=False)

        self.assertEqual([result.frame_index for result in results], [0, 1, 2, 3])
        np.testing.assert_allclose([result.time_s for result in results], [0.0, 0.1, 0.2, 0.3])

    def test_consecutive_missing_observations_keep_advancing_prediction(self) -> None:
        frames = [
            red_dot_frame((80, 90, 3), 20, 30),
            red_dot_frame((80, 90, 3), 30, 30),
            np.zeros((80, 90, 3), dtype=np.uint8),
            np.zeros((80, 90, 3), dtype=np.uint8),
        ]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)

        results = pipeline.run(frames, fps=1.0)

        self.assertEqual([result.status for result in results[-2:]], ["predicted", "predicted"])
        first_prediction = results[-2].filtered_state["x_px"]
        second_prediction = results[-1].filtered_state["x_px"]
        self.assertGreater(first_prediction, results[1].filtered_state["x_px"])
        self.assertGreater(second_prediction, first_prediction)
        self.assertIn("velocity", results[-1].debug["filter"])

    def test_annular_sampling_matches_coordinate_model_for_cw_offset_axis(self) -> None:
        yy, xx = np.indices((64, 72))
        intensity = ((3 * xx + 5 * yy) % 256).astype(np.uint8)
        frame = np.repeat(intensity[:, :, np.newaxis], 3, axis=2)
        center = (35.0, 31.0)
        roi = AnnularROI(center=center, inner_radius=7.0, outer_radius=14.0)
        coordinate = AnnularCoordinate(
            center_px=center,
            theta_zero_px=(35.0, 12.0),
            direction="cw",
            inner_radius=roi.inner_radius,
            outer_radius=roi.outer_radius,
        )
        observation = AnnularRadialFrontObservation(
            n_angles=24,
            n_radii=5,
            response_kind="brightness",
            min_response=0.0,
            smoothing=0,
        )

        result = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))

        angles = np.linspace(0.0, 2.0 * np.pi, observation.n_angles, endpoint=False)
        radii = np.linspace(roi.inner_radius, roi.outer_radius, observation.n_radii)
        expected = np.zeros((observation.n_radii, observation.n_angles), dtype=float)
        for angle_index, theta in enumerate(angles):
            for radius_index, radius in enumerate(radii):
                x, y = coordinate.state_to_image_space({"theta": float(theta), "r": float(radius)})
                ix = int(np.clip(round(x), 0, frame.shape[1] - 1))
                iy = int(np.clip(round(y), 0, frame.shape[0] - 1))
                expected[radius_index, angle_index] = intensity[iy, ix] / 255.0
        np.testing.assert_allclose(result.debug_layers["polar_samples"], expected, atol=1e-6)

    def test_annular_sample_grid_cache_reuses_read_only_geometry_and_invalidates(self) -> None:
        frame = np.zeros((96, 112, 3), dtype=np.uint8)
        center = (56.0, 48.0)
        roi = AnnularROI(center=center, inner_radius=14.0, outer_radius=25.0)
        coordinate = AnnularCoordinate(
            center_px=center,
            inner_radius=roi.inner_radius,
            outer_radius=roi.outer_radius,
        )
        observation = AnnularRadialFrontObservation(
            n_angles=48,
            n_radii=6,
            response_kind="brightness",
            min_response=0.0,
            smoothing=0,
        )

        first = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))
        first_grid = observation._sample_grid_cache
        first_linear_indices = observation._sample_linear_indices_cache
        second = observation.observe(frame, roi, coordinate, FrameContext(1, 1.0 / 30.0, frame))

        self.assertIsNotNone(first_grid)
        self.assertIsNotNone(first_linear_indices)
        self.assertIs(observation._sample_grid_cache, first_grid)
        self.assertIs(observation._sample_linear_indices_cache, first_linear_indices)
        for values in first_grid or ():
            self.assertFalse(values.flags.writeable)
        self.assertFalse(first_linear_indices.flags.writeable)
        np.testing.assert_array_equal(
            first_linear_indices,
            first_grid[1] * frame.shape[1] + first_grid[2],
        )
        np.testing.assert_array_equal(
            second.debug_layers["theta_signal"],
            first.debug_layers["theta_signal"],
        )

        shifted_coordinate = AnnularCoordinate(
            center_px=(center[0] + 2.0, center[1]),
            inner_radius=roi.inner_radius,
            outer_radius=roi.outer_radius,
        )
        observation.observe(frame, roi, shifted_coordinate, FrameContext(2, 2.0 / 30.0, frame))
        shifted_grid = observation._sample_grid_cache
        self.assertIsNot(shifted_grid, first_grid)
        self.assertIsNot(observation._sample_linear_indices_cache, first_linear_indices)

        wider_roi = AnnularROI(center=center, inner_radius=roi.inner_radius, outer_radius=29.0)
        observation.observe(frame, wider_roi, coordinate, FrameContext(3, 3.0 / 30.0, frame))
        wider_grid = observation._sample_grid_cache
        self.assertIsNot(wider_grid, shifted_grid)

        taller_frame = np.zeros((104, 112, 3), dtype=np.uint8)
        observation.observe(
            taller_frame,
            roi,
            coordinate,
            FrameContext(4, 4.0 / 30.0, taller_frame),
        )
        self.assertIsNot(observation._sample_grid_cache, wider_grid)

    def test_annular_fire_samples_only_requested_pixels_and_preserves_theta(self) -> None:
        height, width = 240, 320
        frame = np.empty((height, width, 3), dtype=np.uint8)
        frame[:] = (30, 25, 20)
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
        angles = np.linspace(0.0, 2.0 * np.pi, observation.n_angles, endpoint=False)
        radii = np.linspace(roi.inner_radius, roi.outer_radius, observation.n_radii)
        ix = np.rint(center[0] + radii[:, np.newaxis] * np.cos(angles)[np.newaxis, :]).astype(np.intp)
        iy = np.rint(center[1] - radii[:, np.newaxis] * np.sin(angles)[np.newaxis, :]).astype(np.intp)
        frame[iy[:, 10:18], ix[:, 10:18]] = (180, 80, 10)

        result = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))
        expected_samples = observation_module._fire_response(frame[iy, ix])
        expected_signal = np.mean(expected_samples, axis=0)
        expected_signal = observation._smooth_circular(expected_signal, observation.smoothing)
        expected_signal -= expected_signal.min()
        expected_signal /= expected_signal.max()

        np.testing.assert_array_equal(result.debug_layers["polar_samples"], expected_samples)
        np.testing.assert_allclose(result.debug_layers["theta_signal"], expected_signal, rtol=0.0, atol=1e-12)
        self.assertIsNone(result.response_map)
        self.assertEqual(result.debug_layers["polar_samples"].shape, (8, 72))
        self.assertEqual(result.debug_layers["theta_signal"].shape, (72,))
        self.assertEqual(len(result.candidates), 1)

        outside_distractor = frame.copy()
        outside_distractor[0, 0] = (0, 0, 255)
        distracted = observation.observe(
            outside_distractor,
            roi,
            coordinate,
            FrameContext(0, 0.0, outside_distractor),
        )

        np.testing.assert_array_equal(
            distracted.debug_layers["polar_samples"],
            result.debug_layers["polar_samples"],
        )
        np.testing.assert_array_equal(
            distracted.debug_layers["theta_signal"],
            result.debug_layers["theta_signal"],
        )
        self.assertEqual(len(distracted.candidates), len(result.candidates))
        self.assertEqual(distracted.candidates[0].state, result.candidates[0].state)
        self.assertEqual(distracted.candidates[0].score, result.candidates[0].score)
        self.assertEqual(distracted.candidates[0].image_point, result.candidates[0].image_point)

    def test_annular_linear_uint8_sampling_matches_grid_gather_for_both_responses(self) -> None:
        rng = np.random.default_rng(20260715)
        bgr = rng.integers(0, 256, size=(73, 91, 3), dtype=np.uint8)
        frame = bgr[:, :, ::-1]
        iy = rng.integers(0, frame.shape[0], size=(9, 37), dtype=np.intp)
        ix = rng.integers(0, frame.shape[1], size=(9, 37), dtype=np.intp)
        linear_indices = iy * frame.shape[1] + ix

        for response_kind in ("fire", "brightness"):
            previous = observation_module._annular_sample_response(frame, iy, ix, response_kind)
            current = observation_module._annular_sample_response(
                frame,
                iy,
                ix,
                response_kind,
                linear_indices=linear_indices,
            )

            np.testing.assert_array_equal(current, previous)

    def test_annular_linear_sampling_falls_back_for_spatially_noncontiguous_frames(self) -> None:
        rng = np.random.default_rng(20260715)
        source = rng.integers(0, 256, size=(80, 96, 3), dtype=np.uint8)
        frame = source[::2, ::2]
        iy = np.asarray([[1, 5, 9], [12, 20, 30]], dtype=np.intp)
        ix = np.asarray([[2, 7, 11], [18, 26, 40]], dtype=np.intp)
        linear_indices = iy * frame.shape[1] + ix

        previous = observation_module._annular_sample_response(frame, iy, ix, "fire")
        current = observation_module._annular_sample_response(
            frame,
            iy,
            ix,
            "fire",
            linear_indices=linear_indices,
        )

        np.testing.assert_array_equal(current, previous)

    def test_annular_float_sampling_preserves_whole_frame_scaling_decision(self) -> None:
        frame = np.full((20, 24, 3), 0.5, dtype=np.float32)
        frame[0, 0] = 255.0
        iy = np.asarray([[10, 11], [12, 13]], dtype=np.intp)
        ix = np.asarray([[10, 11], [12, 13]], dtype=np.intp)

        sampled = observation_module._annular_sample_response(frame, iy, ix, "brightness")

        expected = observation_module._intensity(frame)[iy, ix]
        np.testing.assert_array_equal(sampled, expected)

    def test_pipeline_discards_heavy_debug_arrays_outside_history_limit(self) -> None:
        frames = [red_dot_frame((80, 90, 3), 20 + i * 3, 30) for i in range(6)]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)
        pipeline.debug_history_limit = 2

        results = pipeline.run(frames, fps=30.0)

        self.assertTrue(all(result.debug["response_map"] is None for result in results[:-2]))
        self.assertTrue(all(isinstance(result.debug["response_map"], np.ndarray) for result in results[-2:]))

    def test_pipeline_bounds_heavy_debug_history_by_bytes_and_keeps_newest(self) -> None:
        frames = [red_dot_frame((80, 90, 3), 20 + i * 3, 30) for i in range(6)]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)
        response_bytes = 80 * 90 * np.dtype(np.float32).itemsize
        pipeline.debug_history_limit = 4
        pipeline.debug_history_max_bytes = response_bytes * 2

        results = pipeline.run(frames, fps=30.0)

        self.assertTrue(all(result.debug["response_map"] is None for result in results[:-2]))
        self.assertTrue(all(isinstance(result.debug["response_map"], np.ndarray) for result in results[-2:]))
        self.assertEqual(pipeline.debug_history_usage(), (response_bytes * 2, 2))

        pipeline.debug_history_max_bytes = 1
        pipeline.rebuild_debug_history()
        self.assertTrue(all(result.debug["response_map"] is None for result in results[:-1]))
        self.assertIsInstance(results[-1].debug["response_map"], np.ndarray)
        self.assertEqual(pipeline.debug_history_usage(), (response_bytes, 1))

    def test_large_debug_history_updates_and_usage_are_incremental(self) -> None:
        frame = red_dot_frame((80, 90, 3), 45, 40)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 90, 80), tolerance=0.08)
        pipeline.debug_history_limit = 128
        pipeline.debug_history_max_bytes = 128 * frame.shape[0] * frame.shape[1] * 4
        pipeline.reset()
        original = pipeline._heavy_debug_array_bytes

        with patch.object(pipeline, "_heavy_debug_array_bytes", wraps=original) as byte_counter:
            for frame_index in range(96):
                pipeline.process_frame(
                    frame,
                    frame_index,
                    frame_index / 30.0,
                    compact_response_map=True,
                )
                retained_bytes, retained_frames = pipeline.debug_history_usage()
                self.assertGreater(retained_bytes, 0)
                self.assertEqual(retained_frames, frame_index + 1)

        self.assertEqual(byte_counter.call_count, 96)

    def test_incremental_debug_history_preserves_reverse_greedy_byte_selection(self) -> None:
        pipeline = color_marker_preset()
        pipeline.debug_history_limit = 12
        pipeline.debug_history_max_bytes = 163
        pipeline.reset()

        for frame_index, byte_count in enumerate((40, 49, 32, 36, 49)):
            response = np.ones((byte_count,), dtype=np.uint8)
            pipeline._append_result(
                TrackerResult(
                    frame_index=frame_index,
                    time_s=frame_index / 30.0,
                    state={"x": float(frame_index), "y": 0.0},
                    filtered_state={"x": float(frame_index), "y": 0.0},
                    confidence=1.0,
                    status="ok",
                    debug={"response_map": response, "debug_layers": {"alias": response}},
                )
            )

        retained_sizes = [
            result.debug["response_map"].nbytes
            if isinstance(result.debug.get("response_map"), np.ndarray)
            else 0
            for result in pipeline.results
        ]
        self.assertEqual(retained_sizes, [40, 0, 32, 36, 49])
        self.assertEqual(pipeline.debug_history_usage(), (157, 4))

    def test_template_observation_handles_roi_outside_valid_window_range(self) -> None:
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        template = np.arange(64, dtype=np.uint8).reshape(8, 8)
        observation = TemplateObservation(template=template, min_score=0.8)
        roi = RectangularROI(30, 30, 2, 2)

        result = observation.observe(frame, roi, ImageCoordinate(), FrameContext(0, 0.0, frame))

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.response_map.shape, frame.shape[:2])
        self.assertEqual(float(result.response_map.max()), 0.0)

    def test_template_observation_vectorized_match_finds_inserted_patch(self) -> None:
        rng = np.random.default_rng(8)
        template = rng.integers(0, 255, size=(7, 9), dtype=np.uint8)
        frame = np.zeros((42, 48), dtype=np.uint8)
        top, left = 13, 17
        frame[top : top + template.shape[0], left : left + template.shape[1]] = template
        observation = TemplateObservation(template=template, min_score=0.95)
        roi = RectangularROI(0, 0, frame.shape[1], frame.shape[0])
        coordinate = ImageCoordinate()

        result = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].image_point, (left + template.shape[1] // 2, top + template.shape[0] // 2))
        self.assertEqual(float(result.response_map[0, 0]), 0.0)

    def test_template_uint8_roi_window_matches_full_frame_preprocessing(self) -> None:
        rng = np.random.default_rng(20260714)
        frame = rng.integers(0, 80, size=(80, 110, 3), dtype=np.uint8)
        template = rng.integers(0, 256, size=(7, 9, 3), dtype=np.uint8)
        top, left = 43, 72
        frame[top : top + template.shape[0], left : left + template.shape[1]] = template
        roi = RectangularROI(60.0, 35.0, 35.0, 25.0)
        observation = TemplateObservation(template=template, min_score=0.9)

        image = observation_module._intensity(frame)
        template_image = observation_module._intensity(template)
        template_norm = template_image - template_image.mean()
        template_energy = float(
            np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
        )
        x0, y0, x1, y1 = roi.bounds()
        x1 = min(frame.shape[1] - template.shape[1], x1)
        y1 = min(frame.shape[0] - template.shape[0], y1)
        region = image[y0 : y1 + template.shape[0], x0 : x1 + template.shape[1]]
        scores = observation_module._template_scores_numpy(region, template_norm, template_energy)
        center_y = np.arange(y0, y1 + 1, dtype=np.intp) + template.shape[0] // 2
        center_x = np.arange(x0, x1 + 1, dtype=np.intp) + template.shape[1] // 2
        valid = roi.mask(frame.shape)[np.ix_(center_y, center_x)]
        expected = np.zeros(frame.shape[:2], dtype=np.float32)
        expected[np.ix_(center_y, center_x)] = np.where(valid, scores, 0.0)

        original_loader = observation_module._load_template_cv2
        try:
            observation_module._load_template_cv2 = lambda: None
            result = observation.observe(frame, roi, ImageCoordinate(), FrameContext(0, 0.0, frame))
        finally:
            observation_module._load_template_cv2 = original_loader

        np.testing.assert_array_equal(result.response_map, expected)
        self.assertEqual(result.candidates[0].image_point, (left + 4, top + 3))
        self.assertTrue(np.all(result.response_map[:35] == 0.0))

        original_loader = observation_module._load_template_cv2
        try:
            observation_module._load_template_cv2 = lambda: None
            compact = observation.observe(
                frame,
                roi,
                ImageCoordinate(),
                FrameContext(0, 0.0, frame, compact_response_map=True),
            )
        finally:
            observation_module._load_template_cv2 = original_loader
        self.assertIsNotNone(compact.response_origin)
        self.assertEqual(compact.response_frame_shape, frame.shape[:2])
        x0, y0 = compact.response_origin
        height, width = compact.response_map.shape
        np.testing.assert_array_equal(
            compact.response_map,
            expected[y0 : y0 + height, x0 : x0 + width],
        )
        self.assertEqual(compact.candidates[0].image_point, result.candidates[0].image_point)

    def test_template_preprocessing_cache_is_read_only_and_detects_in_place_edits(self) -> None:
        rng = np.random.default_rng(2026071403)
        template = rng.integers(0, 256, size=(9, 11, 3), dtype=np.uint8)
        observation = TemplateObservation(template=template.copy(), min_score=0.9)

        first = observation._cached_template_preprocessing()
        first_snapshot = observation._template_cache_source
        second = observation._cached_template_preprocessing()

        self.assertIs(second, first)
        self.assertIs(observation._template_cache_source, first_snapshot)
        self.assertTrue(all(not values.flags.writeable for values in (first_snapshot, *first[:2])))

        observation.template[2, 3, 0] ^= np.uint8(255)
        changed = observation._cached_template_preprocessing()

        self.assertIsNot(changed, first)
        self.assertIsNot(observation._template_cache_source, first_snapshot)
        self.assertFalse(np.array_equal(changed[0], first[0]))
        self.assertTrue(all(not values.flags.writeable for values in (observation._template_cache_source, *changed[:2])))

        observation.template = np.pad(observation.template, ((0, 1), (0, 0), (0, 0)))
        reshaped = observation._cached_template_preprocessing()
        self.assertEqual(reshaped[0].shape, (10, 11))
        self.assertIsNot(reshaped, changed)

    def test_template_numpy_fft_cache_reuses_kernel_and_invalidates_with_template(self) -> None:
        rng = np.random.default_rng(2026071404)
        template = rng.random((17, 19), dtype=np.float32)
        region = rng.random((96, 112), dtype=np.float32)
        observation = TemplateObservation(template=template.copy(), min_score=0.9)
        _template, template_norm, template_energy = observation._cached_template_preprocessing()

        first_scores = observation._template_scores_numpy_cached(region, template_norm, template_energy)
        first_spectrum = observation._template_fft_cache
        second_scores = observation._template_scores_numpy_cached(region, template_norm, template_energy)

        self.assertIsNotNone(first_spectrum)
        self.assertIs(observation._template_fft_cache, first_spectrum)
        self.assertFalse(first_spectrum.flags.writeable)
        np.testing.assert_array_equal(second_scores, first_scores)

        observation.template[0, 0] = np.float32(1.0 - observation.template[0, 0])
        _template, changed_norm, changed_energy = observation._cached_template_preprocessing()
        observation._template_scores_numpy_cached(region, changed_norm, changed_energy)

        self.assertIsNot(observation._template_fft_cache, first_spectrum)
        self.assertFalse(observation._template_fft_cache.flags.writeable)

    def test_template_compact_response_places_even_template_search_grid_exactly(self) -> None:
        rng = np.random.default_rng(2026071402)
        template = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
        frame = np.zeros((54, 72, 3), dtype=np.uint8)
        top, left = 23, 31
        frame[top : top + template.shape[0], left : left + template.shape[1]] = template
        roi = RectangularROI(18.0, 12.0, 40.0, 32.0)
        observation = TemplateObservation(template=template, min_score=0.9)
        coordinate = ImageCoordinate()

        full = observation.observe(frame, roi, coordinate, FrameContext(0, 0.0, frame))
        compact = observation.observe(
            frame,
            roi,
            coordinate,
            FrameContext(0, 0.0, frame, compact_response_map=True),
        )

        reconstructed = np.zeros(frame.shape[:2], dtype=compact.response_map.dtype)
        x0, y0 = compact.response_origin
        height, width = compact.response_map.shape
        reconstructed[y0 : y0 + height, x0 : x0 + width] = compact.response_map
        np.testing.assert_array_equal(reconstructed, full.response_map)
        self.assertEqual(compact.candidates[0].image_point, full.candidates[0].image_point)

    def test_template_score_backends_match_naive_zero_mean_ncc(self) -> None:
        rng = np.random.default_rng(27)
        region = rng.random((12, 14), dtype=np.float32)
        template = region[4:8, 6:11].copy()
        template_norm = template - template.mean()
        template_energy = float(
            np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
        )
        expected = np.empty((9, 10), dtype=np.float64)
        for y in range(expected.shape[0]):
            for x in range(expected.shape[1]):
                patch = region[y : y + 4, x : x + 5].astype(np.float64)
                patch_norm = patch - patch.mean()
                patch_energy = float(np.sqrt(np.sum(patch_norm**2)) + 1e-12)
                numerator = float(np.sum(patch * template_norm))
                expected[y, x] = np.clip(
                    (numerator / (patch_energy * template_energy) + 1.0) * 0.5,
                    0.0,
                    1.0,
                )

        numpy_scores = observation_module._template_scores_numpy(
            region,
            template_norm,
            template_energy,
            workspace_bytes=128,
        )
        np.testing.assert_allclose(numpy_scores, expected, atol=1e-6)

        cv2 = observation_module._load_template_cv2()
        if cv2 is not None:
            cv_scores = observation_module._template_scores_cv2(region, template, cv2=cv2)
            np.testing.assert_allclose(cv_scores, expected, atol=1e-5)
            self.assertEqual(np.unravel_index(np.argmax(cv_scores), cv_scores.shape), (4, 6))

    def test_template_numpy_fft_matches_direct_across_bounded_chunks(self) -> None:
        rng = np.random.default_rng(2718)
        region = rng.random((52, 68), dtype=np.float32)
        template = region[17:26, 31:42].copy()
        template_norm = template - template.mean()
        template_energy = float(
            np.sqrt(np.dot(template_norm.reshape(-1).astype(np.float64), template_norm.reshape(-1)))
        )
        chunk_rows, _fft_shape = observation_module._template_fft_chunk_shape(
            region.shape,
            template.shape,
            44,
            58,
            64 * 1024,
        )
        self.assertLess(chunk_rows, 44)

        expected = observation_module._template_scores_numpy_direct(
            region,
            template_norm,
            template_energy,
            workspace_bytes=64 * 1024,
        )
        actual = observation_module._template_scores_numpy_fft(
            region,
            template_norm,
            template_energy,
            workspace_bytes=64 * 1024,
        )

        np.testing.assert_allclose(actual, expected, atol=2e-6)
        self.assertEqual(np.unravel_index(np.argmax(actual), actual.shape), (17, 31))

    def test_template_ncc_treats_constant_search_patches_as_neutral(self) -> None:
        region = np.full((20, 24), 0.75, dtype=np.float32)
        template = np.arange(35, dtype=np.float32).reshape(5, 7) / 35.0
        template_norm = template - template.mean()
        template_energy = float(np.linalg.norm(template_norm.astype(np.float64)))

        direct = observation_module._template_scores_numpy_direct(
            region,
            template_norm,
            template_energy,
            workspace_bytes=1024 * 1024,
        )
        fft = observation_module._template_scores_numpy_fft(
            region,
            template_norm,
            template_energy,
            workspace_bytes=64 * 1024,
        )

        np.testing.assert_array_equal(direct, np.full(direct.shape, 0.5, dtype=np.float32))
        np.testing.assert_array_equal(fft, np.full(fft.shape, 0.5, dtype=np.float32))
        cv2 = observation_module._load_template_cv2()
        if cv2 is not None:
            cv_scores = observation_module._template_scores_cv2(region, template, cv2=cv2)
            np.testing.assert_array_equal(cv_scores, np.full(cv_scores.shape, 0.5, dtype=np.float32))

    def test_observation_backend_info_reports_active_compute_path(self) -> None:
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 64, 48), tolerance=0.08)
        component_info = observation_module.observation_backend_info(pipeline.observation_model)
        self.assertIn(component_info.label, {"OpenCV components", "NumPy components"})
        self.assertIn(component_info.state, {"accelerated", "fallback"})
        self.assertTrue(component_info.detail)

        original_loader = observation_module._load_template_cv2
        try:
            observation_module._load_template_cv2 = lambda: None
            template_info = observation_module.observation_backend_info(
                TemplateObservation(np.arange(12, dtype=np.float32).reshape(3, 4))
            )
        finally:
            observation_module._load_template_cv2 = original_loader
        self.assertEqual(template_info.label, "NumPy cached NCC")
        self.assertEqual(template_info.state, "optimized")
        self.assertIn("block FFT", template_info.detail)
        self.assertIn("cached kernel", template_info.detail)

        edge_info = observation_module.observation_backend_info(EdgeFrontObservation(axis="x"))
        self.assertEqual(edge_info.label, "NumPy ROI gradient")
        self.assertEqual(edge_info.state, "optimized")
        self.assertIn("applied ROI", edge_info.detail)

    def test_template_observation_preserves_constant_template_neutral_score(self) -> None:
        frame = np.arange(12 * 14, dtype=np.uint8).reshape(12, 14)
        template = np.full((3, 4), 127, dtype=np.uint8)
        observation = TemplateObservation(template=template, min_score=0.51)

        result = observation.observe(
            frame,
            RectangularROI(0, 0, 14, 12),
            ImageCoordinate(),
            FrameContext(0, 0.0, frame),
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(float(result.response_map.max()), 0.5)
        self.assertEqual(int(np.count_nonzero(result.response_map == 0.5)), 110)

    def test_template_observation_falls_back_after_opencv_runtime_failure(self) -> None:
        rng = np.random.default_rng(31)
        frame = rng.integers(0, 255, size=(20, 24), dtype=np.uint8)
        top, left = 7, 9
        template = frame[top : top + 5, left : left + 6].copy()
        observation = TemplateObservation(template=template, min_score=0.99)
        original_loader = observation_module._load_template_cv2
        original_runtime_error = observation_module._TEMPLATE_CV2_RUNTIME_ERROR
        try:
            observation_module._load_template_cv2 = lambda: object()
            observation_module._TEMPLATE_CV2_RUNTIME_ERROR = None
            result = observation.observe(
                frame,
                RectangularROI(0, 0, 24, 20),
                ImageCoordinate(),
                FrameContext(0, 0.0, frame),
            )
            self.assertIsNotNone(observation_module._TEMPLATE_CV2_RUNTIME_ERROR)
            observation_module._load_template_cv2 = original_loader
            self.assertIsNone(observation_module._load_template_cv2())
        finally:
            observation_module._load_template_cv2 = original_loader
            observation_module._TEMPLATE_CV2_RUNTIME_ERROR = original_runtime_error

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].image_point, (left + 3, top + 2))

    def test_alpha_beta_filter_preserves_velocity_for_zero_dt_update(self) -> None:
        tracker_filter = AlphaBetaFilter(keys=("x_px",))
        previous = TrackerResult(
            frame_index=2,
            time_s=0.2,
            state={"x_px": 4.0},
            filtered_state={"x_px": 4.0},
            confidence=1.0,
            status="ok",
            debug={"filter": {"velocity": {"v_x_px": 12.0}}},
        )
        tracker_filter.prime(previous)

        update = tracker_filter.update({"x_px": 5.0}, previous, dt=0.0)

        self.assertEqual(update.state["x_px"], 5.0)
        self.assertEqual(update.debug["velocity"]["v_x_px"], 12.0)


if __name__ == "__main__":
    unittest.main()
