"""Three-row window over the canonical series model, without copying samples."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from neo_tracker.ui.language import tr


class NearbySamplesModel(QAbstractTableModel):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self._start = 0
        source.modelReset.connect(lambda: self.center_on(None))

    def center_on(self, sample_index):
        self.beginResetModel()
        self._start = max(0, min(int(sample_index or 0) - 1, self.source.rowCount() - 3))
        self.endResetModel()

    def sample_index(self, row):
        return self._start + row

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else min(3, self.source.rowCount())

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < self.rowCount() or not 0 <= index.column() < 3:
            return None
        source_index = self.source.index(self.sample_index(index.row()), index.column())
        if role == Qt.ItemDataRole.DisplayRole and index.column() == 2:
            if not self.source.data(source_index, self.source.VALID_ROLE):
                return tr("Invalid")
        if role == Qt.ItemDataRole.DisplayRole and index.column() in (1, 2):
            value = self.source.data(source_index, self.source.RAW_VALUE_ROLE)
            return format(float(value), ".3f" if index.column() == 1 else ".5g")
        return self.source.data(source_index, role)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            source = self.source.series
            if section == 1:
                return tr("Time (s)")
            if section == 2 and source is not None:
                return f"{tr('Value')} ({source.unit or '—'})"
        return self.source.headerData(section, orientation, role)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
