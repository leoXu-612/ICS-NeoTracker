from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Iterator, TextIO


def fsync_parent_directory(path: str | Path) -> None:
    """Best-effort directory sync after an atomic replacement."""

    parent = Path(path).parent
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(parent, flags)
        os.fsync(descriptor)
    except OSError:
        # Directory fsync is not supported by every filesystem/platform. The
        # file itself has already been synced and atomically replaced.
        pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def atomic_output_path(path: str | Path) -> Iterator[Path]:
    """Yield a same-directory temporary path and replace the target on success."""

    target = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=target.suffix or ".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        yield temporary_path
        descriptor = os.open(temporary_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_path, target)
        temporary_path = None
        fsync_parent_directory(target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def atomic_text_writer(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> Iterator[TextIO]:
    """Write text to a temporary file and atomically publish it on success."""

    target = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline=newline,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        fsync_parent_directory(target)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    with atomic_text_writer(path, encoding=encoding) as handle:
        handle.write(text)
