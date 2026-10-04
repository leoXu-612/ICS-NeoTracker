from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
import math

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QPalette

from neo_tracker.kinematics import SampleSeries
from neo_tracker.ui.language import tr


@dataclass(frozen=True)
class SeriesRow:
    frame_index: int
    time_s: float
    value: float
    valid: bool
    source: str
    unit: str


class SeriesTableModel(QAbstractTableModel):
    """Virtual, bounded-cache table over one immutable physical series."""

    HEADERS = ("Frame", "Time", "Value", "Valid", "Source", "Unit")
    RAW_VALUE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    SERIES_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    VALID_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    def __init__(self, parent=None, *, cache_limit: int = 2048) -> None:
        super().__init__(parent)
        limit = int(cache_limit)
        if limit < 1 or limit > 65_536:
            raise ValueError("cache_limit must be in [1, 65536]")
        self._series: SampleSeries | None = None
        self._cache_limit = limit
        self._display_cache: OrderedDict[tuple[int, int, int], object] = OrderedDict()

    @property
    def series(self) -> SampleSeries | None:
        return self._series

    @property
    def cache_size(self) -> int:
        return len(self._display_cache)

    def set_series(self, series: SampleSeries | None) -> None:
        if series is not None and not isinstance(series, SampleSeries):
            raise TypeError("series must be a SampleSeries or None")
        self.beginResetModel()
        self._series = series
        self._display_cache.clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid() or self._series is None:
            return 0
        return len(self._series)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return tr(self.HEADERS[section])
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        series = self._series
        if (
            series is None
            or not index.isValid()
            or not 0 <= index.row() < len(series)
            or not 0 <= index.column() < len(self.HEADERS)
        ):
            return None
        row, column = index.row(), index.column()
        if role == self.SERIES_ID_ROLE:
            return series.series_id
        if role == self.VALID_ROLE:
            return bool(series.valid_mask[row])
        if role == self.RAW_VALUE_ROLE:
            return self._raw_cell(row, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in (0, 1, 2):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and not bool(series.valid_mask[row]):
            parent = self.parent()
            dark = hasattr(parent, "palette") and parent.palette().color(QPalette.ColorRole.Base).lightness() < 128
            return QBrush(QColor("#ffd18a" if dark else "#7A5B1C"))
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        key = (row, column, int(role))
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        value = (
            self._display_cell(row, column)
            if role == Qt.ItemDataRole.DisplayRole
            else self._tooltip(row, column)
        )
        self._cache_put(key, value)
        return value

    def row_record(self, row: int) -> SeriesRow:
        series = self._series
        if series is None or not 0 <= int(row) < len(series):
            raise IndexError(f"series row is out of range: {row}")
        index = int(row)
        return SeriesRow(
            frame_index=int(series.frame_indices[index]),
            time_s=float(series.time_s[index]),
            value=float(series.values[index]),
            valid=bool(series.valid_mask[index]),
            source=self._source_label(series),
            unit=series.unit,
        )

    def copy_rows(self, rows: Iterable[int]) -> str:
        selected = sorted(set(int(row) for row in rows))
        lines = ["\t".join(self.HEADERS)]
        for row in selected:
            record = self.row_record(row)
            lines.append(
                "\t".join(
                    (
                        str(record.frame_index),
                        self._full_float(record.time_s),
                        self._full_float(record.value),
                        "Valid" if record.valid else "Invalid",
                        record.source,
                        record.unit or "unit unavailable",
                    )
                )
            )
        return "\n".join(lines)

    def refresh_rows(self, first: int, last: int) -> None:
        if self._series is None or len(self._series) == 0:
            return
        start = max(0, int(first))
        end = min(len(self._series) - 1, int(last))
        if start > end:
            return
        for key in tuple(self._display_cache):
            if start <= key[0] <= end:
                del self._display_cache[key]
        self.dataChanged.emit(
            self.index(start, 0),
            self.index(end, self.columnCount() - 1),
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole],
        )

    def _raw_cell(self, row: int, column: int) -> object:
        series = self._series
        assert series is not None
        if column == 0:
            return int(series.frame_indices[row])
        if column == 1:
            return float(series.time_s[row])
        if column == 2:
            return float(series.values[row])
        if column == 3:
            return bool(series.valid_mask[row])
        if column == 4:
            return series.source_kind
        return series.unit

    def _display_cell(self, row: int, column: int) -> str:
        series = self._series
        assert series is not None
        valid = bool(series.valid_mask[row])
        if column == 0:
            return str(int(series.frame_indices[row]))
        if column == 1:
            value = float(series.time_s[row])
            return self._compact_float(value) if math.isfinite(value) else "—"
        if column == 2:
            value = float(series.values[row])
            return self._compact_float(value) if valid and math.isfinite(value) else "—"
        if column == 3:
            return "Valid" if valid else "Invalid"
        if column == 4:
            return self._source_label(series)
        return series.unit or "unit unavailable"

    def _tooltip(self, row: int, column: int) -> str:
        series = self._series
        assert series is not None
        record = self.row_record(row)
        validity = "valid" if record.valid else "invalid or lost"
        time_value = self._full_float(record.time_s)
        time_text = f"true time {time_value} s" if time_value else "true time unavailable"
        detail = (
            f"{series.name} · frame {record.frame_index} · {time_text} · {validity}."
        )
        if column == 4 and series.processing_chain:
            detail += " Processing: " + " → ".join(str(step) for step in series.processing_chain)
        if column == 5 and not series.unit:
            detail += " The source does not provide a unit; Neo-Tracker does not infer one."
        return detail

    @staticmethod
    def _source_label(series: SampleSeries) -> str:
        kind = series.source_kind.strip().replace("_", " ")
        lowered = kind.lower()
        if "filter" in lowered:
            prefix = "FILTERED"
        elif series.is_derived:
            prefix = "DERIVED"
        else:
            prefix = "RAW"
        if series.processing_chain:
            operation = series.processing_chain[-1].operation.replace("_", " ")
            operation = operation.replace("nonuniform ", "nonuniform ")
            return f"{prefix} · {operation}"
        return f"{prefix} · {kind or 'source'}"

    def _cache_get(self, key: tuple[int, int, int]) -> object | None:
        try:
            value = self._display_cache.pop(key)
        except KeyError:
            return None
        self._display_cache[key] = value
        return value

    def _cache_put(self, key: tuple[int, int, int], value: object) -> None:
        self._display_cache[key] = value
        self._display_cache.move_to_end(key)
        while len(self._display_cache) > self._cache_limit:
            self._display_cache.popitem(last=False)

    @staticmethod
    def _compact_float(value: float) -> str:
        return format(float(value), ".8g")

    @staticmethod
    def _full_float(value: float) -> str:
        return format(float(value), ".17g") if math.isfinite(value) else ""
