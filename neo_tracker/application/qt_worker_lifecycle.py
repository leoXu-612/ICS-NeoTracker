from __future__ import annotations

from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QThread


def bind_worker_retirement(
    worker: QObject,
    thread: QThread,
    *terminal_signals: Any,
) -> None:
    """Move a terminal worker to the main loop, then stop its QThread."""

    def retire(*_args: object) -> None:
        application = QCoreApplication.instance()
        if worker.thread() is thread and application is not None:
            worker.moveToThread(application.thread())
        thread.quit()

    for signal in terminal_signals:
        signal.connect(retire)
