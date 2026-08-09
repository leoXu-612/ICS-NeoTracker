from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from benchmarks.kinematics_fixtures import missing_segments, uniform_linear
from neo_tracker.kinematics.export import CSV_FIELDS, export_csv, export_markdown, export_npz
from neo_tracker.kinematics.fitting import fit_series
from neo_tracker.kinematics.runtime import CancellationToken, KinematicsCancelled
from neo_tracker.kinematics.types import FitRequest, SampleSeries


def fit_for(series: SampleSeries):
    return fit_series(
        series,
        FitRequest(
            series.series_id,
            "linear",
            float(series.time_s[0]),
            float(series.time_s[-1]),
            series.source_revision,
        ),
    )


class KinematicsExportTests(unittest.TestCase):
    def test_csv_retains_invalid_rows_fit_columns_and_blocks_formula_injection(self) -> None:
        fixture = missing_segments(90)
        base = fixture.sample_series(series_id="=unsafe-id")
        series = SampleSeries(
            series_id=base.series_id,
            name="+unsafe name",
            frame_indices=base.frame_indices,
            time_s=base.time_s,
            values=base.values,
            valid_mask=base.valid_mask,
            unit=base.unit,
            source_kind=base.source_kind,
            source_revision=base.source_revision,
            processing_chain=base.processing_chain,
            metadata=base.metadata,
        )
        fit = fit_for(series)
        with tempfile.TemporaryDirectory() as directory:
            path = export_csv(Path(directory) / "analysis.csv", series, fit)
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(tuple(rows[0]), CSV_FIELDS)
        self.assertEqual(len(rows), len(series))
        self.assertEqual(rows[0]["series_id"], "'=unsafe-id")
        self.assertEqual(rows[0]["series_name"], "'+unsafe name")
        self.assertEqual(rows[20]["valid"], "false")
        self.assertEqual(rows[20]["value"], "")
        self.assertEqual(rows[20]["fit_prediction"], "")
        self.assertNotEqual(rows[0]["fit_prediction"], "")

    def test_npz_is_pickle_free_shape_checked_and_json_described(self) -> None:
        series = uniform_linear().sample_series()
        fit = fit_for(series)
        original_savez = np.savez_compressed
        destinations: list[object] = []

        def recording_savez(destination: object, **arrays: np.ndarray) -> None:
            destinations.append(destination)
            original_savez(destination, **arrays)

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "neo_tracker.kinematics.export.np.savez_compressed",
                side_effect=recording_savez,
            ):
                path = export_npz(Path(directory) / "analysis.bundle", series, fit)
            self.assertEqual({item.name for item in Path(directory).iterdir()}, {"analysis.bundle"})
            with np.load(path, allow_pickle=False) as archive:
                names = set(archive.files)
                self.assertIn("metadata_json", names)
                self.assertIn("fit_metadata_json", names)
                self.assertFalse(any(archive[name].dtype.hasobject for name in names))
                np.testing.assert_array_equal(archive["frame_indices"], series.frame_indices)
                np.testing.assert_array_equal(archive["valid_mask"], series.valid_mask)
                metadata = json.loads(str(archive["metadata_json"].item()))
                fit_metadata = json.loads(str(archive["fit_metadata_json"].item()))

        self.assertEqual(metadata["source_revision"], series.source_revision)
        self.assertEqual(fit_metadata["model"], "linear")
        self.assertEqual(fit_metadata["sample_count"], len(series))
        self.assertEqual(len(destinations), 1)
        self.assertTrue(hasattr(destinations[0], "write"))
        self.assertFalse(isinstance(destinations[0], (str, Path)))

    def test_markdown_contains_provenance_parameters_metrics_and_limitations(self) -> None:
        series = uniform_linear().sample_series()
        fit = fit_for(series)
        with tempfile.TemporaryDirectory() as directory:
            path = export_markdown(Path(directory) / "analysis.md", series, fit)
            content = path.read_text(encoding="utf-8")

        self.assertIn("## Source", content)
        self.assertIn(series.source_revision, content)
        self.assertIn("## Processing chain", content)
        self.assertIn("## Fit", content)
        self.assertIn("| slope |", content)
        self.assertIn("- R²:", content)
        self.assertIn("## Limitations", content)

    def test_failed_npz_write_preserves_existing_target_and_removes_temporary_file(self) -> None:
        series = uniform_linear().sample_series()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.npz"
            path.write_bytes(b"existing")
            with patch("neo_tracker.kinematics.export.np.savez_compressed", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    export_npz(path, series)
            self.assertEqual(path.read_bytes(), b"existing")
            self.assertEqual(list(Path(directory).glob(".analysis.npz.*.tmp")), [])

    def test_cancelled_export_never_replaces_target(self) -> None:
        series = uniform_linear(10_000).sample_series()
        token = CancellationToken()
        token.cancel()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analysis.csv"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaises(KinematicsCancelled):
                export_csv(path, series, cancellation=token)
            self.assertEqual(path.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
