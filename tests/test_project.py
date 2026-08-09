from __future__ import annotations

import os
import json
from hashlib import sha256
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker.core import ObservationCandidate, TrackerResult
from neo_tracker.media import MediaIdentity
from neo_tracker.presets import color_marker_preset
from neo_tracker.project import (
    TRACKING_RUN_HISTORY_LIMIT,
    NeoTrackerProject,
    ProjectTaskSnapshot,
    TrackingRunRecord,
    append_tracking_run,
    project_content_fingerprint,
)
from neo_tracker.visualization import annular_theta_time_heatmap


class ProjectPersistenceTests(unittest.TestCase):
    def test_project_content_fingerprint_is_compact_order_independent_and_ignores_preview(self) -> None:
        first = NeoTrackerProject(
            name="fingerprint",
            tasks=[
                ProjectTaskSnapshot(
                    media_path=None,
                    pipeline_key="color_marker",
                    preview_frame_index=3,
                    pipeline_config={"z": 1, "nested": {"b": 2, "a": 1}},
                    edit_history=[{"type": "mark_lost", "frame_index": 4}],
                )
            ],
        )
        reordered = NeoTrackerProject(
            name="fingerprint",
            tasks=[
                ProjectTaskSnapshot(
                    media_path=None,
                    pipeline_key="color_marker",
                    preview_frame_index=99,
                    pipeline_config={"nested": {"a": 1, "b": 2}, "z": 1},
                    edit_history=[{"frame_index": 4, "type": "mark_lost"}],
                )
            ],
        )

        first_fingerprint = project_content_fingerprint(first)
        self.assertEqual(len(first_fingerprint), 64)
        self.assertEqual(first_fingerprint, project_content_fingerprint(reordered))

        # A JSON/IPC roundtrip intentionally changes string and container
        # identities. Fingerprints describe persisted content, not pickle memo
        # aliasing decisions.
        detached = NeoTrackerProject.from_dict(
            json.loads(json.dumps(first.to_dict(), ensure_ascii=False, allow_nan=False))
        )
        self.assertEqual(first.to_dict(), detached.to_dict())
        self.assertEqual(first_fingerprint, project_content_fingerprint(detached))

        reordered.tasks[0].edit_history[0]["frame_index"] = 5
        self.assertNotEqual(first_fingerprint, project_content_fingerprint(reordered))

    def test_project_content_fingerprint_cooperates_without_changing_digest(self) -> None:
        results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 30.0,
                state={"x": float(index)},
                filtered_state={"x": float(index)},
                confidence=0.9,
                status="ok",
            )
            for index in range(300)
        ]
        project = NeoTrackerProject(
            name="cooperative fingerprint",
            pipelines=[color_marker_preset().to_config()],
            tasks=[
                ProjectTaskSnapshot(
                    "/media/source.mp4",
                    "color_marker",
                    preview_frame_index=27,
                    media_info={"fps": 30.0, "frame_count": 300},
                    roi={"type": "rectangle", "x": 1, "y": 2, "width": 30, "height": 20},
                    calibration_rod={
                        "start_px": [0.0, 0.0],
                        "end_px": [100.0, 0.0],
                        "real_length": 50.0,
                        "unit": "cm",
                    },
                    pipeline_config={"name": "Color Marker", "nested": {"b": 2, "a": 1}},
                    results=results,
                    edit_history=[{"type": "mark_lost", "frame_index": 4}],
                    tracking_outcome="complete",
                    tracking_note="All frames tracked.",
                    run_history=[
                        TrackingRunRecord(
                            started_at="2026-07-22T00:00:00Z",
                            duration_s=1.0,
                            mode="full",
                            outcome="complete",
                            start_frame=0,
                            end_frame=299,
                            processed_frames=300,
                            result_count=300,
                            pipeline_config=color_marker_preset().to_config(),
                        )
                    ],
                )
            ],
            notes="Preserve exact canonical fields.",
        )
        calls: list[int] = []

        cooperative = project_content_fingerprint(
            project,
            cooperate=lambda: calls.append(len(calls)),
        )

        data = project.to_dict()
        legacy_digest = sha256()

        def add_legacy_record(kind: str, value: object) -> None:
            encoded_kind = kind.encode("ascii")
            encoded_value = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            legacy_digest.update(len(encoded_kind).to_bytes(4, "big"))
            legacy_digest.update(encoded_kind)
            legacy_digest.update(len(encoded_value).to_bytes(8, "big"))
            legacy_digest.update(encoded_value)

        media_paths = data.pop("media_paths")
        pipelines = data.pop("pipeline_library")
        tasks = data.pop("tasks")
        add_legacy_record(
            "project",
            {
                **data,
                "media_path_count": len(media_paths),
                "pipeline_count": len(pipelines),
                "task_count": len(tasks),
            },
        )
        for media_path in media_paths:
            add_legacy_record("media_path", media_path)
        for pipeline in pipelines:
            add_legacy_record("pipeline", pipeline)
        for task in tasks:
            results_data = task.pop("results")
            edit_history = task.pop("edit_history")
            run_history = task.pop("run_history")
            task["preview_frame_index"] = 0
            task["result_count"] = len(results_data)
            task["edit_history_count"] = len(edit_history)
            task["run_history_count"] = len(run_history)
            add_legacy_record("task", task)
            for result in results_data:
                add_legacy_record("result", result)
            for edit in edit_history:
                add_legacy_record("edit_history", edit)
            for run in run_history:
                add_legacy_record("run_history", run)

        self.assertEqual(cooperative, project_content_fingerprint(project))
        self.assertEqual(cooperative, legacy_digest.hexdigest())
        self.assertGreaterEqual(len(calls), 2)

    def test_project_save_load_roundtrip_with_results(self) -> None:
        result = TrackerResult(
            frame_index=3,
            time_s=0.3,
            state={"x_px": 12.0, "y_px": 14.0},
            filtered_state={"x_px": 12.5, "y_px": 14.5},
            confidence=0.9,
            status="manual",
            observation=ObservationCandidate(
                state={"x_px": 12.5, "y_px": 14.5},
                score=1.0,
                image_point=(12.5, 14.5),
                label="manual",
            ),
            debug={"response_map": np.zeros((5, 7), dtype=float), "note": "manual correction"},
        )
        task = ProjectTaskSnapshot(
            media_path="/tmp/example.mp4",
            pipeline_key="color_marker",
            preview_frame_index=3,
            media_info={
                "kind": "video",
                "available": True,
                "fps": 30.0,
                "frame_count": 120,
                "width": 640,
                "height": 480,
                "duration_s": 4.0,
            },
            roi={"type": "rectangle", "x": 1, "y": 2, "width": 30, "height": 20},
            calibration_rod={
                "start_px": [0.0, 0.0],
                "end_px": [100.0, 0.0],
                "real_length": 50.0,
                "unit": "cm",
            },
            pipeline_config={"name": "Color Marker"},
            results=[result],
            edit_history=[
                {
                    "time": "2026-07-08T00:00:00Z",
                    "type": "manual_correction",
                    "frame_index": 3,
                    "superseded_at": "2026-07-08T00:05:00Z",
                    "superseded_by_rerun_start_frame": 3,
                    "details": {"point_px": [12.5, 14.5]},
                }
            ],
            tracking_outcome="partial",
            tracking_note="Media ended at frame 4.",
            run_history=[
                TrackingRunRecord(
                    started_at="2026-07-08T00:00:00Z",
                    duration_s=1.25,
                    mode="full",
                    outcome="partial",
                    start_frame=0,
                    end_frame=3,
                    processed_frames=4,
                    result_count=4,
                    note="Media ended at frame 4.",
                    pipeline_config=color_marker_preset().to_config(),
                    tracking_elapsed_s=1.0,
                    input_s=0.2,
                    processing_s=0.7,
                    peak_debug_bytes=67_108_864,
                    prefetch_frames=1,
                    compute_backend="OpenCV components",
                )
            ],
        )
        project = NeoTrackerProject(name="roundtrip", tasks=[task])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            project.save(path)
            loaded = NeoTrackerProject.load(path)
        self.assertEqual(loaded.name, "roundtrip")
        self.assertEqual(len(loaded.tasks), 1)
        loaded_task = loaded.tasks[0]
        self.assertEqual(loaded_task.media_path, "/tmp/example.mp4")
        self.assertEqual(loaded_task.pipeline_key, "color_marker")
        self.assertEqual(loaded_task.media_info["kind"], "video")
        self.assertEqual(loaded_task.media_info["frame_count"], 120)
        self.assertEqual(loaded_task.media_info["width"], 640)
        self.assertEqual(loaded_task.roi["type"], "rectangle")
        self.assertEqual(loaded_task.calibration_rod["unit"], "cm")
        self.assertEqual(len(loaded_task.results), 1)
        loaded_result = loaded_task.results[0]
        self.assertEqual(loaded_result.status, "manual")
        self.assertAlmostEqual(loaded_result.filtered_state["x_px"], 12.5)
        self.assertEqual(loaded_result.observation.label, "manual")
        self.assertEqual(loaded_result.debug["response_map"]["shape"], [5, 7])
        self.assertTrue(loaded_result.debug["response_map"]["omitted"])
        self.assertEqual(loaded_task.edit_history[0]["type"], "manual_correction")
        self.assertEqual(loaded_task.edit_history[0]["details"]["point_px"], [12.5, 14.5])
        self.assertEqual(loaded_task.edit_history[0]["superseded_at"], "2026-07-08T00:05:00Z")
        self.assertEqual(loaded_task.edit_history[0]["superseded_by_rerun_start_frame"], 3)
        self.assertEqual(loaded_task.tracking_outcome, "partial")
        self.assertEqual(loaded_task.tracking_note, "Media ended at frame 4.")
        self.assertEqual(len(loaded_task.run_history), 1)
        self.assertEqual(loaded_task.run_history[0].mode, "full")
        self.assertEqual(loaded_task.run_history[0].outcome, "partial")
        self.assertEqual(loaded_task.run_history[0].pipeline_digest, task.run_history[0].pipeline_digest)
        self.assertAlmostEqual(loaded_task.run_history[0].throughput_fps, 4.0)
        self.assertAlmostEqual(loaded_task.run_history[0].input_ms_per_frame, 50.0)
        self.assertAlmostEqual(loaded_task.run_history[0].processing_ms_per_frame, 175.0)
        self.assertEqual(loaded_task.run_history[0].peak_debug_bytes, 67_108_864)
        self.assertEqual(loaded_task.run_history[0].prefetch_frames, 1)
        self.assertEqual(loaded_task.run_history[0].compute_backend, "OpenCV components")

    def test_project_save_atomically_replaces_existing_file_without_temporary_residue(self) -> None:
        project = NeoTrackerProject(
            name="atomic-success",
            media_paths=["/tmp/source.mp4"],
            notes="complete payload",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            path.write_text('{"old": "content"}', encoding="utf-8")

            with patch("neo_tracker.project.os.fsync", wraps=os.fsync) as fsync:
                project.save(path)

            self.assertEqual(NeoTrackerProject.load(path), project)
            self.assertEqual(fsync.call_count, 2)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_project_save_replace_failure_preserves_existing_file_and_cleans_temporary_file(self) -> None:
        project = NeoTrackerProject(name="replace-failure")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            previous = '{"existing": "project"}'
            path.write_text(previous, encoding="utf-8")

            with (
                patch("neo_tracker.project.os.replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                project.save(path)

            self.assertEqual(path.read_text(encoding="utf-8"), previous)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_project_save_write_failure_preserves_existing_file_and_cleans_temporary_file(self) -> None:
        project = NeoTrackerProject(name="write-failure")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            previous = '{"existing": "project"}'
            path.write_text(previous, encoding="utf-8")

            def partial_write_then_fail(_payload, stream, **_kwargs) -> None:
                stream.write('{"partial":')
                raise OSError("disk full")

            with (
                patch("neo_tracker.project.json.dump", side_effect=partial_write_then_fail),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                project.save(path)

            self.assertEqual(path.read_text(encoding="utf-8"), previous)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_project_load_rejects_oversized_input_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "oversized.ntproj"
            path.write_bytes(b"{" + b"x" * 128 + b"}")

            with (
                patch("neo_tracker.project.MAX_PROJECT_FILE_BYTES", 64),
                self.assertRaisesRegex(ValueError, "above the 64-byte safety limit"),
            ):
                NeoTrackerProject.load(path)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO rejection requires POSIX mkfifo")
    def test_project_load_rejects_fifo_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blocked.ntproj"
            os.mkfifo(path)

            with self.assertRaisesRegex(ValueError, "not a regular file"):
                NeoTrackerProject.load(path)

    def test_oversized_project_save_preserves_existing_file(self) -> None:
        project = NeoTrackerProject(name="oversized", notes="x" * 512)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            previous = '{"existing": "project"}'
            path.write_text(previous, encoding="utf-8")

            with (
                patch("neo_tracker.project.MAX_PROJECT_FILE_BYTES", 128),
                self.assertRaisesRegex(ValueError, "above the 128-byte safety limit"),
            ):
                project.save(path)

            self.assertEqual(path.read_text(encoding="utf-8"), previous)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_project_load_rejects_nonfinite_json_constants(self) -> None:
        payload = {
            "format": "neo-tracker-project",
            "version": 2,
            "name": float("nan"),
            "media_paths": [],
            "pipeline_library": [],
            "tasks": [],
            "notes": "",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonfinite.ntproj"
            path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite constant NaN"):
                NeoTrackerProject.load(path)

    def test_project_save_rejects_nonfinite_result_without_replacing_target(self) -> None:
        result = TrackerResult(
            frame_index=0,
            time_s=0.0,
            state={"x": float("inf")},
            filtered_state={"x": 1.0},
            confidence=1.0,
            status="ok",
        )
        project = NeoTrackerProject(
            name="nonfinite",
            tasks=[ProjectTaskSnapshot(None, "color_marker", results=[result])],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "project.ntproj"
            previous = '{"existing": "project"}'
            path.write_text(previous, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "finite number"):
                project.save(path)
            self.assertEqual(path.read_text(encoding="utf-8"), previous)

    def test_project_collection_and_nesting_limits_precede_materialization(self) -> None:
        result = {
            "frame_index": 0,
            "time_s": 0.0,
            "state": {},
            "filtered_state": {},
            "confidence": 1.0,
            "status": "ok",
        }
        with (
            patch("neo_tracker.project.MAX_TASK_RESULTS", 1),
            self.assertRaisesRegex(ValueError, "results must not exceed 1 entries"),
        ):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "results": [result, result]}
            )

        nested = NeoTrackerProject(name="nested").to_dict()
        nested["extra"] = [[[[]]]]
        with (
            patch("neo_tracker.project.MAX_PROJECT_NESTING_DEPTH", 2),
            self.assertRaisesRegex(ValueError, "nesting must not exceed 2 levels"),
        ):
            NeoTrackerProject.from_dict(nested)

    def test_project_results_require_unique_ordered_frames_and_time(self) -> None:
        def result(frame: int, time_s: float) -> dict[str, object]:
            return {
                "frame_index": frame,
                "time_s": time_s,
                "state": {},
                "filtered_state": {},
                "confidence": 1.0,
                "status": "ok",
            }

        with self.assertRaisesRegex(ValueError, "unique increasing frame_index"):
            ProjectTaskSnapshot.from_dict(
                {
                    "media_path": None,
                    "pipeline_key": "color_marker",
                    "results": [result(0, 0.0), result(0, 0.1)],
                }
            )
        with self.assertRaisesRegex(ValueError, "time_s values must be non-decreasing"):
            ProjectTaskSnapshot.from_dict(
                {
                    "media_path": None,
                    "pipeline_key": "color_marker",
                    "results": [result(0, 1.0), result(1, 0.5)],
                }
            )

    def test_project_task_outcome_validation_and_legacy_defaults(self) -> None:
        for outcome in ("", "complete", "partial", "canceled", "failed"):
            with self.subTest(outcome=outcome):
                snapshot = ProjectTaskSnapshot.from_dict(
                    {
                        "media_path": None,
                        "pipeline_key": "color_marker",
                        "tracking_outcome": outcome,
                        "tracking_note": "saved run note",
                    }
                )
                self.assertEqual(snapshot.tracking_outcome, outcome)
                self.assertEqual(snapshot.to_dict()["tracking_outcome"], outcome)
                self.assertEqual(snapshot.tracking_note, "saved run note")

        legacy = ProjectTaskSnapshot.from_dict({"media_path": None, "pipeline_key": "color_marker"})
        self.assertEqual(legacy.tracking_outcome, "")
        self.assertEqual(legacy.tracking_note, "")
        with self.assertRaisesRegex(ValueError, "unsupported project task tracking_outcome"):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "tracking_outcome": "running"}
            )
        with self.assertRaisesRegex(ValueError, "tracking_outcome must be a string"):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "tracking_outcome": 3}
            )
        with self.assertRaisesRegex(ValueError, "tracking_note must be a string"):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "tracking_note": {"bad": True}}
            )
        with self.assertRaisesRegex(ValueError, "must not exceed 4096 characters"):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "tracking_note": "x" * 4097}
            )

    def test_tracking_run_validation_history_limit_and_legacy_default(self) -> None:
        valid = {
            "started_at": "2026-07-13T12:00:00Z",
            "duration_s": 0.75,
            "mode": "full",
            "outcome": "complete",
            "start_frame": 0,
            "end_frame": 2,
            "processed_frames": 3,
            "result_count": 3,
            "note": "",
            "pipeline_config": color_marker_preset().to_config(),
        }
        record = TrackingRunRecord.from_dict(valid)
        self.assertEqual(record.to_dict()["mode"], "full")
        self.assertEqual(len(record.pipeline_digest), 12)
        self.assertFalse(record.has_performance_metrics)
        self.assertEqual(record.to_dict()["tracking_elapsed_s"], 0.0)
        self.assertEqual(record.prefetch_frames, 0)
        self.assertEqual(record.compute_backend, "")
        self.assertEqual(record.source_path, "")
        self.assertIsNone(record.source_identity)
        source_identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        source_record = TrackingRunRecord.from_dict(
            {
                **valid,
                "source_path": "/experiments/original.mp4",
                "source_identity": source_identity.to_dict(),
            }
        )
        self.assertEqual(source_record.source_path, "/experiments/original.mp4")
        self.assertEqual(source_record.source_identity, source_identity)
        self.assertEqual(source_record.to_dict()["source_identity"], source_identity.to_dict())
        legacy = ProjectTaskSnapshot.from_dict({"media_path": None, "pipeline_key": "color_marker"})
        self.assertEqual(legacy.run_history, [])

        invalid_cases = [
            ({**valid, "started_at": "not-a-time"}, "valid ISO timestamp"),
            ({**valid, "duration_s": float("nan")}, "finite non-negative"),
            ({**valid, "mode": "segment"}, "unsupported tracking run mode"),
            ({**valid, "outcome": "running"}, "unsupported project task tracking_outcome"),
            ({**valid, "end_frame": 3}, "frame range must match"),
            ({**valid, "pipeline_config": {}}, "non-empty dictionary"),
            ({**valid, "tracking_elapsed_s": float("inf")}, "tracking_elapsed_s must be a finite"),
            ({**valid, "input_s": -0.1}, "input_s must be a finite"),
            ({**valid, "processing_s": "fast"}, "processing_s must be a finite"),
            ({**valid, "peak_debug_bytes": -1}, "peak_debug_bytes must be a non-negative integer"),
            ({**valid, "prefetch_frames": -1}, "prefetch_frames must be a non-negative integer"),
            ({**valid, "compute_backend": 3}, "compute_backend must be a string"),
            ({**valid, "compute_backend": "x" * 129}, "must not exceed 128 characters"),
            ({**valid, "source_path": 3}, "source_path must be a string"),
            ({**valid, "source_identity": {"bad": True}}, "valid media identity"),
        ]
        for data, message in invalid_cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                TrackingRunRecord.from_dict(data)
        with self.assertRaisesRegex(ValueError, "run_history must be a list"):
            ProjectTaskSnapshot.from_dict(
                {"media_path": None, "pipeline_key": "color_marker", "run_history": {"bad": True}}
            )
        with self.assertRaisesRegex(ValueError, "must not exceed 20 entries"):
            ProjectTaskSnapshot.from_dict(
                {
                    "media_path": None,
                    "pipeline_key": "color_marker",
                    "run_history": [valid] * (TRACKING_RUN_HISTORY_LIMIT + 1),
                }
            )

        history: list[TrackingRunRecord] = []
        for index in range(TRACKING_RUN_HISTORY_LIMIT + 3):
            append_tracking_run(history, TrackingRunRecord.from_dict({**valid, "note": str(index)}))
        self.assertEqual(len(history), TRACKING_RUN_HISTORY_LIMIT)
        self.assertEqual(history[0].note, "3")
        self.assertEqual(history[-1].note, str(TRACKING_RUN_HISTORY_LIMIT + 2))

    def test_project_rejects_unsupported_future_version(self) -> None:
        data = NeoTrackerProject(name="future").to_dict()
        data["version"] = 3

        with self.assertRaisesRegex(ValueError, "unsupported Neo-Tracker project version: 3"):
            NeoTrackerProject.from_dict(data)

    def test_project_accepts_legacy_file_without_explicit_version(self) -> None:
        data = NeoTrackerProject(name="legacy").to_dict()
        data.pop("version")

        loaded = NeoTrackerProject.from_dict(data)

        self.assertEqual(loaded.name, "legacy")

    def test_project_v2_roundtrip_preserves_pipeline_library_configs(self) -> None:
        pipeline = color_marker_preset()
        project = NeoTrackerProject(name="library", pipelines=[pipeline])

        serialized = project.to_dict()
        loaded = NeoTrackerProject.from_dict(serialized)

        self.assertEqual(serialized["version"], 2)
        self.assertNotIn("pipelines", serialized)
        self.assertEqual(serialized["pipeline_library"], [pipeline.to_config()])
        self.assertEqual(loaded.pipelines, [pipeline.to_config()])
        self.assertEqual(loaded.to_dict()["pipeline_library"], [pipeline.to_config()])

    def test_project_migrates_version_1_pipelines_without_data_loss(self) -> None:
        pipeline_config = color_marker_preset().to_config()
        legacy = {
            "format": "neo-tracker-project",
            "version": 1,
            "name": "legacy-library",
            "media_paths": [],
            "pipelines": [pipeline_config],
            "tasks": [],
            "notes": "from v1",
        }

        loaded = NeoTrackerProject.from_dict(legacy)
        migrated = loaded.to_dict()

        self.assertEqual(loaded.pipelines, [pipeline_config])
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["pipeline_library"], [pipeline_config])
        self.assertNotIn("pipelines", migrated)

    def test_project_roundtrip_preserves_theta_signal_for_annular_heatmap(self) -> None:
        theta_signal = np.linspace(0.0, 1.0, 12, dtype=np.float32)
        result = TrackerResult(
            frame_index=0,
            time_s=0.0,
            state={"theta": 0.0},
            filtered_state={"theta": 0.0},
            confidence=1.0,
            status="ok",
            debug={
                "debug_layers": {
                    "theta_signal": theta_signal,
                    "polar_samples": np.zeros((4, 12), dtype=np.float32),
                },
                "response_map": np.zeros((4, 12), dtype=np.float32),
            },
        )
        project = NeoTrackerProject(
            name="annular",
            tasks=[ProjectTaskSnapshot(media_path=None, pipeline_key="travelling_flame", results=[result])],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "annular.ntproj"
            project.save(path)
            loaded_result = NeoTrackerProject.load(path).tasks[0].results[0]

        heatmap = annular_theta_time_heatmap([loaded_result])
        self.assertEqual(heatmap.shape, (1, theta_signal.size))
        np.testing.assert_allclose(heatmap[0], theta_signal)
        self.assertTrue(loaded_result.debug["response_map"]["omitted"])


if __name__ == "__main__":
    unittest.main()
