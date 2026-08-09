from __future__ import annotations

import unittest

import numpy as np

from neo_tracker.analysis import AnalysisConfig, SignalSeries
from neo_tracker.ui.analysis_controller import AnalysisSource
from neo_tracker.ui.analysis_worker import AnalysisWorker


class AnalysisWorkerTests(unittest.TestCase):
    def test_worker_completes_without_owning_ui_controller_state(self) -> None:
        source = AnalysisSource(kind="tracking", label="Tracking: x", key="x", sample_rate_hz=8.0)
        series = SignalSeries(
            name="x",
            time_s=np.arange(16, dtype=float) / 8.0,
            values=np.sin(np.arange(16, dtype=float)),
            sample_rate_hz=8.0,
        )
        completed: list[object] = []
        failed: list[str] = []
        stages: list[str] = []
        worker = AnalysisWorker(
            source=source,
            config=AnalysisConfig(window="boxcar"),
            owner_token=42,
            series_loader=lambda _cancel: series,
        )
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        worker.stage_changed.connect(stages.append)

        worker.run()

        self.assertEqual(failed, [])
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].owner_token, 42)
        self.assertEqual(completed[0].source.identity, source.identity)
        self.assertEqual(stages, ["loading", "processing"])

    def test_worker_honors_cancel_before_loading(self) -> None:
        loaded: list[bool] = []
        canceled: list[bool] = []
        worker = AnalysisWorker(
            source=AnalysisSource(kind="audio", label="Audio WAV: mono"),
            config=AnalysisConfig(),
            owner_token=1,
            series_loader=lambda _cancel: loaded.append(True),  # type: ignore[arg-type,return-value]
        )
        worker.canceled.connect(lambda: canceled.append(True))

        worker.request_cancel()
        worker.run()

        self.assertEqual(loaded, [])
        self.assertEqual(canceled, [True])

    def test_audio_worker_reports_loading_then_processing_stages(self) -> None:
        source = AnalysisSource(kind="audio", label="Audio WAV: mono", sample_rate_hz=8.0)
        series = SignalSeries(
            name="audio",
            time_s=np.arange(16, dtype=float) / 8.0,
            values=np.sin(np.arange(16, dtype=float)),
            sample_rate_hz=8.0,
        )
        stages: list[str] = []
        worker = AnalysisWorker(
            source=source,
            config=AnalysisConfig(window="boxcar"),
            owner_token=7,
            series_loader=lambda _cancel: series,
        )
        worker.stage_changed.connect(stages.append)

        worker.run()

        self.assertEqual(stages, ["loading", "processing"])

    def test_worker_rejects_oversized_source_before_loading(self) -> None:
        loaded: list[bool] = []
        failed: list[str] = []
        worker = AnalysisWorker(
            source=AnalysisSource(
                kind="audio",
                label="Audio WAV: mono",
                sample_rate_hz=48_000.0,
                sample_count=20_000_000,
            ),
            config=AnalysisConfig(method="fft"),
            owner_token=1,
            series_loader=lambda _cancel: loaded.append(True),  # type: ignore[arg-type,return-value]
        )
        worker.failed.connect(failed.append)

        worker.run()

        self.assertEqual(loaded, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("safety limit", failed[0])

    def test_worker_rejects_wav_decode_cost_before_loading(self) -> None:
        loaded: list[bool] = []
        failed: list[str] = []
        worker = AnalysisWorker(
            source=AnalysisSource(
                kind="audio",
                label="Audio WAV: mono",
                sample_rate_hz=48_000.0,
                sample_count=100,
                decoded_source_bytes=512 * 1024 * 1024 + 1,
            ),
            config=AnalysisConfig(method="fft"),
            owner_token=3,
            series_loader=lambda _cancel: loaded.append(True),  # type: ignore[arg-type,return-value]
        )
        worker.failed.connect(failed.append)

        worker.run()

        self.assertEqual(loaded, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("decode", failed[0].lower())

    def test_worker_preflight_differentiates_decode_cost_with_same_frame_count(self) -> None:
        calls: list[int] = []

        def make_worker(decoded_bytes: int, token: int) -> AnalysisWorker:
            return AnalysisWorker(
                source=AnalysisSource(
                    kind="audio",
                    label=f"Audio WAV: {token}",
                    sample_rate_hz=48_000.0,
                    sample_count=100,
                    decoded_source_bytes=decoded_bytes,
                ),
                config=AnalysisConfig(method="fft"),
                owner_token=token,
                series_loader=lambda _cancel: calls.append(token),  # type: ignore[arg-type,return-value]
            )

        within_limit = make_worker(100 * 2 * 2, 1)
        over_limit = make_worker(512 * 1024 * 1024 + 1, 2)
        failed: list[str] = []
        over_limit.failed.connect(failed.append)

        within_limit.run()
        over_limit.run()

        self.assertEqual(calls, [1])
        self.assertEqual(len(failed), 1)
        self.assertIn("decode", failed[0].lower())


if __name__ == "__main__":
    unittest.main()
