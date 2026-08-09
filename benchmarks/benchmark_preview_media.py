from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import cv2
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

from neo_tracker.media import MediaReader
from neo_tracker.ui.main_window import NeoTrackerWindow
from neo_tracker.ui.preview_canvas import PreviewCanvas


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio)))
    return ordered[index]


def _timing(samples: list[float]) -> dict[str, float]:
    return {
        "median_ms": median(samples) * 1000.0,
        "p95_ms": _percentile(samples, 0.95) * 1000.0,
        "max_ms": max(samples) * 1000.0,
    }


def _write_video(path: Path, width: int, height: int, frames: int, fps: float) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV VideoWriter could not create the temporary MP4.")
    base_x = np.linspace(0, 254, width, dtype=np.uint16)[None, :]
    base_y = np.linspace(0, 254, height, dtype=np.uint16)[:, None]
    try:
        for frame_index in range(frames):
            frame = np.empty((height, width, 3), dtype=np.uint8)
            frame[..., 0] = ((base_x + frame_index) % 255).astype(np.uint8)
            frame[..., 1] = ((base_y + frame_index * 2) % 255).astype(np.uint8)
            frame[..., 2] = 32
            marker_x = 100 + (frame_index * 7) % max(1, width - 200)
            cv2.circle(frame, (marker_x, height // 2), max(8, height // 26), (0, 0, 255), -1)
            writer.write(frame)
    finally:
        writer.release()


def _read_pattern(path: Path, indices: range) -> dict[str, float]:
    samples: list[float] = []
    with MediaReader(str(path)) as reader:
        for frame_index in indices:
            started = perf_counter()
            reader.read_frame(frame_index)
            samples.append(perf_counter() - started)
    return _timing(samples)


def benchmark(
    *,
    width: int,
    height: int,
    frames: int,
    fps: float,
    playback_seconds: float,
) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "preview-benchmark.mp4"
        _write_video(path, width, height, frames, fps)
        sequential_count = min(frames, 90)
        gap_stop = min(frames, 234)
        metrics: dict[str, object] = {
            "resolution": f"{width}x{height}",
            "frames": frames,
            "fps": fps,
            "temporary_file_mib": path.stat().st_size / 1024.0 / 1024.0,
            "sequential_read": _read_pattern(path, range(sequential_count)),
            "gap_3_read": _read_pattern(path, range(0, gap_stop, 3)),
            "gap_13_read": _read_pattern(path, range(0, gap_stop, 13)),
            "gap_14_seek_read": _read_pattern(path, range(0, gap_stop, 14)),
        }

        with MediaReader(str(path)) as reader:
            sample_frame = reader.read_frame(min(frames - 1, frames // 2))
        canvas = PreviewCanvas()
        canvas.resize(860, 600)
        canvas.show()
        app.processEvents()
        canvas_samples: list[float] = []
        for _ in range(40):
            started = perf_counter()
            canvas.set_frame(sample_frame)
            app.processEvents()
            canvas_samples.append(perf_counter() - started)
        metrics["canvas_set_and_paint"] = _timing(canvas_samples)
        canvas.close()

        window = NeoTrackerWindow()
        window.resize(1440, 900)
        window.show()
        app.processEvents()
        task = window.current_task
        task.media_path = str(path)
        task.media_reader = MediaReader(str(path))
        task.media_info = task.media_reader.info
        task.preview_frame_index = 0
        window._set_playback_enabled(True)
        window._render_task()

        heartbeat: list[float] = []
        last_heartbeat = [monotonic()]
        heartbeat_timer = QTimer()
        heartbeat_timer.setTimerType(Qt.TimerType.PreciseTimer)
        heartbeat_timer.setInterval(10)

        def record_heartbeat() -> None:
            now = monotonic()
            heartbeat.append(now - last_heartbeat[0])
            last_heartbeat[0] = now

        heartbeat_timer.timeout.connect(record_heartbeat)
        heartbeat_timer.start()
        loop = QEventLoop()
        quit_timer = QTimer()
        quit_timer.setSingleShot(True)
        quit_timer.setTimerType(Qt.TimerType.PreciseTimer)
        duration_ms = max(100, int(round(min(playback_seconds, frames / fps - 0.1) * 1000.0)))
        quit_timer.setInterval(duration_ms)
        quit_timer.timeout.connect(loop.quit)
        started = monotonic()
        window._toggle_playback()
        quit_timer.start()
        loop.exec()
        elapsed = monotonic() - started
        expected_frame = min(frames - 1, int(elapsed * fps))
        displayed_frame = int(task.preview_frame_index)
        skipped = int(window.playback_clock.skipped_total)
        heartbeat_timer.stop()
        window._stop_playback()
        window.close()
        app.processEvents()
        metrics["window_playback"] = {
            "elapsed_s": elapsed,
            "expected_source_frame": expected_frame,
            "displayed_frame": displayed_frame,
            "lag_frames": expected_frame - displayed_frame,
            "preview_skips": skipped,
            "heartbeat_count": len(heartbeat),
            **{f"heartbeat_{key}": value for key, value in _timing(heartbeat).items()},
        }
        return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark high-resolution preview media and GUI heartbeat.")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--frames", type=int, default=240)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--playback-seconds", type=float, default=3.0)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                width=max(64, args.width),
                height=max(64, args.height),
                frames=max(2, args.frames),
                fps=max(0.1, args.fps),
                playback_seconds=max(0.1, args.playback_seconds),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
