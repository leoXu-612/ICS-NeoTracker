from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QAbstractButton

from neo_tracker.ui.view_state import ActionViewState, ViewState


CommandHandler = Callable[[], Any]


class ActionRegistry(QObject):
    """Own canonical QActions and bind all command surfaces to them."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._actions: dict[str, QAction] = {}
        self._handlers: dict[str, Callable[[bool], None]] = {}
        self._buttons: dict[str, list[QAbstractButton]] = {}
        self._button_slots: list[Callable[..., None]] = []
        self._blocked_actions: set[str] = set()
        self._view_state: ViewState | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._actions)

    def register(self, key: str, text: str, handler: CommandHandler) -> QAction:
        normalized = str(key).strip()
        if not normalized:
            raise ValueError("action key must not be empty")
        if normalized in self._actions:
            raise KeyError(f"action already registered: {normalized}")
        action = QAction(str(text), self)

        def invoke(_checked: bool = False) -> None:
            handler()

        action.triggered.connect(invoke)
        self._actions[normalized] = action
        self._handlers[normalized] = invoke
        self._buttons[normalized] = []
        return action

    def action(self, key: str) -> QAction:
        normalized = str(key)
        try:
            return self._actions[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown action key: {normalized}") from exc

    def bind_button(self, key: str, button: QAbstractButton) -> None:
        action = self.action(key)
        bound = self._buttons[str(key)]
        if button in bound:
            return
        if not bound:
            action.setEnabled(button.isEnabled())
            action.setText(button.text())
            action.setToolTip(button.toolTip())
            if not button.icon().isNull():
                action.setIcon(button.icon())
        bound.append(button)

        def sync_button() -> None:
            button.setEnabled(action.isEnabled())
            button.setText(action.text())
            button.setToolTip(action.toolTip())
            button.setIcon(action.icon())

        def trigger_action(_checked: bool = False) -> None:
            action.trigger()

        action.changed.connect(sync_button)
        button.clicked.connect(trigger_action)
        self._button_slots.extend((sync_button, trigger_action))
        sync_button()

    def snapshot_view_state(self) -> ViewState:
        if self._view_state is not None:
            return self._view_state
        self._view_state = ViewState.from_mapping(
            {
                key: ActionViewState(
                    enabled=action.isEnabled(),
                    text=action.text(),
                    tool_tip=action.toolTip(),
                )
                for key, action in self._actions.items()
            }
        )
        return self._view_state

    def apply_view_state(self, state: ViewState) -> None:
        self._view_state = state
        for key in self.keys:
            presentation = state.action(key)
            action = self._actions[key]
            action.setEnabled(
                presentation.enabled and key not in self._blocked_actions
            )
            action.setText(presentation.text)
            action.setToolTip(presentation.tool_tip)
            for button in self._buttons[key]:
                button.setEnabled(action.isEnabled())
                button.setText(action.text())
                button.setToolTip(action.toolTip())
                button.setIcon(action.icon())

    def set_actions_blocked(self, keys: Iterable[str], blocked: bool) -> None:
        normalized = {str(key) for key in keys}
        unknown = normalized.difference(self._actions)
        if unknown:
            raise KeyError(f"unknown action keys: {', '.join(sorted(unknown))}")
        if blocked:
            self._blocked_actions.update(normalized)
        else:
            self._blocked_actions.difference_update(normalized)
        self.apply_view_state(self.snapshot_view_state())

    def set_icon(self, key: str, icon: QIcon) -> None:
        self.action(key).setIcon(icon)
