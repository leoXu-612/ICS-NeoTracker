"""Compact workbench commands backed by the existing action and parameter gates."""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QMenu, QPushButton, QSizePolicy, QStackedWidget,
    QStyle, QToolButton, QVBoxLayout, QWidget,
)

from neo_tracker.ui.language import tr


def native_icon(widget, name: str, fallback: QStyle.StandardPixmap) -> QIcon:
    icon = QIcon.fromTheme(name)
    return icon if not icon.isNull() else widget.style().standardIcon(fallback)


class ParameterCommand(QToolButton):
    """Mirror the original control, including disabled ancestors during a run."""

    def __init__(self, source, parent):
        super().__init__(parent)
        self.source = source
        self.setText(source.text())
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


class WorkspaceCommands(QFrame):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setObjectName("workspaceCommands")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(tr("Contextual experiment commands"))
        self.command_buttons = {}
        self.parameter_buttons = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 0, 10, 4)
        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.pages)
        for _ in range(owner.sidebar_tabs.count()):
            page = QWidget()
            grid = QGridLayout(page)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(5)
            self.pages.addWidget(page)

        self._fill(0, (self._action("project.open"), self._action("project.save")))
        self._fill(1, (self._parameter("marker.sample", owner.sample_marker_button),
                       self._route("Calib", 4), self._action("review.correct"), self._action("review.undo")))
        self._fill(2, tuple(self._action(key) for key in (
            "physics.velocity", "physics.acceleration", "physics.smooth", "physics.residual",
            "review.mark_lost", "review.rerun")))
        self._fill(3, (self._parameter("signal.refresh", owner.refresh_analysis_sources_button),
                       self._action("analysis.run")))
        self._fill(4, (*tuple(self._parameter(key, source) for key, source in owner.roi_draw_buttons.items()),
                       self._parameter("calibration.mark", owner.mark_calibration_button),
                       self._parameter("calibration.reset", owner.reset_calibration_button),
                       self._parameter("drawing.finish", owner.finish_roi_drawing_button),
                       self._parameter("drawing.cancel", owner.cancel_roi_drawing_button)))
        self._fill(5, (self._route("Tracking", 1), self._route("JSON", 6)))
        self._fill(6, (self._parameter("json.validate", owner.validate_json_button),
                       self._parameter("json.apply", owner.apply_json_button),
                       self._parameter("json.reset", owner.reset_json_button)))

        for key, name, fallback in (
            ("media.add", "document-open", QStyle.StandardPixmap.SP_DialogOpenButton),
            ("tracking.run", "view-refresh", QStyle.StandardPixmap.SP_BrowserReload),
            ("playback.previous", "media-skip-backward", QStyle.StandardPixmap.SP_MediaSkipBackward),
            ("playback.toggle", "media-playback-start", QStyle.StandardPixmap.SP_MediaPlay),
            ("playback.next", "media-skip-forward", QStyle.StandardPixmap.SP_MediaSkipForward),
        ):
            owner.action_registry.set_icon(key, native_icon(owner, name, fallback))

        focus = self._action("view.canvas_focus")
        focus.setText(tr("Canvas Focus"))
        owner.toolbar_actions.addWidget(focus)
        self.export_button = QToolButton()
        self.export_button.setObjectName("exportMenuButton")
        self.export_button.setText(tr("Export"))
        self.export_button.setAccessibleName(tr("Export experiment results"))
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.export_button)
        for key in ("tracking.export_csv", "physics.export", "tracking.export_report",
                    "analysis.export_csv", "analysis.export_npz"):
            menu.addAction(owner.action_registry.action(key))
        menu.addSeparator()
        self.plot_export_action = menu.addAction(tr("Export PNG"))
        self.plot_export_action.triggered.connect(owner.physics_workspace.export_plot_image_button.click)
        menu.aboutToShow.connect(lambda: self.plot_export_action.setEnabled(
            owner.physics_workspace.export_plot_image_button.isEnabled()))
        self.export_button.setMenu(menu)
        owner.toolbar_actions.addWidget(self.export_button)
        owner.physics_workspace.plot_toolbar.addWidget(self._action("physics.fit"))
        self.set_page(owner.sidebar_tabs.currentIndex())

    def _action(self, key):
        button = QToolButton()
        button.setObjectName("command_" + key.replace(".", "_"))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        button.setDefaultAction(self.owner.action_registry.action(key))
        self.command_buttons[key] = button
        return button

    def _parameter(self, key, source):
        button = ParameterCommand(source, self)
        button.setObjectName("command_" + key.replace(".", "_"))
        self.parameter_buttons[key] = button
        return button

    def _route(self, label, index):
        button = QPushButton(tr(label))
        button.clicked.connect(lambda: self.owner._workflow_selected(self.owner.workflow_order.index(index)))
        return button

    def _fill(self, index, buttons):
        grid = self.pages.widget(index).layout()
        for position, button in enumerate(buttons):
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            grid.addWidget(button, position // 2, position % 2)

    def set_page(self, index):
        self.pages.setCurrentIndex(index)
        self.pages.setFixedHeight(self.pages.widget(index).sizeHint().height())
