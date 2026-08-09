from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from neo_tracker import analysis
from neo_tracker.analysis import (
    AnalysisConfig,
    SignalSeries,
    compute_fft,
    compute_stft,
    estimate_analysis_working_set_bytes,
    available_tracking_series_info,
    tracking_series,
    validate_analysis_workload,
    wav_signal_series,
    write_fft_csv,
    write_fft_npz,
    write_stft_csv,
    write_stft_npz,
)
from neo_tracker.core import TrackerResult


class SignalAnalysisTests(unittest.TestCase):
    def test_cleaned_series_reuses_all_finite_arrays_and_copies_sparse_values(self) -> None:
        time_s = np.arange(8, dtype=float)
        values = np.arange(8, dtype=float)

        dense = SignalSeries("dense", time_s, values, 1.0).cleaned()
        sparse_values = values.copy()
        sparse_values[3] = float("nan")
        sparse = SignalSeries("sparse", time_s, sparse_values, 1.0).cleaned()

        self.assertIs(dense.time_s, time_s)
        self.assertIs(dense.values, values)
        self.assertEqual(sparse.values.size, 7)
        self.assertFalse(np.shares_memory(sparse.time_s, time_s))
        self.assertFalse(np.shares_memory(sparse.values, sparse_values))

    def test_fft_detects_known_sine_peak(self) -> None:
        sample_rate = 200.0
        frequency = 12.5
        time_s = np.arange(0, 4.0, 1.0 / sample_rate)
        values = np.sin(2.0 * math.pi * frequency * time_s)
        series = SignalSeries("sine", time_s, values, sample_rate)
        result = compute_fft(series, AnalysisConfig(detrend="mean", window="boxcar"))
        bin_width = result.frequency_hz[1] - result.frequency_hz[0]
        self.assertLessEqual(abs(result.peak_frequency_hz - frequency), bin_width)

    def test_fft_mean_detrend_reduces_dc_component(self) -> None:
        sample_rate = 100.0
        time_s = np.arange(0, 2.0, 1.0 / sample_rate)
        values = 4.0 + np.sin(2.0 * math.pi * 8.0 * time_s)
        series = SignalSeries("offset", time_s, values, sample_rate)
        raw = compute_fft(series, AnalysisConfig(detrend="none", window="boxcar"))
        detrended = compute_fft(series, AnalysisConfig(detrend="mean", window="boxcar"))
        self.assertGreater(raw.amplitude[0], detrended.amplitude[0] * 100.0)

    def test_fft_window_options_keep_frequency_axis_shape(self) -> None:
        sample_rate = 80.0
        time_s = np.arange(0, 1.0, 1.0 / sample_rate)
        values = np.sin(2.0 * math.pi * 5.0 * time_s)
        series = SignalSeries("windowed", time_s, values, sample_rate)
        lengths = []
        for window_name in ("boxcar", "hann", "hamming", "blackman"):
            result = compute_fft(series, AnalysisConfig(window=window_name))
            lengths.append(result.frequency_hz.size)
        self.assertEqual(len(set(lengths)), 1)

    def test_analysis_config_rejects_invalid_ranges_and_stft_overlap(self) -> None:
        series = SignalSeries("short", np.arange(8, dtype=float), np.arange(8, dtype=float), 1.0)
        with self.assertRaisesRegex(ValueError, "minimum frequency must not exceed maximum"):
            compute_fft(series, AnalysisConfig(frequency_min_hz=3.0, frequency_max_hz=2.0))
        with self.assertRaisesRegex(ValueError, "frequency range contains no FFT bins"):
            compute_fft(series, AnalysisConfig(frequency_min_hz=10.0))
        with self.assertRaisesRegex(ValueError, "STFT overlap ratio"):
            compute_stft(series, AnalysisConfig(method="stft", stft_overlap_ratio=1.0))
        with self.assertRaisesRegex(ValueError, "must not exceed 1,000,000 Hz"):
            compute_fft(series, AnalysisConfig(sample_rate_hz=1_000_001.0))
        with self.assertRaisesRegex(ValueError, "must not exceed 1,000,000 samples"):
            compute_stft(series, AnalysisConfig(method="stft", stft_window_size=1_000_001))

    def test_stft_reports_rising_frequency_trend(self) -> None:
        sample_rate = 200.0
        first_t = np.arange(0, 2.0, 1.0 / sample_rate)
        second_t = np.arange(2.0, 4.0, 1.0 / sample_rate)
        time_s = np.concatenate([first_t, second_t])
        values = np.concatenate(
            [
                np.sin(2.0 * math.pi * 12.0 * first_t),
                np.sin(2.0 * math.pi * 32.0 * second_t),
            ]
        )
        series = SignalSeries("step", time_s, values, sample_rate)
        result = compute_stft(
            series,
            AnalysisConfig(method="stft", window="hann", stft_window_size=128, stft_overlap_ratio=0.5),
        )
        ridge = result.frequency_hz[np.argmax(result.amplitude, axis=0)]
        self.assertGreater(float(np.median(ridge[-3:])), float(np.median(ridge[:3])))

    def test_stft_without_scipy_reports_clear_error(self) -> None:
        old_signal = analysis._SCIPY_SIGNAL
        old_error = analysis._SCIPY_SIGNAL_IMPORT_ERROR
        try:
            analysis._SCIPY_SIGNAL = None
            analysis._SCIPY_SIGNAL_IMPORT_ERROR = ModuleNotFoundError("No module named 'scipy'")
            series = SignalSeries("short", np.array([0.0, 1.0]), np.array([0.0, 1.0]), 1.0)
            with self.assertRaisesRegex(RuntimeError, "STFT requires scipy.signal"):
                compute_stft(series)
        finally:
            analysis._SCIPY_SIGNAL = old_signal
            analysis._SCIPY_SIGNAL_IMPORT_ERROR = old_error

    def test_wav_signal_series_reads_pcm_and_mono_mix(self) -> None:
        sample_rate = 8000
        time_s = np.arange(0, 0.1, 1.0 / sample_rate)
        left = 0.5 * np.sin(2.0 * math.pi * 440.0 * time_s)
        right = np.zeros_like(left)
        stereo = np.column_stack([left, right])
        pcm = np.clip(stereo * 32767.0, -32768, 32767).astype("<i2")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tone.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm.tobytes())
            series = wav_signal_series(path, channel="mono")
        self.assertEqual(series.source_type, "audio")
        self.assertEqual(series.sample_rate_hz, sample_rate)
        self.assertEqual(series.values.size, time_s.size)
        self.assertLess(np.max(np.abs(series.values)), np.max(np.abs(left)))

    def test_wav_signal_series_streams_exact_channel_and_mono_values(self) -> None:
        pcm = np.column_stack(
            [
                np.arange(-50, 73, dtype="<i2"),
                np.arange(72, -51, -1, dtype="<i2"),
            ]
        )
        expected_channels = pcm.astype(float) / 32768.0
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chunked.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(12_000)
                wav.writeframes(pcm.tobytes())
            mono = wav_signal_series(path, channel="mono", chunk_frames=17)
            right = wav_signal_series(path, channel=1, chunk_frames=17)

        self.assertTrue(np.array_equal(mono.values, expected_channels.mean(axis=1)))
        self.assertTrue(np.array_equal(right.values, expected_channels[:, 1]))
        self.assertTrue(np.array_equal(right.time_s, np.arange(pcm.shape[0], dtype=float) / 12_000.0))
        self.assertIsNone(mono.values.base)
        self.assertIsNone(right.values.base)

    def test_wav_signal_series_streams_exact_24_bit_values(self) -> None:
        pcm = np.asarray(
            [
                [-8_388_608, 8_388_607],
                [-1, 1],
                [0, -4_194_304],
                [4_194_304, 0],
                [1_234_567, -7_654_321],
            ],
            dtype=np.int32,
        )
        packed = pcm.reshape(-1).astype(np.int64) & 0xFFFFFF
        raw = np.column_stack(
            [
                packed & 0xFF,
                (packed >> 8) & 0xFF,
                (packed >> 16) & 0xFF,
            ]
        ).astype(np.uint8)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pcm24.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(3)
                wav.setframerate(24_000)
                wav.writeframes(raw.tobytes())
            series = wav_signal_series(path, channel="mono", chunk_frames=2)

        expected = (pcm.astype(float) / 8_388_608.0).mean(axis=1)
        self.assertTrue(np.array_equal(series.values, expected))

    def test_wav_signal_series_honors_preexisting_cancel_before_output_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "canceled.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8_000)
                wav.writeframes(np.arange(32, dtype="<i2").tobytes())
            with (
                patch.object(analysis.np, "empty", side_effect=AssertionError("must not allocate")),
                self.assertRaisesRegex(InterruptedError, "canceled"),
            ):
                wav_signal_series(path, cancel_requested=lambda: True)

    def test_wav_loading_checks_cancellation_between_chunks(self) -> None:
        pcm = np.arange(200, dtype="<i2")
        checks = 0

        def cancel_requested() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "long.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(pcm.tobytes())
            with self.assertRaisesRegex(InterruptedError, "canceled"):
                wav_signal_series(
                    path,
                    cancel_requested=cancel_requested,
                    chunk_frames=16,
                )

    def test_analysis_workload_estimate_guards_large_fft_and_stft(self) -> None:
        series = SignalSeries(
            "large",
            np.arange(10_000, dtype=float),
            np.zeros(10_000, dtype=float),
            1000.0,
        )
        fft_config = AnalysisConfig(method="fft")
        stft_config = AnalysisConfig(method="stft", stft_window_size=256, stft_overlap_ratio=0.75)

        self.assertGreater(estimate_analysis_working_set_bytes(10_000, stft_config), 0)
        self.assertGreater(
            estimate_analysis_working_set_bytes(10_000, stft_config),
            estimate_analysis_working_set_bytes(10_000, fft_config),
        )
        with self.assertRaisesRegex(ValueError, "safety limit"):
            validate_analysis_workload(series, fft_config, max_working_set_bytes=1024)
        with self.assertRaisesRegex(ValueError, "reduce STFT overlap"):
            validate_analysis_workload(series, stft_config, max_working_set_bytes=1024)

    def test_tracking_series_reads_velocity_debug_values(self) -> None:
        results = [
            TrackerResult(
                frame_index=i,
                time_s=i / 10.0,
                state={"x_px": float(i)},
                filtered_state={"x_px": float(i)},
                confidence=1.0,
                status="ok",
                debug={"filter": {"velocity": {"v_x_px": 10.0}}},
            )
            for i in range(4)
        ]
        series = tracking_series(results, "v_x_px")
        self.assertTrue(np.allclose(series.values, 10.0))

    def test_tracking_series_uses_discovered_sample_rate_without_reinferring(self) -> None:
        results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 120.0,
                state={"x": float(index)},
                filtered_state={"x": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(8)
        ]

        with patch.object(analysis, "_infer_sample_rate", side_effect=AssertionError("must reuse rate")):
            series = tracking_series(results, "x", sample_rate_hz=120.0)

        self.assertEqual(series.sample_rate_hz, 120.0)

    def test_tracking_series_honors_cancellation_during_snapshot_preparation(self) -> None:
        results = [
            TrackerResult(
                frame_index=index,
                time_s=index / 120.0,
                state={"x": float(index)},
                filtered_state={"x": float(index)},
                confidence=1.0,
                status="ok",
            )
            for index in range(8)
        ]
        checks = 0

        def cancel_after_time_axis() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 2

        with self.assertRaisesRegex(InterruptedError, "preparation was canceled"):
            tracking_series(
                results,
                "x",
                sample_rate_hz=120.0,
                cancel_requested=cancel_after_time_axis,
            )

        self.assertEqual(checks, 2)

    def test_tracking_series_info_matches_materialized_series_with_sparse_nonfinite_values(self) -> None:
        results = [
            TrackerResult(
                frame_index=0,
                time_s=0.0,
                state={"x": 1.0},
                filtered_state={"x": 1.0, "shared": float("nan")},
                confidence=1.0,
                status="ok",
                debug={"filter": {"velocity": {"v_x": 4.0, "shared": 9.0}}},
            ),
            TrackerResult(
                frame_index=1,
                time_s=0.5,
                state={"x": 2.0},
                filtered_state={"x": 2.0},
                confidence=1.0,
                status="ok",
                debug={"filter": {"velocity": {"v_x": float("nan"), "omega": 0.5}}},
            ),
            TrackerResult(
                frame_index=2,
                time_s=float("nan"),
                state={"x": 3.0},
                filtered_state={"x": 3.0, "late": 7.0},
                confidence=1.0,
                status="ok",
                debug={"filter": {"velocity": {"v_x": 8.0}}},
            ),
        ]

        info = available_tracking_series_info(results)

        self.assertEqual([item.key for item in info], ["late", "omega", "shared", "v_x", "x"])
        for item in info:
            series = tracking_series(results, item.key)
            self.assertEqual(item.sample_count, series.values.size)
            self.assertEqual(item.sample_rate_hz, series.sample_rate_hz)
        self.assertEqual({item.key: item.sample_count for item in info}, {
            "late": 0,
            "omega": 1,
            "shared": 0,
            "v_x": 1,
            "x": 2,
        })
        self.assertTrue(all(item.result_count == 3 for item in info))

    def test_analysis_results_export_csv_and_npz(self) -> None:
        sample_rate = 64.0
        time_s = np.arange(0, 2.0, 1.0 / sample_rate)
        values = np.sin(2.0 * math.pi * 4.0 * time_s)
        series = SignalSeries(
            "x_world",
            time_s,
            values,
            sample_rate,
            unit="cm",
            source_type="tracking",
            metadata={"key": "x_world"},
        )
        fft = compute_fft(series, AnalysisConfig(window="boxcar"))
        stft = compute_stft(series, AnalysisConfig(method="stft", stft_window_size=32, stft_overlap_ratio=0.5))
        self.assertEqual(fft.metadata["unit"], "cm")
        self.assertEqual(stft.metadata["source_type"], "tracking")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            fft_csv = tmpdir_path / "fft.csv"
            fft_npz = tmpdir_path / "fft.npz"
            stft_csv = tmpdir_path / "stft.csv"
            stft_npz = tmpdir_path / "stft.npz"
            write_fft_csv(fft_csv, fft)
            write_fft_npz(fft_npz, fft)
            write_stft_csv(stft_csv, stft)
            write_stft_npz(stft_npz, stft)
            fft_text = fft_csv.read_text(encoding="utf-8")
            stft_text = stft_csv.read_text(encoding="utf-8")
            self.assertIn("series,unit,frequency_hz", fft_text)
            self.assertIn("x_world,cm", fft_text)
            self.assertIn("series,unit,time_s", stft_text)
            self.assertIn("x_world,cm", stft_text)
            self.assertTrue(fft_npz.exists())
            self.assertTrue(stft_npz.exists())
            with np.load(fft_npz, allow_pickle=False) as loaded:
                metadata = json.loads(str(loaded["metadata_json"].item()))
                self.assertEqual(int(loaded["format_version"]), 2)
                self.assertEqual(metadata["unit"], "cm")
                self.assertEqual(metadata["source_metadata"]["key"], "x_world")
            with np.load(stft_npz, allow_pickle=False) as loaded:
                metadata = json.loads(str(loaded["metadata_json"].item()))
                self.assertEqual(metadata["series"], "x_world")

    def test_analysis_csv_exports_neutralize_spreadsheet_formula_text(self) -> None:
        sample_rate = 32.0
        time_s = np.arange(0, 1.0, 1.0 / sample_rate)
        values = np.sin(2.0 * math.pi * 4.0 * time_s)
        series = SignalSeries(
            " =HYPERLINK(\"https://invalid.example\")",
            time_s,
            values,
            sample_rate,
            unit="+SUM(1,1)",
        )
        fft = compute_fft(series)
        stft = compute_stft(
            series,
            AnalysisConfig(method="stft", stft_window_size=16, stft_overlap_ratio=0.5),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            fft_path = Path(tmpdir) / "fft.csv"
            stft_path = Path(tmpdir) / "stft.csv"
            write_fft_csv(fft_path, fft)
            write_stft_csv(stft_path, stft)
            with fft_path.open(newline="", encoding="utf-8") as handle:
                fft_row = next(csv.DictReader(handle))
            with stft_path.open(newline="", encoding="utf-8") as handle:
                stft_row = next(csv.DictReader(handle))

        for row in (fft_row, stft_row):
            self.assertTrue(row["series"].startswith("' =HYPERLINK"))
            self.assertTrue(row["unit"].startswith("'+SUM"))

    def test_npz_write_failure_preserves_existing_export(self) -> None:
        series = SignalSeries(
            "x",
            np.arange(8, dtype=float),
            np.arange(8, dtype=float),
            1.0,
        )
        result = compute_fft(series)

        def partial_then_fail(handle: object, **_arrays: object) -> None:
            handle.write(b"partial archive")  # type: ignore[attr-defined]
            raise OSError("disk full")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "analysis.npz"
            sentinel = b"previous valid archive"
            path.write_bytes(sentinel)
            with (
                patch.object(analysis.np, "savez", side_effect=partial_then_fail),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                write_fft_npz(path, result)
            self.assertEqual(path.read_bytes(), sentinel)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.npz")), [])

    def test_npz_export_atomic_for_all_suffixes(self) -> None:
        series = SignalSeries(
            "x",
            np.arange(8, dtype=float),
            np.arange(8, dtype=float),
            1.0,
        )
        result = compute_fft(series)
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("analysis.npz", "export", "archive.dat"):
                with self.subTest(target=name):
                    path = Path(tmpdir) / name
                    write_fft_npz(path, result)
                    self.assertTrue(path.exists())
                    self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])
                    with np.load(path, allow_pickle=False) as loaded:
                        self.assertEqual(int(loaded["format_version"]), 2)
                        self.assertTrue(
                            np.array_equal(loaded["frequency_hz"], result.frequency_hz)
                        )

    def test_npz_write_failure_cleans_sidecars_for_all_suffixes(self) -> None:
        series = SignalSeries(
            "x",
            np.arange(8, dtype=float),
            np.arange(8, dtype=float),
            1.0,
        )
        result = compute_fft(series)

        def partial_then_fail(handle: object, **_arrays: object) -> None:
            handle.write(b"partial archive")  # type: ignore[attr-defined]
            raise OSError("disk full")

        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ("analysis.npz", "export", "archive.dat"):
                with self.subTest(target=name):
                    path = Path(tmpdir) / name
                    sentinel = b"previous valid archive"
                    path.write_bytes(sentinel)
                    with (
                        patch.object(analysis.np, "savez", side_effect=partial_then_fail),
                        self.assertRaisesRegex(OSError, "disk full"),
                    ):
                        write_fft_npz(path, result)
                    self.assertEqual(path.read_bytes(), sentinel)
                    self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])

    def test_wav_signal_series_rejects_extreme_channel_metadata(self) -> None:
        def raw_wav(channels: int, sample_width: int, frame_count: int = 0) -> bytes:
            data_size = frame_count * channels * sample_width
            block_align = channels * sample_width
            return (
                b"RIFF"
                + ((36 + data_size) % (2**32)).to_bytes(4, "little")
                + b"WAVE"
                + b"fmt "
                + (16).to_bytes(4, "little")
                + (1).to_bytes(2, "little")
                + (channels % (2**16)).to_bytes(2, "little")
                + (44100 % (2**32)).to_bytes(4, "little")
                + ((44100 * block_align) % (2**32)).to_bytes(4, "little")
                + (block_align % (2**16)).to_bytes(2, "little")
                + ((sample_width * 8) % (2**16)).to_bytes(2, "little")
                + b"data"
                + (data_size % (2**32)).to_bytes(4, "little")
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            for index, header in enumerate(
                (
                    raw_wav(channels=65535, sample_width=2),
                    raw_wav(channels=2, sample_width=8),
                    raw_wav(channels=2, sample_width=2, frame_count=2**29),
                )
            ):
                with self.subTest(header_index=index):
                    path = Path(tmpdir) / f"extreme-{index}.wav"
                    path.write_bytes(header)
                    with self.assertRaisesRegex(ValueError, "WAV"):
                        wav_signal_series(path)

    def test_wav_signal_series_chunk_scales_with_channel_count(self) -> None:
        class RecordingWave:
            def __init__(self) -> None:
                self.max_requested = 0

            def getnchannels(self) -> int:
                return 16

            def getsampwidth(self) -> int:
                return 2

            def getframerate(self) -> int:
                return 44100

            def getnframes(self) -> int:
                return 600_000

            def readframes(self, count: int) -> bytes:
                self.max_requested = max(self.max_requested, int(count))
                return b"\x00" * (int(count) * 16 * 2)

            def close(self) -> None:
                pass

            def __enter__(self) -> "RecordingWave":
                return self

            def __exit__(self, *_exc: object) -> bool:
                self.close()
                return False

        fake = RecordingWave()
        with patch("neo_tracker.analysis.wave.open", return_value=fake):
            series = wav_signal_series("fake.wav", "mono", chunk_frames=1_048_576)

        self.assertEqual(series.values.size, 600_000)
        self.assertLessEqual(fake.max_requested, (16 * 1024 * 1024) // (16 * 2))


if __name__ == "__main__":
    unittest.main()
