import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from neo_tracker.ui.language import configure_language
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore

app = QApplication([])
configure_language(app, "zh_CN")
window = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
window.show()
app.processEvents()
out = Path(__file__).resolve().parent
visible_text = {}
for index in range(window.sidebar_tabs.count()):
    window.sidebar_tabs.setCurrentIndex(index)
    app.processEvents()
    name = window.sidebar_tabs.widget(index).objectName()
    visible_text[name] = sorted({widget.text() for widget in window.findChildren(QLabel) + window.findChildren(QPushButton)
                                if widget.isVisible() and widget.text()})
    if index in (0, 1, 4):
        window.grab().save(str(out / f"{name}.png"))
window.resize(1024, 768)
app.processEvents()
window.grab().save(str(out / "compact.png"))
visible_text["window_size"] = [window.width(), window.height()]
(out / "visible-text.json").write_text(json.dumps(visible_text, ensure_ascii=False, indent=2))
window._set_project_clean()
window.close()
app.processEvents()
