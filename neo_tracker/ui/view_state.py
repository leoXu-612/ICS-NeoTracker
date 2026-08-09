from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


PHYSICS_WORKSPACE_PAGES = (
    "Data",
    "Plot",
    "Fit",
    "Diagnostics",
    "Runs",
    "Edits",
    "Signal",
)


@dataclass(frozen=True)
class PhysicsWorkspaceState:
    """Bounded, serializable presentation state; it never contains analysis data."""

    collapsed: bool = False
    page: str = "Data"
    height: int = 280

    def __post_init__(self) -> None:
        if not isinstance(self.collapsed, bool):
            raise TypeError("collapsed must be a boolean")
        if self.page not in PHYSICS_WORKSPACE_PAGES:
            raise ValueError(f"unknown physics workspace page: {self.page}")
        if isinstance(self.height, bool) or not isinstance(self.height, int):
            raise TypeError("physics workspace height must be an integer")
        if not 120 <= self.height <= 1_200:
            raise ValueError("physics workspace height must be in [120, 1200]")

    def to_mapping(self) -> dict[str, object]:
        return {
            "collapsed": self.collapsed,
            "page": self.page,
            "height": self.height,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PhysicsWorkspaceState":
        unknown = set(value) - {"collapsed", "page", "height"}
        if unknown:
            raise ValueError(f"unknown physics workspace fields: {sorted(unknown)}")
        return cls(
            collapsed=value.get("collapsed", False),  # type: ignore[arg-type]
            page=value.get("page", "Data"),  # type: ignore[arg-type]
            height=value.get("height", 280),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ActionViewState:
    """Presentation state shared by every surface bound to one command."""

    enabled: bool = True
    text: str = ""
    tool_tip: str = ""


@dataclass(frozen=True)
class ViewState:
    """Immutable snapshot of the application shell's command state."""

    actions: Mapping[str, ActionViewState]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", MappingProxyType(dict(self.actions)))

    @classmethod
    def from_mapping(cls, actions: Mapping[str, ActionViewState]) -> "ViewState":
        return cls(actions)

    def action(self, key: str) -> ActionViewState:
        try:
            return self.actions[str(key)]
        except KeyError as exc:
            raise KeyError(f"unknown action key: {key}") from exc

    def with_action(
        self,
        key: str,
        *,
        enabled: bool | None = None,
        text: str | None = None,
        tool_tip: str | None = None,
    ) -> "ViewState":
        normalized = str(key)
        current = self.action(normalized)
        updated = ActionViewState(
            enabled=current.enabled if enabled is None else bool(enabled),
            text=current.text if text is None else str(text),
            tool_tip=current.tool_tip if tool_tip is None else str(tool_tip),
        )
        if updated == current:
            return self
        actions = dict(self.actions)
        actions[normalized] = updated
        return ViewState.from_mapping(actions)


ViewStateListener = Callable[[ViewState], None]


class ViewStateStore:
    """Small observable store; decisions stay testable without constructing Qt widgets."""

    def __init__(self, state: ViewState) -> None:
        self._state = state
        self._listeners: list[ViewStateListener] = []

    @property
    def state(self) -> ViewState:
        return self._state

    def subscribe(self, listener: ViewStateListener) -> Callable[[], None]:
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def update_action(
        self,
        key: str,
        *,
        enabled: bool | None = None,
        text: str | None = None,
        tool_tip: str | None = None,
    ) -> ViewState:
        updated = self._state.with_action(
            key,
            enabled=enabled,
            text=text,
            tool_tip=tool_tip,
        )
        if updated is self._state:
            return self._state
        self._state = updated
        for listener in tuple(self._listeners):
            listener(updated)
        return updated
