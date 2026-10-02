"""Grouped engineering commands; existing actions remain the source of truth."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from neo_tracker.ui.language import tr


class _ParameterCommand(QToolButton):
    """Forward to an existing parameter control without bypassing its enable gate."""

    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source
        self.setText(source.text())
        self.setIcon(source.icon())
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setAccessibleName(source.accessibleName() or source.text())
        self.clicked.connect(source.click)
        source.installEventFilter(self)
        self._sync()

    def _sync(self):
        self.setEnabled(self.source.isEnabled())
        self.setToolTip(self.source.toolTip())

    def eventFilter(self, watched, event):
        if watched is self.source and event.type() in (QEvent.Type.EnabledChange, QEvent.Type.ToolTipChange):
            self._sync()
        return False


class CommandRibbon(QFrame):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setObjectName("commandRibbon")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(tr("Grouped experiment commands"))
        self.command_buttons = {}
        self.parameter_buttons = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(5)

        files = self._group("Project files", (
            owner.open_project_button, owner.save_project_button, owner.add_media_button,
        ), columns=2)
        layout.addWidget(files)

        self.pages = QStackedWidget()
        self.pages.setObjectName("ribbonPages")
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for _index in range(owner.sidebar_tabs.count()):
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page = QWidget()
            row = QHBoxLayout(page)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        layout.addWidget(self.pages, 1)

        owner.run_tracking_button.setMinimumHeight(44)
        layout.addWidget(self._group("Execution", (owner.run_tracking_button,)))
        layout.addWidget(self._group("Tracking output", (
            owner.export_tracking_csv_button, owner.export_report_button,
        )))
        layout.addStretch(1)

        self._fill(0, (
            self._group("Experiment setup", (self._route("Calib", 4), self._route("Tracking", 1))),
            self._group("Inspection", (self._route("Review", 2), self._route("Signal", 3))),
            self._group("Analysis views", (self._view("Data"), self._view("Plot"))),
        ))
        self._fill(1, (
            self._group("Target", (self._parameter("marker.sample", owner.sample_marker_button), self._route("Calib", 4))),
            self._group("Correct results", (self._action("review.correct"), self._action("review.mark_lost"))),
            self._group("Revision", (self._action("review.undo"), self._action("review.rerun"))),
            self._group("Model pipeline", (self._route("Flow", 5),)),
        ))
        self._fill(2, (
            self._group("Kinematics", (self._action("physics.velocity"), self._action("physics.acceleration"))),
            self._group("Model fit", (self._action("physics.smooth"), self._action("physics.fit"))),
            self._group("Analysis output", (self._action("physics.residual"), self._action("physics.export"))),
        ))
        self._fill(3, (
            self._group("Signal source", (self._parameter("signal.refresh", owner.refresh_analysis_sources_button),)),
            self._group("Frequency analysis", (self._action("analysis.run"),)),
            self._group("Signal output", (self._action("analysis.export_csv"), self._action("analysis.export_npz"))),
        ))
        self._fill(4, (
            self._group("Draw ROI", tuple(self._parameter(key, button) for key, button in owner.roi_draw_buttons.items()), columns=3),
            self._group("Calibration", (self._parameter("calibration.mark", owner.mark_calibration_button), self._parameter("calibration.reset", owner.reset_calibration_button))),
            self._group("Drawing", (self._parameter("drawing.finish", owner.finish_roi_drawing_button), self._parameter("drawing.cancel", owner.cancel_roi_drawing_button))),
        ))
        self._fill(5, (
            self._group("Model pipeline", (self._route("Tracking", 1), self._route("JSON", 6))),
            self._group("Analysis views", (self._view("Data"), self._view("Plot"), self._action("view.canvas_focus"))),
        ))
        self._fill(6, (
            self._group("Pipeline JSON", (self._parameter("json.validate", owner.validate_json_button), self._parameter("json.apply", owner.apply_json_button), self._parameter("json.reset", owner.reset_json_button))),
            self._group("Model pipeline", (self._route("Flow", 5),)),
        ))
        self.set_page(owner.sidebar_tabs.currentIndex())

    def _group(self, title, buttons, columns=1):
        group = QFrame()
        group.setObjectName("ribbonGroup")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(7, 2, 9, 1)
        outer.setSpacing(2)
        commands = QGridLayout()
        commands.setContentsMargins(0, 0, 0, 0)
        commands.setSpacing(2)
        for index, button in enumerate(buttons):
            button.setProperty("ribbonCommand", True)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            commands.addWidget(button, index // columns, index % columns)
        outer.addLayout(commands, 1)
        caption = QLabel(tr(title))
        caption.setObjectName("ribbonGroupCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(caption)
        return group

    def _action(self, key):
        button = QToolButton()
        button.setObjectName("ribbon_" + key.replace(".", "_"))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setDefaultAction(self.owner.action_registry.action(key))
        self.command_buttons[key] = button
        return button

    def _parameter(self, key, source):
        button = _ParameterCommand(source, self)
        button.setObjectName("ribbon_" + key.replace(".", "_"))
        self.parameter_buttons[key] = button
        return button

    def _route(self, label, index):
        button = QPushButton(tr(label))
        button.clicked.connect(lambda: self.owner._workflow_selected(self.owner.workflow_order.index(index)))
        return button

    def _view(self, name):
        button = QPushButton(tr(name))
        button.clicked.connect(lambda: self.owner.physics_workspace.show_page(name))
        return button

    def _fill(self, index, groups):
        row = self.pages.widget(index).widget().layout()
        for group in groups:
            row.addWidget(group)
        row.addStretch(1)

    def set_page(self, index):
        self.pages.setCurrentIndex(index)
        # Keep commands together instead of spreading them across a wide monitor.
        self.pages.setMaximumWidth(self.pages.widget(index).widget().sizeHint().width() + 12)
