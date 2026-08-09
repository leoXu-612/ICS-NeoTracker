from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
sys.path.insert(0, os.getcwd())

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from neo_tracker.ui.main_window import NeoTrackerWindow


def describe(widget: QWidget | None) -> dict | None:
    if widget is None:
        return None
    return {
        "class": widget.__class__.__name__,
        "object_name": widget.objectName(),
        "text": (widget.text() if hasattr(widget, "text") else "") or "",
        "accessible_name": widget.accessibleName(),
        "enabled": widget.isEnabled(),
        "visible": widget.isVisible(),
        "has_focus": widget.hasFocus(),
    }


def main() -> int:
    app = QApplication.instance() or QApplication([])
    window = NeoTrackerWindow()
    window.show()
    for _ in range(30):
        app.processEvents()

    visited: list[dict] = []
    seen = set()
    focus = QApplication.focusWidget()
    if focus is None:
        window.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)
        for _ in range(10):
            app.processEvents()
        focus = QApplication.focusWidget()
    first_key = None
    for step in range(60):
        focus = QApplication.focusWidget()
        entry = describe(focus)
        if entry is None:
            break
        key = (entry["class"], entry["object_name"], entry["text"], entry["accessible_name"])
        if first_key is None:
            first_key = key
        entry["step"] = step
        visited.append(entry)
        if key in seen:
            break
        seen.add(key)
        key = Qt.Key.Key_Backtab if "--reverse" in sys.argv else Qt.Key.Key_Tab
        QTest.keyClick(focus, key)
        for _ in range(8):
            app.processEvents()

    result = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "window_title": window.windowTitle(),
        "steps": visited,
        "loop_closed": first_key is not None and any(
            (e["class"], e["object_name"], e["text"], e["accessible_name"]) == first_key
            for e in visited[1:]
        ),
    }
    if "--activate" in sys.argv:
        activation: list[dict] = []
        from PySide6.QtWidgets import QFileDialog
        from unittest.mock import patch
        with patch.object(
            QFileDialog,
            "getOpenFileNames",
            return_value=([]),
        ) as dialog:
            window.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)
            for _ in range(10):
                app.processEvents()
            QTest.keyClick(window.add_media_button, Qt.Key.Key_Space)
            for _ in range(10):
                app.processEvents()
            activation.append(
                {
                    "target": "add_media_button",
                    "key": "Space",
                    "dialog_called": dialog.called,
                }
            )
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=("", ""),
        ) as dialog:
            window.open_project_button.setFocus(Qt.FocusReason.OtherFocusReason)
            for _ in range(10):
                app.processEvents()
            QTest.keyClick(window.open_project_button, Qt.Key.Key_Space)
            for _ in range(10):
                app.processEvents()
            activation.append(
                {
                    "target": "open_project_button",
                    "key": "Space",
                    "dialog_called": dialog.called,
                }
            )
        result["activation"] = activation
    print(json.dumps(result, ensure_ascii=False, indent=2))
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
