"""Engineering desktop: grouped commands and clearly bounded work panes."""


def apply_desktop_theme(window) -> None:
    # Inherit the system/user font; density comes from layout, not smaller text.
    window.setStyleSheet("""
        QMainWindow, QWidget#appRoot, QStatusBar { background: #f3f5f7; color: #24364a; }
        QWidget { color: #24364a; }
        QFrame#topToolbar { background: #0b4778; border: none; }
        QLabel#appTitle { color: white; font-weight: 650; }
        QLabel#mediaTitle, QLabel#summaryChip { color: #dae6f2; background: transparent; border: none; }
        QLabel#statusChip { color: #16436c; background: #e3eef8; border-radius: 2px; padding: 3px 7px; }
        QLabel#statusChip[trackingOutcome="complete"] { color: #236045; background: #e3f2e9; }
        QLabel#statusChip[trackingOutcome="partial"] { color: #805914; background: #fff0ce; }
        QLabel#statusChip[trackingOutcome="failed"] { color: #a03030; background: #ffe9e5; }
        QLabel#globalProjectDirtyLabel, QLabel#globalDraftLabel { color: #835818; background: #fff0cf; border-radius: 2px; padding: 3px 6px; }
        QListWidget#workflowNavigation { background: #0b4778; border: none; padding: 0; outline: none; }
        QListWidget#workflowNavigation::item { color: #edf4fb; padding: 3px 13px; margin: 0; border-radius: 0; }
        QListWidget#workflowNavigation::item:selected { background: #f3f5f7; color: #0b4778; font-weight: 650; }
        QListWidget#workflowNavigation::item:hover:!selected { background: #23608f; }
        QListWidget#workflowNavigation:focus { border-bottom: 2px solid #82b7e5; }
        QWidget#ribbonHost, QFrame#commandRibbon { background: #f3f5f7; border: none; border-bottom: 1px solid #bdc9d6; }
        QFrame#ribbonGroup { background: transparent; border: none; border-right: 1px solid #cad2db; }
        QLabel#ribbonGroupCaption { color: #637285; background: transparent; padding-top: 2px; }
        QFrame#commandRibbon QScrollArea, QFrame#commandRibbon QScrollArea > QWidget > QWidget { background: #f3f5f7; }
        QPushButton[ribbonCommand="true"], QToolButton[ribbonCommand="true"] { background: transparent; border: 1px solid transparent; border-radius: 2px; min-height: 19px; padding: 2px 6px; }
        QPushButton[ribbonCommand="true"]:hover, QToolButton[ribbonCommand="true"]:hover { background: #e1ecf7; border-color: #9fbcd7; }
        QPushButton[ribbonCommand="true"]:pressed, QToolButton[ribbonCommand="true"]:pressed { background: #cbdff1; }
        QPushButton#runTrackingButton { background: #197654; border: 1px solid #17694c; color: white; padding: 5px 12px; font-weight: 600; }
        QPushButton#runTrackingButton:hover { background: #126746; }
        QPushButton#runTrackingButton:disabled { background: #e6ebe9; border-color: #d4ded8; color: #8c9d94; }
        QPushButton#runTrackingButton[trackingBusy="true"] { background: #b74337; border-color: #b74337; color: white; }
        QFrame#navigationRail, QWidget#rightSidebar { background: white; border: 1px solid #cad2db; border-radius: 0; }
        QLabel#projectExplorerHeading, QLabel#inspectorHeading { background: #e5ecf3; border: none; border-bottom: 1px solid #cad2db; font-weight: 600; color: #254869; padding: 5px 7px; }
        QFrame#previewPaneHeader { background: #0b4778; border: none; }
        QLabel#previewTitle { color: white; font-weight: 600; }
        QLabel#previewCanvas { background: #1e252d; border: none; border-radius: 0; color: #c8d0da; }
        QFrame#transportBar { background: #eef2f6; border: 1px solid #cad2db; border-radius: 0; }
        QLabel#sectionLabel { color: #61748b; font-weight: 600; padding: 5px 0 2px; }
        QLabel#projectNameLabel { font-weight: 600; }
        QLabel#projectStateLabel { color: #637285; padding: 2px 4px; }
        QScrollArea, QTabWidget::pane { background: #f3f5f7; border: none; }
        QTabBar::tab { background: #edf1f5; border: 1px solid #cad2db; border-bottom: none; color: #566a80; padding: 4px 11px; margin: 0; border-radius: 0; }
        QTabBar::tab:selected { background: white; color: #0b4778; font-weight: 600; }
        QPushButton, QToolButton { background: #fbfcfd; border: 1px solid #c7d0db; border-radius: 2px; padding: 3px 7px; min-height: 20px; color: #24364a; }
        QPushButton:hover, QToolButton:hover { background: #e3eef8; border-color: #9ab6cf; }
        QPushButton:pressed, QToolButton:pressed { background: #cfdfef; }
        QPushButton:disabled, QToolButton:disabled { background: #f1f3f5; border-color: #e0e4e9; color: #98a2af; }
        QPushButton#transportButton { background: transparent; border-color: transparent; min-width: 30px; }
        QPushButton#transportButton:hover { background: #dae7f4; }
        QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QTableView, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #c7d0db; border-radius: 1px; padding: 3px; selection-background-color: #d4e6f6; selection-color: #16476f; }
        QListWidget#taskList { border: none; background: white; }
        QListWidget::item { padding: 4px 6px; border-radius: 0; }
        QListWidget::item:selected { background: #d4e6f6; color: #16476f; }
        QListWidget::item:disabled { color: #8190a0; }
        QTextBrowser#presetDescription { background: transparent; border: none; color: #637285; padding: 0; }
        QTableView { gridline-color: #e4e9ef; alternate-background-color: #f6f8fa; }
        QHeaderView::section { background: #e7edf4; color: #435d78; border: none; border-right: 1px solid #cad2db; border-bottom: 1px solid #cad2db; padding: 4px 7px; }
        QCheckBox { spacing: 5px; }
        QSlider::groove:horizontal { height: 3px; background: #c5d2e1; }
        QSlider::sub-page:horizontal { background: #437faf; }
        QSlider::handle:horizontal { background: #fbfcfe; border: 1px solid #809cb9; width: 10px; margin: -5px 0; border-radius: 2px; }
        QSplitter::handle { background: #e2e8ee; }
        QSplitter::handle:hover { background: #a9c4de; }
        QLabel#trackingBackendLabel, QLabel#trackingPerformanceLabel, QLabel#responseStatusLabel, QLabel#taskActionSummary, QLabel#mediaRelinkDetail { color: #687c92; }
        QLabel[calibrationState="error"], QLabel[roiGeometryState="error"], QLabel[analysisState="failed"] { color: #a12d30; background: #ffede9; }
        QLabel[mediaRelinkState="unverified"], QLabel[mediaRelinkState="mismatch"], QLabel[reviewTone="attention"] { color: #835818; background: #fff2d8; }
        QPushButton:focus, QToolButton:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTableView:focus { border: 2px solid #4b93e1; }
        QStatusBar { border-top: 1px solid #cad2db; color: #61748b; }
    """)
