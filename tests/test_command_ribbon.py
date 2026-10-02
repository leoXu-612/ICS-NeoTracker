import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListView

from neo_tracker.ui.language import set_language
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.view_state import PhysicsWorkspaceStateStore
from tests.test_ui_main_window import close_window_safely


class CommandRibbonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        set_language("zh_CN")
        self.addCleanup(set_language, "en")
        self.window = NeoTrackerWindow(physics_layout_store=PhysicsWorkspaceStateStore())
        self.addCleanup(close_window_safely, self.window)
        self.window.resize(1024, 768)
        self.window.show()
        self.app.processEvents()

    def test_grouped_commands_fit_and_primary_actions_remain_visible_on_every_page(self):
        window = self.window
        self.assertEqual((window.width(), window.height()), (1024, 768))
        self.assertEqual(window.workflow_navigation.flow(), QListView.Flow.LeftToRight)
        for index in range(7):
            window.workflow_navigation.setCurrentRow(index)
            self.app.processEvents()
            self.assertEqual(window.command_ribbon.pages.currentIndex(), window.workflow_order[index])
            for button in (window.add_media_button, window.open_project_button, window.save_project_button,
                           window.run_tracking_button, window.export_tracking_csv_button):
                self.assertTrue(button.isVisible())
                rectangle = button.rect()
                point = button.mapTo(window, rectangle.bottomRight())
                self.assertTrue(window.rect().contains(point), button.objectName())

    def test_registry_command_has_one_handler_and_cannot_bypass_disabled_state(self):
        window = self.window
        button = window.command_ribbon.command_buttons["physics.velocity"]
        self.assertIs(button.defaultAction(), window.action_registry.action("physics.velocity"))
        window._set_action_enabled("physics.velocity", True)
        with patch.object(window, "_create_physics_velocity") as handler:
            button.click()
            handler.assert_called_once()
            window._set_action_enabled("physics.velocity", False)
            self.assertFalse(button.isEnabled())
            button.click()
            handler.assert_called_once()

    def test_parameter_command_tracks_source_and_parent_enable_gates(self):
        window = self.window
        source = window.sample_marker_button
        button = window.command_ribbon.parameter_buttons["marker.sample"]
        calls = []
        with patch.object(window, "_prepare_preview_selection", return_value=False):
            source.clicked.connect(lambda: calls.append(True))
            source.setEnabled(True)
            button.click()
            self.assertEqual(calls, [True])
            source.setEnabled(False)
            self.assertFalse(button.isEnabled())
            button.click()
            self.assertEqual(calls, [True])
            source.setEnabled(True)
            window.sidebar_tabs.setEnabled(False)
            self.assertFalse(button.isEnabled())

    def test_busy_gate_keeps_cancel_reachable_and_rejects_stage_switch(self):
        window = self.window
        window.sidebar_tabs.setCurrentWidget(window.tracking_tab)
        window.sidebar_tabs.setEnabled(False)
        window._update_action("tracking.run", enabled=True, text="取消")
        window.workflow_navigation.setCurrentRow(0)
        self.assertIs(window.sidebar_tabs.currentWidget(), window.tracking_tab)
        self.assertEqual(window.workflow_navigation.currentRow(), window.workflow_order.index(1))
        self.assertTrue(window.run_tracking_button.isVisible())
        self.assertTrue(window.run_tracking_button.isEnabled())

    def test_arrow_navigation_routes_by_stable_index_and_parameters_are_not_duplicated(self):
        window = self.window
        window.workflow_navigation.setFocus()
        QTest.keyClick(window.workflow_navigation, Qt.Key.Key_Right)
        self.assertIs(window.sidebar_tabs.currentWidget(), window.calibration_tab)
        self.assertEqual(window.command_ribbon.pages.currentIndex(), 4)
        self.assertTrue(window.calibration_editor.isVisible())
        self.assertFalse(window.mark_calibration_button.isVisible())
        self.assertTrue(window.command_ribbon.parameter_buttons["calibration.mark"].isVisible())
