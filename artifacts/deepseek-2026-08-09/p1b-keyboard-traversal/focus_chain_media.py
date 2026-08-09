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

    # Keyboard-activate Add media with a real media file selected through the dialog.
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([MEDIA], "")):
        window.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)
        for _ in range(10):
            app.processEvents()
        QTest.keyClick(window.add_media_button, Qt.Key.Key_Space)

        # Evidence: while the media probe is running, the Cancel Import control
        # must be keyboard-reachable. Walk Tab from the current focus briefly.
        importing_focus: list[dict] = []
        importing_seen: set[tuple] = set()
        for _ in range(12):
            current = QApplication.focusWidget()
            entry = describe(current)
            if entry is None:
                break
            key = (
                entry["class"],
                entry["object_name"],
                entry["text"],
                entry["accessible_name"],
            )
            entry["step"] = len(importing_focus)
            importing_focus.append(entry)
            if key in importing_seen:
                break
            importing_seen.add(key)
            QTest.keyClick(current, Qt.Key.Key_Tab)
            for _ in range(4):
                app.processEvents()

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

    focus = QApplication.focusWidget()
    if focus is None:
        window.add_media_button.setFocus(Qt.FocusReason.OtherFocusReason)
        for _ in range(10):
            app.processEvents()
        focus = QApplication.focusWidget()

    visited: list[dict] = []
    seen = set()
    first_key = None
    for step in range(80):
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
        QTest.keyClick(focus, Qt.Key.Key_Tab)
        for _ in range(8):
            app.processEvents()

    result = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "media_added_via_keyboard": task_created,
        "media_available": media_available,
        "task_title": window.tasks[0].title() if window.tasks else None,
        "importing_focus_steps": importing_focus,
        "cancel_import_reachable": any(
            "Cancel Import" in (e.get("text") or "")
            for e in importing_focus
        ),
        "steps": visited,
        "loop_closed": first_key is not None and any(
            (e["class"], e["object_name"], e["text"], e["accessible_name"]) == first_key
            for e in visited[1:]
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    # Evidence harness only: exit without Qt teardown so a still-finishing
    # background thread cannot SIGABRT this probe process. Graceful-close
    # evidence is captured separately (p1b-native-ui.md, AX close button).
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
