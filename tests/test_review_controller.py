from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from neo_tracker.core import ObservationCandidate, TrackerResult
from neo_tracker.presets import default_preset_registry
from neo_tracker.ui.review_controller import ReviewController


def tracking_result(
    frame_index: int,
    x: float,
    y: float,
    *,
    confidence: float = 0.9,
    status: str = "ok",
) -> TrackerResult:
    point = (x, y)
    return TrackerResult(
        frame_index=frame_index,
        time_s=frame_index / 10.0,
        state={"x_px": x, "y_px": y},
        filtered_state={"x_px": x, "y_px": y},
        confidence=confidence,
        status=status,
        observation=ObservationCandidate(
            state={"x_px": x, "y_px": y},
            score=confidence,
            image_point=point,
        ),
        prediction={"x_px": x + 1.0, "y_px": y},
    )


class ReviewControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ReviewController(
            clock=lambda: datetime(2026, 7, 13, 8, 30, tzinfo=timezone.utc)
        )

    def test_table_and_selection_include_units_status_and_tone(self) -> None:
        results = [
            tracking_result(0, 10.0, 20.0),
            tracking_result(1, 12.0, 21.0, confidence=0.3, status="predicted"),
        ]

        table = self.controller.table(results, {"x_px": "px", "y_px": "px"}, "Source ended early.")
        selection = self.controller.selection(results, 1, {"x_px": "px", "y_px": "px"})

        self.assertEqual(table.headers, ("Frame", "Time", "x_px (px)", "y_px (px)", "Conf", "Status"))
        self.assertEqual(table.rows[1].tone, "attention")
        self.assertIn("Run note: Source ended early.", table.summary)
        self.assertIsNotNone(selection)
        self.assertIn("Frame 1", selection.text)
        self.assertIn("confidence 0.300", selection.text)
        self.assertIn("x_px 12 px", selection.detail)
        self.assertEqual(selection.tone, "attention")
        self.assertEqual(self.controller.preferred_result_index(results, None, 1), 1)

    def test_manual_correction_and_lost_edit_have_reproducible_history(self) -> None:
        pipeline = default_preset_registry()["color_marker"].factory()
        response = np.ones((2, 2), dtype=float)
        pipeline.results = [tracking_result(0, 10.0, 20.0), tracking_result(1, 12.0, 21.0)]
        pipeline.results[1].debug = {
            "response_map": response,
            "candidates": [{"image_point": [12.0, 21.0], "selected": True, "score": 0.9}],
        }
        history: list[dict[str, object]] = []
        original = pipeline.results[1]

        correction = self.controller.manual_correction(pipeline, 1, (30.0, 35.0))
        self.controller.append_edit(history, correction)

        corrected = pipeline.results[1]
        self.assertIsNot(corrected, original)
        self.assertEqual(corrected.status, "manual")
        self.assertEqual(corrected.filtered_state, {"x_px": 30.0, "y_px": 35.0})
        self.assertIn("v_x_px", corrected.debug["filter"]["velocity"])
        self.assertIs(corrected.debug["response_map"], response)
        self.assertEqual(history[0]["time"], "2026-07-13T08:30:00Z")
        self.assertIn("corrected point", self.controller.format_edit_history_entry(history[0]))

        lost = self.controller.mark_lost(pipeline.results, 1)
        self.controller.append_edit(history, lost)

        lost_result = pipeline.results[1]
        self.assertIsNot(lost_result, corrected)
        self.assertEqual(corrected.status, "manual")
        self.assertEqual(lost_result.status, "manual_lost")
        self.assertEqual(lost_result.confidence, 0.0)
        self.assertEqual(self.controller.selection(pipeline.results, 1, {}).tone, "lost")
        self.assertIn("marked lost", self.controller.format_edit_history_entry(history[1]))

    def test_rerun_supersedes_only_current_manual_edits_in_replaced_tail(self) -> None:
        history = [
            {"type": "manual_correction", "frame_index": 20, "details": {}},
            {"type": "mark_lost", "frame_index": 50, "details": {}},
            {
                "type": "manual_correction",
                "frame_index": 60,
                "superseded_at": "2026-07-13T08:00:00Z",
                "superseded_by_rerun_start_frame": 40,
                "details": {},
            },
            {"type": "rerun_after", "frame_index": 20, "details": {"start_frame": 21}},
        ]

        self.assertEqual(self.controller.active_manual_edit_count_from_frame(history, 21), 1)
        changed = self.controller.supersede_manual_edits_from_frame(
            history,
            21,
            superseded_at="2026-07-14T10:00:00Z",
        )

        self.assertEqual(changed, 1)
        self.assertNotIn("superseded_at", history[0])
        self.assertEqual(history[1]["superseded_at"], "2026-07-14T10:00:00Z")
        self.assertEqual(history[1]["superseded_by_rerun_start_frame"], 21)
        self.assertEqual(history[2]["superseded_by_rerun_start_frame"], 40)
        self.assertNotIn("superseded_at", history[3])
        self.assertIn(
            "superseded by rerun from frame 21",
            self.controller.format_edit_history_entry(history[1]),
        )
        self.assertEqual(self.controller.active_manual_edit_count_from_frame(history, 21), 0)

    def test_overlay_uses_filtered_measurement_and_diagnostic_candidates(self) -> None:
        pipeline = default_preset_registry()["color_marker"].factory()
        pipeline.results = [tracking_result(0, 10.0, 20.0), tracking_result(1, 12.0, 21.0)]
        pipeline.results[1].debug["candidates"] = [
            {"image_point": [12.0, 21.0], "combined_score": 0.8, "selected": True}
        ]

        overlay = self.controller.overlay(
            pipeline,
            1,
            show_observation=True,
            show_measurement=True,
            show_candidates=True,
            show_prediction=True,
        )

        self.assertEqual(overlay.trajectory, ((10.0, 20.0), (12.0, 21.0)))
        self.assertEqual(overlay.current_point, (12.0, 21.0))
        self.assertEqual(overlay.observation_point, (12.0, 21.0))
        self.assertEqual(overlay.candidate_points, ((12.0, 21.0, 0.8, True),))
        self.assertEqual(overlay.prediction_point, (13.0, 21.0))
        self.assertEqual(len(overlay.measurements), 2)

    def test_result_lookup_keeps_unsorted_plugin_results_compatible(self) -> None:
        results = [
            tracking_result(1, 11.0, 20.0),
            tracking_result(2, 12.0, 20.0),
            tracking_result(0, 10.0, 20.0),
        ]

        self.assertEqual(self.controller.preferred_result_index(results, None, 0), 2)
        self.assertIs(self.controller.result_for_frame(results, 0), results[2])
        self.assertIsNone(self.controller.preferred_result_index(results, None, 9))
        self.assertIsNone(self.controller.result_for_frame(results, 9))

    def test_overlay_static_geometry_is_cached_and_edit_invalidates_it(self) -> None:
        pipeline = default_preset_registry()["color_marker"].factory()
        pipeline.results = [tracking_result(index, float(index), 20.0) for index in range(20)]

        first = self.controller.overlay(
            pipeline,
            3,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )
        cached = self.controller._overlay_static
        second = self.controller.overlay(
            pipeline,
            19,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )

        self.assertIs(self.controller._overlay_static, cached)
        self.assertEqual(first.trajectory, second.trajectory)
        self.assertEqual(second.current_point, (19.0, 20.0))

        self.controller.mark_lost(pipeline.results, 19)
        self.assertIsNone(self.controller._overlay_static)
        updated = self.controller.overlay(
            pipeline,
            19,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )
        self.assertNotIn((19.0, 20.0), updated.trajectory)

    def test_overlay_cache_updates_one_result_when_pipeline_is_supplied(self) -> None:
        pipeline = default_preset_registry()["color_marker"].factory()
        pipeline.results = [tracking_result(index, float(index), 20.0) for index in range(20)]
        self.controller.overlay(
            pipeline,
            10,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )
        cached = self.controller._overlay_static

        self.controller.mark_lost(pipeline.results, 10, pipeline=pipeline)
        updated_cache = self.controller._overlay_static
        overlay = self.controller.overlay(
            pipeline,
            10,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )

        self.assertIsNotNone(cached)
        self.assertIsNotNone(updated_cache)
        self.assertIs(self.controller._overlay_static, updated_cache)
        self.assertNotIn((10.0, 20.0), overlay.trajectory)
        self.assertNotIn((10.0, 20.0), overlay.measurements)

    def test_manual_correction_updates_cached_overlay_for_first_result(self) -> None:
        pipeline = default_preset_registry()["color_marker"].factory()
        pipeline.results = [tracking_result(index, float(index), 20.0) for index in range(3)]
        self.controller.overlay(
            pipeline,
            0,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )

        self.controller.manual_correction(pipeline, 0, (30.0, 35.0))
        overlay = self.controller.overlay(
            pipeline,
            0,
            show_observation=False,
            show_measurement=True,
            show_candidates=False,
            show_prediction=False,
        )

        self.assertEqual(overlay.current_point, (30.0, 35.0))
        self.assertIn((30.0, 35.0), overlay.trajectory)
        self.assertIn((30.0, 35.0), overlay.measurements)
        self.assertNotIn((0.0, 20.0), overlay.trajectory)
        self.assertNotIn((0.0, 20.0), overlay.measurements)


if __name__ == "__main__":
    unittest.main()
