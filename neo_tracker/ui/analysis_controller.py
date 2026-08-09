from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from neo_tracker.analysis import (
    AnalysisConfig,
    FFTResult,
    SignalSeries,
    STFTResult,
    TrackingSeriesInfo,
    available_tracking_series_info,
    compute_fft,
    compute_stft,
    tracking_series,
    wav_signal_series,
    write_fft_csv,
    write_fft_npz,
    write_stft_csv,
    write_stft_npz,
)
from neo_tracker.core import TrackerResult
from neo_tracker.media import MAX_WAV_CHANNELS, MediaInfo


@dataclass(frozen=True)
class AnalysisSource:
    kind: str
    label: str
    key: str = ""
    channel: int | str = "mono"
    unit: str = ""
    sample_rate_hz: float = 0.0
    sample_count: int = 0
    total_sample_count: int = 0
    decoded_source_bytes: int = 0

    @property
    def identity(self) -> tuple[str, str, int | str]:
        return self.kind, self.key, self.channel

    @property
    def available(self) -> bool:
        return self.kind in {"tracking", "audio"}

    def to_data(self) -> dict[str, object]:
        data: dict[str, object] = {
            "type": self.kind,
            "unit": self.unit,
            "sample_rate_hz": float(self.sample_rate_hz),
            "sample_count": int(self.sample_count),
            "total_sample_count": int(self.total_sample_count),
        }
        if self.decoded_source_bytes:
            data["decoded_source_bytes"] = int(self.decoded_source_bytes)
        if self.kind == "tracking":
            data["key"] = self.key
        elif self.kind == "audio":
            data["channel"] = self.channel
        return data

    @classmethod
    def from_data(cls, data: object, label: str = "") -> "AnalysisSource":
        if not isinstance(data, dict):
            return cls(kind="none", label=label or "No signal source available")
        kind = str(data.get("type", "none"))
        sample_count = _safe_int(data.get("sample_count"))
        return cls(
            kind=kind,
            label=label or str(data.get("label", "")),
            key=str(data.get("key", "")),
            channel=data.get("channel", "mono"),  # type: ignore[arg-type]
            unit=str(data.get("unit", "")),
            sample_rate_hz=_safe_float(data.get("sample_rate_hz")),
            sample_count=sample_count,
            total_sample_count=_safe_int(data.get("total_sample_count", sample_count)),
            decoded_source_bytes=_safe_int(data.get("decoded_source_bytes", 0)),
        )

    @property
    def omitted_sample_count(self) -> int:
        return max(0, int(self.total_sample_count) - int(self.sample_count))

    def detail(self) -> str:
        if not self.available:
            return "Run tracking or add an available WAV file to create a signal source."
        if self.kind == "tracking" and self.omitted_sample_count > 0:
            sample_detail = (
                f"{self.sample_count:,} usable samples of {self.total_sample_count:,} results · "
                f"{self.omitted_sample_count:,} omitted"
            )
            source_parts: list[str] = []
            if self.sample_rate_hz > 0.0:
                source_parts.append(f"source rate {self.sample_rate_hz:.6g} Hz")
            source_parts.append(self.unit or "unitless")
            return f"{sample_detail}\n{' · '.join(source_parts)}"
        else:
            parts = [f"{self.sample_count:,} samples"] if self.sample_count > 0 else []
        if self.sample_rate_hz > 0.0:
            parts.append(f"source rate {self.sample_rate_hz:.6g} Hz")
        parts.append(self.unit or "unitless")
        return " · ".join(parts)


@dataclass(frozen=True)
class AnalysisRun:
    kind: str
    source: AnalysisSource
    series: SignalSeries
    config: AnalysisConfig
    result: FFTResult | STFTResult
    summary: str
    owner_token: int


