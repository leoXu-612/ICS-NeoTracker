from __future__ import annotations

import unittest
from dataclasses import replace

from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaIdentity, MediaInfo
from neo_tracker.presets import default_preset_registry
from neo_tracker.project import ProjectTaskSnapshot, TrackingRunRecord
from neo_tracker.ui.project_controller import CalibrationRod, ProjectTaskController


class ProjectTaskControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = default_preset_registry()
        self.probed_paths: list[str] = []

        def offline_probe(path: str) -> MediaInfo:
            self.probed_paths.append(path)
            return MediaInfo(
                fps=30.0,
                frame_count=120,
                width=640,
                height=360,
                duration_s=4.0,
                available=False,
                error="media unavailable",
            )

        self.controller = ProjectTaskController(self.registry, "color_marker", media_probe=offline_probe)

    def test_unknown_pipeline_falls_back_and_media_probe_is_injected(self) -> None:
        task = self.controller.new_task("missing.mp4", "unknown")

        self.assertEqual(task.pipeline_key, "color_marker")
        self.assertEqual(self.probed_paths, ["missing.mp4"])
        self.assertFalse(task.media_info.available)

    def test_preprobed_media_info_avoids_duplicate_source_inspection(self) -> None:
        preprobed = MediaInfo(
            fps=60.0,
            frame_count=300,
            width=1920,
            height=1080,
            duration_s=5.0,
            available=True,
        )

        task = self.controller.new_task(
            "/media/preprobed.mp4",
            "color_marker",
            media_info=preprobed,
        )

        self.assertIs(task.media_info, preprobed)
        self.assertIs(task.saved_media_info, preprobed)
        self.assertEqual(self.probed_paths, [])

    def test_snapshot_roundtrip_preserves_geometry_calibration_and_outcome(self) -> None:
        task = self.controller.new_task("missing.mp4", "color_marker")
        self.assertTrue(
            self.controller.apply_roi_config_to_task(
                task,
                {"type": "rectangle", "x": 10.0, "y": 20.0, "width": 200.0, "height": 100.0},
            )
        )
        self.assertTrue(
            self.controller.apply_calibration_rod_to_task(
                task,
                CalibrationRod(start_px=(0.0, 0.0), end_px=(100.0, 0.0), real_length=50.0, unit="cm"),
            )
        )
        task.preview_frame_index = 17
        task.tracking_outcome = "partial"
        task.tracking_note = "Source ended at frame 18."
        task.run_history = [
            TrackingRunRecord(
                started_at="2026-07-13T12:00:00Z",
                duration_s=1.5,
                mode="full",
                outcome="partial",
                start_frame=0,
                end_frame=17,
                processed_frames=18,
                result_count=18,
                note=task.tracking_note,
                pipeline_config=task.pipeline.to_config(),
            )
        ]

        restored = self.controller.task_from_snapshot(self.controller.snapshot_from_task(task))

        self.assertEqual(restored.preview_frame_index, 17)
        self.assertEqual(restored.roi["width"], 200.0)
        self.assertAlmostEqual(restored.calibration_rod.unit_per_pixel(), 0.5)
        self.assertEqual(restored.pipeline.coordinate_model.to_config()["unit"], "cm")
        self.assertEqual(restored.tracking_outcome, "partial")
        self.assertEqual(restored.tracking_note, "Source ended at frame 18.")
        self.assertEqual(len(restored.run_history), 1)
        self.assertEqual(restored.run_history[0].processed_frames, 18)
        self.assertEqual(restored.run_history[0].pipeline_digest, task.run_history[0].pipeline_digest)

    def test_invalid_snapshot_pipeline_geometry_is_rejected(self) -> None:
        config = self.registry["color_marker"].factory().to_config()
        config["roi"]["width"] = 0.0
        snapshot = ProjectTaskSnapshot(
            media_path=None,
            pipeline_key="color_marker",
            pipeline_config=config,
        )

        with self.assertRaisesRegex(ValueError, r"project pipeline \$\.roi is invalid"):
            self.controller.task_from_snapshot(snapshot)

    def test_invalid_optional_roi_and_calibration_are_ignored(self) -> None:
        snapshot = ProjectTaskSnapshot(
            media_path=None,
            pipeline_key="color_marker",
            roi={"type": "circle", "center": [10.0, 10.0], "radius": float("nan")},
            calibration_rod={
                "start_px": [0.0, 0.0],
                "end_px": [0.0, 0.0],
                "real_length": -5.0,
                "unit": "cm",
            },
        )

        task = self.controller.task_from_snapshot(snapshot)

        self.assertIsNone(task.roi)
        self.assertEqual(task.pipeline.roi.to_config()["type"], "rectangle")
        self.assertIsNone(task.calibration_rod.unit_per_pixel())

    def test_apply_roi_config_rejects_oversized_polygon_without_partial_application(self) -> None:
        task = self.controller.new_task("missing.mp4", "color_marker")
        original_roi = task.roi
        oversized = {
            "type": "polygon",
            "points": [[float(index), 0.0] for index in range(4097)],
        }

        self.assertFalse(self.controller.apply_roi_config_to_task(task, oversized))
        self.assertEqual(task.roi, original_roi)

        allowed = {
            "type": "polygon",
            "points": [[float(index), 0.0] for index in range(4096)],
        }
        self.assertTrue(self.controller.apply_roi_config_to_task(task, allowed))
        self.assertEqual(len(task.roi["points"]), 4096)

    def test_project_load_blocks_same_path_media_drift_until_explicit_relink(self) -> None:
        live = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=1280,
            height=720,
            duration_s=4.0,
            available=True,
        )
        controller = ProjectTaskController(
            self.registry,
            "color_marker",
            media_probe=lambda _path: live,
        )
        snapshot = ProjectTaskSnapshot(
            media_path="/experiments/source.mp4",
            pipeline_key="color_marker",
            media_info={
                "kind": "video",
                "available": True,
                "fps": 20.0,
                "frame_count": 72,
                "width": 640,
                "height": 360,
                "duration_s": 3.6,
            },
            results=[TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")],
            edit_history=[{"event": "manual_point", "frame_index": 0}],
            tracking_outcome="complete",
        )

        task = controller.task_from_snapshot(snapshot)

        self.assertFalse(task.media_info.available)
        self.assertIn("differs from the project snapshot", task.media_info.error)
        self.assertTrue(task.media_identity_requires_review)
        self.assertIsNotNone(task.pending_media_relink)
        candidate, assessment = task.pending_media_relink
        self.assertIs(candidate, live)
        self.assertEqual(assessment.state, "mismatch")
        self.assertTrue(assessment.clear_results)
        self.assertEqual(len(task.pipeline.results), 1)
        self.assertEqual(len(task.edit_history), 1)

        unresolved_snapshot = controller.snapshot_from_task(task)
        self.assertEqual(unresolved_snapshot.media_info["width"], 640)
        self.assertEqual(unresolved_snapshot.media_info["frame_count"], 72)

        controller.relink_media(task, snapshot.media_path or "", candidate, clear_results=True)
        self.assertTrue(task.media_info.available)
        self.assertFalse(task.media_identity_requires_review)
        self.assertIsNone(task.pending_media_relink)
        self.assertEqual(task.pipeline.results, [])
        self.assertEqual(task.edit_history, [])

    def test_project_load_blocks_same_metadata_when_source_digest_differs(self) -> None:
        saved_identity = MediaIdentity("full-sha256-v1", "1" * 64, 4096, 4096)
        live_identity = MediaIdentity("full-sha256-v1", "2" * 64, 4096, 4096)
        live = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=live_identity,
        )
        controller = ProjectTaskController(
            self.registry,
            "color_marker",
            media_probe=lambda _path: live,
        )
        snapshot = ProjectTaskSnapshot(
            media_path="/experiments/source.mp4",
            pipeline_key="color_marker",
            media_info={
                "kind": "video",
                "available": True,
                "fps": 20.0,
                "frame_count": 72,
                "width": 640,
                "height": 360,
                "duration_s": 3.6,
                "source_identity": saved_identity.to_dict(),
            },
            results=[TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")],
        )

        task = controller.task_from_snapshot(snapshot)

        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertEqual(task.saved_media_info.source_identity, saved_identity)
        self.assertIsNotNone(task.pending_media_relink)
        _candidate, assessment = task.pending_media_relink
        self.assertEqual(assessment.state, "mismatch")
        self.assertEqual(assessment.identity_state, "mismatch")
        self.assertEqual(assessment.differences, ("Source content digest differs",))
        persisted = controller.snapshot_from_task(task)
        self.assertEqual(persisted.media_info["source_identity"], saved_identity.to_dict())

    def test_relink_assessment_distinguishes_verified_and_digest_mismatch(self) -> None:
        saved_identity = MediaIdentity("sampled-sha256-v1", "a" * 64, 50_000_000, 786_432)
        changed_identity = MediaIdentity("sampled-sha256-v1", "b" * 64, 50_000_000, 786_432)
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            available=False,
            source_identity=saved_identity,
        )
        verified_candidate = replace(saved, available=True)
        changed_candidate = replace(saved, available=True, source_identity=changed_identity)

        verified = self.controller.assess_media_relink(saved, verified_candidate, has_result_state=True)
        changed = self.controller.assess_media_relink(saved, changed_candidate, has_result_state=True)

        self.assertEqual(verified.state, "match")
        self.assertEqual(verified.identity_state, "sampled")
        self.assertIn("Bounded sampled SHA-256", verified.summary)
        self.assertTrue(verified.clear_results)
        self.assertTrue(verified.requires_review)
        self.assertEqual(changed.state, "mismatch")
        self.assertEqual(changed.identity_state, "mismatch")
        self.assertEqual(changed.differences, ("Source content digest differs",))
        self.assertTrue(changed.clear_results)

    def test_sampled_identity_match_without_results_does_not_clear_or_review(self) -> None:
        saved_identity = MediaIdentity("sampled-sha256-v1", "a" * 64, 50_000_000, 786_432)
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            available=False,
            source_identity=saved_identity,
        )
        candidate = replace(saved, available=True)

        empty_task = self.controller.assess_media_relink(saved, candidate, has_result_state=False)

        self.assertEqual(empty_task.state, "match")
        self.assertEqual(empty_task.identity_state, "sampled")
        self.assertFalse(empty_task.clear_results)
        self.assertFalse(empty_task.requires_review)

    def test_full_identity_match_preserves_results_without_review(self) -> None:
        saved_identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            available=False,
            source_identity=saved_identity,
        )
        candidate = replace(saved, available=True)

        assessment = self.controller.assess_media_relink(saved, candidate, has_result_state=True)

        self.assertEqual(assessment.state, "match")
        self.assertEqual(assessment.identity_state, "full")
        self.assertFalse(assessment.clear_results)
        self.assertFalse(assessment.requires_review)

    def test_snapshot_open_quarantines_sampled_identity_match_with_results(self) -> None:
        saved_identity = MediaIdentity("sampled-sha256-v1", "1" * 64, 50_000_000, 786_432)
        live_identity = MediaIdentity("sampled-sha256-v1", "1" * 64, 50_000_000, 786_432)
        live = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=live_identity,
        )
        controller = ProjectTaskController(
            self.registry,
            "color_marker",
            media_probe=lambda _path: live,
        )
        snapshot = ProjectTaskSnapshot(
            media_path="/experiments/source.mp4",
            pipeline_key="color_marker",
            media_info={
                "kind": "video",
                "available": True,
                "fps": 20.0,
                "frame_count": 72,
                "width": 640,
                "height": 360,
                "duration_s": 3.6,
                "source_identity": saved_identity.to_dict(),
            },
            results=[TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")],
        )

        task = controller.task_from_snapshot(snapshot)

        self.assertTrue(task.media_identity_requires_review)
        self.assertFalse(task.media_info.available)
        self.assertIsNotNone(task.pending_media_relink)
        _candidate, assessment = task.pending_media_relink
        self.assertEqual(assessment.state, "match")
        self.assertEqual(assessment.identity_state, "sampled")
        self.assertTrue(assessment.clear_results)

    def test_saved_digest_without_candidate_digest_is_unverified_not_match(self) -> None:
        saved_identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            available=True,
            source_identity=saved_identity,
        )
        candidate = replace(saved, source_identity=None)

        with_results = self.controller.assess_media_relink(saved, candidate, has_result_state=True)
        empty_task = self.controller.assess_media_relink(saved, candidate, has_result_state=False)

        self.assertEqual(with_results.state, "unverified")
        self.assertEqual(with_results.identity_state, "unverified")
        self.assertTrue(with_results.clear_results)
        self.assertIn("could not be verified", with_results.summary)
        self.assertEqual(empty_task.state, "unverified")
        self.assertFalse(empty_task.clear_results)

    def test_legacy_saved_without_digest_requires_clear_only_with_results(self) -> None:
        saved = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
        )
        candidate = replace(saved)
        candidate_identity = MediaIdentity("full-sha256-v1", "d" * 64, 4096, 4096)
        candidate_with_digest = replace(saved, source_identity=candidate_identity)

        with_results = self.controller.assess_media_relink(saved, candidate, has_result_state=True)
        with_candidate_digest = self.controller.assess_media_relink(
            saved,
            candidate_with_digest,
            has_result_state=True,
        )
        empty_task = self.controller.assess_media_relink(saved, candidate, has_result_state=False)

        self.assertEqual(with_results.state, "unverified")
        self.assertEqual(with_results.identity_state, "unavailable")
        self.assertTrue(with_results.can_apply)
        self.assertTrue(with_results.clear_results)
        self.assertIn("neither the older project nor the selected file", with_results.summary)
        self.assertEqual(with_candidate_digest.state, "unverified")
        self.assertEqual(with_candidate_digest.identity_state, "unavailable")
        self.assertTrue(with_candidate_digest.can_apply)
        self.assertTrue(with_candidate_digest.clear_results)
        self.assertIn("older project has no source digest", with_candidate_digest.summary)
        self.assertEqual(empty_task.state, "match")
        self.assertTrue(empty_task.can_apply)
        self.assertFalse(empty_task.clear_results)

    def test_matching_media_relink_preserves_results_and_clamps_preview(self) -> None:
        task = self.controller.new_task("missing.mp4", "color_marker")
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        task.preview_frame_index = 200
        identity = MediaIdentity("full-sha256-v1", "c" * 64, 4096, 4096)
        task.media_info = replace(task.media_info, source_identity=identity)
        candidate = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=640,
            height=360,
            duration_s=4.0,
            available=True,
            source_identity=identity,
        )

        assessment = self.controller.assess_media_relink(task.media_info, candidate, has_result_state=True)
        self.assertEqual(assessment.state, "match")
        self.assertTrue(assessment.can_apply)
        self.assertFalse(assessment.clear_results)

        self.controller.relink_media(task, "/new/location/video.mp4", candidate, clear_results=False)
        self.assertEqual(task.media_path, "/new/location/video.mp4")
        self.assertEqual(task.preview_frame_index, 119)
        self.assertEqual(len(task.pipeline.results), 1)

    def test_mismatched_media_relink_clears_result_scoped_state_but_keeps_runs(self) -> None:
        task = self.controller.new_task("missing.mp4", "color_marker")
        task.pipeline.results = [
            TrackerResult(0, 0.0, {"x_px": 12.0}, {"x_px": 12.0}, 0.9, "ok")
        ]
        task.edit_history = [{"event": "manual_point", "frame_index": 0}]
        task.tracking_outcome = "complete"
        task.tracking_note = "Previous run"
        task.run_history = [
            TrackingRunRecord(
                started_at="2026-07-14T00:00:00Z",
                duration_s=1.0,
                mode="full",
                outcome="complete",
                start_frame=0,
                end_frame=0,
                processed_frames=1,
                result_count=1,
                pipeline_config=task.pipeline.to_config(),
            )
        ]
        candidate = MediaInfo(
            fps=25.0,
            frame_count=80,
            width=1280,
            height=720,
            duration_s=3.2,
            available=True,
        )

        assessment = self.controller.assess_media_relink(task.media_info, candidate, has_result_state=True)
        self.assertEqual(assessment.state, "mismatch")
        self.assertTrue(assessment.can_apply)
        self.assertTrue(assessment.clear_results)
        self.assertIn("Resolution 640×360 → 1280×720", assessment.differences)
        self.assertIn("Frames 120 → 80", assessment.differences)
        self.assertIn("FPS 30 → 25", assessment.differences)

        self.controller.relink_media(task, "/new/location/video.mp4", candidate, clear_results=True)
        self.assertEqual(task.pipeline.results, [])
        self.assertEqual(task.edit_history, [])
        self.assertEqual(task.tracking_outcome, "")
        self.assertEqual(task.tracking_note, "")
        self.assertEqual(len(task.run_history), 1)

    def test_media_relink_rejects_wrong_kind_and_unavailable_candidate(self) -> None:
        saved = MediaInfo(
            fps=30.0,
            frame_count=120,
            width=640,
            height=360,
            duration_s=4.0,
            available=False,
        )
        audio = MediaInfo(
            fps=8000.0,
            frame_count=400,
            width=0,
            height=0,
            duration_s=0.05,
            available=True,
            kind="audio",
            sample_rate_hz=8000.0,
            channels=1,
        )
        unavailable = MediaInfo(available=False, error="Decoder failed")

        wrong_kind = self.controller.assess_media_relink(saved, audio, has_result_state=False)
        failed = self.controller.assess_media_relink(saved, unavailable, has_result_state=False)

        self.assertEqual(wrong_kind.state, "incompatible")
        self.assertFalse(wrong_kind.can_apply)
        self.assertEqual(failed.state, "unavailable")
        self.assertFalse(failed.can_apply)
        self.assertIn("Decoder failed", failed.summary)


if __name__ == "__main__":
    unittest.main()
