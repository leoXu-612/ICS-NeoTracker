from __future__ import annotations

from neo_tracker.ui.language import tr

from PySide6.QtWidgets import QMessageBox


class PreviewSelectionMixin:
    """Coordinate preview tools without replacing an active selection draft silently."""

    def _active_preview_selection_draft_name(self) -> str | None:
        return {
            "roi_rectangle": "ROI drawing",
            "roi_circle": "ROI drawing",
            "roi_annulus": "ROI drawing",
            "roi_polygon": "ROI drawing",
            "roi_curve_band": "ROI drawing",
            "calibration": "Calibration drawing",
            "manual_point": "Manual correction",
            "sample_color": "Color sampling",
        }.get(self.preview_label.selection_mode())

    def _confirm_preview_selection_replacement(self, action: str) -> bool:
        draft_name = self._active_preview_selection_draft_name()
        return draft_name is None or self._confirm_draft_replacement(action, (draft_name,))

    def _prepare_preview_selection(self, action: str) -> bool:
        if not self._confirm_preview_selection_replacement(action):
            return False
        self._stop_playback()
        return True

    def _finish_preview_selection_before(self, action: str) -> bool:
        if self._active_preview_selection_draft_name() is None:
            return True
        if not self._confirm_preview_selection_replacement(action):
            return False
        self.preview_label.cancel_selection()
        return True

    def _start_roi_selection(self) -> None:
        if not self._prepare_preview_selection("starting a rectangle ROI drawing"):
            return
        if not self.preview_label.begin_roi_selection():
            QMessageBox.information(
                self,
                tr('ROI selection'),
                tr('Load a readable video frame before marking an ROI.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)

    def _start_circular_roi_selection(self) -> None:
        if not self._prepare_preview_selection("starting a circle ROI drawing"):
            return
        if not self.preview_label.begin_circular_roi_selection():
            QMessageBox.information(
                self,
                tr('Circle ROI selection'),
                tr('Load a readable video frame before marking a circle ROI.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)

    def _start_annular_roi_selection(self) -> None:
        if not self._prepare_preview_selection("starting an annular ROI drawing"):
            return
        if not self.preview_label.begin_annular_roi_selection():
            QMessageBox.information(
                self,
                tr('Annular ROI selection'),
                tr('Load a readable video frame before marking an annular ROI.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)

    def _start_polygon_roi_selection(self) -> None:
        if not self._prepare_preview_selection("starting a polygon ROI drawing"):
            return
        if not self.preview_label.begin_polygon_roi_selection():
            QMessageBox.information(
                self,
                tr('Polygon ROI selection'),
                tr('Load a readable video frame before marking a polygon ROI.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)
        self.statusBar().showMessage(
            tr('Click polygon vertices in the preview, then use Finish Drawing, right click, or double click.'),
            8000,
        )

    def _start_curve_band_roi_selection(self) -> None:
        if not self._prepare_preview_selection("starting a curve-band ROI drawing"):
            return
        if not self.preview_label.begin_curve_band_roi_selection(self.curve_half_width_spin.value()):
            QMessageBox.information(
                self,
                tr('Curve band ROI selection'),
                tr('Load a readable video frame before marking a curve band ROI.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)
        self.statusBar().showMessage(
            tr('Click curve centerline points in the preview, then use Finish Drawing, right click, or double click.'),
            8000,
        )

    def _start_calibration_selection(self) -> None:
        if not self._prepare_preview_selection("starting a calibration drawing"):
            return
        if not self.preview_label.begin_calibration_selection():
            QMessageBox.information(
                self,
                tr('Calibration rod selection'),
                tr('Load a readable video frame before marking a calibration rod.'),
            )
            return
        if self.calibration_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.calibration_tab)

    def _start_color_sampling(self) -> None:
        if self._color_blob_observation() is None:
            QMessageBox.information(
                self,
                tr('Marker color'),
                tr('The selected preset does not use color-marker detection.'),
            )
            return
        if not self._prepare_preview_selection("starting color sampling"):
            return
        if not self.preview_label.begin_color_sample_selection():
            QMessageBox.information(
                self,
                tr('Marker color'),
                tr('Load a readable video frame before sampling marker color.'),
            )
            return
        if self.tracking_tab is not None:
            self.sidebar_tabs.setCurrentWidget(self.tracking_tab)
        self.statusBar().showMessage(tr('Click the marker color in the preview canvas.'), 6000)
