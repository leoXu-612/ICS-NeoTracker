from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker.analysis import FFTResult, write_fft_npz
from neo_tracker.atomic_io import atomic_output_path, atomic_text_writer
from neo_tracker.csv_utils import spreadsheet_safe_cell, write_dict_csv
from neo_tracker.project import NeoTrackerProject


class AtomicIOTests(unittest.TestCase):
    def test_text_writer_publishes_content_and_leaves_no_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.txt"
            target.write_text("old", encoding="utf-8")
            with atomic_text_writer(target) as handle:
                handle.write("new content")
            self.assertEqual(target.read_text(encoding="utf-8"), "new content")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["out.txt"])

    def test_text_writer_failure_preserves_target_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.txt"
            target.write_text("old content", encoding="utf-8")
            with self.assertRaisesRegex(OSError, "synthetic write failure"):
                with atomic_text_writer(target) as handle:
                    handle.write("partial")
                    raise OSError("synthetic write failure")
            self.assertEqual(target.read_text(encoding="utf-8"), "old content")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["out.txt"])

    def test_output_path_failure_preserves_target_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.bin"
            target.write_bytes(b"old bytes")
            with self.assertRaisesRegex(OSError, "synthetic export failure"):
                with atomic_output_path(target) as temporary_path:
                    temporary_path.write_bytes(b"partial")
                    raise OSError("synthetic export failure")
            self.assertEqual(target.read_bytes(), b"old bytes")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["out.bin"])

    def test_output_path_rejects_directory_target_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "collision"
            target.mkdir()
            with self.assertRaises(OSError):
                with atomic_output_path(target) as temporary_path:
                    temporary_path.write_bytes(b"x")
            self.assertTrue(target.is_dir())
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["collision"])

    def test_disk_full_at_fsync_preserves_target_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "out.txt"
            target.write_text("old", encoding="utf-8")
            with patch(
                "neo_tracker.atomic_io.os.fsync",
                side_effect=OSError(28, "No space left on device"),
            ):
                with self.assertRaises(OSError):
                    with atomic_text_writer(target) as handle:
                        handle.write("new")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["out.txt"])


class CsvFormulaGuardTests(unittest.TestCase):
    def test_formula_like_cells_are_escaped(self) -> None:
        for raw, expected in [
            ("=SUM(A1)", "'=SUM(A1)"),
            ("+1+2", "'+1+2"),
            ("-cmd", "'-cmd"),
            ("@SUM(A1)", "'@SUM(A1)"),
            ("  =DANGER()", "'  =DANGER()"),
            ("plain text", "plain text"),
            ("3.14", "3.14"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(spreadsheet_safe_cell(raw), expected)

    def test_csv_exports_escape_headers_and_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.csv"
            write_dict_csv(
                path,
                [{"name": "=cmd", "value": 7, "note": "+2"}],
                ["name", "value", "note", "=header"],
            )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["name", "value", "note", "'=header"])
        self.assertEqual(rows[1], ["'=cmd", "7", "'+2", ""])

    def test_csv_failure_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.csv"
            path.write_text("old", encoding="utf-8")
            with patch(
                "neo_tracker.csv_utils.csv.writer",
                side_effect=OSError(28, "No space left on device"),
            ):
                with self.assertRaises(OSError):
                    write_dict_csv(path, [{"a": "1"}], ["a"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["rows.csv"])


class NpzExportSafetyTests(unittest.TestCase):
    def test_npz_failure_preserves_existing_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "fft.npz"
            target.write_bytes(b"old archive")
            result = FFTResult(
                frequency_hz=np.asarray([1.0]),
                amplitude=np.asarray([0.5]),
                power=np.asarray([0.25]),
                peak_frequency_hz=1.0,
                metadata={"series": "s", "unit": "u"},
            )
            with patch(
                "neo_tracker.analysis.np.savez",
                side_effect=OSError(28, "No space left on device"),
            ):
                with self.assertRaises(OSError):
                    write_fft_npz(target, result)
            self.assertEqual(target.read_bytes(), b"old archive")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["fft.npz"])


class ProjectSaveSafetyTests(unittest.TestCase):
    def test_project_save_failure_preserves_existing_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "proj.ntproj"
            target.write_text("old project", encoding="utf-8")
            project = NeoTrackerProject(name="safety")
            with patch(
                "neo_tracker.project.os.replace",
                side_effect=OSError(28, "No space left on device"),
            ):
                with self.assertRaises(OSError):
                    project.save(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "old project")
            self.assertEqual([path.name for path in Path(tmpdir).iterdir()], ["proj.ntproj"])


if __name__ == "__main__":
    unittest.main()
