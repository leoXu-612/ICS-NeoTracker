from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from neo_tracker.media import MediaInfo
from neo_tracker.ui.project_controller import MediaRelinkAssessment


class MediaRelinkPanel(QWidget):
    """Inline media replacement workflow for an existing task."""

    browseRequested = Signal()
    applyRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status_label = QLabel("No media")
        self.status_label.setObjectName("mediaRelinkStatus")
        self.status_label.setProperty("mediaRelinkState", "empty")
        self.status_label.setAccessibleName("Media source status")

        self.detail_label = QLabel("Select a media task before relinking a source.")
        self.detail_label.setObjectName("mediaRelinkDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAccessibleName("Media source status detail")

        self.candidate_label = QLabel()
        self.candidate_label.setObjectName("mediaRelinkCandidate")
        self.candidate_label.setWordWrap(True)
        self.candidate_label.setAccessibleName("Replacement media candidate")
        self.candidate_label.hide()

        self.differences_label = QLabel()
        self.differences_label.setObjectName("mediaRelinkDifferences")
        self.differences_label.setWordWrap(True)
        self.differences_label.setAccessibleName("Replacement media differences")
        self.differences_label.hide()

        self.browse_button = QPushButton("Relink Media…")
        self.browse_button.setObjectName("browseMediaRelinkButton")
        self.browse_button.setToolTip("Choose a replacement file for this task without creating a new task.")
        self.browse_button.setAccessibleName("Choose replacement media")
        self.browse_button.clicked.connect(self.browseRequested.emit)

        self.apply_button = QPushButton("Apply Relink")
        self.apply_button.setObjectName("applyMediaRelinkButton")
        self.apply_button.setToolTip("Apply the checked replacement to the current task.")
        self.apply_button.setAccessibleName("Apply replacement media")
        self.apply_button.clicked.connect(self.applyRequested.emit)
        self.apply_button.setEnabled(False)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelMediaRelinkButton")
        self.cancel_button.setToolTip("Discard the replacement candidate and keep the current task unchanged.")
        self.cancel_button.setAccessibleName("Cancel replacement media")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        self.cancel_button.hide()

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(self.browse_button, 1)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.status_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.candidate_label)
        layout.addWidget(self.differences_label)
        layout.addLayout(button_row)

    def set_current(self, media_path: str | None, info: MediaInfo | None) -> None:
        self._clear_candidate()
        self.browse_button.setText("Relink Media…")
        self.browse_button.setEnabled(media_path is not None)
        if media_path is None:
            self._set_status("No media", "empty", "Select a media task before relinking a source.")
            return
        if info is not None and info.available:
            identity_detail = ""
            if info.source_identity is not None:
                if info.source_identity.complete:
                    identity_detail = " Full-file SHA-256 identity is available for project verification."
                else:
                    identity_detail = (
                        f" Bounded sampled SHA-256 identity uses {info.source_identity.sampled_bytes:,} bytes "
                        "for project verification."
                    )
            self._set_status(
                "Media ready",
                "ready",
                "The current source is available."
                + identity_detail
                + " Relink only if the experiment file has moved or been replaced.",
            )
            return
        detail = "Saved media is unavailable. Choose a replacement; this task stays unchanged until Apply."
        if info is not None and info.error.strip():
            detail += f" {info.error.strip()}"
        self._set_status("Media missing", "missing", detail)
        self.browse_button.setText("Choose Replacement…")

    def set_candidate(
        self,
        media_path: str,
        info: MediaInfo,
        assessment: MediaRelinkAssessment,
    ) -> None:
        titles = {
            "match": "Metadata match",
            "mismatch": "Metadata differs",
            "unverified": "Source identity unverified",
            "incompatible": "Wrong media type",
            "unavailable": "Replacement unavailable",
        }
        title = titles.get(assessment.state, "Replacement checked")
        if assessment.state == "match" and assessment.identity_state in {"full", "sampled"}:
            title = "Source verified"
        elif assessment.state == "mismatch" and assessment.identity_state == "mismatch":
            title = "Source differs"
        self._set_status(title, assessment.state, assessment.summary)
        self.candidate_label.setText(f"Candidate: {Path(media_path).name} · {self.media_summary(info)}")
        self.candidate_label.setToolTip(media_path)
        self.candidate_label.setAccessibleDescription(media_path)
        self.candidate_label.show()
        self.differences_label.setText(" · ".join(assessment.differences))
        self.differences_label.setVisible(bool(assessment.differences))
        self.apply_button.setText("Relink + Clear Results/Edits" if assessment.clear_results else "Apply Relink")
        self.apply_button.setAccessibleDescription(assessment.summary)
        self.apply_button.setEnabled(assessment.can_apply)
        self.cancel_button.show()

    def show_source_review_required(self, info: MediaInfo | None) -> None:
        """Render a quarantined source as changed, even after its staged probe is dismissed."""

        self._clear_candidate()
        detail = (
            "The file at the saved path changed or could not be verified. Choose it or another "
            "replacement and review the detected differences before applying."
        )
        if info is not None and info.error.strip():
            detail += f" {info.error.strip()}"
        self._set_status("Source changed", "mismatch", detail)
        self.browse_button.setText("Review Source…")
        self.browse_button.setEnabled(True)

    def show_applied(self, info: MediaInfo, *, results_preserved: bool) -> None:
        self._clear_candidate()
        result_text = "Results preserved." if results_preserved else "Results cleared."
        self._set_status(
            "Relinked",
            "applied",
            f"{result_text} Save Project to persist the new media path.",
        )
        self.browse_button.setText("Relink Again…")
        self.browse_button.setEnabled(True)

    @staticmethod
    def media_summary(info: MediaInfo) -> str:
        identity_suffix = ""
        if info.source_identity is not None:
            identity_suffix = " · full digest" if info.source_identity.complete else " · sampled digest"
        if info.kind == "audio":
            rate = info.sample_rate_hz or info.fps
            return f"audio · {rate:g} Hz · {info.frame_count} samples · {info.channels} ch{identity_suffix}"
        return f"video · {info.width}×{info.height} · {info.frame_count} frames · {info.fps:g} fps{identity_suffix}"

    def _clear_candidate(self) -> None:
        self.candidate_label.clear()
        self.candidate_label.hide()
        self.differences_label.clear()
        self.differences_label.hide()
        self.apply_button.setText("Apply Relink")
        self.apply_button.setAccessibleDescription(
            "No verified replacement media candidate is ready to apply."
        )
        self.apply_button.setEnabled(False)
        self.cancel_button.hide()

    def _set_status(self, text: str, state: str, detail: str) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("mediaRelinkState", state)
        self.status_label.setToolTip(detail)
        self.status_label.setAccessibleDescription(detail)
        self.detail_label.setText(detail)
        self.detail_label.setToolTip(detail)
        self.detail_label.setAccessibleDescription(detail)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
