from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from neo_tracker.media import MediaIdentity, MediaInfo
from neo_tracker.ui.media_relink_panel import MediaRelinkPanel
from neo_tracker.ui.project_controller import MediaRelinkAssessment


class MediaRelinkPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def video_info(*, available: bool = True) -> MediaInfo:
        return MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=available,
            error="Media file does not exist" if not available else "",
        )

    def test_missing_media_exposes_replacement_action(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)

        panel.set_current("/missing/video.mp4", self.video_info(available=False))

        self.assertEqual(panel.status_label.text(), "Media missing")
        self.assertEqual(panel.status_label.property("mediaRelinkState"), "missing")
        self.assertEqual(panel.browse_button.text(), "Choose Replacement…")
        self.assertTrue(panel.browse_button.isEnabled())
        self.assertFalse(panel.apply_button.isEnabled())
        self.assertTrue(panel.cancel_button.isHidden())

    def test_quarantined_source_is_not_misreported_as_missing(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)

        panel.show_source_review_required(
            MediaInfo(
                fps=20.0,
                frame_count=72,
                width=640,
                height=360,
                duration_s=3.6,
                available=False,
                error="Media at this path changed after it was loaded.",
            )
        )

        self.assertEqual(panel.status_label.text(), "Source changed")
        self.assertEqual(panel.status_label.property("mediaRelinkState"), "mismatch")
        self.assertEqual(panel.browse_button.text(), "Review Source…")
        self.assertTrue(panel.browse_button.isEnabled())
        self.assertFalse(panel.apply_button.isEnabled())

    def test_ready_media_explains_available_source_identity(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        identity = MediaIdentity("sampled-sha256-v1", "a" * 64, 50_000_000, 786_432)
        info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=identity,
        )

        panel.set_current("/media/video.mp4", info)

        self.assertEqual(panel.status_label.text(), "Media ready")
        self.assertIn("Bounded sampled SHA-256", panel.detail_label.text())
        self.assertIn("786,432 bytes", panel.detail_label.text())

    def test_matching_candidate_allows_apply_without_clearing_results(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        assessment = MediaRelinkAssessment(
            state="match",
            summary="Saved metadata matches the selected file.",
            can_apply=True,
        )

        panel.set_candidate("/new/video.mp4", self.video_info(), assessment)

        self.assertEqual(panel.status_label.text(), "Metadata match")
        self.assertIn("video.mp4", panel.candidate_label.text())
        self.assertIn("640×360", panel.candidate_label.text())
        self.assertEqual(panel.apply_button.text(), "Apply Relink")
        self.assertTrue(panel.apply_button.isEnabled())
        self.assertTrue(panel.differences_label.isHidden())

    def test_verified_candidate_names_full_source_identity(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        identity = MediaIdentity("full-sha256-v1", "a" * 64, 4096, 4096)
        info = MediaInfo(
            fps=20.0,
            frame_count=72,
            width=640,
            height=360,
            duration_s=3.6,
            available=True,
            source_identity=identity,
        )
        assessment = MediaRelinkAssessment(
            state="match",
            summary="Full-file SHA-256 source identity matches the saved project.",
            can_apply=True,
            identity_state="full",
        )

        panel.set_candidate("/new/video.mp4", info, assessment)

        self.assertEqual(panel.status_label.text(), "Source verified")
        self.assertIn("full digest", panel.candidate_label.text())
        self.assertEqual(panel.apply_button.text(), "Apply Relink")

    def test_digest_mismatch_uses_source_differs_title(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        assessment = MediaRelinkAssessment(
            state="mismatch",
            summary="Apply clears current results and edits.",
            differences=("Source content digest differs",),
            can_apply=True,
            clear_results=True,
            identity_state="mismatch",
        )

        panel.set_candidate("/new/video.mp4", self.video_info(), assessment)

        self.assertEqual(panel.status_label.text(), "Source differs")
        self.assertIn("Source content digest differs", panel.differences_label.text())

    def test_unverified_candidate_names_source_identity_and_clears_stale_apply_description(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        summary = "Applying this source clears trusted results."
        assessment = MediaRelinkAssessment(
            state="unverified",
            summary=summary,
            can_apply=True,
            clear_results=True,
            identity_state="unavailable",
        )

        panel.set_candidate("/new/video.mp4", self.video_info(), assessment)

        self.assertEqual(panel.status_label.text(), "Source identity unverified")
        self.assertEqual(panel.apply_button.accessibleDescription(), summary)

        panel.show_applied(self.video_info(), results_preserved=False)

        self.assertFalse(panel.apply_button.isEnabled())
        self.assertEqual(
            panel.apply_button.accessibleDescription(),
            "No verified replacement media candidate is ready to apply.",
        )

    def test_mismatch_names_differences_and_destructive_apply(self) -> None:
        panel = MediaRelinkPanel()
        self.addCleanup(panel.close)
        assessment = MediaRelinkAssessment(
            state="mismatch",
            summary="Saved metadata differs. Results will be cleared.",
            differences=("Resolution 640×360 → 1280×720", "FPS 20 → 30"),
            can_apply=True,
            clear_results=True,
        )

        panel.set_candidate("/new/video.mp4", self.video_info(), assessment)

        self.assertEqual(panel.status_label.text(), "Metadata differs")
        self.assertEqual(panel.status_label.property("mediaRelinkState"), "mismatch")
        self.assertIn("Resolution 640×360 → 1280×720", panel.differences_label.text())
        self.assertEqual(panel.apply_button.text(), "Relink + Clear Results/Edits")
        self.assertTrue(panel.apply_button.isEnabled())
        self.assertFalse(panel.cancel_button.isHidden())


if __name__ == "__main__":
    unittest.main()
