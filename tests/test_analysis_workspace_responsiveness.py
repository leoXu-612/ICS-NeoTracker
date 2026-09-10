from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QPoint, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QScrollArea, QWidget

from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.physics_plot import PhysicsPlot
from neo_tracker.ui.view_state import PhysicsWorkspaceState, PhysicsWorkspaceStateStore
from neo_tracker.ui.workspaces import PhysicsWorkspace
from tests.test_analysis_workspace import make_series


class RetinaPhysicsPlot(PhysicsPlot):
    def devicePixelRatioF(self) -> float:  # noqa: N802
        return 2.0


class PhysicsWorkspaceResponsiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.gui_anchor = QWidget()

    def make_window(
        self,
        store: PhysicsWorkspaceStateStore | None = None,
    ) -> NeoTrackerWindow:
        window = NeoTrackerWindow(physics_layout_store=store)

        def close_cleanly() -> None:
            window._discard_unapplied_drafts(show_status=False)
            window._set_project_clean()
            window.close()
            QCoreApplication.processEvents()

        self.addCleanup(close_cleanly)
        return window

    def test_supported_window_sizes_keep_shell_workspace_and_recovery_controls_visible(self) -> None:
        window = self.make_window(PhysicsWorkspaceStateStore())
        window.show()
        for width, height in ((1024, 768), (1280, 808), (1440, 900)):
            with self.subTest(size=(width, height)):
                window.resize(width, height)
                QCoreApplication.processEvents()

                self.assertEqual((window.width(), window.height()), (width, height))
                self.assertGreater(window.main_splitter.sizes()[0], 0)
                self.assertGreater(window.main_splitter.sizes()[1], 0)
                self.assertGreaterEqual(window.physics_workspace.height(), 38)
                self.assertLess(
                    window.physics_workspace.collapse_button.geometry().right(),
                    window.physics_workspace.width(),
                )
                self.assertTrue(window.physics_workspace.tabs.isVisible())
                window.physics_workspace.set_collapsed(True)
                QCoreApplication.processEvents()
                self.assertEqual(window.physics_workspace.collapse_button.text(), "Expand")
                window.physics_workspace.set_collapsed(False)
                QCoreApplication.processEvents()
                self.assertEqual(window.physics_workspace.collapse_button.text(), "Collapse")

    def test_fit_controls_keep_native_height_and_scroll_with_keyboard_focus(self) -> None:
        window = self.make_window(PhysicsWorkspaceStateStore())
        window.resize(1024, 768)
        window.show()
        window.set_physics_series((make_series(),))
        workspace, panel = window.physics_workspace, window.fit_panel
        workspace.show_page("Fit")
        for model in ("Linear", "Sinusoidal"):
            with self.subTest(model=model):
                panel.model_combo.setCurrentText(model)
                QCoreApplication.processEvents()
                controls = [
                    panel.series_combo, panel.model_combo,
                    panel.range_start_spin, panel.range_end_spin, panel.valid_only_checkbox,
                ]
                if model == "Sinusoidal":
                    controls.extend((panel.initial_parameters_edit, panel.bounds_edit))
                controls.extend((panel.run_button, panel.export_button, panel.parameter_table))
                for widget in controls:
                    self.assertGreaterEqual(widget.height(), widget.minimumSizeHint().height())
                self.assertEqual((window.width(), window.height()), (1024, 768))
                self.assertLessEqual(window.minimumSizeHint().height(), 768)

                scroll = workspace.tabs.currentWidget()
                self.assertIsInstance(scroll, QScrollArea)
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                panel.series_combo.setFocus(Qt.FocusReason.TabFocusReason)
                QCoreApplication.processEvents()
                for widget in controls[1:]:
                    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)
                    QCoreApplication.processEvents()
                    self.assertIs(QApplication.focusWidget(), widget)
                    rectangle = QRect(widget.mapTo(scroll.viewport(), QPoint()), widget.size())
                    self.assertTrue(scroll.viewport().rect().contains(rectangle))
                self.assertGreater(scroll.verticalScrollBar().value(), 0)
                for widget in reversed(controls[:-1]):
                    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
                    QCoreApplication.processEvents()
                    self.assertIs(QApplication.focusWidget(), widget)
                    rectangle = QRect(widget.mapTo(scroll.viewport(), QPoint()), widget.size())
                    self.assertTrue(scroll.viewport().rect().contains(rectangle))

    def test_plot_stays_inside_its_containers_at_small_and_restored_tray_sizes(self) -> None:
        for stored_height in (None, 120):
            store = PhysicsWorkspaceStateStore()
            if stored_height is not None:
                store.save(PhysicsWorkspaceState(True, "Plot", stored_height))
            window = self.make_window(store)
            window.set_physics_series((make_series(),))
            window.show()
            workspace = window.physics_workspace
            workspace.show_page("Plot")
            for width, height in ((1024, 768), (1280, 808), (1440, 900)):
                window.resize(width, height)
                for stage in ("opened", "compressed", "expanded"):
                    with self.subTest(stored_height=stored_height, size=(width, height), stage=stage):
                        if stage == "compressed":
                            window.workspace_splitter.setSizes([window.workspace_splitter.height(), 120])
                        elif stage == "expanded":
                            workspace.set_collapsed(True)
                            QCoreApplication.processEvents()
                            self.assertLessEqual(workspace.height(), 38)
                            self.assertFalse(workspace.export_plot_image_button.isVisible())
                            workspace.set_collapsed(False)
                        QCoreApplication.processEvents()
                        self.assertEqual((window.width(), window.height()), (width, height))
                        self.assertGreaterEqual(workspace.plot.height(), 180)
                        self.assertTrue(workspace.export_plot_image_button.isVisible())
                        self.assertGreaterEqual(
                            workspace.export_plot_image_button.height(),
                            workspace.export_plot_image_button.minimumSizeHint().height(),
                        )
                        for widget in (workspace.plot, workspace.export_plot_image_button):
                            parent = widget.parentWidget()
                            while parent is not None:
                                rectangle = QRect(widget.mapTo(parent, QPoint()), widget.size())
                                self.assertTrue(
                                    parent.rect().contains(rectangle),
                                    f"{parent.objectName()}: {parent.rect()} clips {rectangle}",
                                )
                                parent = parent.parentWidget()
            for page in ("Data", "Fit", "Plot"):
                workspace.show_page(page)
                QCoreApplication.processEvents()
                self.assertEqual(workspace.export_plot_image_button.isVisible(), page == "Plot")
            workspace.export_plot_image_button.setFocus(Qt.FocusReason.TabFocusReason)
            self.assertIs(window.focusWidget(), workspace.export_plot_image_button)

    def test_canvas_focus_is_reversible_and_does_not_persist_transient_collapse(self) -> None:
        store = PhysicsWorkspaceStateStore()
        window = self.make_window(store)
        window.physics_workspace.show_page("Plot")
        window.physics_workspace.remember_height(320)
        store.save(window.physics_workspace.layout_state())
        window.show()
        QCoreApplication.processEvents()
        before_sizes = tuple(window.main_splitter.sizes())

        window.action_registry.action("view.canvas_focus").trigger()
        QCoreApplication.processEvents()
        self.assertTrue(window.canvas_focus_active)
        self.assertTrue(window.right_sidebar.isHidden())
        self.assertTrue(window.physics_workspace.collapsed)
        self.assertEqual(window.canvas_focus_button.text(), "Exit Focus")
        self.assertIn(
            "Restore the inspector",
            window.canvas_focus_button.accessibleDescription(),
        )
        self.assertEqual(store.load(), PhysicsWorkspaceState(False, "Plot", 320))

        window.action_registry.action("view.canvas_focus").trigger()
        QCoreApplication.processEvents()
        self.assertFalse(window.canvas_focus_active)
        self.assertFalse(window.right_sidebar.isHidden())
        self.assertFalse(window.physics_workspace.collapsed)
        self.assertEqual(window.physics_workspace.current_page, "Plot")
        self.assertEqual(window.canvas_focus_button.text(), "Canvas Focus")
        self.assertIn(
            "Temporarily hide the inspector",
            window.canvas_focus_button.accessibleDescription(),
        )
        self.assertEqual(len(window.main_splitter.sizes()), len(before_sizes))

    def test_bounded_layout_state_is_restored_by_a_new_window(self) -> None:
        store = PhysicsWorkspaceStateStore()
        first = self.make_window(store)
        first.physics_workspace.show_page("Plot")
        first.physics_workspace.remember_height(333)
        first.physics_workspace.set_collapsed(True)
        self.assertEqual(store.load(), PhysicsWorkspaceState(True, "Plot", 333))
        first._set_project_clean()
        first.close()
        QCoreApplication.processEvents()

        restored = self.make_window(store)

        self.assertEqual(restored.physics_workspace.layout_state(), PhysicsWorkspaceState(True, "Plot", 333))
        self.assertLessEqual(restored.workspace_splitter.sizes()[1], 38)

    def test_forward_and_reverse_tab_traverse_data_controls(self) -> None:
        workspace = PhysicsWorkspace()
        workspace.set_series((make_series(),))
        workspace.show()
        workspace.series_combo.setFocus(Qt.FocusReason.TabFocusReason)
        QCoreApplication.processEvents()
        self.assertIs(workspace.focusWidget(), workspace.series_combo)

        QTest.keyClick(workspace.series_combo, Qt.Key.Key_Tab)
        QCoreApplication.processEvents()
        self.assertIs(workspace.focusWidget(), workspace.series_table)

        QTest.keyClick(
            workspace.series_table,
            Qt.Key.Key_Tab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        QCoreApplication.processEvents()
        self.assertIs(workspace.focusWidget(), workspace.series_combo)
        workspace.close()

    def test_virtual_table_never_scans_all_rows_to_size_columns(self) -> None:
        workspace = PhysicsWorkspace()
        header = workspace.series_table.horizontalHeader()

        for section in range(workspace.series_model.columnCount()):
            self.assertNotEqual(
                header.sectionResizeMode(section),
                QHeaderView.ResizeMode.ResizeToContents,
            )

    def test_retina_export_uses_physical_pixels_and_plot_has_textual_semantics(self) -> None:
        plot = RetinaPhysicsPlot()
        plot.resize(320, 200)
        plot.set_series((make_series(),))
        self.assertIn("true time", plot.accessibleDescription().lower())
        self.assertIn("invalid", plot.accessibleDescription().lower())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "retina-plot.png"
            self.assertTrue(plot.export_image(path))
            exported = QImage(str(path))
            self.assertEqual((exported.width(), exported.height()), (640, 400))

    def test_physics_actions_and_states_have_non_color_text_and_accessibility(self) -> None:
        window = self.make_window(PhysicsWorkspaceStateStore())
        buttons = (
            window.create_velocity_button,
            window.create_acceleration_button,
            window.smooth_series_button,
            window.fit_model_button,
            window.export_physics_analysis_button,
            window.show_residual_button,
            window.canvas_focus_button,
        )
        for button in buttons:
            with self.subTest(button=button.text()):
                self.assertTrue(button.text().strip())
                self.assertTrue(button.accessibleName().strip())
                self.assertTrue(button.accessibleDescription().strip())
        self.assertIn("No physical series", window.fit_panel.status_label.text())
        self.assertIn("no sample selected", window.physics_workspace.cursor_label.text())
        self.assertIn("No analysis selection", window.physics_inspector.object_label.text())


if __name__ == "__main__":
    unittest.main()
