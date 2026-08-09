from __future__ import annotations

import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableView, QWidget

from neo_tracker.kinematics import ProcessingStep, SampleSeries
from neo_tracker.ui.series_table_model import SeriesTableModel


def make_series(count: int = 4, *, unit: str = "m") -> SampleSeries:
    values = np.linspace(0.125, 4.125, count, dtype=np.float64)
    valid = np.ones(count, dtype=bool)
    if count > 2:
        valid[2] = False
        values[2] = np.nan
    return SampleSeries(
        series_id="velocity:x",
        name="Horizontal velocity",
        frame_indices=np.arange(count, dtype=np.int64) * 2,
        time_s=np.arange(count, dtype=np.float64) * 0.037,
        values=values,
        valid_mask=valid,
        unit=unit,
        source_kind="derived",
        source_revision="results:table",
        processing_chain=(
            ProcessingStep("nonuniform_finite_difference", {"order": 1}),
        ),
    )


class SeriesTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def test_columns_expose_true_values_units_validity_and_provenance(self) -> None:
        model = SeriesTableModel(cache_limit=32)
        model.set_series(make_series())

        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(
            [model.headerData(i, Qt.Orientation.Horizontal) for i in range(model.columnCount())],
            ["Frame", "Time", "Value", "Valid", "Source", "Unit"],
        )
        self.assertEqual(model.data(model.index(1, 0)), "2")
        self.assertEqual(model.data(model.index(1, 1), SeriesTableModel.RAW_VALUE_ROLE), 0.037)
        self.assertEqual(model.data(model.index(2, 2)), "—")
        self.assertEqual(model.data(model.index(2, 3)), "Invalid")
        self.assertIn("DERIVED", model.data(model.index(1, 4)))
        self.assertIn("finite difference", model.data(model.index(1, 4)).lower())
        self.assertEqual(model.data(model.index(1, 5)), "m")

    def test_unknown_unit_is_rendered_as_unavailable_not_inferred(self) -> None:
        model = SeriesTableModel()
        model.set_series(make_series(unit=""))
        self.assertEqual(model.data(model.index(0, 5)), "unit unavailable")

    def test_100k_rows_are_virtual_and_display_cache_is_bounded(self) -> None:
        model = SeriesTableModel(cache_limit=64)
        model.set_series(make_series(100_000))
        view = QTableView()
        view.setModel(model)

        for row in range(0, 100_000, 101):
            model.data(model.index(row, 2))
            model.data(model.index(row, 4))

        self.assertEqual(model.rowCount(), 100_000)
        self.assertLessEqual(model.cache_size, 64)
        self.assertIsNone(view.indexWidget(model.index(50_000, 2)))

    def test_copy_rows_uses_full_numeric_values_and_units(self) -> None:
        model = SeriesTableModel()
        source = make_series()
        model.set_series(source)

        copied = model.copy_rows([1, 2])

        self.assertEqual(copied.splitlines()[0], "Frame\tTime\tValue\tValid\tSource\tUnit")
        self.assertIn(format(float(source.time_s[1]), ".17g"), copied)
        self.assertIn(format(float(source.values[1]), ".17g"), copied)
        self.assertIn("Invalid", copied)
        self.assertTrue(copied.endswith("m"))


if __name__ == "__main__":
    unittest.main()
