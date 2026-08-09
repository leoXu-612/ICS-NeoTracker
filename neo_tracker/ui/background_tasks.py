from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackgroundTaskToken:
    """Identifies one generation of a named background operation."""

    kind: str
    generation: int


@dataclass
class BackgroundTaskCoordinator:
    """Coordinates task generations and the window-closing gate.

    Thread ownership remains with the UI. This class only answers the lifecycle
    questions shared by tracking, signal processing, and review-response work:
    whether a task may start, whether a callback still belongs to the current
    generation, and whether an ignored close event can now be retried safely.
    """

    _generations: dict[str, int] = field(default_factory=dict)
    _active: dict[str, BackgroundTaskToken] = field(default_factory=dict)
    _closing: bool = False

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def active_kinds(self) -> tuple[str, ...]:
        return tuple(self._active)

    @property
    def idle(self) -> bool:
        return not self._active

    @property
    def ready_to_close(self) -> bool:
        return self._closing and self.idle

    def start(self, kind: str) -> BackgroundTaskToken | None:
        normalized = str(kind).strip()
        if not normalized:
            raise ValueError("background task kind must not be empty")
        if self._closing or normalized in self._active:
            return None
        generation = self._generations.get(normalized, 0) + 1
        token = BackgroundTaskToken(normalized, generation)
        self._generations[normalized] = generation
        self._active[normalized] = token
        return token

    def is_current(self, token: BackgroundTaskToken) -> bool:
        return self._active.get(token.kind) == token

    def finish(self, token: BackgroundTaskToken) -> bool:
        if not self.is_current(token):
            return False
        del self._active[token.kind]
        return True

    def begin_close(self) -> tuple[str, ...]:
        self._closing = True
        return self.active_kinds

    def cancel_close(self) -> bool:
        """Reopen the start gate after an idle close request is canceled."""

        if not self.idle:
            return False
        self._closing = False
        return True
