"""Graphite workbench chrome; scientific exports keep their print palette."""

from PySide6.QtGui import QColor, QPalette


def apply_desktop_theme(window) -> None:
    # Window-local palette: do not change the user's macOS appearance or font.
    palette = window.palette()
    for role, color in (
        (QPalette.ColorRole.Window, "#23282e"),
        (QPalette.ColorRole.WindowText, "#ecf0f5"),
        (QPalette.ColorRole.Base, "#20252b"),
        (QPalette.ColorRole.AlternateBase, "#272d34"),
        (QPalette.ColorRole.Text, "#ecf0f5"),
        (QPalette.ColorRole.Button, "#30373f"),
        (QPalette.ColorRole.ButtonText, "#ecf0f5"),
        (QPalette.ColorRole.Highlight, "#234c75"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, "#a7b0bc"),
    ):
        palette.setColor(role, QColor(color))
    window.setPalette(palette)
    window.physics_workspace.setStyleSheet("")
    window.physics_workspace.plot.set_dark_mode(True)
    window.setStyleSheet("""
        QMainWindow, QWidget#appRoot, QStatusBar { background: #20252b; color: #ecf0f5; }
        QWidget { color: #ecf0f5; }
        QFrame#topToolbar { background: #282e35; border: none; border-bottom: 1px solid #12171c; }
        QLabel#appTitle { font-weight: 650; }
        QLabel#mediaTitle, QLabel#summaryChip { color: #b9c3ce; background: transparent; }
        QLabel#statusChip { color: #c5d4e4; background: #343e49; border-radius: 4px; padding: 2px 6px; }
        QLabel#statusChip[trackingOutcome="complete"] { color: #a3dac2; background: #29463d; }
        QLabel#statusChip[trackingOutcome="partial"] { color: #ffd18a; background: #51432f; }
        QLabel#statusChip[trackingOutcome="failed"] { color: #ffb4ab; background: #553633; }
        QLabel#globalProjectDirtyLabel, QLabel#globalDraftLabel { color: #ffd18a; background: #51432f; border-radius: 4px; padding: 2px 5px; }
        QFrame#navigationRail, QWidget#rightSidebar { background: #23282e; border: none; }
        QLabel#projectExplorerHeading, QLabel#inspectorHeading { font-weight: 600; padding: 8px 10px; }
        QFrame#previewPaneHeader, QFrame#physicsWorkspaceHeader { background: #23282e; border: none; }
        QLabel#previewTitle, QLabel#physicsWorkspaceTitle { font-weight: 600; }
        QLabel#previewCanvas { background: #181d22; border: none; color: #adb8c4; }
        QFrame#transportBar { background: #23282e; border: none; border-top: 1px solid #3b434c; }
        QLabel#sectionLabel { color: #b0bac6; font-weight: 600; padding: 8px 0 3px; }
        QLabel#projectNameLabel { font-weight: 600; }
        QLabel#projectStateLabel, QLabel#physicsCursorLabel { color: #aebac8; }
        QScrollArea, QTabWidget::pane { background: #23282e; border: none; }
        QTabBar::tab { background: transparent; border: none; color: #aeb8c4; padding: 7px 13px; margin: 0; }
        QTabBar::tab:selected { background: #2c4258; color: #a9d5ff; border-bottom: 2px solid #409cff; }
        QTabBar::tab:hover:!selected { background: #303740; }
        QPushButton, QToolButton { background: #30373f; border: 1px solid #48515b; border-radius: 5px; padding: 4px 8px; min-height: 20px; color: #ecf0f5; }
        QPushButton:hover, QToolButton:hover { background: #3a444f; border-color: #687685; }
        QPushButton:pressed, QToolButton:pressed { background: #224b76; border-color: #409cff; }
        QPushButton:disabled, QToolButton:disabled { background: #282e35; border-color: #373f48; color: #7e8995; }
        QPushButton#runTrackingButton { background: transparent; border-color: transparent; padding: 5px 10px; }
        QPushButton#runTrackingButton:hover { background: #3a444f; }
        QPushButton#runTrackingButton[trackingBusy="true"] { background: #963f3c; border-color: #b75853; color: white; }
        QToolButton#exportMenuButton { background: #087cf0; border-color: #2994ff; padding: 5px 16px; color: white; font-weight: 600; }
        QPushButton#addMediaButton, QPushButton#transportButton { background: transparent; border-color: transparent; }
        QPushButton#addMediaButton:hover, QPushButton#transportButton:hover { background: #3a444f; }
        QPushButton#transportButton { min-width: 22px; padding: 3px 5px; }
        QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTableView, QComboBox, QSpinBox, QDoubleSpinBox { background: #2d343c; border: 1px solid #48515b; border-radius: 4px; padding: 4px; selection-background-color: #234c75; selection-color: #ffffff; }
        QComboBox::drop-down { border: none; width: 20px; }
        QListWidget#taskList { border: none; background: #23282e; padding: 8px; }
        QListWidget::item { padding: 9px 7px; border: 1px solid transparent; border-radius: 4px; }
        QListWidget::item:selected { background: #243e59; border-color: #409cff; color: #f5f8fc; }
        QListWidget::item:disabled { color: #929eab; }
        QTextBrowser#presetDescription { background: transparent; border: none; color: #aeb8c4; padding: 0; }
        QTableView { gridline-color: #39424c; alternate-background-color: #272e36; background: #22282e; }
        QHeaderView::section { background: #2e363f; color: #bec8d3; border: none; border-right: 1px solid #424b55; border-bottom: 1px solid #424b55; padding: 7px; }
        QCheckBox { spacing: 6px; }
        QSlider::groove:horizontal { height: 4px; background: #47515c; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: #298dff; border-radius: 2px; }
        QSlider::handle:horizontal { background: #f2f7fc; border: 2px solid #409cff; width: 10px; margin: -5px 0; border-radius: 7px; }
        QSplitter::handle { background: #11171d; }
        QSplitter::handle:hover { background: #409cff; }
        QFrame#physicsWorkspace { background: #20252b; border: none; border-top: 1px solid #11171d; }
        QFrame#nearbySamples { background: #232930; border: 1px solid #3d4650; border-radius: 4px; }
        QLabel#trackingBackendLabel, QLabel#trackingPerformanceLabel, QLabel#responseStatusLabel, QLabel#taskActionSummary, QLabel#mediaRelinkDetail { color: #aeb8c4; }
        QLabel[calibrationState="error"], QLabel[roiGeometryState="error"], QLabel[analysisState="failed"] { color: #ffb4ab; background: #553633; }
        QLabel[mediaRelinkState="unverified"], QLabel[mediaRelinkState="mismatch"], QLabel[reviewTone="attention"] { color: #ffd18a; background: #51432f; }
        QPushButton:focus, QToolButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTableView:focus { border: 2px solid #409cff; }
        QStatusBar { border-top: 1px solid #3b434c; color: #aeb8c4; }
        QMenu { background: #2c333b; color: #ecf0f5; border: 1px solid #48515b; padding: 5px; }
        QMenu::item { padding: 5px 22px; }
        QMenu::item:selected { background: #234c75; }
        QMenu::item:disabled { color: #7e8995; }
    """)
