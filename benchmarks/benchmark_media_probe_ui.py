from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
import time
from time import monotonic, perf_counter
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog

from neo_tracker.media import MediaInfo, probe_media
from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot
from neo_tracker.ui.main_window import NeoTrackerWindow


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * ratio)))
    return ordered[index]


def _heartbeat_metrics(samples_ms: list[float]) -> dict[str, float | int]:
    return {
        "count": len(samples_ms),
        "median_ms": median(samples_ms),
        "p95_ms": _percentile(samples_ms, 0.95),
        "max_ms": max(samples_ms),
        "over_20ms": sum(sample > 20.0 for sample in samples_ms),
    }


def _info_signature(info: MediaInfo | None) -> tuple[object, ...]:
    if info is None:
        return ()
    identity = info.source_identity
    return (
        info.available,
        info.kind,
        info.fps,
        info.frame_count,
        info.width,
        info.height,
        info.duration_s,
        info.error,
        identity.strategy if identity is not None else None,
        identity.sha256 if identity is not None else None,
    )


def _scenario(
    paths: tuple[str, ...],
    media_probe,
    *,
    background: bool,
) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    window = NeoTrackerWindow()
    window.project_controller.media_probe = media_probe
    heartbeat_ms: list[float] = []
    last_heartbeat = [monotonic()]
    heartbeat_timer = QTimer()
    heartbeat_timer.setTimerType(Qt.TimerType.PreciseTimer)
    heartbeat_timer.setInterval(5)

    def heartbeat() -> None:
        now = monotonic()
        heartbeat_ms.append((now - last_heartbeat[0]) * 1000.0)
        last_heartbeat[0] = now

    heartbeat_timer.timeout.connect(heartbeat)
    heartbeat_timer.start()
    loop = QEventLoop()
    return_elapsed_ms: list[float] = []
    completion_elapsed_ms: list[float] = []
    signatures: list[tuple[object, ...]] = []
    started_at = [0.0]

    def finish() -> None:
        completion_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
        if background:
            signatures.extend(_info_signature(task.media_info) for task in window.tasks)
        QTimer.singleShot(60, loop.quit)

    def start() -> None:
        started_at[0] = perf_counter()
        if background:
            if not window._start_media_probe(paths):
                raise RuntimeError("background media probe did not start")
            return_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
            thread = window._media_probe_thread
            if thread is None:
                raise RuntimeError("background media probe thread was not created")
            thread.finished.connect(finish)
            return
        tasks = [window._new_task(path, window.default_pipeline_key) for path in paths]
        return_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
        signatures.extend(_info_signature(task.media_info) for task in tasks)
        finish()

    QTimer.singleShot(60, start)
    loop.exec()
    heartbeat_timer.stop()
    window.close()
    app.processEvents()
    return {
        "path_count": len(paths),
        "call_return_ms": return_elapsed_ms[0],
        "batch_complete_ms": completion_elapsed_ms[0],
        "heartbeat": _heartbeat_metrics(heartbeat_ms),
        "result_signatures": signatures,
    }


