from __future__ import annotations

"""P0-B real-acquisition SloMo matrix for ICS-NeoTracker.

Runs the bounded tracking pipeline against real iPhone SloMo captures
(HEVC 1080p 240fps VFR .mov from the PHY-EE experiment album). Output is one
valid JSON document on stdout; Qt/system warnings stay on stderr.

Usage:
    PYTHONPATH=. python3 benchmarks/benchmark_real_slomo_matrix.py
        [--media-dir /Users/leo.xu/Desktop/PHY-EE-导出]
        [--matrix-dir artifacts/deepseek-2026-08-09/slomo-matrix]
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmarks.benchmark_real_media_matrix import (
    run_cancel,
    run_full_tracking,
    residual_processes,
)
from neo_tracker.media import MediaReader, probe_media, probe_media_identity
from neo_tracker.roi import RectangularROI
from neo_tracker.ui.tracking_worker import TRACKING_SOURCE_CHANGED_PREFIX


def ffprobe_stream(path: Path) -> dict[str, Any]:
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(output)
    stream = (data.get("streams") or [{}])[0]

    def parse_rate(value: str) -> float | None:
        if not value or value == "0/0":
            return None
        numerator, _, denominator = value.partition("/")
        try:
            return float(numerator) / float(denominator or 1)
        except (ValueError, ZeroDivisionError):
            return None

    nominal = parse_rate(stream.get("r_frame_rate", ""))
    average = parse_rate(stream.get("avg_frame_rate", ""))
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "nominal_fps": nominal,
        "avg_fps": average,
        "vfr": (
            nominal is not None
            and average is not None
            and abs(nominal - average) > 0.05
        ),
        "frame_count": int(stream.get("nb_frames") or 0),
        "duration_s": float(stream.get("duration") or 0.0),
    }


def unique_media(media_dir: Path) -> list[Path]:
    all_candidates = sorted(media_dir.glob("*.mov"))
    candidates = [
        path for path in all_candidates if " (1)" not in path.name
    ] + [
        path for path in all_candidates if " (1)" in path.name
    ]
    seen_sizes: set[int] = set()
    unique: list[Path] = []
    for path in candidates:
        size = path.stat().st_size
        if size in seen_sizes:
            continue
        seen_sizes.add(size)
        unique.append(path)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-dir", default="/Users/leo.xu/Desktop/PHY-EE-导出")
    parser.add_argument(
        "--matrix-dir",
        default="/tmp/nt-slomo-matrix",
    )
    args = parser.parse_args()
    media_dir = Path(args.media_dir)
    matrix_dir = Path(args.matrix_dir)
    matrix_dir.mkdir(parents=True, exist_ok=True)

    sources = unique_media(media_dir)
    if not sources:
        print(json.dumps({"error": f"no .mov media in {media_dir}"}))
        return 1

    roi_1080 = RectangularROI(500.0, 300.0, 920.0, 480.0)
    report: dict[str, Any] = {
        "environment": {
            "os": "macOS 15.7.7",
            "python": sys.version.split()[0],
            "media_dir": str(media_dir),
            "unique_sources": [str(path.name) for path in sources],
        },
        "sources": {},
        "runs": [],
    }

    for path in sources:
        stream = ffprobe_stream(path)
        probe = probe_media(str(path))
        report["sources"][path.name] = {
            **stream,
            "app_probe": {
                "available": probe.available,
                "error": probe.error or "",
                "fps": probe.fps,
                "frame_count": probe.frame_count,
                "width": probe.width,
                "height": probe.height,
            },
        }
        if not probe.available:
            continue
        identity = probe_media_identity(path)
        fps = float(stream["nominal_fps"] or 240.0)
        frame_count = int(stream["frame_count"] or probe.frame_count)
        run = run_full_tracking(
            path,
            roi=roi_1080,
            identity=identity,
            fps=fps,
            frame_count=frame_count,
            label=path.stem,
        )
        run_again = run_full_tracking(
            path,
            roi=roi_1080,
            identity=identity,
            fps=fps,
            frame_count=frame_count,
            label=f"{path.stem}-run2",
        )
        report["runs"].append(
            {
                "source": path.name,
                "run": run,
                "deterministic": run["result_digest"] == run_again["result_digest"],
                "run2": run_again,
            }
        )

    # Cancel against the shortest unique source (0.5s request), replacement
    # against a working copy, truncation probe, and reopen x10.
    shortest = min(sources, key=lambda path: ffprobe_stream(path)["duration_s"])
    shortest_stream = ffprobe_stream(shortest)
    report["cancel"] = run_cancel(
        shortest,
        roi=roi_1080,
        identity=probe_media_identity(shortest),
        fps=float(shortest_stream["nominal_fps"] or 240.0),
        frame_count=int(shortest_stream["frame_count"]),
        cancel_after_s=0.5,
    )

    swap_path = matrix_dir / f"{shortest.stem}-swap.mov"
    shutil.copyfile(shortest, swap_path)
    swap_stream = ffprobe_stream(shortest)
    report["source_replacement"] = run_source_replacement_copy(
        swap_path,
        shortest,
        roi=roi_1080,
        identity=probe_media_identity(swap_path),
        fps=float(swap_stream["nominal_fps"] or 240.0),
        frame_count=int(swap_stream["frame_count"]),
        replace_after_s=0.4,
    )

    truncated_path = matrix_dir / f"{shortest.stem}-truncated.mov"
    raw = shortest.read_bytes()
    truncated_path.write_bytes(raw[: int(len(raw) * 0.60)])
    info = probe_media(str(truncated_path))
    truncated_read: dict[str, Any] = {
        "probe": {
            "available": info.available,
            "error": info.error or "",
            "frame_count": info.frame_count,
        }
    }
    if info.available:
        try:
            with MediaReader(str(truncated_path)) as reader:
                truncated_read["last_frame"] = list(reader.read_frame(info.frame_count - 1).shape)
                truncated_read["end_error"] = ""
        except Exception as exc:
            truncated_read["end_error"] = f"{type(exc).__name__}: {exc}"[:160]
    report["truncated"] = truncated_read

    reopen_rows: list[dict[str, Any]] = []
    for _index in range(10):
        try:
            with MediaReader(str(shortest)) as reader:
                frame = reader.read_frame(0)
            reopen_rows.append({"ok": True, "shape": list(frame.shape)})
        except Exception as exc:
            reopen_rows.append({"ok": False, "error": str(exc)[:120]})
    report["reopen"] = {
        "cycles": len(reopen_rows),
        "all_ok": all(row["ok"] for row in reopen_rows),
        "residual_processes": residual_processes(),
    }

    report["gaps"] = {
        "4k_original_acquisition": (
            "BLOCKED: SloMo captures are 1080p; no 4K original in the album copy."
        ),
        "long_session_10min": (
            "BLOCKED: longest clip is 2.5 min; four clips total ~6.4 min of real "
            "footage. No single >=10 min acquisition available."
        ),
        "power_temperature": "powermetrics requires root; not measured. CPU sampled via ps.",
    }
    print(json.dumps(report, indent=2))
    over_limit = [row for row in report["runs"] if row["run"]["failures"]]
    if over_limit:
        return 1
    return 0


def run_source_replacement_copy(
    path: Path,
    replacement: Path,
    *,
    roi: RectangularROI,
    identity: Any,
    fps: float,
    frame_count: int,
    replace_after_s: float,
) -> dict[str, Any]:
    """Local copy of run_source_replacement that swaps a real SloMo capture."""

    import shutil
    import threading
    from functools import partial

    from PySide6.QtCore import Qt

    from neo_tracker.presets import color_marker_preset
    from neo_tracker.ui.tracking_worker import TrackingWorker

    failures: list[tuple[str, int]] = []
    completions: list[Any] = []
    pipeline = color_marker_preset(roi=roi)
    worker = TrackingWorker(
        pipeline=pipeline,
        reader_factory=partial(MediaReader, str(path)),
        isolated_reader_factory=partial(MediaReader, str(path)),
        frame_count=frame_count,
        fps=fps,
        process_isolation=True,
        expected_source_path=str(path),
        expected_source_identity=identity,
    )
    worker.failed.connect(
        lambda message, completed: failures.append((message, completed)),
        Qt.ConnectionType.DirectConnection,
    )
    worker.completed.connect(
        lambda *args: completions.append(args),
        Qt.ConnectionType.DirectConnection,
    )
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    time.sleep(replace_after_s)
    if not replacement.exists():
        raise FileNotFoundError(f"replacement media is missing: {replacement}")
    shutil.copyfile(replacement, path)
    thread.join(timeout=600.0)
    time.sleep(0.4)
    source_changed = any(
        message.startswith(TRACKING_SOURCE_CHANGED_PREFIX)
        for message, _completed in failures
    )
    return {
        "replace_after_s": replace_after_s,
        "finished": not thread.is_alive(),
        "source_changed_detected": source_changed,
        "completions": completions,
        "failures": [message[:160] for message, _c in failures],
        "results": len(pipeline.results),
        "residual_processes": residual_processes(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
