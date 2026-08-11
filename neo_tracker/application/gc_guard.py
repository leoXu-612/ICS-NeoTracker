from __future__ import annotations

"""Process-wide guard for allocation-heavy background Python stages."""

import gc
from threading import Lock


_LOCK = Lock()
_DEPTH = 0
_ORIGINAL_THRESHOLDS: tuple[int, int, int] | None = None


def acquire_high_generation_gc_guard() -> None:
    """Delay stop-the-world generation 1/2 scans until all guarded stages end."""

    global _DEPTH, _ORIGINAL_THRESHOLDS
    with _LOCK:
        if _DEPTH == 0:
            thresholds = gc.get_threshold()
            gc.set_threshold(
                thresholds[0],
                max(thresholds[1], 1_000_000),
                max(thresholds[2], 1_000_000),
            )
            _ORIGINAL_THRESHOLDS = thresholds
        _DEPTH += 1


def release_high_generation_gc_guard() -> None:
    """Restore the caller's thresholds after the final guarded stage."""

    global _DEPTH, _ORIGINAL_THRESHOLDS
    with _LOCK:
        if _DEPTH <= 0:
            raise RuntimeError("high-generation GC guard release is unbalanced")
        _DEPTH -= 1
        if _DEPTH == 0:
            thresholds = _ORIGINAL_THRESHOLDS
            _ORIGINAL_THRESHOLDS = None
            if thresholds is not None:
                gc.set_threshold(*thresholds)


__all__ = [
    "acquire_high_generation_gc_guard",
    "release_high_generation_gc_guard",
]
