from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackgroundTaskToken:
    """Identifies one generation of a named application operation."""

    kind: str
    generation: int


@dataclass
class TaskSupervisor:
    """Own application task generations and the process-wide close gate.

    Concrete coordinators own threads, workers, and cancellation. The
    supervisor only determines whether an operation may start, whether a
    callback still belongs to the active generation, and whether closing may
    proceed after every owner has finished.
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

    def generation_for(self, kind: str) -> int:
        """Return the last issued generation for ``kind``, or zero."""

        return self._generations.get(str(kind).strip(), 0)

    def current_token(self, kind: str) -> BackgroundTaskToken | None:
        """Return the active token for ``kind`` without changing ownership."""

        return self._active.get(str(kind).strip())

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
