from __future__ import annotations

import unittest

import numpy as np

from neo_tracker.config import MAX_ROI_POINTS, apply_pipeline_config, validate_roi_config
from neo_tracker.coordinates import PathCoordinate
from neo_tracker.filters import ExponentialSmoothingFilter
from neo_tracker.motion import PathMotionPrior
from neo_tracker.observations import AnnularRadialFrontObservation, BrightnessPeakObservation, TemplateObservation
from neo_tracker.optimizers import GridSearchOptimizer
from neo_tracker.presets import color_marker_preset, default_preset_registry, wavefront_preset
from neo_tracker.roi import PolygonROI, RectangularROI
from neo_tracker.states import FrontState


class PipelineConfigTests(unittest.TestCase):
    def test_builtin_preset_configs_roundtrip(self) -> None:
        for key, descriptor in default_preset_registry().items():
            with self.subTest(preset=key):
                original_config = descriptor.factory().to_config()
                restored = descriptor.factory()
                apply_pipeline_config(restored, original_config)
                self.assertEqual(restored.to_config(), original_config)

    def test_apply_pipeline_config_restores_built_in_modules(self) -> None:
        pipeline = color_marker_preset()
        apply_pipeline_config(
            pipeline,
            {
                "name": "Restored Pipeline",
                "roi": {
                    "type": "polygon",
                    "points": [[1.0, 2.0], [50.0, 3.0], [45.0, 40.0], [4.0, 38.0]],
                },
                "coordinate_model": {
                    "type": "path",
                    "polyline": [[5.0, 6.0], [20.0, 16.0], [40.0, 15.0]],
                    "unit_per_pixel": 0.25,
                    "unit": "cm",
                },
                "observation_model": {
                    "type": "brightness_peak",
                    "polarity": "dark",
                    "min_response": 0.42,
                    "percentile_floor": 72.0,
                },
                "state_model": {"type": "front", "key": "s", "unit": "cm"},
                "motion_model": {"type": "path_motion", "key": "s", "max_speed": 42.0, "margin": 3.5},
                "tracker_filter": {"type": "exponential_smoothing", "alpha": 0.33, "keys": ["s"]},
                "optimizer": {"type": "grid_search", "samples_per_axis": 9, "maximize": False},
                "min_confidence": 0.27,
                "metadata": {"restored": True},
            },
        )

        self.assertEqual(pipeline.name, "Restored Pipeline")
        self.assertIsInstance(pipeline.roi, PolygonROI)
        self.assertEqual(pipeline.roi.points[1], (50.0, 3.0))
        self.assertIsInstance(pipeline.coordinate_model, PathCoordinate)
        self.assertAlmostEqual(pipeline.coordinate_model.unit_per_pixel, 0.25)
        self.assertIsInstance(pipeline.observation_model, BrightnessPeakObservation)
        self.assertEqual(pipeline.observation_model.polarity, "dark")
        self.assertAlmostEqual(pipeline.observation_model.min_response, 0.42)
        self.assertIsInstance(pipeline.state_model, FrontState)
        self.assertEqual(pipeline.state_model.unit, "cm")
        self.assertIsInstance(pipeline.motion_model, PathMotionPrior)
        self.assertAlmostEqual(pipeline.motion_model.max_speed, 42.0)
        self.assertIsInstance(pipeline.tracker_filter, ExponentialSmoothingFilter)
        self.assertEqual(pipeline.tracker_filter.keys, ("s",))
        self.assertIsInstance(pipeline.optimizer, GridSearchOptimizer)
        self.assertEqual(pipeline.optimizer.samples_per_axis, 9)
        self.assertFalse(pipeline.optimizer.maximize)
        self.assertAlmostEqual(pipeline.min_confidence, 0.27)
        self.assertTrue(pipeline.metadata["restored"])

    def test_invalid_or_missing_config_preserves_existing_module(self) -> None:
        pipeline = color_marker_preset()
        original_roi = pipeline.roi
        original_optimizer = pipeline.optimizer
        self.assertIsInstance(original_roi, RectangularROI)

        apply_pipeline_config(
            pipeline,
            {
                "roi": {"type": "circle", "center": [10.0, 10.0]},
                "coordinate_model": {"type": "not_real"},
            },
        )

        self.assertIs(pipeline.roi, original_roi)
        self.assertIsNotNone(pipeline.optimizer)
        self.assertIs(pipeline.optimizer, original_optimizer)

    def test_invalid_roi_geometry_preserves_existing_module(self) -> None:
        invalid_configs = [
            {"type": "rectangle", "x": 0.0, "y": 0.0, "width": 0.0, "height": 20.0},
            {"type": "circle", "center": [10.0, 10.0], "radius": float("nan")},
            {"type": "annulus", "center": [10.0, 10.0], "inner_radius": 20.0, "outer_radius": 10.0},
            {"type": "polygon", "points": [[0.0, 0.0], [1.0, float("inf")], [2.0, 0.0]]},
            {"type": "curve_band", "polyline": [[0.0, 0.0], [2.0, 2.0]], "half_width": -1.0},
        ]
        for config in invalid_configs:
            with self.subTest(roi_type=config["type"]):
                pipeline = color_marker_preset()
                original_roi = pipeline.roi

                self.assertIsNotNone(validate_roi_config(config))
                apply_pipeline_config(pipeline, {"roi": config})

                self.assertIs(pipeline.roi, original_roi)

    def test_validate_roi_config_enforces_max_roi_points(self) -> None:
        polygon_allowed = {
            "type": "polygon",
            "points": [[float(index), 0.0] for index in range(MAX_ROI_POINTS)],
        }
        polygon_rejected = {
            "type": "polygon",
            "points": [[float(index), 0.0] for index in range(MAX_ROI_POINTS + 1)],
        }
        curve_allowed = {
            "type": "curve_band",
            "polyline": [[float(index), 0.0] for index in range(MAX_ROI_POINTS)],
            "half_width": 1.0,
        }
        curve_rejected = {
            "type": "curve_band",
            "polyline": [[float(index), 0.0] for index in range(MAX_ROI_POINTS + 1)],
            "half_width": 1.0,
        }

        self.assertIsNone(validate_roi_config(polygon_allowed))
        self.assertIsNotNone(validate_roi_config(polygon_rejected))
        self.assertIsNone(validate_roi_config(curve_allowed))
        self.assertIsNotNone(validate_roi_config(curve_rejected))

    def test_invalid_edge_front_axis_preserves_existing_observation(self) -> None:
        pipeline = wavefront_preset()
        original_observation = pipeline.observation_model
        config = pipeline.to_config()
        config["observation_model"]["axis"] = "depth"

        apply_pipeline_config(pipeline, config)

        self.assertIs(pipeline.observation_model, original_observation)

    def test_color_blob_candidate_settings_roundtrip_and_clamp(self) -> None:
        pipeline = color_marker_preset()
        config = pipeline.to_config()
        config["observation_model"]["max_candidates"] = 7
        config["observation_model"]["min_component_area"] = 11

        apply_pipeline_config(pipeline, config)

        observation_config = pipeline.observation_model.to_config()
        self.assertEqual(observation_config["max_candidates"], 7)
        self.assertEqual(observation_config["min_component_area"], 11)

        config["observation_model"]["max_candidates"] = 0
        config["observation_model"]["min_component_area"] = -4
        apply_pipeline_config(pipeline, config)
        observation_config = pipeline.observation_model.to_config()
        self.assertEqual(observation_config["max_candidates"], 1)
        self.assertEqual(observation_config["min_component_area"], 1)

    def test_unsafe_observation_workloads_preserve_existing_model(self) -> None:
        pipeline = color_marker_preset()
        original = pipeline.observation_model
        for observation_config in (
            {
                "type": "annular_radial_front",
                "n_angles": 16_384,
                "n_radii": 4_096,
                "smoothing": 4,
            },
            {
                "type": "template",
                "template": [[0.0] * 4097],
                "min_score": 0.5,
            },
            {
                "type": "color_blob",
                "sample_rgb": [255.0, 0.0, 0.0],
                "max_candidates": 13,
            },
        ):
            with self.subTest(kind=observation_config["type"]):
                apply_pipeline_config(pipeline, {"observation_model": observation_config})
                self.assertIs(pipeline.observation_model, original)

        with self.assertRaisesRegex(ValueError, "sample grid"):
            AnnularRadialFrontObservation(n_angles=16_384, n_radii=4_096, smoothing=0)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            TemplateObservation(template=np.zeros((1, 4097), dtype=np.uint8))

    def test_pipeline_history_and_confidence_limits_preserve_safe_values(self) -> None:
        pipeline = color_marker_preset()
        original = (
            pipeline.min_confidence,
            pipeline.debug_history_limit,
            pipeline.debug_history_max_bytes,
        )
        apply_pipeline_config(
            pipeline,
            {
                "min_confidence": float("nan"),
                "debug_history_limit": 129,
                "debug_history_max_bytes": 256 * 1024 * 1024 + 1,
            },
        )
        self.assertEqual(
            (
                pipeline.min_confidence,
                pipeline.debug_history_limit,
                pipeline.debug_history_max_bytes,
            ),
            original,
        )


if __name__ == "__main__":
    unittest.main()
