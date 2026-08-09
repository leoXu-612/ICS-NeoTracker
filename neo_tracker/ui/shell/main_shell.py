from __future__ import annotations

from typing import Any

from PySide6.QtGui import QIcon

from neo_tracker.ui.action_registry import ActionRegistry
from neo_tracker.ui.shell.bindings import bind_primary_actions
from neo_tracker.ui.view_state import ViewState, ViewStateStore


class ApplicationShell:
    """Bind Window surfaces to canonical commands and one immutable ViewState."""

    def __init__(self, owner: Any) -> None:
        self.registry = ActionRegistry(owner)
        bind_primary_actions(owner, self.registry)
        self.view_states = ViewStateStore(self.registry.snapshot_view_state())
        self._unsubscribe = self.view_states.subscribe(self.registry.apply_view_state)

    @property
    def view_state(self) -> ViewState:
        return self.view_states.state

    def update_action(
        self,
        key: str,
        *,
        enabled: bool | None = None,
        text: str | None = None,
        tool_tip: str | None = None,
    ) -> ViewState:
        return self.view_states.update_action(
            key,
            enabled=enabled,
            text=text,
            tool_tip=tool_tip,
        )

    def set_icon(self, key: str, icon: QIcon) -> None:
        self.registry.set_icon(key, icon)

    def close(self) -> None:
        self._unsubscribe()
