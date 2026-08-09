from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from neo_tracker.project import NeoTrackerProject, project_content_fingerprint


ProjectSaver = Callable[[NeoTrackerProject, Path], None]


@dataclass(frozen=True)
class CompletedProjectSave:
    path: Path
    fingerprint: str


def save_project(project: NeoTrackerProject, path: Path) -> None:
    project.save(path)


class ProjectSaveWorker(QObject):
    """Persist one UI-owned project snapshot outside the Qt event loop."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        project: NeoTrackerProject,
        path: str | Path,
        *,
        project_saver: ProjectSaver = save_project,
    ) -> None:
        super().__init__()
        self.project = project
        self.path = Path(path)
        self.project_saver = project_saver

    @Slot()
    def run(self) -> None:
        try:
            self.project_saver(self.project, self.path)
            fingerprint = project_content_fingerprint(self.project)
            self.completed.emit(
                CompletedProjectSave(
                    path=self.path,
                    fingerprint=fingerprint,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))
