from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from neo_tracker.media import MediaInfo
from neo_tracker.ui.media_probe_worker import MediaProbeWorker


class MediaProbeWorkerTests(unittest.TestCase):
    def test_default_video_probe_crosses_isolated_decoder_boundary(self) -> None:
        completed: list[object] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "selected.mp4"
            path.write_bytes(b"selected media")
            with patch(
                "neo_tracker.ui.media_probe_worker.probe_media_isolated",
                return_value=MediaInfo(
                    fps=30.0,
                    frame_count=2,
                    width=4,
                    height=3,
                    available=True,
                ),
            ) as isolated_probe:
                worker = MediaProbeWorker((str(path),))
                worker.completed.connect(completed.append)
                worker.run()

        isolated_probe.assert_called_once()
        self.assertTrue(completed[0][0][1].available)  # type: ignore[index]

    def test_worker_preserves_order_and_reports_progress(self) -> None:
        paths = ("/media/a.mp4", "/media/b.wav")
        completed: list[object] = []
        progress: list[tuple[int, int, str]] = []
        worker = MediaProbeWorker(
            paths,
            media_probe=lambda path: MediaInfo(
                available=True,
                kind="audio" if path.endswith(".wav") else "video",
            ),
        )
        worker.completed.connect(completed.append)
        worker.progressed.connect(lambda done, total, name: progress.append((done, total, name)))

        worker.run()

        self.assertEqual(len(completed), 1)
        results = completed[0]
        self.assertEqual([path for path, _info in results], list(paths))
        self.assertEqual([info.kind for _path, info in results], ["video", "audio"])
        self.assertEqual(progress, [(1, 2, "a.mp4"), (2, 2, "b.wav")])

    def test_probe_exception_becomes_unavailable_media_result(self) -> None:
        completed: list[object] = []

        def fail_probe(_path: str) -> MediaInfo:
            raise RuntimeError("decoder exploded")

        worker = MediaProbeWorker(("/media/broken.wav",), media_probe=fail_probe)
        worker.completed.connect(completed.append)

        worker.run()

        results = completed[0]
        _path, info = results[0]
        self.assertFalse(info.available)
        self.assertEqual(info.kind, "audio")
        self.assertIn("decoder exploded", info.error)

    def test_cancel_discards_the_whole_batch(self) -> None:
        completed: list[object] = []
        canceled: list[bool] = []
        worker: MediaProbeWorker

        def cancel_during_probe(_path: str) -> MediaInfo:
            worker.request_cancel()
            return MediaInfo(available=True)

        worker = MediaProbeWorker(("/media/a.mp4", "/media/b.mp4"), media_probe=cancel_during_probe)
        worker.completed.connect(completed.append)
        worker.canceled.connect(lambda: canceled.append(True))

        worker.run()

        self.assertEqual(completed, [])
        self.assertEqual(canceled, [True])


if __name__ == "__main__":
    unittest.main()