def _project_scenario(
    paths: tuple[str, ...],
    media_probe,
    *,
    background: bool,
) -> dict[str, object]:
    app = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as tmpdir:
        project_path = Path(tmpdir) / "probe-benchmark.ntproj"
        project = NeoTrackerProject(
            name="probe-benchmark",
            tasks=[
                ProjectTaskSnapshot(media_path=path, pipeline_key="color_marker")
                for path in paths
            ],
        )
        project.save(project_path)
        window = NeoTrackerWindow()
        window.project_controller.media_probe = media_probe
        heartbeat_ms: list[float] = []
        last_heartbeat = [monotonic()]
        heartbeat_timer = QTimer()
        heartbeat_timer.setTimerType(Qt.TimerType.PreciseTimer)
        heartbeat_timer.setInterval(5)

        def heartbeat() -> None:
            now = monotonic()
            heartbeat_ms.append((now - last_heartbeat[0]) * 1000.0)
            last_heartbeat[0] = now

        heartbeat_timer.timeout.connect(heartbeat)
        heartbeat_timer.start()
        loop = QEventLoop()
        return_elapsed_ms: list[float] = []
        completion_elapsed_ms: list[float] = []
        started_at = [0.0]

        def finish() -> None:
            completion_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
            QTimer.singleShot(60, loop.quit)

        def start() -> None:
            started_at[0] = perf_counter()
            if background:
                with patch.object(
                    QFileDialog,
                    "getOpenFileName",
                    return_value=(str(project_path), ""),
                ):
                    window._open_project()
                return_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
                thread = window._media_probe_thread
                if thread is None:
                    raise RuntimeError("background project media probe did not start")
                thread.finished.connect(finish)
                return
            window._load_project(project_path)
            return_elapsed_ms.append((perf_counter() - started_at[0]) * 1000.0)
            finish()

        QTimer.singleShot(60, start)
        loop.exec()
        heartbeat_timer.stop()
        signatures = [_info_signature(task.media_info) for task in window.tasks]
        window.close()
        app.processEvents()
        return {
            "path_count": len(paths),
            "unique_path_count": len(set(paths)),
            "call_return_ms": return_elapsed_ms[0],
            "batch_complete_ms": completion_elapsed_ms[0],
            "heartbeat": _heartbeat_metrics(heartbeat_ms),
            "result_signatures": signatures,
        }


def benchmark(source: Path, *, slow_count: int, delay_ms: float, real_count: int) -> dict[str, object]:
    if not source.exists():
        raise FileNotFoundError(source)

    def delayed_probe(path: str) -> MediaInfo:
        time.sleep(max(0.0, delay_ms) / 1000.0)
        index = int(Path(path).stem.rsplit("-", 1)[-1])
        return MediaInfo(
            fps=30.0,
            frame_count=300 + index,
            width=1920,
            height=1080,
            duration_s=(300 + index) / 30.0,
            available=True,
        )

    slow_paths = tuple(f"/diagnostic/slow-media-{index}.mp4" for index in range(max(1, slow_count)))
    real_paths = tuple(str(source) for _ in range(max(1, real_count)))
    slow_sync = _scenario(slow_paths, delayed_probe, background=False)
    slow_background = _scenario(slow_paths, delayed_probe, background=True)
    real_sync = _scenario(real_paths, probe_media, background=False)
    real_background = _scenario(real_paths, probe_media, background=True)
    slow_project_sync = _project_scenario(slow_paths, delayed_probe, background=False)
    slow_project_background = _project_scenario(slow_paths, delayed_probe, background=True)
    real_project_sync = _project_scenario(real_paths, probe_media, background=False)
    real_project_background = _project_scenario(real_paths, probe_media, background=True)

    def compact_pair(
        synchronous: dict[str, object],
        background: dict[str, object],
    ) -> dict[str, object]:
        synchronous_signatures = synchronous.pop("result_signatures")
        background_signatures = background.pop("result_signatures")
        for result, signatures in (
            (synchronous, synchronous_signatures),
            (background, background_signatures),
        ):
            result["result_count"] = len(signatures)
            result["result_digest"] = sha256(repr(signatures).encode("utf-8")).hexdigest()
        return {
            "synchronous": synchronous,
            "background": background,
            "results_exact": synchronous_signatures == background_signatures,
        }

    return {
        "source": str(source),
        "slow_probe_delay_ms_per_file": delay_ms,
        "slow_probe": compact_pair(slow_sync, slow_background),
        "local_real_probe": compact_pair(real_sync, real_background),
        "project_open_slow_probe": compact_pair(
            slow_project_sync,
            slow_project_background,
        ),
        "project_open_repeated_local_source": compact_pair(
            real_project_sync,
            real_project_background,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Add Media probing and GUI heartbeat.")
    parser.add_argument(
        "--source",
        type=Path,
        default=WORKSPACE_ROOT / "artifacts/experiment-videos/red-dot-tracking.mp4",
    )
    parser.add_argument("--slow-count", type=int, default=12)
    parser.add_argument("--delay-ms", type=float, default=25.0)
    parser.add_argument("--real-count", type=int, default=40)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(
                args.source,
                slow_count=args.slow_count,
                delay_ms=args.delay_ms,
                real_count=args.real_count,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
