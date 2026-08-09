from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from neo_tracker.analysis import AnalysisConfig, available_tracking_series_info
from neo_tracker.core import TrackerResult
from neo_tracker.media import MAX_WAV_CHANNELS, MediaInfo
from neo_tracker.ui.analysis_controller import AnalysisController


def tracking_results(count: int = 32, sample_rate: float = 16.0) -> list[TrackerResult]:
    return [
        TrackerResult(
            frame_index=index,
            time_s=index / sample_rate,
            state={"x_world": float(index)},
            filtered_state={"x_world": float(index)},
            confidence=1.0,
            status="ok",
        )
        for index in range(count)
    ]


class AnalysisControllerTests(unittest.TestCase):
    def test_audio_sources_are_capped_at_max_channels(self) -> None:
        media_info = MediaInfo(
            kind="audio",
            available=True,
            channels=70,
            sample_rate_hz=1.0,
            frame_count=1,
        )

        sources = AnalysisController.available_sources([], {}, "clip.wav", media_info)
        audio_sources = [source for source in sources if source.kind == "audio"]

        self.assertLessEqual(len(audio_sources), 1 + MAX_WAV_CHANNELS)
        channel_labels = [
            source.channel for source in audio_sources if source.channel != "mono"
        ]
        self.assertEqual(len(channel_labels), MAX_WAV_CHANNELS)
        self.assertNotIn(MAX_WAV_CHANNELS, channel_labels)

    def test_audio_sources_carry_decoded_source_bytes(self) -> None:
        media_info = MediaInfo(
            kind="audio",
            available=True,
            channels=2,
            sample_width_bytes=2,
            sample_rate_hz=48_000.0,
            frame_count=1000,
        )

        sources = AnalysisController.available_sources([], {}, "clip.wav", media_info)
        audio_sources = [source for source in sources if source.kind == "audio"]

        self.assertEqual(len(audio_sources), 3)  # mono + 2 channels
        for source in audio_sources:
            self.assertEqual(source.decoded_source_bytes, 1000 * 2 * 2)

    def test_tracking_sources_include_units_rate_and_sample_count(self) -> None:
        controller = AnalysisController()
        sources = controller.available_sources(tracking_results(), {"x_world": "cm"}, None, None)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].label, "Tracking: x_world (cm)")
        self.assertAlmostEqual(sources[0].sample_rate_hz, 16.0)
        self.assertEqual(sources[0].sample_count, 32)
        self.assertEqual(sources[0].total_sample_count, 32)
        self.assertIn("32 samples", sources[0].detail())
        self.assertIn("16 Hz", sources[0].detail())

    def test_tracking_source_detail_reports_omitted_samples(self) -> None:
        results = tracking_results()
        results[3].filtered_state["x_world"] = float("nan")

        source = AnalysisController.available_sources(results, {"x_world": "cm"}, None, None)[0]
        restored = type(source).from_data(source.to_data(), source.label)

        self.assertEqual(restored.sample_count, 31)
        self.assertEqual(restored.total_sample_count, 32)
        self.assertEqual(restored.omitted_sample_count, 1)
        self.assertIn("31 usable samples of 32 results", restored.detail())
        self.assertIn("1 omitted", restored.detail())

    def test_tracking_source_discovery_does_not_materialize_each_series(self) -> None:
        controller = AnalysisController()
        results = tracking_results()

        with patch(
            "neo_tracker.ui.analysis_controller.tracking_series",
            side_effect=AssertionError("source discovery must use metadata only"),
        ):
            sources = controller.available_sources(results, {"x_world": "cm"}, None, None)

        self.assertEqual([(source.key, source.sample_count) for source in sources], [("x_world", 32)])

    def test_cached_source_discovery_reuses_metadata_until_results_change(self) -> None:
        controller = AnalysisController()
        results = tracking_results(8)
        with patch(
            "neo_tracker.ui.analysis_controller.available_tracking_series_info",
            wraps=available_tracking_series_info,
        ) as scan:
            first = controller.available_sources_cached(results, {"x_world": "cm"}, None, None)
            repeated = controller.available_sources_cached(results, {"x_world": "mm"}, None, None)
            results.append(tracking_results(1)[0])
            grown = controller.available_sources_cached(results, {"x_world": "cm"}, None, None)

        self.assertEqual(scan.call_count, 2)
        self.assertEqual(first[0].unit, "cm")
        self.assertEqual(repeated[0].unit, "mm")
        self.assertEqual(grown[0].total_sample_count, 9)

    def test_cached_source_discovery_can_rescan_in_place_edits(self) -> None:
        controller = AnalysisController()
        results = tracking_results(4)
        initial = controller.available_sources_cached(results, {"x_world": "cm"}, None, None)
        results[1].filtered_state["y_world"] = 2.0

        cached = controller.available_sources_cached(results, {"x_world": "cm"}, None, None)
        forced = controller.available_sources_cached(
            results,
            {"x_world": "cm", "y_world": "cm"},
            None,
            None,
            force=True,
        )
        controller.invalidate_source_cache()
        invalidated = controller.available_sources_cached(
            results,
            {"x_world": "cm", "y_world": "cm"},
            None,
            None,
        )

        self.assertEqual([source.key for source in initial], ["x_world"])
        self.assertEqual([source.key for source in cached], ["x_world"])
        self.assertEqual([source.key for source in forced], ["x_world", "y_world"])
        self.assertEqual([source.key for source in invalidated], ["x_world", "y_world"])

    def test_cached_source_metadata_updates_one_copy_on_write_result(self) -> None:
        controller = AnalysisController()
        results = tracking_results(8)
        controller.available_sources_cached(results, {"x_world": "cm"}, None, None)
        previous = results[3]
        replacement = replace(previous, filtered_state={"x_world": float("nan")})
        results[3] = replacement

        with patch(
            "neo_tracker.ui.analysis_controller.available_tracking_series_info",
            wraps=available_tracking_series_info,
        ) as scan, patch.object(
            controller,
            "invalidate_source_cache",
            wraps=controller.invalidate_source_cache,
        ) as invalidate:
            self.assertTrue(
                controller.refresh_result_source_cache(results, 3, previous, replacement)
            )
            sources = controller.available_sources_cached(
                results,
                {"x_world": "cm"},
                None,
                None,
            )

        invalidate.assert_called_once_with()
        scan.assert_not_called()
        self.assertEqual(sources[0].sample_count, 7)
        self.assertEqual(sources[0].total_sample_count, 8)

    def test_cached_source_metadata_rejects_state_key_changes(self) -> None:
        controller = AnalysisController()
        results = tracking_results(4)
        controller.available_sources_cached(results, {"x_world": "cm"}, None, None)
        previous = results[1]
        replacement = replace(
            previous,
            filtered_state={"x_world": 1.0, "y_world": 2.0},
        )
        results[1] = replacement

        self.assertFalse(
            controller.refresh_result_source_cache(results, 1, previous, replacement)
        )
        sources = controller.available_sources_cached(
            results,
            {"x_world": "cm", "y_world": "cm"},
            None,
            None,
        )

        self.assertEqual([source.key for source in sources], ["x_world", "y_world"])

    def test_audio_source_discovery_uses_metadata_without_reading_file(self) -> None:
        controller = AnalysisController()
        info = MediaInfo(
            fps=8000.0,
            frame_count=400,
            width=0,
            height=0,
            duration_s=0.05,
            available=True,
            kind="audio",
            sample_rate_hz=8000.0,
            channels=2,
        )

        sources = controller.available_sources([], {}, "/missing/metadata-only.wav", info)

        self.assertEqual([source.label for source in sources], [
            "Audio WAV: mono",
            "Audio WAV: channel 0",
            "Audio WAV: channel 1",
        ])
        self.assertTrue(all(source.sample_count == 400 for source in sources))

    def test_run_owns_result_context_and_exports(self) -> None:
        controller = AnalysisController()
        results = tracking_results()
        source = controller.available_sources(results, {"x_world": "cm"}, None, None)[0]

        run = controller.run(source, results, None, AnalysisConfig(window="boxcar"), owner_token=7)

        self.assertEqual(run.kind, "fft")
        self.assertTrue(controller.has_result)
        self.assertTrue(controller.matches_context(source, 7))
        self.assertFalse(controller.matches_context(source, 8))
        self.assertIn("FFT spectrum", run.summary)
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "fft.csv"
            npz_path = Path(tmpdir) / "fft.npz"
            controller.export_csv(csv_path)
            controller.export_npz(npz_path)
            self.assertTrue(csv_path.exists())
            self.assertTrue(npz_path.exists())

        controller.clear()
        self.assertFalse(controller.has_result)
        with self.assertRaisesRegex(ValueError, "Run FFT or STFT"):
            controller.export_csv("unused.csv")


if __name__ == "__main__":
    unittest.main()
