from __future__ import annotations

"""Real-media matrix for ICS-NeoTracker P0-B.

Runs the bounded tracking pipeline against real captured footage
(artifacts/experiment-videos/red-dot-tracking.mp4), its HEVC and 1080p
transcodes, a byte-truncated copy, and a mid-run source replacement. Output is
one valid JSON document on stdout; Qt/system warnings stay on stderr.

Usage:
    PYTHONPATH=. python3 benchmarks/benchmark_real_media_matrix.py
        [--matrix-dir artifacts/deepseek-2026-08-09/media-matrix]

VFR original-acquisition footage is not available in this workspace; the
truncation/VFR EOF behavior is covered by unit tests (tests/test_media.py) and
this run marks the VFR acquisition cell as blocked.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt

from neo_tracker.media import MediaReader, probe_media, probe_media_identity
from neo_tracker.presets import color_marker_preset
from neo_tracker.roi import RectangularROI
from neo_tracker.ui.tracking_worker import (
    TRACKING_SOURCE_CHANGED_PREFIX,
    TrackingWorker,
)


REAL_SOURCE = Path("artifacts/experiment-videos/red-dot-tracking.mp4")
SYNTHETIC_REPLACEMENT = Path("artifacts/code-review-ui-2026-07-22/synthetic-red-marker.mp4")
def run_ffmpeg(arguments: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_matrix(matrix_dir: Path) -> dict[str, Path]:
    matrix_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[str, Path] = {}
    hevc = matrix_dir / "hevc-transcode.mp4"
    if not hevc.exists():
        run_ffmpeg(
            [
                "-i", str(REAL_SOURCE),
                "-c:v", "libx265", "-crf", "28", "-tag:v", "hvc1", "-an",
                str(hevc),
            ]
        )
    prepared["hevc"] = hevc

    upscale = matrix_dir / "upscale-1080p.mp4"
    if not upscale.exists():
        run_ffmpeg(
            [
                "-i", str(REAL_SOURCE),
                "-vf", "scale=1920:1080", "-c:v", "libx264", "-crf", "26", "-an",
                str(upscale),
            ]
        )
    prepared["1080p"] = upscale

    truncated = matrix_dir / "truncated.mp4"
    if not truncated.exists():
        raw = REAL_SOURCE.read_bytes()
        truncated.write_bytes(raw[: int(len(raw) * 0.60)])
    prepared["truncated"] = truncated

    swap = matrix_dir / "source-swap-base.mp4"
    if not swap.exists():
        swap.write_bytes(REAL_SOURCE.read_bytes())
    prepared["swap"] = swap
    return prepared


def _ps_value(pid: int, column: str) -> str:
    try:
        return subprocess.run(
            ["ps", "-o", f"{column}=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def child_pids() -> list[tuple[int, int]]:
    """Return (pid, rss_kb) for live multiprocessing children of this process."""

    results: list[tuple[int, int]] = []
    for child in multiprocessing.active_children():
        pid = child.pid
        if pid is None:
            continue
        rss_text = _ps_value(pid, "rss")
        if rss_text.isdigit():
            results.append((pid, int(rss_text)))
    return results


def residual_processes() -> list[tuple[int, int, str]]:
    """Return any live spawn-like children of this process that survived a run."""

    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return []
    results: list[tuple[int, int, str]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid_text, ppid_text, command = parts
        try:
            pid, ppid = int(pid_text), int(ppid_text)
        except ValueError:
            continue
        if ppid == os.getpid() and ("spawn" in command or "neo_tracker" in command):
            results.append((pid, ppid, command[:120]))
    return results


def result_digest(pipeline: Any) -> str:
    digest = hashlib.sha256()
    for result in pipeline.results:
        digest.update(
            f"{int(result.frame_index)}:{repr(result.filtered_state)}".encode("utf-8")
        )
    return digest.hexdigest()


def run_full_tracking(
    path: Path,
    *,
    roi: RectangularROI,
    identity: Any,
    fps: float,
    frame_count: int,
    label: str,
) -> dict[str, Any]:
    failures: list[tuple[str, int]] = []
    completions: list[tuple[int, bool, bool, str]] = []
    progresses: list[Any] = []
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
        progress_interval_s=0.02,
    )
    worker.failed.connect(
        lambda message, completed: failures.append((message, completed)),
        Qt.ConnectionType.DirectConnection,
    )
    worker.completed.connect(
        lambda *args: completions.append(args),
        Qt.ConnectionType.DirectConnection,
    )
    worker.progress.connect(progresses.append, Qt.ConnectionType.DirectConnection)

    peak_child_rss_kb = 0
    sampling = threading.Event()

    def sample_rss() -> None:
        nonlocal peak_child_rss_kb
        while not sampling.is_set():
            for _pid, rss_kb in child_pids():
                peak_child_rss_kb = max(peak_child_rss_kb, rss_kb)
            time.sleep(0.04)

    sampler = threading.Thread(target=sample_rss, daemon=True)
    sampler.start()
    started_at = time.perf_counter()
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    thread.join(timeout=600.0)
    elapsed_s = time.perf_counter() - started_at
    sampling.set()
    sampler.join(timeout=1.0)
    if thread.is_alive():
        raise RuntimeError(f"{label}: tracking run did not finish within 600s")

    parent_max_rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    time.sleep(0.4)
    residual = residual_processes()
    input_s = sum(float(progress.input_s) for progress in progresses)
    processing_s = sum(float(progress.processing_s) for progress in progresses)
    return {
        "label": label,
        "elapsed_s": round(elapsed_s, 3),
        "completed": completions[0][0] if completions else None,
        "cancelled": completions[0][1] if completions else None,
        "ended_early": completions[0][2] if completions else None,
        "note": completions[0][3] if completions else "",
        "failures": failures,
        "results": len(pipeline.results),
        "result_digest": result_digest(pipeline),
        "input_s": round(input_s, 3),
        "processing_s": round(processing_s, 3),
        "throughput_fps": round(
            (len(pipeline.results) - int(worker.start_frame)) / elapsed_s, 3
        )
        if elapsed_s > 0.0
        else 0.0,
        "peak_parent_rss_kb": parent_max_rss_kb,
        "peak_child_rss_kb": peak_child_rss_kb,
        "progress_samples": len(progresses),
        "residual_processes": residual,
    }


def run_cancel(
    path: Path,
    *,
    roi: RectangularROI,
    identity: Any,
    fps: float,
    frame_count: int,
    cancel_after_s: float = 0.6,
) -> dict[str, Any]:
    failures: list[tuple[str, int]] = []
    completions: list[tuple[int, bool, bool, str]] = []
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
    time.sleep(cancel_after_s)
    cancel_started = time.perf_counter()
    worker.request_cancel()
    thread.join(timeout=10.0)
    cancel_latency_s = time.perf_counter() - cancel_started
    time.sleep(0.4)
    return {
        "cancel_requested_after_s": cancel_after_s,
        "cancel_latency_s": round(cancel_latency_s, 3),
        "finished": not thread.is_alive(),
        "completions": completions,
        "failures": failures,
        "results": len(pipeline.results),
        "residual_processes": residual_processes(),
    }


def run_source_replacement(
    path: Path,
    replacement: Path,
    *,
    roi: RectangularROI,
    identity: Any,
    fps: float,
    frame_count: int,
    replace_after_s: float = 1.2,
) -> dict[str, Any]:
    failures: list[tuple[str, int]] = []
    completions: list[tuple[int, bool, bool, str]] = []
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
    thread.join(timeout=30.0)
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


def probe_all(prepared: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, path in prepared.items():
        info = probe_media(str(path))
        identity = probe_media_identity(path)
        rows.append(
            {
                "name": name,
                "path": str(path),
                "available": info.available,
                "error": info.error or "",
                "fps": info.fps,
                "frame_count": info.frame_count,
                "width": info.width,
                "height": info.height,
                "identity": identity.to_dict() if identity is not None else None,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix-dir",
        default="artifacts/deepseek-2026-08-09/media-matrix",
    )
    args = parser.parse_args()
    matrix_dir = Path(args.matrix_dir)
    prepared = prepare_matrix(matrix_dir)
    real_info = probe_media(str(REAL_SOURCE))
    real_identity = probe_media_identity(REAL_SOURCE)

    roi_640 = RectangularROI(100.0, 60.0, 440.0, 240.0)
    roi_1080 = RectangularROI(500.0, 300.0, 920.0, 480.0)
    roi_1080_full = RectangularROI(300.0, 180.0, 1620.0, 900.0)

    report: dict[str, Any] = {
        "environment": {
            "os": "macOS 15.7.7",
            "python": sys.version.split()[0],
            "source": str(REAL_SOURCE),
            "source_probe": {
                "fps": real_info.fps,
                "frame_count": real_info.frame_count,
                "width": real_info.width,
                "height": real_info.height,
            },
        },
        "probe_matrix": probe_all(prepared),
        "runs": [],
    }

    runs = [
        ("real-h264", REAL_SOURCE, real_info.fps, real_info.frame_count, roi_640),
        ("hevc-transcode", prepared["hevc"], 20.0, 72, roi_640),
        ("upscale-1080p", prepared["1080p"], 20.0, 72, roi_1080),
    ]
    for label, path, fps, frame_count, roi in runs:
        identity = probe_media_identity(path)
        run = run_full_tracking(
            path,
            roi=roi,
            identity=identity,
            fps=fps,
            frame_count=frame_count,
            label=label,
        )
        run_again = run_full_tracking(
            path,
            roi=roi,
            identity=identity,
            fps=fps,
            frame_count=frame_count,
            label=f"{label}-run2",
        )
        report["runs"].append(
            {
                "run": run,
                "deterministic": run["result_digest"] == run_again["result_digest"],
                "run2": run_again,
            }
        )

    report["cancel"] = run_cancel(
        prepared["1080p"],
        roi=roi_1080_full,
        identity=probe_media_identity(prepared["1080p"]),
        fps=20.0,
        frame_count=72,
        cancel_after_s=0.15,
    )

    swap_path = prepared["swap"]
    swap_path.write_bytes(prepared["1080p"].read_bytes())
    report["source_replacement"] = run_source_replacement(
        swap_path,
        SYNTHETIC_REPLACEMENT,
        roi=roi_1080_full,
        identity=probe_media_identity(swap_path),
        fps=20.0,
        frame_count=72,
        replace_after_s=0.4,
    )

    truncated_path = prepared["truncated"]
    truncated_read = {}
    info = probe_media(str(truncated_path))
    truncated_read["probe"] = {
        "available": info.available,
        "error": info.error or "",
        "frame_count": info.frame_count,
    }
    if info.available:
        try:
            with MediaReader(str(truncated_path)) as reader:
                truncated_read["last_frame"] = reader.read_frame(info.frame_count - 1).shape
                truncated_read["end_error"] = ""
        except Exception as exc:
            truncated_read["end_error"] = f"{type(exc).__name__}: {exc}"[:160]
    report["truncated"] = truncated_read

    reopen_rows: list[dict[str, Any]] = []
    for index in range(10):
        try:
            with MediaReader(str(REAL_SOURCE)) as reader:
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
        "vfr_original_acquisition": (
            "BLOCKED: no real VFR camera footage in workspace; ffmpeg CFR->VFR "
            "re-timing was unreliable, and synthetic VFR cannot substitute. "
            "VFR/truncation EOF behavior is covered by tests/test_media.py."
        ),
        "hevc_1080p": (
            "Transcodes of the real 640x360 clip (content real, acquisition "
            "characteristics re-encoded); they do not close the original 1080p/"
            "4K camera gate."
        ),
        "power_temperature": (
            "powermetrics requires root; not measured. CPU sampled via ps."
        ),
        "session_duration": (
            "BLOCKED: no long-duration real footage; clips are 72 frames (~3.6s)."
        ),
    }

    print(json.dumps(report, indent=2))
    over_limit = [row for row in report["runs"] if row["run"]["failures"]]
    if over_limit:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
