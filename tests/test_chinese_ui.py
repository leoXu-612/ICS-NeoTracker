import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from neo_tracker.ui.language import set_language, tr
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.project_status_panel import build_result_replacement_dialog
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore, PhysicsWorkspaceState
from tests.test_ui_main_window import close_window_safely


class ChineseDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language("zh_CN")
        self.addCleanup(set_language, "en")
        self.window = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
        self.addCleanup(close_window_safely, self.window)

    def test_core_actions_navigation_and_small_window_are_chinese(self):
        window = self.window
        window.resize(1024, 768)
        window.show()
        self.app.processEvents()
        self.assertEqual((window.width(), window.height()), (1024, 768))
        self.assertEqual(window.add_media_button.text(), "导入素材")
        self.assertEqual(window.save_project_button.text(), "保存项目")
        self.assertEqual(window.run_tracking_button.text(), "开始追踪")
        self.assertEqual([window.workflow_navigation.itemText(i) for i in range(7)],
                         ["素材", "定标", "追踪", "检查", "信号", "流程", "高级"])
        self.assertFalse(window.sidebar_tabs.tabBar().isVisible())
        self.assertIn("从一段实验视频开始", window.preview_label.text())
        for index in range(7):
            window.workflow_navigation.setCurrentIndex(index)
            self.app.processEvents()
            target = window.workflow_order[index]
            self.assertEqual(window.sidebar_tabs.currentIndex(), target)
            self.assertFalse(window.sidebar_tabs.widget(target).horizontalScrollBar().isVisible())

    def test_translated_pages_keep_canonical_persistence_and_routes(self):
        workspace = self.window.physics_workspace
        for name in ("Data", "Plot", "Fit", "Diagnostics", "Runs", "Edits", "Signal"):
            workspace.show_page(name)
            self.assertEqual(workspace.layout_state().page, name)
            self.assertEqual(workspace.tabs.tabText(workspace.tabs.currentIndex()), tr(name))
        workspace.apply_layout_state(PhysicsWorkspaceState(page="Plot"))
        self.assertEqual(workspace.current_page, "Plot")

    def test_model_labels_do_not_change_scientific_identifiers(self):
        panel = self.window.fit_panel
        index = panel.model_combo.findData("exponential")
        panel.model_combo.setCurrentIndex(index)
        self.assertEqual(panel.model_combo.currentText(), "指数")
        self.assertEqual(panel.model_combo.currentData(), "exponential")
        self.assertFalse(panel.initial_parameters_edit.isHidden())
        self.assertEqual(self.window.preset_combo.currentData(), "color_marker")

    def test_chinese_destructive_dialog_keeps_reject_as_default(self):
        dialog = build_result_replacement_dialog(self.window, task_name="实验 A", result_count=23, edit_count=4)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.defaultButton().text(), "保留当前结果")
        self.assertEqual(dialog.buttonRole(dialog.defaultButton()), QMessageBox.ButtonRole.RejectRole)
        self.assertIn("23 条结果", dialog.informativeText())
        self.assertIn("4 条人工修订", dialog.informativeText())
        self.assertIn("实验 A", dialog.text())

    def test_navigation_respects_busy_gate_and_export_headers_stay_canonical(self):
        window = self.window
        original = window.sidebar_tabs.currentIndex()
        window.sidebar_tabs.setEnabled(False)
        window.workflow_navigation.setCurrentIndex(4)
        self.assertEqual(window.sidebar_tabs.currentIndex(), original)
        self.assertEqual(window.workflow_navigation.currentIndex(), window.workflow_order.index(original))
        model = window.physics_workspace.series_model
        self.assertEqual(model.headerData(0, Qt.Orientation.Horizontal), "帧")
        self.assertEqual(model.copy_rows([]), "Frame\tTime\tValue\tValid\tSource\tUnit")
