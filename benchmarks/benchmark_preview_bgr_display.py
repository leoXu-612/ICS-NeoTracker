from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import numpy as np
from PySide6.QtWidgets import QApplication

from benchmarks.benchmark_preview_media import _write_video
from neo_tracker.media import MediaReader
from neo_tracker.ui.preview_canvas import PreviewCanvas


def _run_path(
    app: QApplication,
    media_path: Path,
    frame_count: int,
    *,
    bgr_display: bool,
) -> tuple[float, np.ndarray]:
    canvas = PreviewCanvas()
    canvas.resize(860, 600)
    canvas.show()
    app.processEvents()
    last_rgb: np.ndarray | None = None
    started = perf_counter()
    with MediaReader(str(media_path)) as reader:
        for frame_index in range(frame_count):
            if bgr_display:
                bgr = reader.read_frame_for_display(frame_index)
                last_rgb = bgr[:, :, ::-1]
                canvas.set_bgr_frame(bgr)
            else:
                last_rgb = reader.read_frame(frame_index)
                canvas.set_frame(last_rgb)
            app.processEvents()
    elapsed = perf_counter() - started
    if last_rgb is None:
        raise RuntimeError("Preview benchmark did not decode a frame")
    owned_rgb = np.ascontiguousarray(last_rgb)
    canvas.close()
    app.processEvents()
    return elapsed, owned_rgb


def benchmark(*, width: int, height: int, frames: int, fps: float, rounds: int) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    rounds = max(1, int(rounds))
    frames = max(2, int(frames))
    legacy_samples: list[float] = []
    current_samples: list[float] = []
    legacy_rgb: np.ndarray | None = None
    current_rgb: np.ndarray | None = None
    with TemporaryDirectory() as tmpdir:
        media_path = Path(tmpdir) / "preview-display.mp4"
        _write_video(media_path, width, height, frames, fps)
        for round_index in range(rounds):
            order = (False, True) if round_index % 2 == 0 else (True, False)
            for bgr_display in order:
                elapsed, rgb = _run_path(
                    app,
                    media_path,
                    frames,
                    bgr_display=bgr_display,
                )
                if bgr_display:
                    current_samples.append(elapsed)
                    current_rgb = rgb
                else:
                    legacy_samples.append(elapsed)
                    legacy_rgb = rgb

    if legacy_rgb is None or current_rgb is None:
        raise RuntimeError("Preview benchmark did not complete both paths")
    legacy_s = median(legacy_samples)
    current_s = median(current_samples)
    return {
        "resolution": f"{width}x{height}",
        "frames": frames,
        "fps": fps,
        "rounds": rounds,
        "legacy_rgb_display_ms_per_frame": 1000.0 * legacy_s / frames,
        "current_bgr_display_ms_per_frame": 1000.0 * current_s / frames,
        "display_speedup": legacy_s / current_s if current_s > 0.0 else 0.0,
        "elapsed_reduction_percent": (
            100.0 * (legacy_s - current_s) / legacy_s if legacy_s > 0.0 else 0.0
        ),
        "final_frame_rgb_exact": bool(np.array_equal(legacy_rgb, current_rgb)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the legacy RGB preview handoff with Qt's native BGR888 path."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--frames", type=int, default=48)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--rounds", type=int, default=9)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                width=max(64, int(args.width)),
                height=max(64, int(args.height)),
                frames=max(2, int(args.frames)),
                fps=max(0.1, float(args.fps)),
                rounds=max(1, int(args.rounds)),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
