"""Exercise the shipped executable, including its real spawned helpers."""
from __future__ import annotations

import argparse
from functools import partial
import json
from pathlib import Path
import platform
import sys
import tempfile
from threading import Event
import traceback


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    args = parser.parse_args(arguments)
    report: dict[str, object] = {
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        import numpy as np
        from PySide6.QtWidgets import QApplication
        from scipy.signal import stft

        from neo_tracker.media import MediaReader
        from neo_tracker.presets import color_marker_preset
        from neo_tracker.project import NeoTrackerProject, ProjectTaskSnapshot
        from neo_tracker.roi import RectangularROI
        from neo_tracker.ui.isolated_media import PreviewDecoderSession, probe_media_isolated
        from neo_tracker.ui.main_window import NeoTrackerWindow
        from neo_tracker.ui.project_open_worker import load_project_isolated
        from neo_tracker.ui.tracking_worker import TrackingWorker

        app = QApplication.instance() or QApplication(["Neo-Tracker smoke test"])
        window = NeoTrackerWindow()
        window.show()
        app.processEvents()
        assert window.isVisible()
        report["gui"] = "passed"
        media = str(args.media.resolve())
        info = probe_media_isolated(media)
        assert info.available and info.frame_count > 0
        report["media_probe"] = "passed"
        with PreviewDecoderSession(media, expected_width=info.width, expected_height=info.height) as decoder:
            frame = decoder.decode(0)
            assert frame.shape == (info.height, info.width, 3)
        report["preview_decode"] = "passed"
        pipeline = color_marker_preset(roi=RectangularROI(0, 0, info.width, info.height))
        failures: list[tuple[object, ...]] = []
        completions: list[tuple[object, ...]] = []
        count = min(12, info.frame_count)
        worker = TrackingWorker(
            pipeline=pipeline,
            reader_factory=partial(MediaReader, media),
            frame_count=count,
            fps=info.fps,
            process_isolation=True,
        )
        worker.failed.connect(lambda *items: failures.append(items))
        worker.completed.connect(lambda *items: completions.append(items))
        worker.run()
        assert not failures, failures
        assert worker.process_isolation_used and len(pipeline.results) == count
        assert completions and completions[0][0] == count, completions
        report["isolated_tracking_frames"] = count
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "smoke.ntproj"
            project = NeoTrackerProject(
                name="Release smoke test",
                pipelines=[pipeline.to_config()],
                tasks=[ProjectTaskSnapshot(media, "color_marker", results=pipeline.results)],
            )
            project.save(path)
            restored = load_project_isolated(path, Event())
            assert restored.to_dict() == project.to_dict()
        report["project_save_open"] = "passed"
        _, _, spectrum = stft(np.sin(np.arange(1024) * 0.1), fs=100.0)
        assert np.isfinite(spectrum).all()
        report["scipy_stft"] = "passed"
        window.close()
        app.processEvents()
        report["status"] = "passed"
        result = 0
    except Exception:
        report["status"] = "failed"
        report["traceback"] = traceback.format_exc()
        result = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return result
