from __future__ import annotations

import gc
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event

from neo_tracker.core import TrackerResult
from neo_tracker.media import MediaInfo
from neo_tracker.presets import default_preset_registry
from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot, project_content_fingerprint
from neo_tracker.ui.project_controller import ProjectTaskController
from neo_tracker.ui.project_open_worker import (
    PreparedProjectOpen,
    ProjectOpenWorker,
    _acquire_project_open_gc_guard,
    _read_project_json_for_stage,
    _read_project_open_stage,
    _release_project_open_gc_guard,
    _stop_load_process,
)


class ProjectOpenWorkerTests(unittest.TestCase):
    @staticmethod
    def write_stage(path: Path, records: list[dict[str, object]]) -> tuple[int, str]:
        payload = b"".join(
            json.dumps(record, separators=(",", ":"), allow_nan=False).encode("utf-8")
            + b"\n"
            for record in records
        )
        path.write_bytes(payload)
        return len(payload), hashlib.sha256(payload).hexdigest()

    def make_controller(self, media_probe) -> ProjectTaskController:
        registry = default_preset_registry()
        return ProjectTaskController(
            registry,
            next(iter(registry)),
            media_probe=media_probe,
        )

    def test_loads_probes_unique_paths_and_prepares_clean_tasks(self) -> None:
        shared_path = "/media/shared.mp4"
        probed: list[str] = []

        def probe(path: str) -> MediaInfo:
            probed.append(path)
            return MediaInfo(
                fps=20.0,
                frame_count=72,
                width=640,
                height=360,
                duration_s=3.6,
                available=True,
            )

        controller = self.make_controller(probe)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shared.ntproj"
            NeoTrackerProject(
                name="shared",
                pipelines=[controller.registry[controller.default_pipeline_key].factory().to_config()],
                tasks=[
                    ProjectTaskSnapshot(shared_path, controller.default_pipeline_key),
                    ProjectTaskSnapshot(shared_path, controller.default_pipeline_key),
                ],
                notes="Preserve this project note.",
            ).save(path)
            completed: list[PreparedProjectOpen] = []
            progress: list[tuple[str, int, int, str]] = []
            worker = ProjectOpenWorker(path, controller)
            worker.completed.connect(completed.append)
            worker.progressed.connect(lambda *items: progress.append(items))

            worker.run()

        self.assertEqual(probed, [shared_path])
        self.assertEqual(len(completed), 1)
        payload = completed[0]
        self.assertEqual(payload.path, path)
        self.assertEqual(payload.media_count, 1)
        self.assertEqual(len(payload.tasks), 2)
        self.assertTrue(all(task.media_info.available for task in payload.tasks))
        expected_canonical = NeoTrackerProject(
            name=path.stem,
            pipelines=list(payload.project.pipelines),
            tasks=[controller.snapshot_from_task(task) for task in payload.tasks],
            notes=payload.project.notes,
        )
        self.assertEqual(payload.fingerprint, project_content_fingerprint(expected_canonical))
        self.assertEqual(expected_canonical.notes, "Preserve this project note.")
        self.assertEqual(len(expected_canonical.pipelines), 1)
        self.assertEqual(progress[0][0], "loading")
        self.assertIn(("inspecting", 1, 1, "shared.mp4"), progress)
        self.assertIn(("preparing", 2, 2), [entry[:3] for entry in progress])
        self.assertIn(("indexing-review", 1, 1), [entry[:3] for entry in progress])
        self.assertIn(("indexing-analysis", 1, 1), [entry[:3] for entry in progress])
        self.assertEqual(progress[-1][:3], ("fingerprinting", 1, 1))
        self.assertIsNotNone(payload.review_diagnostics)
        self.assertEqual(len(payload.review_diagnostics.results), 0)
        self.assertEqual(len(payload.analysis_sources), 1)

    def test_cooperative_index_scan_honors_cancellation(self) -> None:
        gc_thresholds = gc.get_threshold()
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
            name="cancel-index",
            tasks=[ProjectTaskSnapshot(None, "color_marker", results=results)],
        )
        controller = self.make_controller(lambda _path: MediaInfo(available=False))
        worker = ProjectOpenWorker(
            "cancel-index.ntproj",
            controller,
            project_loader=lambda _path: project,
        )
        completed: list[PreparedProjectOpen] = []
        canceled: list[bool] = []
        failed: list[str] = []
        worker.completed.connect(completed.append)
        worker.canceled.connect(lambda: canceled.append(True))
        worker.failed.connect(failed.append)
        worker.progressed.connect(
            lambda phase, *_items: worker.request_cancel()
            if phase == "indexing-review"
            else None
        )

        worker.run()

        self.assertEqual(completed, [])
        self.assertEqual(canceled, [True])
        self.assertEqual(failed, [])
        self.assertEqual(gc.get_threshold(), gc_thresholds)

    def test_nested_project_open_gc_guards_restore_only_after_last_worker(self) -> None:
        original = gc.get_threshold()
        _acquire_project_open_gc_guard()
        try:
            guarded = gc.get_threshold()
            self.assertEqual(guarded[0], original[0])
            self.assertGreaterEqual(guarded[1], 1_000_000)
            self.assertGreaterEqual(guarded[2], 1_000_000)
            _acquire_project_open_gc_guard()
            try:
                self.assertEqual(gc.get_threshold(), guarded)
            finally:
                _release_project_open_gc_guard()
            self.assertEqual(gc.get_threshold(), guarded)
        finally:
            _release_project_open_gc_guard()
        self.assertEqual(gc.get_threshold(), original)

    def test_probe_exception_preserves_project_with_unavailable_source(self) -> None:
        controller = self.make_controller(lambda _path: (_ for _ in ()).throw(OSError("offline")))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "offline.ntproj"
            NeoTrackerProject(
                name="offline",
                tasks=[
                    ProjectTaskSnapshot(
                        "/media/offline.mp4",
                        controller.default_pipeline_key,
                    )
                ],
            ).save(path)
            completed: list[PreparedProjectOpen] = []
            worker = ProjectOpenWorker(path, controller)
            worker.completed.connect(completed.append)

            worker.run()

        self.assertEqual(len(completed), 1)
        info = completed[0].tasks[0].media_info
        self.assertIsNotNone(info)
        self.assertFalse(info.available)
        self.assertIn("offline", info.error)

    def test_cancel_before_start_does_not_read_project(self) -> None:
        loaded: list[Path] = []
        controller = self.make_controller(lambda _path: MediaInfo(available=True))
        worker = ProjectOpenWorker(
            "/tmp/canceled.ntproj",
            controller,
            project_loader=lambda path: loaded.append(Path(path)),  # type: ignore[arg-type,return-value]
        )
        canceled: list[bool] = []
        worker.canceled.connect(lambda: canceled.append(True))

        worker.request_cancel()
        worker.run()

        self.assertEqual(canceled, [True])
        self.assertEqual(loaded, [])

    def test_invalid_project_reports_failure(self) -> None:
        controller = self.make_controller(lambda _path: MediaInfo(available=True))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "invalid.ntproj"
            path.write_text("not-json", encoding="utf-8")
            failures: list[str] = []
            worker = ProjectOpenWorker(path, controller)
            worker.failed.connect(failures.append)

            worker.run()

        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0])

    def test_open_rejects_oversized_task_roi_fail_closed(self) -> None:
        controller = self.make_controller(lambda _path: MediaInfo(available=True))
        project = {
            "format": "neo-tracker-project",
            "version": 2,
            "name": "oversized-roi",
            "media_paths": [],
            "pipeline_library": [],
            "notes": "",
            "tasks": [
                {
                    "media_path": None,
                    "pipeline_key": "color_marker",
                    "preview_frame_index": 0,
                    "roi": {
                        "type": "polygon",
                        "points": [[float(index), 0.0] for index in range(4097)],
                    },
                    "results": [],
                    "edit_history": [],
                    "tracking_outcome": "",
                    "tracking_note": "",
                    "run_history": [],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "oversized-roi.ntproj"
            path.write_text(json.dumps(project), encoding="utf-8")
            failures: list[str] = []
            worker = ProjectOpenWorker(path, controller)
            worker.failed.connect(failures.append)

            worker.run()

        self.assertEqual(len(failures), 1)
        self.assertIn("roi", failures[0])

    def test_open_rejects_duplicate_json_keys_at_isolated_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "duplicate-keys.ntproj"
            path.write_text(
                '{"format":"neo-tracker-project","version":2,"name":"first","name":"second",'
                '"media_paths":[],"pipeline_library":[],"tasks":[],"notes":""}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate key"):
                _read_project_json_for_stage(str(path))

    def test_stream_stage_rejects_boolean_counts_and_interleaved_collections(self) -> None:
        project = {
            "format": "neo-tracker-project",
            "version": 2,
            "name": "strict-stage",
            "notes": "",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stage.jsonl"
            boolean_count = [
                {
                    "schema": "neo-tracker-project-open/v1",
                    "project": project,
                    "media_path_count": True,
                    "pipeline_count": 0,
                    "task_count": 0,
                },
                {"kind": "media_path", "index": 0, "value": "/tmp/source.mp4"},
            ]
            size, digest = self.write_stage(path, boolean_count)
            with self.assertRaisesRegex(ValueError, "media_path_count is invalid"):
                _read_project_open_stage(
                    path,
                    expected_bytes=size,
                    expected_digest=digest,
                    cancel_requested=Event(),
                )

            interleaved = [
                {
                    "schema": "neo-tracker-project-open/v1",
                    "project": project,
                    "media_path_count": 1,
                    "pipeline_count": 0,
                    "task_count": 1,
                },
                {
                    "kind": "task",
                    "index": 0,
                    "value": {"media_path": None, "pipeline_key": "color_marker"},
                    "result_count": 0,
                    "edit_history_count": 0,
                    "run_history_count": 0,
                },
                {"kind": "media_path", "index": 0, "value": "/tmp/source.mp4"},
            ]
            size, digest = self.write_stage(path, interleaved)
            with self.assertRaisesRegex(ValueError, "tasks are invalid or out of order"):
                _read_project_open_stage(
                    path,
                    expected_bytes=size,
                    expected_digest=digest,
                    cancel_requested=Event(),
                )

    def test_stubborn_loader_process_escalates_from_terminate_to_bounded_kill(self) -> None:
        class StubbornProcess:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def poll(self):
                return None if "kill" not in self.calls else -9

            def terminate(self) -> None:
                self.calls.append("terminate")

            def kill(self) -> None:
                self.calls.append("kill")

            def wait(self, _timeout: float) -> int:
                self.calls.append("wait")
                if "kill" not in self.calls:
                    raise subprocess.TimeoutExpired("loader", _timeout)
                return -9

        process = StubbornProcess()

        _stop_load_process(process)

        self.assertEqual(process.calls, ["terminate", "wait", "kill", "wait"])


if __name__ == "__main__":
    unittest.main()
