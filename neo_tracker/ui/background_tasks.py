"""Compatibility imports for the pre-Application lifecycle API."""

from neo_tracker.application.task_supervisor import BackgroundTaskToken, TaskSupervisor


class BackgroundTaskCoordinator(TaskSupervisor):
    """Backward-compatible name for :class:`TaskSupervisor`."""
