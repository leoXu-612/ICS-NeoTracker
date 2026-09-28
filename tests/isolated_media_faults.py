"""Lightweight spawn targets: fault timing must not include OpenCV test discovery."""

from __future__ import annotations

import os
import pickle
import time
from pathlib import Path


def _crash_without_response(connection, *_args) -> None:
    connection.close()
    os._exit(23)


def _block_forever(_connection, *_args) -> None:
    while True:
        time.sleep(1.0)


def _write_unpickle_marker(path: str) -> None:
    Path(path).write_text("unsafe parent unpickle", encoding="utf-8")


class _MaliciousPayload:
    def __init__(self, marker_path: str) -> None:
        self.marker_path = marker_path

    def __reduce__(self):
        return (_write_unpickle_marker, (self.marker_path,))


def _send_malicious_pickle(connection, _operation, _request_id, path, *_args) -> None:
    connection.send_bytes(pickle.dumps(_MaliciousPayload(path + ".unpickled")))
    connection.close()


def _session_send_malicious_pickle(connection, path, *_args) -> None:
    connection.send_bytes(pickle.dumps(_MaliciousPayload(path + ".unpickled")))
    connection.close()