class AnalysisController:
    """UI-independent source discovery, processing state, summaries, and export."""

    def __init__(self) -> None:
        self.current_run: AnalysisRun | None = None
        self._tracking_source_results: list[TrackerResult] | None = None
        self._tracking_source_result_count = -1
        self._tracking_source_first_result: TrackerResult | None = None
        self._tracking_source_last_result: TrackerResult | None = None
        self._tracking_source_info: tuple[TrackingSeriesInfo, ...] = ()

    @property
    def result(self) -> FFTResult | STFTResult | None:
        return self.current_run.result if self.current_run is not None else None

    @property
    def result_kind(self) -> str | None:
        return self.current_run.kind if self.current_run is not None else None

    @property
    def has_result(self) -> bool:
        return self.current_run is not None

    def clear(self) -> None:
        self.current_run = None

    def invalidate_source_cache(self) -> None:
        """Discard tracking metadata after an in-place result edit."""

        self._tracking_source_results = None
        self._tracking_source_result_count = -1
        self._tracking_source_first_result = None
        self._tracking_source_last_result = None
        self._tracking_source_info = ()

    def refresh_result_source_cache(
        self,
        results: list[TrackerResult],
        result_index: int,
        previous: TrackerResult,
        result: TrackerResult,
    ) -> bool:
        """Increment cached source counts for one copy-on-write result edit."""

        index = int(result_index)
        cache_matches = bool(
            self._tracking_source_results is results
            and self._tracking_source_result_count == len(results)
            and 0 <= index < len(results)
            and (index != 0 or self._tracking_source_first_result is previous)
            and (index != len(results) - 1 or self._tracking_source_last_result is previous)
            and float(previous.time_s) == float(result.time_s)
        )
        cached_info = self._tracking_source_info
        self.invalidate_source_cache()
        if not cache_matches:
            return False

        def values_for(item: TrackerResult) -> dict[str, object]:
            values: dict[str, object] = dict(item.filtered_state)
            velocity = item.debug.get("filter", {}).get("velocity", {})
            if isinstance(velocity, dict):
                for key, value in velocity.items():
                    values.setdefault(str(key), value)
            return values

        def contributions(item: TrackerResult, values: dict[str, object]) -> set[str]:
            try:
                if not np.isfinite(float(item.time_s)):
                    return set()
            except (TypeError, ValueError, OverflowError):
                return set()
            contributed: set[str] = set()
            for key, value in values.items():
                try:
                    if np.isfinite(float(value)):
                        contributed.add(str(key))
                except (TypeError, ValueError, OverflowError):
                    continue
            return contributed

        previous_values = values_for(previous)
        current_values = values_for(result)
        if previous_values.keys() != current_values.keys():
            return False
        previous_keys = contributions(previous, previous_values)
        current_keys = contributions(result, current_values)
        info_by_key = {info.key: info for info in cached_info}
        sample_rate_hz = cached_info[0].sample_rate_hz if cached_info else 0.0
        counts = {info.key: int(info.sample_count) for info in cached_info}
        for key in previous_keys - current_keys:
            counts[key] = max(0, counts.get(key, 0) - 1)
        for key in current_keys - previous_keys:
            counts[key] = counts.get(key, 0) + 1
        self._tracking_source_info = tuple(
            TrackingSeriesInfo(
                key=key,
                sample_rate_hz=(
                    info_by_key[key].sample_rate_hz if key in info_by_key else sample_rate_hz
                ),
                sample_count=counts[key],
                result_count=len(results),
            )
            for key in sorted(counts)
        )
        self._tracking_source_results = results
        self._tracking_source_result_count = len(results)
        self._tracking_source_first_result = results[0] if results else None
        self._tracking_source_last_result = results[-1] if results else None
        return True

    def available_sources_cached(
        self,
        results: list[TrackerResult],
        state_units: dict[str, str],
        media_path: str | None,
        media_info: MediaInfo | None,
        *,
        force: bool = False,
    ) -> list[AnalysisSource]:
        """Reuse metadata while a result list remains unchanged.

        Result-producing code replaces, grows, or clears the list, which is detected in
        constant time. In-place edits must call ``invalidate_source_cache``; the UI's
        explicit Refresh sources action also sets ``force`` for a recovery rescan.
        """

        first_result = results[0] if results else None
        last_result = results[-1] if results else None
        cache_matches = (
            not force
            and self._tracking_source_results is results
            and self._tracking_source_result_count == len(results)
            and self._tracking_source_first_result is first_result
            and self._tracking_source_last_result is last_result
        )
        if not cache_matches:
            self._tracking_source_results = results
            self._tracking_source_result_count = len(results)
            self._tracking_source_first_result = first_result
            self._tracking_source_last_result = last_result
            self._tracking_source_info = tuple(available_tracking_series_info(results))
        return self._sources_from_tracking_info(
            self._tracking_source_info,
            state_units,
            media_path,
            media_info,
        )

    @classmethod
    def available_sources(
        cls,
        results: list[TrackerResult],
        state_units: dict[str, str],
        media_path: str | None,
        media_info: MediaInfo | None,
        *,
        cooperate: Callable[[], None] | None = None,
    ) -> list[AnalysisSource]:
        return cls._sources_from_tracking_info(
            available_tracking_series_info(results, cooperate=cooperate),
            state_units,
            media_path,
            media_info,
        )

    @classmethod
    def _sources_from_tracking_info(
        cls,
        tracking_info: Sequence[TrackingSeriesInfo],
        state_units: dict[str, str],
        media_path: str | None,
        media_info: MediaInfo | None,
    ) -> list[AnalysisSource]:
        sources: list[AnalysisSource] = []
        for series_info in tracking_info:
            key = series_info.key
            unit = cls.tracking_unit_for_key(key, state_units)
            unit_suffix = f" ({unit})" if unit else ""
            sources.append(
                AnalysisSource(
                    kind="tracking",
                    label=f"Tracking: {key}{unit_suffix}",
                    key=key,
                    unit=unit,
                    sample_rate_hz=series_info.sample_rate_hz,
                    sample_count=series_info.sample_count,
                    total_sample_count=series_info.result_count,
                )
            )

        is_audio = bool(media_path and media_info and media_info.available and media_info.kind == "audio")
        if is_audio and media_info is not None:
            sample_rate = media_info.sample_rate_hz if media_info.sample_rate_hz > 0.0 else media_info.fps
            sample_count = max(0, int(media_info.frame_count))
            channel_count = max(0, min(int(media_info.channels), MAX_WAV_CHANNELS))
            decoded_source_bytes = (
                sample_count * channel_count * max(0, int(media_info.sample_width_bytes))
            )
            sources.append(
                AnalysisSource(
                    kind="audio",
                    label="Audio WAV: mono",
                    channel="mono",
                    unit="amplitude",
                    sample_rate_hz=sample_rate,
                    sample_count=sample_count,
                    total_sample_count=sample_count,
                    decoded_source_bytes=decoded_source_bytes,
                )
            )
            for channel_index in range(channel_count):
                sources.append(
                    AnalysisSource(
                        kind="audio",
                        label=f"Audio WAV: channel {channel_index}",
                        channel=channel_index,
                        unit="amplitude",
                        sample_rate_hz=sample_rate,
                        sample_count=sample_count,
                        total_sample_count=sample_count,
                        decoded_source_bytes=decoded_source_bytes,
                    )
                )
        return sources or [AnalysisSource(kind="none", label="No signal source available")]

    @staticmethod
    def series_for_source(
        source: AnalysisSource,
        results: Sequence[TrackerResult],
        media_path: str | None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SignalSeries:
        if source.kind == "tracking":
            if not source.key:
                raise ValueError("Tracking signal source has no state key.")
            return tracking_series(
                results,
                source.key,
                unit=source.unit,
                sample_rate_hz=source.sample_rate_hz,
                cancel_requested=cancel_requested,
            )
        if source.kind == "audio":
            if not media_path:
                raise ValueError("No WAV file is selected.")
            return wav_signal_series(
                media_path,
                channel=source.channel,
                cancel_requested=cancel_requested,
            )
        raise ValueError("No signal source is available yet. Run tracking or add a WAV file first.")

    @staticmethod
    def make_config(
        *,
        method: str,
        detrend: str,
        window: str,
        sample_rate_hz: float,
        frequency_min_hz: float,
        frequency_max_hz: float,
        stft_window_size: int,
        stft_overlap_ratio: float,
    ) -> AnalysisConfig:
        return AnalysisConfig(
            method=method.lower(),
            detrend=detrend,
            window=window,
            sample_rate_hz=sample_rate_hz if sample_rate_hz > 0.0 else None,
            frequency_min_hz=frequency_min_hz if frequency_min_hz > 0.0 else None,
            frequency_max_hz=frequency_max_hz if frequency_max_hz > 0.0 else None,
            stft_window_size=int(stft_window_size),
            stft_overlap_ratio=float(stft_overlap_ratio),
        )

    def run(
        self,
        source: AnalysisSource,
        results: list[TrackerResult],
        media_path: str | None,
        config: AnalysisConfig,
        owner_token: int,
    ) -> AnalysisRun:
        self.clear()
        series = self.series_for_source(source, results, media_path)
        run = self.compute_run(source, series, config, owner_token)
        self.current_run = run
        return run

    @classmethod
    def compute_run(
        cls,
        source: AnalysisSource,
        series: SignalSeries,
        config: AnalysisConfig,
        owner_token: int,
    ) -> AnalysisRun:
        method = config.method.lower()
        if method == "fft":
            result: FFTResult | STFTResult = compute_fft(series, config)
            summary = cls.format_fft_summary(series, result)
        elif method == "stft":
            result = compute_stft(series, config)
            summary = cls.format_stft_summary(series, result)
        else:
            raise ValueError(f"Unsupported analysis method: {config.method}")
        return AnalysisRun(
            kind=method,
            source=source,
            series=series,
            config=config,
            result=result,
            summary=summary,
            owner_token=int(owner_token),
        )

    def accept_run(self, run: AnalysisRun) -> None:
        self.current_run = run

    def matches_context(self, source: AnalysisSource, owner_token: int) -> bool:
        return bool(
            self.current_run is not None
            and self.current_run.owner_token == int(owner_token)
            and self.current_run.source.identity == source.identity
        )

    def export_csv(self, path: str | Path) -> None:
        run = self._required_run()
        if run.kind == "fft":
            write_fft_csv(path, run.result)  # type: ignore[arg-type]
        else:
            write_stft_csv(path, run.result)  # type: ignore[arg-type]

    def export_npz(self, path: str | Path) -> None:
        run = self._required_run()
        if run.kind == "fft":
            write_fft_npz(path, run.result)  # type: ignore[arg-type]
        else:
            write_stft_npz(path, run.result)  # type: ignore[arg-type]

    def _required_run(self) -> AnalysisRun:
        if self.current_run is None:
            raise ValueError("Run FFT or STFT before exporting.")
        return self.current_run

    @staticmethod
    def tracking_unit_for_key(key: str, state_units: dict[str, str]) -> str:
        if key in state_units:
            return state_units[key]
        if key == "omega":
            return "rad/s"
        if key.startswith("v_"):
            base_unit = state_units.get(key[2:])
            if base_unit:
                return f"{base_unit}/s"
        return ""

    @staticmethod
    def format_fft_summary(series: SignalSeries, result: FFTResult) -> str:
        duration = float(series.time_s[-1] - series.time_s[0]) if series.time_s.size > 1 else 0.0
        rows = [
            f"Source: {series.name} ({series.source_type})",
            f"Unit: {series.unit or 'unitless'}",
            f"Samples: {series.values.size}",
            f"Duration: {duration:.6g} s",
            f"Sample rate: {series.sample_rate_hz:.6g} Hz",
            "",
            "FFT spectrum",
            f"Bins: {result.frequency_hz.size}",
            f"Peak frequency: {result.peak_frequency_hz:.6g} Hz",
            "",
            "First bins:",
        ]
        for frequency, amplitude in zip(result.frequency_hz[:8], result.amplitude[:8]):
            rows.append(f"  {frequency:.6g} Hz -> {amplitude:.6g}")
        return "\n".join(rows)

    @staticmethod
    def format_stft_summary(series: SignalSeries, result: STFTResult) -> str:
        duration = float(series.time_s[-1] - series.time_s[0]) if series.time_s.size > 1 else 0.0
        rows = [
            f"Source: {series.name} ({series.source_type})",
            f"Unit: {series.unit or 'unitless'}",
            f"Samples: {series.values.size}",
            f"Duration: {duration:.6g} s",
            f"Sample rate: {series.sample_rate_hz:.6g} Hz",
            "",
            "STFT spectrum",
            f"Time bins: {result.time_s.size}",
            f"Frequency bins: {result.frequency_hz.size}",
            f"Amplitude matrix: {result.amplitude.shape[0]} x {result.amplitude.shape[1]}",
        ]
        if result.amplitude.size:
            ridge_indices = np.argmax(result.amplitude, axis=0)
            ridge = result.frequency_hz[ridge_indices]
            rows.extend(["", "Frequency ridge preview:", "  " + ", ".join(f"{value:.4g} Hz" for value in ridge[:12])])
        return "\n".join(rows)


def _safe_float(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if np.isfinite(parsed) else 0.0


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0
