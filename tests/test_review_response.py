from __future__ import annotations

import unittest

import numpy as np

from neo_tracker.core import FrameContext, ObservationResult
from neo_tracker.presets import color_marker_preset, wavefront_preset
from neo_tracker.roi import RectangularROI
from neo_tracker.ui.review_response import ReviewResponseService


def red_dot_frame(x: float, shape: tuple[int, int, int] = (48, 64, 3)) -> np.ndarray:
    yy, xx = np.indices(shape[:2])
    frame = np.zeros(shape, dtype=np.uint8)
    frame[(xx - x) ** 2 + (yy - 24.0) ** 2 <= 3.0**2, 0] = 255
    return frame


class MutatingObservation:
    name = "mutating_test"

    def __init__(self) -> None:
        self.calls = 0

    def observe(self, frame, roi, coordinate_model, context: FrameContext) -> ObservationResult:
        self.calls += 1
        return ObservationResult(candidates=[], response_map=np.ones(frame.shape[:2], dtype=np.float32))

    def to_config(self) -> dict[str, object]:
        return {"type": self.name}


class ReviewResponseServiceTests(unittest.TestCase):
    def test_compact_tracking_response_expands_once_and_then_uses_review_cache(self) -> None:
        frame = red_dot_frame(24.0)
        roi = RectangularROI(12, 12, 28, 24)
        pipeline = color_marker_preset(roi=roi, tolerance=0.08)
        expected = pipeline.observation_model.observe(
            frame,
            pipeline.roi,
            pipeline.coordinate_model,
            FrameContext(0, 0.0, frame),
        ).response_map
        result = pipeline.process_frame(frame, 0, 0.0, compact_response_map=True)
        stored = result.debug["response_map"]

        self.assertIsInstance(stored, np.ndarray)
        self.assertLess(stored.nbytes, expected.nbytes)
        self.assertEqual(result.debug["response_frame_shape"], frame.shape[:2])
        self.assertEqual(pipeline.debug_history_usage(), (stored.nbytes, 1))

        service = ReviewResponseService(max_entries=2)
        owner = object()
        expanded = service.response_for(owner, pipeline, result, frame)
        self.assertEqual(expanded.source, "stored")
        self.assertFalse(expanded.response_map.flags.writeable)
        np.testing.assert_array_equal(expanded.response_map, expected)

        cached = service.response_for(owner, pipeline, result, frame)
        self.assertEqual(cached.source, "cached")
        self.assertIs(cached.response_map, expanded.response_map)

    def test_edge_front_compact_response_expands_to_exact_review_evidence(self) -> None:
        frame = np.zeros((80, 120, 3), dtype=np.uint8)
        frame[:, 76:, :] = 96
        roi = RectangularROI(60, 10, 40, 60)
        pipeline = wavefront_preset(roi=roi, axis="x")
        expected = pipeline.observation_model.observe(
            frame,
            pipeline.roi,
            pipeline.coordinate_model,
            FrameContext(0, 0.0, frame),
        ).response_map
        result = pipeline.process_frame(frame, 0, 0.0, compact_response_map=True)

        self.assertIsInstance(result.debug["response_map"], np.ndarray)
        self.assertLess(result.debug["response_map"].nbytes, expected.nbytes)
        expanded = ReviewResponseService().response_for(object(), pipeline, result, frame)

        self.assertEqual(expanded.source, "stored")
        self.assertFalse(expanded.response_map.flags.writeable)
        np.testing.assert_array_equal(expanded.response_map, expected)

    def test_invalid_compact_response_placement_recomputes_instead_of_using_local_shape(self) -> None:
        frame = red_dot_frame(24.0)
        pipeline = color_marker_preset(
            roi=RectangularROI(12, 12, 28, 24),
            tolerance=0.08,
        )
        result = pipeline.process_frame(frame, 0, 0.0, compact_response_map=True)
        result.debug["response_frame_shape"] = (1, 1)

        response = ReviewResponseService().response_for(object(), pipeline, result, frame)

        self.assertEqual(response.source, "recomputed")
        self.assertEqual(response.response_map.shape, frame.shape[:2])
        self.assertFalse(response.response_map.flags.writeable)

    def test_old_response_is_recomputed_cached_and_lru_bounded(self) -> None:
        frames = [red_dot_frame(12.0 + index * 4.0) for index in range(4)]
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 64, 48), tolerance=0.08)
        pipeline.debug_history_limit = 1
        pipeline.run(frames, fps=20.0)
        owner = object()
        service = ReviewResponseService(max_entries=2)

        first = service.response_for(owner, pipeline, pipeline.results[0], frames[0])
        self.assertEqual(first.source, "recomputed")
        self.assertIsNotNone(first.response_map)
        self.assertFalse(first.response_map.flags.writeable)
        self.assertIsNone(pipeline.results[0].debug["response_map"])

        cached = service.response_for(owner, pipeline, pipeline.results[0], frames[0])
        self.assertEqual(cached.source, "cached")
        self.assertIs(cached.response_map, first.response_map)

        service.response_for(owner, pipeline, pipeline.results[1], frames[1])
        service.response_for(owner, pipeline, pipeline.results[2], frames[2])
        self.assertEqual(service.cached_frames(owner), [1, 2])
        self.assertEqual(len(service), 2)

        stored = service.response_for(owner, pipeline, pipeline.results[3], frames[3])
        self.assertEqual(stored.source, "stored")
        self.assertEqual(service.cached_frames(owner), [1, 2])
        service.invalidate(owner, first_frame=2)
        self.assertEqual(service.cached_frames(owner), [1])
        service.invalidate(owner)
        self.assertEqual(len(service), 0)

    def test_recomputation_uses_observation_copy_and_preserves_pipeline_state(self) -> None:
        frame = red_dot_frame(20.0)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 64, 48), tolerance=0.08)
        pipeline.debug_history_limit = 0
        pipeline.run([frame], fps=20.0)
        observation = MutatingObservation()
        pipeline.observation_model = observation  # type: ignore[assignment]
        result_ids = [id(result) for result in pipeline.results]
        filter_velocities = dict(getattr(pipeline.tracker_filter, "velocities", {}))

        response = ReviewResponseService().response_for(object(), pipeline, pipeline.results[0], frame)

        self.assertEqual(response.source, "recomputed")
        self.assertEqual(observation.calls, 0)
        self.assertEqual([id(result) for result in pipeline.results], result_ids)
        self.assertEqual(dict(getattr(pipeline.tracker_filter, "velocities", {})), filter_velocities)
        self.assertIsNone(pipeline.results[0].debug["response_map"])


if __name__ == "__main__":
    unittest.main()
