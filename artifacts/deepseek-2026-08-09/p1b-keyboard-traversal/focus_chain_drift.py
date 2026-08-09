from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.getcwd())

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from neo_tracker.ui.main_window import NeoTrackerWindow

MEDIA = "/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker/artifacts/experiment-videos/red-dot-tracking.mp4"


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
    for _ in range(20):
        app.processEvents()

    with patch.object(QFileDialog, "getOpenFileNames", return_value=([MEDIA], "")):
        window.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)
        for _ in range(10):
            app.processEvents()
        QTest.keyClick(window.add_media_button, Qt.Key.Key_Space)
        deadline = 800
        while deadline > 0 and (
            window._media_probe_thread is not None or not window.tasks
        ):
            app.processEvents()
            time.sleep(0.01)
            deadline -= 1
        for _ in range(40):
            app.processEvents()
            time.sleep(0.005)

    task_created = bool(window.tasks)
    media_available = bool(
        window.tasks
        and window.tasks[0].media_info is not None
        and window.tasks[0].media_info.available
    )

    # Enter the source-drift state: the saved identity no longer matches.
    window.tasks[0].media_identity_requires_review = True
    window._render_task(refresh_project_state=False)
    for _ in range(20):
        app.processEvents()

    run_tracking_enabled = window.run_tracking_button.isEnabled()
    relink_reachable = False

    visited: list[dict] = []
    seen = set()
    first_key = None
    focus = window.add_media_button
    for step in range(80):
        focus = QApplication.focusWidget() or focus
        entry = describe(focus)
        if entry is None:
            break
        key = (entry["class"], entry["object_name"], entry["text"], entry["accessible_name"])
        if first_key is None:
            first_key = key
        entry["step"] = step
        visited.append(entry)
        if "Relink" in entry["text"] or "Choose replacement media" in entry["accessible_name"]:
            relink_reachable = True
        if key in seen:
            break
        seen.add(key)
        QTest.keyClick(focus, Qt.Key.Key_Tab)
        for _ in range(8):
            app.processEvents()

    result = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "media_available": media_available,
        "drift_state_entered": bool(window.tasks and window.tasks[0].media_identity_requires_review),
        "run_tracking_enabled_in_drift": run_tracking_enabled,
        "relink_reachable": relink_reachable,
        "steps": visited,
        "loop_closed": first_key is not None and any(
            (e["class"], e["object_name"], e["text"], e["accessible_name"]) == first_key
            for e in visited[1:]
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
