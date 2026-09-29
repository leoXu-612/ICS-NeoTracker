"""Quiet desktop chrome: system typography, one accent, clear content hierarchy."""

def apply_desktop_theme(window) -> None:
    # Inherit QApplication's system/user font, including larger text settings.
    window.setStyleSheet("""
        QMainWindow, QWidget#appRoot, QScrollArea, QStatusBar { background: #f5f6f8; color: #24272d; }
        QWidget { color: #24272d; }
        QFrame#topToolbar { background: #fbfbfc; border: none; border-bottom: 1px solid #e2e4e8; }
        QLabel#appTitle { font-weight: 650; color: #20242c; }
        QLabel#mediaTitle { color: #6c737f; }
        QFrame#navigationRail { background: #edf0f4; border: none; border-radius: 12px; }
        QListWidget#workflowNavigation { background: transparent; border: none; padding: 0; outline: none; }
        QListWidget#workflowNavigation::item { min-height: 25px; padding: 6px 10px; margin: 2px 0; border-radius: 7px; }
        QListWidget#workflowNavigation::item:selected { background: #dbe7f6; color: #125eae; font-weight: 600; }
        QListWidget#workflowNavigation::item:hover:!selected { background: #e3e7ec; }
        QLabel#navigationTitle, QLabel#sectionLabel { color: #747d89; font-weight: 600; padding: 8px 2px 4px; }
        QLabel#inspectorHeading { font-weight: 600; padding: 5px 9px 9px; }
        QWidget#rightSidebar { background: #fbfcfd; border-radius: 12px; }
        QLabel#previewTitle { color: #67717f; font-weight: 500; }
        QLabel#previewCanvas { background: #171c24; color: #bac3cf; border: 1px solid #242c37; border-radius: 12px; }
        QFrame#transportBar { background: #ffffff; border: 1px solid #e0e5eb; border-radius: 9px; }
        QTabWidget::pane { border: none; background: transparent; }
        QTabBar::tab { background: transparent; color: #727b88; padding: 6px 11px; margin: 3px 1px; border: none; border-radius: 6px; }
        QTabBar::tab:selected { background: #e4edf8; color: #155faa; font-weight: 600; }
        QTabBar::tab:hover:!selected { background: #edf0f4; }
        QPushButton, QToolButton { background: #ffffff; border: 1px solid #dce1e8; border-radius: 7px; padding: 5px 9px; min-height: 22px; color: #343e4c; }
        QPushButton:hover, QToolButton:hover { background: #f0f4f9; border-color: #c5d1df; }
        QPushButton:pressed, QToolButton:pressed { background: #dde8f5; border-color: #aac4e1; }
        QPushButton:disabled, QToolButton:disabled { background: #f3f5f7; border-color: #e5e9ee; color: #929ba7; }
        QPushButton#runTrackingButton, QPushButton#addMediaButton, QPushButton#applyRoiGeometryButton,
        QPushButton#applyCalibrationButton, QPushButton#applyMediaRelinkButton, QPushButton#runPhysicsFitButton {
            background: #2478d4; border-color: #2478d4; color: white; font-weight: 600;
        }
        QPushButton#runTrackingButton:hover, QPushButton#addMediaButton:hover { background: #1868c2; }
        QPushButton#runTrackingButton:pressed, QPushButton#addMediaButton:pressed { background: #1156a4; }
        QPushButton#runTrackingButton:disabled, QPushButton#addMediaButton:disabled,
        QPushButton#applyRoiGeometryButton:disabled, QPushButton#applyCalibrationButton:disabled,
        QPushButton#applyMediaRelinkButton:disabled, QPushButton#runPhysicsFitButton:disabled {
            background: #e6eef8; border-color: #e0e9f3; color: #809ab7;
        }
        QPushButton[trackingBusy="true"], QPushButton[taskDestructive="true"] { background: #c8403c; border-color: #c8403c; color: white; }
        QPushButton#transportButton { min-width: 34px; background: transparent; border-color: transparent; }
        QPushButton#transportButton:hover { background: #eaf0f7; }
        QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTableView, QComboBox, QSpinBox, QDoubleSpinBox {
            background: #ffffff; border: 1px solid #dfe4eb; border-radius: 6px; padding: 4px; selection-background-color: #dceafb; selection-color: #1b4f8e;
        }
        QTextBrowser#presetDescription { background: transparent; border: none; color: #6e7886; padding: 2px; }
        QListWidget#taskList { background: #f7f9fc; border: 1px solid #e0e5ec; }
        QListWidget::item { padding: 6px; border-radius: 5px; }
        QListWidget::item:selected { background: #dceafb; color: #1b4f8e; }
        QListWidget::item:disabled { color: #7d8794; }
        QTableView { gridline-color: #eef1f5; alternate-background-color: #f8fafc; }
        QHeaderView::section { background: #f1f4f8; color: #647082; border: none; padding: 7px 8px; font-weight: 500; }
        QCheckBox { spacing: 7px; }
        QSlider::groove:horizontal { height: 4px; background: #dce3ed; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: #76a6df; border-radius: 2px; }
        QSlider::handle:horizontal { background: #ffffff; border: 1px solid #bdc9d8; width: 13px; margin: -5px 0; border-radius: 7px; }
        QSplitter::handle { background: transparent; }
        QSplitter::handle:hover { background: #d7e3f2; }
        QLabel#statusChip, QLabel#projectStateLabel { color: #637184; background: #eef2f7; border-radius: 6px; padding: 4px 7px; }
        QLabel#summaryChip { color: #728094; background: transparent; border: none; }
        QLabel#globalProjectDirtyLabel, QLabel#globalDraftLabel { color: #99681b; background: #fff0d4; border-radius: 6px; padding: 4px 7px; }
        QLabel#projectNameLabel { font-weight: 600; }
        QLabel[trackingOutcome="complete"], QLabel[calibrationState="applied"], QLabel[mediaRelinkState="match"], QLabel[analysisState="complete"] { color: #27724c; background: #eaf5ee; }
        QLabel[trackingOutcome="failed"], QLabel[calibrationState="error"], QLabel[roiGeometryState="error"], QLabel[analysisState="failed"] { color: #ad3434; background: #fff0ee; }
        QLabel[trackingOutcome="partial"], QLabel[mediaRelinkState="unverified"], QLabel[mediaRelinkState="mismatch"], QLabel[reviewTone="attention"] { color: #93651e; background: #fff4de; }
        QLabel#reviewSelectionLabel, QLabel#reviewSelectionDetailLabel, QLabel#analysisStatusLabel,
        QLabel#calibrationEditorMessage, QLabel#roiGeometryMessage, QLabel#mediaRelinkStatus,
        QLabel#mediaRelinkDifferences { border: none; border-radius: 6px; padding: 6px 8px; }
        QLabel#trackingBackendLabel, QLabel#trackingPerformanceLabel, QLabel#responseStatusLabel,
        QLabel#taskActionSummary, QLabel#mediaRelinkDetail { color: #728094; }
        QPushButton:focus, QToolButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTableView:focus { border: 2px solid #4b93e1; }
        QStatusBar { color: #748092; border-top: 1px solid #e5e9ef; }
    """)
