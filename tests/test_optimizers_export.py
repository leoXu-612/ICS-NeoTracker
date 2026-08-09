from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker.export import (
    edit_history_rows,
    markdown_report,
    result_summary,
    results_to_rows,
    tracking_run_rows,
    write_edit_history_csv,
    write_csv,
    write_markdown_report,
    write_tracking_run_csv,
)
from neo_tracker.media import MediaIdentity, MediaInfo
from neo_tracker.optimizers import GridSearchOptimizer, ParticleSwarmOptimizer
from neo_tracker.presets import color_marker_preset
from neo_tracker.project import TrackingRunRecord
from neo_tracker.roi import RectangularROI


class OptimizerExportTests(unittest.TestCase):
    def test_grid_search_optimizer_finds_quadratic_peak(self) -> None:
        optimizer = GridSearchOptimizer(samples_per_axis=11)
        result = optimizer.optimize(lambda p: -((p["x"] - 0.4) ** 2), {"x": (0.0, 1.0)})
        self.assertAlmostEqual(result.params["x"], 0.4, places=6)

    def test_particle_swarm_optimizer_improves_quadratic(self) -> None:
        optimizer = ParticleSwarmOptimizer(particles=12, iterations=20, seed=3)
        result = optimizer.optimize(
            lambda p: -((p["x"] - 0.33) ** 2 + (p["y"] + 0.2) ** 2),
            {"x": (0.0, 1.0), "y": (-1.0, 1.0)},
        )
        self.assertLess(abs(result.params["x"] - 0.33), 0.08)
        self.assertLess(abs(result.params["y"] + 0.2), 0.08)

    def test_export_rows_and_csv(self) -> None:
        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        frame[20, 10, 0] = 255
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 40, 40), tolerance=0.08)
        results = pipeline.run([frame], fps=10.0)
        rows = results_to_rows(results)
        self.assertIn("x_px", rows[0])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tracks.csv"
            write_csv(path, results)
            text = path.read_text(encoding="utf-8")
            self.assertIn("frame_index", text)
            self.assertIn("x_px", text)
            header = text.splitlines()[0].split(",")
            self.assertEqual(header[:4], ["frame_index", "time_s", "x_px", "y_px"])
            self.assertLess(header.index("raw_x_px"), header.index("confidence"))

            unit_path = Path(tmpdir) / "tracks-units.csv"
            write_csv(unit_path, results, state_units=pipeline.state_model.units())
            unit_header = unit_path.read_text(encoding="utf-8").splitlines()[0].split(",")
            self.assertEqual(unit_header[:4], ["frame_index", "time_s (s)", "x_px (px)", "y_px (px)"])
            self.assertIn("raw_x_px (px)", unit_header)

    def test_tracking_run_csv_preserves_filtered_history_sequence_numbers(self) -> None:
        pipeline = color_marker_preset()
        source_identity = MediaIdentity("full-sha256-v1", "b" * 64, 4096, 4096)
        records = [
            TrackingRunRecord(
                started_at="2026-07-13T11:00:00Z",
                duration_s=0.25,
                mode="full",
                outcome="complete",
                start_frame=0,
                end_frame=2,
                processed_frames=3,
                result_count=3,
                pipeline_config=pipeline.to_config(),
                tracking_elapsed_s=0.25,
                input_s=0.03,
                processing_s=0.12,
                peak_debug_bytes=2_097_152,
                prefetch_frames=1,
                compute_backend="OpenCV components",
                source_path="/experiments/source.mp4",
                source_identity=source_identity,
            ),
            TrackingRunRecord(
                started_at="2026-07-13T11:05:00Z",
                duration_s=0.1,
                mode="rerun",
                outcome="partial",
                start_frame=3,
                end_frame=3,
                processed_frames=1,
                result_count=4,
                note="early end",
                pipeline_config=pipeline.to_config(),
            ),
        ]
        rows = tracking_run_rows(records, sequence_numbers=[2, 5])
        legacy_fields = [
            "index",
            "started_at",
            "mode",
            "outcome",
            "compute_backend",
            "frame_range",
            "processed_frames",
            "result_count",
            "duration_s",
            "tracking_elapsed_s",
            "throughput_fps",
            "input_ms_per_frame",
            "processing_ms_per_frame",
            "prefetch_frames",
            "stage_overlap_ms_per_frame",
            "peak_debug_bytes",
            "pipeline_digest",
            "note",
        ]
        provenance_fields = [
            "source_path",
            "source_identity_strategy",
            "source_identity_sha256",
            "source_size_bytes",
        ]
        self.assertEqual(list(rows[0])[: len(legacy_fields)], legacy_fields)
        self.assertEqual(list(rows[0])[len(legacy_fields) :], provenance_fields)
        self.assertEqual([row["index"] for row in rows], [2, 5])
        self.assertEqual(rows[0]["throughput_fps"], "12.000")
        self.assertEqual(rows[0]["input_ms_per_frame"], "10.000")
        self.assertEqual(rows[0]["processing_ms_per_frame"], "40.000")
        self.assertEqual(rows[0]["compute_backend"], "OpenCV components")
        self.assertEqual(rows[0]["prefetch_frames"], 1)
        self.assertEqual(rows[0]["stage_overlap_ms_per_frame"], "0.000")
        self.assertEqual(rows[0]["peak_debug_bytes"], 2_097_152)
        self.assertEqual(rows[0]["source_path"], "/experiments/source.mp4")
        self.assertEqual(rows[0]["source_identity_strategy"], "full-sha256-v1")
        self.assertEqual(rows[0]["source_identity_sha256"], source_identity.sha256)
        self.assertEqual(rows[0]["source_size_bytes"], 4096)
        self.assertEqual(rows[1]["throughput_fps"], "")
        with self.assertRaisesRegex(ValueError, "sequence count"):
            tracking_run_rows(records, sequence_numbers=[2])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run-history.csv"
            write_tracking_run_csv(path, records, sequence_numbers=[2, 5])
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0].split(",")[:4], ["index", "started_at", "mode", "outcome"])
        self.assertEqual(lines[0].split(",")[: len(legacy_fields)], legacy_fields)
        self.assertEqual(lines[0].split(",")[len(legacy_fields) :], provenance_fields)
        self.assertIn("input_ms_per_frame", lines[0].split(","))
        self.assertIn("compute_backend", lines[0].split(","))
        self.assertIn("prefetch_frames", lines[0].split(","))
        self.assertIn("stage_overlap_ms_per_frame", lines[0].split(","))
        self.assertIn("peak_debug_bytes", lines[0].split(","))
        self.assertTrue(lines[1].startswith("2,2026-07-13T11:00:00Z,full,complete,"))
        self.assertTrue(lines[2].startswith("5,2026-07-13T11:05:00Z,rerun,partial,"))

    def test_edit_history_csv_preserves_filtered_sequence_numbers(self) -> None:
        entries = [
            {
                "time": "2026-07-13T12:00:00Z",
                "type": "manual_correction",
                "frame_index": 4,
                "details": {"point_px": [12.0, 18.0]},
            },
            {
                "time": "2026-07-13T12:05:00Z",
                "type": "mark_lost",
                "frame_index": 9,
                "superseded_at": "2026-07-13T12:10:00Z",
                "superseded_by_rerun_start_frame": 5,
                "details": {"previous_status": "ok"},
            },
        ]
        rows = edit_history_rows(entries, sequence_numbers=[2, 7])
        self.assertEqual([row["index"] for row in rows], [2, 7])
        self.assertEqual(rows[0]["state"], "current")
        self.assertEqual(rows[1]["state"], "superseded")
        self.assertEqual(rows[1]["superseded_by_rerun_start_frame"], 5)
        with self.assertRaisesRegex(ValueError, "sequence count"):
            edit_history_rows(entries, sequence_numbers=[2])
        with self.assertRaisesRegex(ValueError, "positive integers"):
            edit_history_rows(entries, sequence_numbers=[0, 7])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "edit-history.csv"
            write_edit_history_csv(path, entries, sequence_numbers=[2, 7])
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0].split(",")[:4], ["index", "time", "type", "frame_index"])
        self.assertTrue(lines[1].startswith("2,2026-07-13T12:00:00Z,manual_correction,4,"))
        self.assertTrue(lines[2].startswith("7,2026-07-13T12:05:00Z,mark_lost,9,"))
        self.assertIn(",superseded,2026-07-13T12:10:00Z,5,", lines[2])

    def test_csv_exports_neutralize_spreadsheet_formula_text(self) -> None:
        result = color_marker_preset().run([np.zeros((8, 8, 3), dtype=np.uint8)], fps=10.0)[0]
        result.filtered_state = {"=FORMULA_HEADER()": 1.0}
        result.state = {"=FORMULA_HEADER()": -2.0}
        result.status = " =HYPERLINK(\"https://invalid.example\")"
        record = TrackingRunRecord(
            started_at="2026-07-13T11:00:00Z",
            duration_s=0.25,
            mode="full",
            outcome="complete",
            start_frame=0,
            end_frame=0,
            processed_frames=1,
            result_count=1,
            note="+SUM(1,1)",
            pipeline_config=color_marker_preset().to_config(),
            compute_backend="@unsafe",
            source_path="-cmd|' /C calc'!A0",
        )
        edit = {
            "time": "=NOW()",
            "type": "+edit",
            "frame_index": 0,
            "details": {"comment": "@formula"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "results.csv"
            run_path = Path(tmpdir) / "runs.csv"
            edit_path = Path(tmpdir) / "edits.csv"
            write_csv(result_path, [result])
            write_tracking_run_csv(run_path, [record])
            write_edit_history_csv(edit_path, [edit])

            with result_path.open(newline="", encoding="utf-8") as handle:
                result_rows = list(csv.reader(handle))
            with run_path.open(newline="", encoding="utf-8") as handle:
                run_rows = list(csv.DictReader(handle))
            with edit_path.open(newline="", encoding="utf-8") as handle:
                edit_rows = list(csv.DictReader(handle))

        self.assertIn("'=FORMULA_HEADER()", result_rows[0])
        status_index = result_rows[0].index("status")
        self.assertTrue(result_rows[1][status_index].startswith("' =HYPERLINK"))
        self.assertTrue(run_rows[0]["compute_backend"].startswith("'@"))
        self.assertTrue(run_rows[0]["note"].startswith("'+"))
        self.assertTrue(run_rows[0]["source_path"].startswith("'-"))
        self.assertTrue(edit_rows[0]["time"].startswith("'="))
        self.assertTrue(edit_rows[0]["type"].startswith("'+"))

    def test_csv_and_markdown_replace_failures_preserve_existing_exports(self) -> None:
        result = color_marker_preset().run(
            [np.zeros((8, 8, 3), dtype=np.uint8)],
            fps=10.0,
        )[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, writer in (
                ("results.csv", lambda path: write_csv(path, [result])),
                (
                    "report.md",
                    lambda path: write_markdown_report(
                        path,
                        "Atomic report",
                        color_marker_preset(),
                        [result],
                    ),
                ),
            ):
                with self.subTest(name=name):
                    path = Path(tmpdir) / name
                    sentinel = b"previous valid export"
                    path.write_bytes(sentinel)
                    with (
                        patch("neo_tracker.atomic_io.os.replace", side_effect=OSError("replace failed")),
                        self.assertRaisesRegex(OSError, "replace failed"),
                    ):
                        writer(path)
                    self.assertEqual(path.read_bytes(), sentinel)
                    self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_markdown_report_summarizes_pipeline_results_and_attention_frames(self) -> None:
        frames = []
        for index in range(3):
            frame = np.zeros((40, 40, 3), dtype=np.uint8)
            frame[20, 10 + index, 0] = 255
            frames.append(frame)
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, 40, 40), tolerance=0.08)
        results = pipeline.run(frames, fps=10.0)
        results[0].filtered_state["x_px"] = results[0].state["x_px"] + 2.0
        results[1].status = "manual_lost"
        results[1].confidence = 0.0
        summary = result_summary(results)
        self.assertEqual(summary["status_counts"]["manual_lost"], 1)
        self.assertAlmostEqual(summary["filter_shift"]["x_px"]["max_abs"], 2.0)
        historical_config = pipeline.to_config()
        historical_config["observation_model"]["tolerance"] = 0.2
        source_identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        run_history = [
            TrackingRunRecord(
                started_at="2026-07-13T11:00:00Z",
                duration_s=0.25,
                mode="full",
                outcome="partial",
                start_frame=0,
                end_frame=1,
                processed_frames=2,
                result_count=2,
                note="decoder | stopped",
                pipeline_config=historical_config,
                tracking_elapsed_s=0.2,
                input_s=0.02,
                processing_s=0.15,
                compute_backend="OpenCV components",
                source_path="/experiments/demo.mp4",
                source_identity=source_identity,
            ),
            TrackingRunRecord(
                started_at="2026-07-13T11:05:00Z",
                duration_s=0.5,
                mode="rerun",
                outcome="complete",
                start_frame=2,
                end_frame=2,
                processed_frames=1,
                result_count=3,
                pipeline_config=pipeline.to_config(),
            ),
        ]
        text = markdown_report(
            title="Demo Report",
            pipeline=pipeline,
            results=results,
            media_path="demo.mp4",
            roi=pipeline.roi.to_config(),
            edit_history=[
                {
                    "time": "2026-07-08T00:00:00Z",
                    "type": "manual_correction",
                    "frame_index": 1,
                    "superseded_at": "2026-07-08T00:05:00Z",
                    "superseded_by_rerun_start_frame": 1,
                    "details": {"point_px": [12.0, 20.0]},
                }
            ],
            run_history=run_history,
        )
        self.assertIn("# Demo Report", text)
        self.assertIn("manual_lost", text)
        self.assertIn("State Units", text)
        self.assertIn("x_px: px", text)
        self.assertIn("Filter Shift", text)
        self.assertIn("| State key | Unit | Mean abs shift | Max abs shift |", text)
        self.assertIn("Edit History", text)
        self.assertIn("manual_correction", text)
        self.assertIn("| superseded | 1 |", text)
        self.assertIn("Tracking Run History", text)
        self.assertIn("Input (ms/f)", text)
        self.assertIn("Compute backend", text)
        self.assertIn("OpenCV components", text)
        self.assertIn("| 0.200 | 10.000 | 10.000 | 75.000 |", text)
        self.assertIn(
            "| 1 | 2026-07-13T11:00:00Z | full | partial | OpenCV components | 0-1 |",
            text,
        )
        self.assertIn("| Source path | Identity strategy | Identity SHA-256 | Source size (bytes) |", text)
        self.assertIn("demo.mp4", text)
        self.assertNotIn("/experiments/demo.mp4", text)
        self.assertIn("full-sha256-v1", text)
        self.assertIn(source_identity.sha256, text)
        self.assertIn("decoder \\| stopped", text)
        self.assertIn("Historical Pipeline Configurations", text)
        self.assertIn(run_history[0].pipeline_digest, text)
        self.assertIn("Pipeline JSON", text)
        self.assertIn("| frame_index | time_s | x_px | y_px | raw_x_px | raw_y_px | confidence | status |", text)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.md"
            write_markdown_report(path, "Demo Report", pipeline, results, media_path="demo.mp4")
            saved = path.read_text(encoding="utf-8")
        self.assertIn("Result Preview", saved)

        provenance_text = markdown_report(
            title="Demo Report",
            pipeline=pipeline,
            results=results,
            run_history=run_history,
            include_absolute_paths=True,
        )
        self.assertIn("/experiments/demo.mp4", provenance_text)

    def test_markdown_report_escapes_project_text_and_hides_absolute_paths_by_default(self) -> None:
        pipeline = color_marker_preset()
        pipeline.name = '<img src="https://invalid.example/pixel">'
        result = pipeline.run([np.zeros((8, 8, 3), dtype=np.uint8)], fps=10.0)[0]
        result.status = "[open](https://invalid.example)"
        text = markdown_report(
            title='<script src="https://invalid.example/x.js"></script>',
            pipeline=pipeline,
            results=[result],
            media_path="/Users/private/experiment.mp4",
            project_path="/Users/private/project.ntproj",
            notes='<img src="https://invalid.example/note">',
        )

        self.assertNotIn("# <script", text)
        self.assertNotIn("- Pipeline: <img", text)
        self.assertNotIn("## Notes\n\n<img", text)
        self.assertNotIn("/Users/private", text)
        self.assertIn("experiment.mp4", text)
        self.assertIn("project.ntproj", text)
        self.assertIn("&lt;img", text)
        self.assertIn(r"\[open\]\(https://invalid.example\)", text)

    def test_markdown_report_uses_audio_media_fields(self) -> None:
        pipeline = color_marker_preset()
        text = markdown_report(
            title="Audio Report",
            pipeline=pipeline,
            results=[],
            media_path="tone.wav",
            media_info=MediaInfo(
                available=True,
                kind="audio",
                fps=8000.0,
                frame_count=400,
                duration_s=0.05,
                sample_rate_hz=8000.0,
                channels=2,
            ),
        )

        self.assertIn("- Media type: audio", text)
        self.assertIn("- Sample rate: 8000 Hz", text)
        self.assertIn("- Samples: 400", text)
        self.assertIn("- Channels: 2", text)
        self.assertNotIn("- Resolution:", text)


if __name__ == "__main__":
    unittest.main()
