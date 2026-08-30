from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from neo_tracker.kinematics import SampleSeries
from neo_tracker.ui.physics_plot import PhysicsPlot
from neo_tracker.ui.series_table_model import SeriesTableModel
from neo_tracker.ui.view_state import PHYSICS_WORKSPACE_PAGES, PhysicsWorkspaceState


class PhysicsWorkspace(QFrame):
    """Collapsible bottom instrument tray for data, plot, fit, and legacy routes."""

    seriesActivated = Signal(str)
    sampleActivated = Signal(str, int)
    plotSampleActivated = Signal(str, int)
    rangeSelected = Signal(float, float)
    plotImageExportRequested = Signal()
    pageRouteRequested = Signal(str)
    pageChanged = Signal(str)
    layoutStateChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("physicsWorkspace")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(38)
        self.setAccessibleName("Physics analysis workspace")
        self.setAccessibleDescription(
            "Collapsible Data, Plot, Fit, Diagnostics, Runs, Edits, and Signal workspace. "
            "Selections remain aligned to stored true time."
        )
        self._collapsed = False
        self._canvas_focus = False
        self._before_focus_collapsed = False
        self._preferred_height = 280
        self._series: dict[str, SampleSeries] = {}
        self._page_widgets: dict[str, QWidget] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("physicsWorkspaceHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 5, 8, 5)
        header_layout.setSpacing(8)
        title = QLabel("PHYSICS")
        title.setObjectName("physicsWorkspaceTitle")
        self.cursor_label = QLabel("true time — · no sample selected")
        self.cursor_label.setObjectName("physicsCursorLabel")
        self.cursor_label.setAccessibleName("Shared physics true-time cursor")
        self.cursor_label.setAccessibleDescription("No physical sample is selected.")
        self.cursor_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("physicsCollapseButton")
        self.collapse_button.setText("Collapse")
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.collapse_button.setAccessibleName("Collapse physics analysis workspace")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        self.focus_button = QToolButton()
        self.focus_button.setObjectName("canvasFocusButton")
        self.focus_button.setText("Canvas Focus")
        self.focus_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.focus_button.setToolTip("Temporarily enlarge the video canvas.")
        self.focus_button.setAccessibleName("Enter canvas focus mode")
        self.focus_button.setAccessibleDescription(
            "Temporarily hide the inspector and collapse this workspace to enlarge the video canvas."
        )
        header_layout.addWidget(title)
        header_layout.addWidget(self.cursor_label, 1)
        header_layout.addWidget(self.focus_button)
        header_layout.addWidget(self.collapse_button)
        outer.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("physicsWorkspaceTabs")
        self.tabs.setAccessibleName("Physics analysis pages")
        self.tabs.tabBar().setAccessibleName("Physics analysis page tabs")
        self.tabs.currentChanged.connect(self._page_changed)
        outer.addWidget(self.tabs, 1)

        self.series_model = SeriesTableModel(self)
        self.series_table = QTableView()
        self.series_table.setObjectName("physicsSeriesTable")
        self.series_table.setModel(self.series_model)
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.series_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.series_table.setTabKeyNavigation(False)
        self.series_table.setAlternatingRowColors(True)
        self.series_table.verticalHeader().setVisible(False)
        header = self.series_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for section, width in enumerate((86, 112, 132, 82, 176, 96)):
            header.resizeSection(section, width)
        header.setStretchLastSection(True)
        self.series_table.setAccessibleName("Physical series data")
        self.series_table.setAccessibleDescription(
            "Virtual frame-aligned physical data. Columns identify validity, provenance, and units in text."
        )
        self.series_table.selectionModel().selectionChanged.connect(
            lambda _selected, _deselected: self._table_selection_changed()
        )
        self.series_combo = QComboBox()
        self.series_combo.setObjectName("physicsSeriesCombo")
        self.series_combo.setAccessibleName("Physical quantity")
        self.series_combo.setToolTip("Choose the physical series shown in the Data table.")
        self.series_combo.currentIndexChanged.connect(self._series_changed)
        data_page = QWidget()
        data_layout = QVBoxLayout(data_page)
        data_layout.setContentsMargins(8, 7, 8, 8)
        data_header = QHBoxLayout()
        data_header.addWidget(QLabel("Quantity"))
        data_header.addWidget(self.series_combo, 1)
        self.copy_status_label = QLabel("Select rows to inspect exact values and units.")
        self.copy_status_label.setAccessibleName("Physical data selection status")
        self.copy_row_button = QPushButton("Copy Row")
        self.copy_row_button.setAccessibleName("Copy selected physical data row")
        self.copy_row_button.setAccessibleDescription(
            "Copy the selected frame, true time, full-precision value, validity, source, and unit."
        )
        self.copy_row_button.setEnabled(False)
        self.copy_row_button.clicked.connect(self._copy_selected_rows)
        data_header.addWidget(self.copy_status_label, 2)
        data_header.addWidget(self.copy_row_button)
        data_layout.addLayout(data_header)
        data_layout.addWidget(self.series_table, 1)
        self._add_page("Data", data_page)

        self.plot = PhysicsPlot()
        self.plot.sampleActivated.connect(self._plot_sample_activated)
        self.plot.rangeSelected.connect(self.rangeSelected)
        self.export_plot_image_button = QPushButton("Export PNG")
        self.export_plot_image_button.setAccessibleName("Export physics plot image")
        self.export_plot_image_button.setAccessibleDescription(
            "Save the current true-time plot, including fit and residual layers, as a PNG image."
        )
        self.export_plot_image_button.setEnabled(False)
        self.export_plot_image_button.clicked.connect(self.plotImageExportRequested)
        plot_page = QWidget()
        plot_layout = QVBoxLayout(plot_page)
        plot_layout.setContentsMargins(8, 7, 8, 8)
        plot_actions = QHBoxLayout()
        plot_actions.addStretch(1)
        plot_actions.addWidget(self.export_plot_image_button)
        plot_layout.addLayout(plot_actions)
        plot_layout.addWidget(self.plot, 1)
        self._add_page("Plot", plot_page)

        self.fit_stack = QStackedWidget()
        self.fit_placeholder = self._placeholder(
            "Choose a physical series, model, and true-time range to run a background fit."
        )
        self.fit_stack.addWidget(self.fit_placeholder)
        self._add_page("Fit", self.fit_stack)

        self._add_page(
            "Diagnostics",
            self._route_page(
                "Diagnostics",
                "Tracking diagnostics remain available without changing physical-series data.",
                "Open Review diagnostics",
            ),
        )

        QWidget.setTabOrder(self.series_combo, self.series_table)
        self._add_page(
            "Runs",
            self._route_page(
                "Runs",
                "Tracking run history remains the source of provenance for this analysis.",
                "Open run history",
            ),
        )
        self._add_page(
            "Edits",
            self._route_page(
                "Edits",
                "Manual edits remain explicit and can invalidate derived analysis by source revision.",
                "Open edit history",
            ),
        )
        self._add_page(
            "Signal",
            self._route_page(
                "Signal",
                "FFT and STFT keep their existing background workflow and source semantics.",
                "Open Signal controls",
            ),
        )

        self.setStyleSheet(
            """
            QFrame#physicsWorkspace {
                background: #FBFCFC;
                border: 1px solid #D8DEE2;
                border-radius: 8px;
            }
            QFrame#physicsWorkspaceHeader {
                background: #F3F5F6;
                border: none;
                border-bottom: 1px solid #D8DEE2;
            }
            QLabel#physicsWorkspaceTitle {
                color: #20272C;
                font-family: "Avenir Next Condensed", "Helvetica Neue", sans-serif;
                font-weight: 650;
                letter-spacing: 1px;
            }
            QLabel#physicsCursorLabel {
                color: #C55232;
                font-family: "SF Mono", "Menlo", monospace;
                font-size: 11px;
            }
            QTableView#physicsSeriesTable {
                font-family: "SF Mono", "Menlo", monospace;
                color: #20272C;
                selection-background-color: #DCEAF4;
                selection-color: #20272C;
            }
            QToolButton:focus {
                border: 2px solid #2F6F9F;
                border-radius: 4px;
            }
            """
        )

    @property
    def preferred_height(self) -> int:
        return self._preferred_height

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @property
    def current_page(self) -> str:
        return self.tabs.tabText(max(0, self.tabs.currentIndex())) or "Data"

    def set_series(self, series: Sequence[SampleSeries]) -> None:
        items = tuple(series)
        if len(items) > 64:
            raise ValueError("the workspace supports at most 64 attached series")
        series_ids = tuple(item.series_id for item in items)
        if len(set(series_ids)) != len(series_ids):
            raise ValueError("workspace series_id values must be unique")
        revisions = {item.source_revision for item in items}
        if len(revisions) > 1:
            raise ValueError("workspace series must share one source revision")
        self._series = {item.series_id: item for item in items}
        previous = self.series_combo.currentData()
        self.series_combo.blockSignals(True)
        self.series_combo.clear()
        for item in items:
            unit = item.unit or "unit unavailable"
            self.series_combo.addItem(f"{item.name} · {unit}", item.series_id)
        index = self.series_combo.findData(previous)
        self.series_combo.setCurrentIndex(index if index >= 0 else (0 if items else -1))
        self.series_combo.blockSignals(False)
        selected = self._series.get(str(self.series_combo.currentData()))
        self.plot.set_series(self._plot_series_for(selected) if selected is not None else ())
        self._series_changed(self.series_combo.currentIndex())
        self.export_plot_image_button.setEnabled(bool(items))
        if not items:
            self.set_cursor(None, None, "unavailable")

    def set_fit_widget(self, widget: QWidget) -> None:
        if widget is self.fit_placeholder:
            return
        self.fit_stack.addWidget(widget)
        self.fit_stack.setCurrentWidget(widget)

    def set_cursor(
        self,
        frame_index: int | None,
        time_s: float | None,
        match: str,
    ) -> None:
        if frame_index is None or time_s is None or not math.isfinite(float(time_s)):
            text = "true time — · no sample selected"
            detail = "No physical sample is selected."
        else:
            normalized_match = "nearest" if str(match) == "nearest" else "exact"
            text = f"true time {float(time_s):.6f} s · frame {int(frame_index)} · {normalized_match}"
            detail = (
                f"The shared cursor is at true time {float(time_s):.6f} seconds and source frame "
                f"{int(frame_index)} using a {normalized_match} sample match."
            )
        self.cursor_label.setText(text)
        self.cursor_label.setToolTip(detail)
        self.cursor_label.setAccessibleDescription(detail)

    def apply_selection(
        self,
        series_id: str | None,
        sample_index: int | None,
        frame_index: int | None,
        time_s: float | None,
        match: str,
    ) -> None:
        """Render a session event without emitting a feedback selection."""

        source = self._series.get(series_id or "")
        if source is not None and sample_index is not None and 0 <= int(sample_index) < len(source):
            combo_index = self.series_combo.findData(source.series_id)
            if combo_index >= 0 and combo_index != self.series_combo.currentIndex():
                self.series_combo.blockSignals(True)
                self.series_combo.setCurrentIndex(combo_index)
                self.series_combo.blockSignals(False)
                self.series_model.set_series(source)
            selection = self.series_table.selectionModel()
            selection.blockSignals(True)
            self.series_table.selectRow(int(sample_index))
            self.series_table.scrollTo(
                self.series_model.index(int(sample_index), 0),
                QAbstractItemView.ScrollHint.EnsureVisible,
            )
            selection.blockSignals(False)
            self._ensure_plot_series_visible(source)
            self.plot.set_selected_sample(int(sample_index), source.series_id)
            self.copy_row_button.setEnabled(True)
        else:
            self.series_table.selectionModel().blockSignals(True)
            self.series_table.clearSelection()
            self.series_table.selectionModel().blockSignals(False)
            self.plot.set_selected_sample(None)
            self.copy_row_button.setEnabled(False)
        self.set_cursor(frame_index, time_s, match)

    def remember_height(self, height: int) -> None:
        self._preferred_height = max(120, min(1_200, int(height)))

    def layout_state(self) -> PhysicsWorkspaceState:
        page = self.tabs.tabText(max(0, self.tabs.currentIndex())) or "Data"
        return PhysicsWorkspaceState(
            collapsed=self._collapsed,
            page=page,
            height=self._preferred_height,
        )

    def apply_layout_state(self, state: PhysicsWorkspaceState) -> None:
        if not isinstance(state, PhysicsWorkspaceState):
            raise TypeError("state must be PhysicsWorkspaceState")
        page_index = next(
            index
            for index in range(self.tabs.count())
            if self.tabs.tabText(index) == state.page
        )
        self.tabs.setCurrentIndex(page_index)
        self.remember_height(state.height)
        self.set_collapsed(state.collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        resolved = bool(collapsed)
        if self._collapsed == resolved:
            return
        self._collapsed = resolved
        self.tabs.setVisible(not resolved)
        self.collapse_button.setText("Expand" if resolved else "Collapse")
        self.collapse_button.setAccessibleName(
            "Expand physics analysis workspace" if resolved else "Collapse physics analysis workspace"
        )
        self.setMaximumHeight(38 if resolved else 16_777_215)
        self.layoutStateChanged.emit(self.layout_state())

    def set_canvas_focus(self, enabled: bool) -> None:
        resolved = bool(enabled)
        if self._canvas_focus == resolved:
            return
        self._canvas_focus = resolved
        if resolved:
            self._before_focus_collapsed = self._collapsed
            self.set_collapsed(True)
        else:
            self.set_collapsed(self._before_focus_collapsed)

    def show_page(self, page: str) -> None:
        if page not in PHYSICS_WORKSPACE_PAGES:
            raise ValueError(f"unknown physics workspace page: {page}")
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == page:
                self.tabs.setCurrentIndex(index)
                self.set_collapsed(False)
                return

    def _add_page(self, name: str, widget: QWidget) -> None:
        widget.setObjectName(f"physics{name}Page")
        widget.setAccessibleName(f"Physics {name} page")
        self._page_widgets[name] = widget
        self.tabs.addTab(widget, name)

    def _series_changed(self, _index: int) -> None:
        self.copy_row_button.setEnabled(False)
        series_id = self.series_combo.currentData()
        source = self._series.get(str(series_id)) if series_id is not None else None
        self.series_model.set_series(source)
        if source is None:
            self.copy_status_label.setText("No physical series is available.")
        else:
            self._ensure_plot_series_visible(source)
            unit = source.unit or "unit unavailable"
            self.copy_status_label.setText(
                f"{len(source):,} aligned samples · {source.source_kind} · {unit}"
            )
            self.seriesActivated.emit(source.series_id)

    def _ensure_plot_series_visible(self, source: SampleSeries) -> None:
        if self.plot.series_ids[:1] == (source.series_id,):
            return
        selected_range = self.plot.selected_range
        self.plot.set_series(self._plot_series_for(source))
        if selected_range is not None:
            self.plot.set_selected_range(*selected_range)

    def _plot_series_for(self, source: SampleSeries) -> tuple[SampleSeries, ...]:
        compatible = tuple(
            item
            for item in self._series.values()
            if source.unit
            and item.series_id != source.series_id
            and item.unit == source.unit
        )
        return (source, *compatible[:7])

    def _table_selection_changed(self) -> None:
        rows = self.series_table.selectionModel().selectedRows()
        source = self.series_model.series
        if source is None or not rows:
            self.copy_row_button.setEnabled(False)
            return
        row = int(rows[0].row())
        if not 0 <= row < len(source):
            return
        self.copy_row_button.setEnabled(True)
        frame = int(source.frame_indices[row])
        time_s = float(source.time_s[row])
        self.plot.set_selected_sample(row, source.series_id)
        self.set_cursor(frame, time_s if math.isfinite(time_s) else None, "exact")
        validity = "Valid" if bool(source.valid_mask[row]) else "Invalid"
        unit = source.unit or "unit unavailable"
        self.copy_status_label.setText(
            f"{validity} · {source.name} · {format(float(source.values[row]), '.8g') if source.valid_mask[row] else '—'} {unit}"
        )
        self.sampleActivated.emit(source.series_id, row)

    def _copy_selected_rows(self) -> None:
        rows = self.series_table.selectionModel().selectedRows()
        if not rows:
            self.copy_status_label.setText("Select a physical data row to copy.")
            return
        QApplication.clipboard().setText(
            self.series_model.copy_rows((rows[0].row(),))
        )
        self.copy_status_label.setText("Copied 1 physical data row.")

    def _plot_sample_activated(self, series_id: str, row: int, _time_s: float) -> None:
        source = self._series.get(series_id)
        if source is None or not 0 <= int(row) < len(source):
            return
        combo_index = self.series_combo.findData(series_id)
        if combo_index >= 0 and combo_index != self.series_combo.currentIndex():
            self.series_combo.setCurrentIndex(combo_index)
        selection = self.series_table.selectionModel()
        selection.blockSignals(True)
        self.series_table.selectRow(int(row))
        self.series_table.scrollTo(
            self.series_model.index(int(row), 0),
            QAbstractItemView.ScrollHint.EnsureVisible,
        )
        selection.blockSignals(False)
        self.copy_row_button.setEnabled(True)
        time_s = float(source.time_s[int(row)])
        self.set_cursor(int(source.frame_indices[int(row)]), time_s, "exact")
        self.plotSampleActivated.emit(series_id, int(row))

    def _page_changed(self, _index: int) -> None:
        self.pageChanged.emit(self.current_page)
        if not self._collapsed:
            self.layoutStateChanged.emit(self.layout_state())

    def _route_page(self, route: str, text: str, button_text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAccessibleDescription(text)
        button = QPushButton(button_text)
        button.setAccessibleDescription(f"Show the existing {route} workflow in the inspector.")
        button.clicked.connect(lambda _checked=False, route=route: self.pageRouteRequested.emit(route))
        layout.addWidget(label)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    @staticmethod
    def _placeholder(text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAccessibleDescription(text)
        layout.addWidget(label)
        layout.addStretch(1)
        return page
