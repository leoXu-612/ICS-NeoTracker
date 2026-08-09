from __future__ import annotations

from collections.abc import Sequence
import json
from math import isfinite
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from neo_tracker.atomic_io import atomic_output_path
from neo_tracker.core import TrackerResult
from neo_tracker.csv_utils import write_dict_csv
from neo_tracker.media import MAX_WAV_DECODE_BLOCK_BYTES, MAX_WAV_DECODE_BYTES, validate_wav_header


_SCIPY_SIGNAL: Any | None = None
_SCIPY_SIGNAL_IMPORT_ERROR: Exception | None = None
_MAX_ANALYSIS_WORKING_SET_BYTES = 512 * 1024 * 1024
_MAX_ANALYSIS_RATE_HZ = 1_000_000.0
_MAX_STFT_WINDOW_SIZE = 1_000_000


@dataclass(frozen=True)
class SignalSeries:
    name: str
    time_s: np.ndarray
    values: np.ndarray
    sample_rate_hz: float
    unit: str = ""
    source_type: str = "tracking"
    metadata: dict[str, Any] = field(default_factory=dict)

    def cleaned(self) -> "SignalSeries":
        time_s = np.asarray(self.time_s, dtype=float)
        values = np.asarray(self.values, dtype=float)
        mask = np.isfinite(time_s) & np.isfinite(values)
        if bool(np.all(mask)):
            clean_time_s = time_s
            clean_values = values
        else:
            clean_time_s = time_s[mask]
            clean_values = values[mask]
        return SignalSeries(
            name=self.name,
            time_s=clean_time_s,
            values=clean_values,
            sample_rate_hz=float(self.sample_rate_hz),
            unit=self.unit,
            source_type=self.source_type,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class TrackingSeriesInfo:
    key: str
    sample_rate_hz: float
    sample_count: int
    result_count: int


@dataclass(frozen=True)
class AnalysisConfig:
    method: str = "fft"
    detrend: str = "mean"
    window: str = "hann"
    sample_rate_hz: float | None = None
    frequency_min_hz: float | None = None
    frequency_max_hz: float | None = None
    stft_window_size: int = 256
    stft_overlap_ratio: float = 0.75


@dataclass(frozen=True)
class FFTResult:
    frequency_hz: np.ndarray
    amplitude: np.ndarray
    power: np.ndarray
    peak_frequency_hz: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class STFTResult:
    time_s: np.ndarray
    frequency_hz: np.ndarray
    amplitude: np.ndarray
    power: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


def tracking_series(
    results: Iterable[TrackerResult],
    key: str,
    unit: str = "",
    *,
    sample_rate_hz: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> SignalSeries:
    result_items = results if isinstance(results, Sequence) else list(results)
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("Tracking signal preparation was canceled")
    time_s = np.asarray([result.time_s for result in result_items], dtype=float)
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("Tracking signal preparation was canceled")
    values = np.asarray([_result_series_value(result, key) for result in result_items], dtype=float)
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("Tracking signal preparation was canceled")
    resolved_sample_rate_hz = (
        float(sample_rate_hz)
        if sample_rate_hz is not None and sample_rate_hz > 0.0
        else _infer_sample_rate(time_s)
    )
    return SignalSeries(
        name=key,
        time_s=time_s,
        values=values,
        sample_rate_hz=resolved_sample_rate_hz,
        unit=unit,
        source_type="tracking",
        metadata={"key": key},
    ).cleaned()


def available_tracking_series_info(
    results: Iterable[TrackerResult],
    *,
    cooperate: Callable[[], None] | None = None,
) -> list[TrackingSeriesInfo]:
    result_items = results if isinstance(results, list) else list(results)
    time_s = np.empty(len(result_items), dtype=float)
    sample_counts: dict[str, int] = {}
    for index, result in enumerate(result_items):
        time_value = float(result.time_s)
        time_s[index] = time_value
        time_is_finite = isfinite(time_value)
        state = result.filtered_state
        velocity = result.debug.get("filter", {}).get("velocity", {})
        for key, value in state.items():
            if key not in sample_counts:
                sample_counts[key] = 0
            if time_is_finite and isfinite(float(value)):
                sample_counts[key] += 1
        for key, value in velocity.items():
            if key not in sample_counts:
                sample_counts[key] = 0
            if key not in state and time_is_finite and isfinite(float(value)):
                sample_counts[key] += 1
        if cooperate is not None and ((index + 1) % 256 == 0 or index + 1 == len(result_items)):
            cooperate()
    sample_rate_hz = _infer_sample_rate(time_s)
    return [
        TrackingSeriesInfo(
            key=key,
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_counts[key],
            result_count=len(result_items),
        )
        for key in sorted(sample_counts)
    ]


def available_tracking_series(results: Iterable[TrackerResult]) -> list[str]:
    keys: set[str] = set()
    for result in results:
        keys.update(result.filtered_state.keys())
        velocity = result.debug.get("filter", {}).get("velocity", {})
        keys.update(velocity.keys())
        if "omega" in velocity:
            keys.add("omega")
    return sorted(keys)


def _result_series_value(result: TrackerResult, key: str) -> float:
    if key in result.filtered_state:
        return float(result.filtered_state[key])
    velocity = result.debug.get("filter", {}).get("velocity", {})
    if key in velocity:
        return float(velocity[key])
    if key == "omega" and "omega" in velocity:
        return float(velocity["omega"])
    return float("nan")


def wav_signal_series(
    path: str | Path,
    channel: int | str = "mono",
    *,
    cancel_requested: Callable[[], bool] | None = None,
    chunk_frames: int = 1_048_576,
) -> SignalSeries:
    path = Path(path)
    with wave.open(str(path), "rb") as wav:
        channels = int(wav.getnchannels())
        sample_width = int(wav.getsampwidth())
        sample_rate = float(wav.getframerate())
        frame_count = int(wav.getnframes())
        validation_error = validate_wav_header(channels, sample_width, sample_rate, frame_count)
        if validation_error is not None:
            raise ValueError(validation_error)
        if channel == "mono":
            channel_index: int | None = None
            channel_label = "mono"
        else:
            channel_index = int(channel)
            if channel_index < 0 or channel_index >= channels:
                raise ValueError(f"channel index must be in [0, {channels - 1}]")
            channel_label = str(channel_index)

        if cancel_requested is not None and cancel_requested():
            raise InterruptedError("WAV loading was canceled")
        values = np.empty(frame_count, dtype=float)
        written = 0
        remaining = frame_count
        decode_bytes_per_frame = channels * sample_width
        max_chunk_frames = max(1, MAX_WAV_DECODE_BLOCK_BYTES // decode_bytes_per_frame)
        chunk_size = max(1, min(int(chunk_frames), max_chunk_frames))
        while remaining > 0:
            if cancel_requested is not None and cancel_requested():
                raise InterruptedError("WAV loading was canceled")
            block = wav.readframes(min(chunk_size, remaining))
            if not block:
                break
            samples = _decode_pcm(block, sample_width)
            complete_samples = samples.size - (samples.size % channels)
            if complete_samples <= 0:
                break
            frames = samples[:complete_samples].reshape((-1, channels))
            decoded_frames = min(int(frames.shape[0]), remaining)
            if decoded_frames <= 0:
                break
            target = values[written : written + decoded_frames]
            if channel_index is None:
                np.mean(frames[:decoded_frames], axis=1, out=target)
            else:
                target[:] = frames[:decoded_frames, channel_index]
            written += decoded_frames
            remaining -= decoded_frames
    if cancel_requested is not None and cancel_requested():
        raise InterruptedError("WAV loading was canceled")
    if written != values.size:
        values = values[:written].copy()
    time_s = np.arange(values.size, dtype=float) / sample_rate
    return SignalSeries(
        name=f"{path.stem}:{channel_label}",
        time_s=time_s,
        values=values,
        sample_rate_hz=sample_rate,
        unit="amplitude",
        source_type="audio",
        metadata={"path": str(path), "channels": channels, "channel": channel_label},
    )


def compute_fft(series: SignalSeries, config: AnalysisConfig | None = None) -> FFTResult:
    config = config or AnalysisConfig(method="fft")
    validate_analysis_config(config, method="fft")
    validate_analysis_workload(series, config, method="fft")
    clean = series.cleaned()
    values = _preprocess_values(clean.values, clean.time_s, config)
    if values.size < 2:
        raise ValueError("FFT requires at least two finite samples")
    sample_rate = _resolve_sample_rate(clean, config)
    windowed = values * _window_values(config.window, values.size)
    frequency = np.fft.rfftfreq(values.size, d=1.0 / sample_rate)
    spectrum = np.fft.rfft(windowed)
    amplitude = np.abs(spectrum) / values.size
    if amplitude.size > 2:
        amplitude[1:-1] *= 2.0
    power = amplitude**2
    frequency, amplitude, power = _filter_frequency_range(frequency, amplitude, power, config)
    if frequency.size == 0:
        raise ValueError("frequency range contains no FFT bins")
    peak_frequency = _peak_frequency(frequency, amplitude)
    return FFTResult(
        frequency_hz=frequency,
        amplitude=amplitude,
        power=power,
        peak_frequency_hz=peak_frequency,
        metadata=_analysis_metadata(clean, sample_rate, config),
    )


def compute_stft(series: SignalSeries, config: AnalysisConfig | None = None) -> STFTResult:
    config = config or AnalysisConfig(method="stft")
    validate_analysis_config(config, method="stft")
    validate_analysis_workload(series, config, method="stft")
    signal_module = _load_scipy_signal()
    if signal_module is None:
        raise RuntimeError(_scipy_error_message())
    clean = series.cleaned()
    values = _preprocess_values(clean.values, clean.time_s, config)
    if values.size < 2:
        raise ValueError("STFT requires at least two finite samples")
    sample_rate = _resolve_sample_rate(clean, config)
    nperseg = max(2, min(int(config.stft_window_size), values.size))
    noverlap = int(np.clip(round(nperseg * config.stft_overlap_ratio), 0, nperseg - 1))
    frequency, time_s, zxx = signal_module.stft(
        values,
        fs=sample_rate,
        window=config.window,
        nperseg=nperseg,
        noverlap=noverlap,
        detrend=False,
        boundary=None,
        padded=False,
    )
    amplitude = np.abs(zxx)
    power = amplitude**2
    mask = _frequency_mask(frequency, config)
    if not np.any(mask):
        raise ValueError("frequency range contains no STFT bins")
    return STFTResult(
        time_s=time_s,
        frequency_hz=frequency[mask],
        amplitude=amplitude[mask, :],
        power=power[mask, :],
        metadata={
            **_analysis_metadata(clean, sample_rate, config),
            "nperseg": nperseg,
            "noverlap": noverlap,
        },
    )


def validate_analysis_config(config: AnalysisConfig, method: str | None = None) -> None:
    """Reject invalid analysis settings before NumPy/SciPy silently clamp them."""

    resolved_method = (method or config.method).lower()
    if resolved_method not in {"fft", "stft"}:
        raise ValueError(f"unsupported analysis method: {resolved_method}")
    if config.detrend not in {"none", "mean", "linear"}:
        raise ValueError(f"unsupported detrend mode: {config.detrend}")
    if config.window not in {"boxcar", "hann", "hamming", "blackman"}:
        raise ValueError(f"unsupported window: {config.window}")
    if config.sample_rate_hz is not None:
        sample_rate = float(config.sample_rate_hz)
        if not isfinite(sample_rate) or sample_rate <= 0.0:
            raise ValueError("sample rate override must be a positive finite number")
        if sample_rate > _MAX_ANALYSIS_RATE_HZ:
            raise ValueError(
                f"sample rate override must not exceed {_MAX_ANALYSIS_RATE_HZ:,.0f} Hz"
            )

    minimum = config.frequency_min_hz
    maximum = config.frequency_max_hz
    if minimum is not None and (not isfinite(float(minimum)) or float(minimum) < 0.0):
        raise ValueError("minimum frequency must be a finite non-negative number")
    if maximum is not None and (not isfinite(float(maximum)) or float(maximum) < 0.0):
        raise ValueError("maximum frequency must be a finite non-negative number")
    if minimum is not None and float(minimum) > _MAX_ANALYSIS_RATE_HZ:
        raise ValueError(f"minimum frequency must not exceed {_MAX_ANALYSIS_RATE_HZ:,.0f} Hz")
    if maximum is not None and float(maximum) > _MAX_ANALYSIS_RATE_HZ:
        raise ValueError(f"maximum frequency must not exceed {_MAX_ANALYSIS_RATE_HZ:,.0f} Hz")
    if minimum is not None and maximum is not None and float(minimum) > float(maximum):
        raise ValueError("minimum frequency must not exceed maximum frequency")

    if resolved_method == "stft":
        if int(config.stft_window_size) < 2:
            raise ValueError("STFT window size must be at least 2 samples")
        if int(config.stft_window_size) > _MAX_STFT_WINDOW_SIZE:
            raise ValueError(
                f"STFT window size must not exceed {_MAX_STFT_WINDOW_SIZE:,} samples"
            )
        overlap = float(config.stft_overlap_ratio)
        if not isfinite(overlap) or overlap < 0.0 or overlap > 0.99:
            raise ValueError("STFT overlap ratio must be in [0, 0.99]")


def estimate_analysis_working_set_bytes(
    sample_count: int,
    config: AnalysisConfig,
    *,
    method: str | None = None,
) -> int:
    """Conservatively estimate peak numeric working memory for one analysis run."""

    count = max(0, int(sample_count))
    input_workspace = count * 48
    resolved_method = (method or config.method).lower()
    if resolved_method == "fft":
        return input_workspace + count * 16
    if count < 2:
        return input_workspace
    nperseg = max(2, min(int(config.stft_window_size), count))
    noverlap = int(np.clip(round(nperseg * float(config.stft_overlap_ratio)), 0, nperseg - 1))
    step = max(1, nperseg - noverlap)
    time_bins = 1 if count <= nperseg else 1 + (count - nperseg) // step
    frequency_bins = nperseg // 2 + 1
    spectrum_cells = frequency_bins * time_bins
    return input_workspace + spectrum_cells * 32


def validate_analysis_workload(
    series: SignalSeries,
    config: AnalysisConfig,
    *,
    max_working_set_bytes: int = _MAX_ANALYSIS_WORKING_SET_BYTES,
    method: str | None = None,
) -> None:
    sample_count = max(int(np.asarray(series.values).size), int(np.asarray(series.time_s).size))
    validate_analysis_sample_count(
        sample_count,
        config,
        max_working_set_bytes=max_working_set_bytes,
        method=method,
    )


def validate_analysis_sample_count(
    sample_count: int,
    config: AnalysisConfig,
    *,
    max_working_set_bytes: int = _MAX_ANALYSIS_WORKING_SET_BYTES,
    method: str | None = None,
) -> None:
    resolved_method = (method or config.method).lower()
    estimate = estimate_analysis_working_set_bytes(sample_count, config, method=resolved_method)
    limit = max(1, int(max_working_set_bytes))
    if estimate <= limit:
        return
    estimate_mib = estimate / (1024 * 1024)
    limit_mib = limit / (1024 * 1024)
    method_label = resolved_method.upper()
    advice = "analyze a shorter/downsampled signal"
    if resolved_method == "stft":
        advice += " or reduce STFT overlap"
    raise ValueError(
        f"{method_label} workload needs about {estimate_mib:,.0f} MiB, above the {limit_mib:,.0f} MiB safety limit; "
        + advice
    )


def validate_wav_decode_workload(
    decoded_source_bytes: int,
    *,
    max_bytes: int = MAX_WAV_DECODE_BYTES,
) -> None:
    """Reject a WAV source whose raw decode cost exceeds the bounded limit."""

    decoded = max(0, int(decoded_source_bytes))
    if decoded > max_bytes:
        raise ValueError(
            f"WAV decode workload {decoded:,} bytes exceeds the "
            f"limit of {max_bytes:,} bytes"
        )


def fft_to_rows(result: FFTResult) -> list[dict[str, float | str]]:
    series = str(result.metadata.get("series", ""))
    unit = str(result.metadata.get("unit", ""))
    return [
        {"series": series, "unit": unit, "frequency_hz": float(f), "amplitude": float(a), "power": float(p)}
        for f, a, p in zip(result.frequency_hz, result.amplitude, result.power)
    ]


def stft_to_rows(result: STFTResult) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    series = str(result.metadata.get("series", ""))
    unit = str(result.metadata.get("unit", ""))
    for time_index, time_s in enumerate(result.time_s):
        for freq_index, frequency in enumerate(result.frequency_hz):
            rows.append(
                {
                    "series": series,
                    "unit": unit,
                    "time_s": float(time_s),
                    "frequency_hz": float(frequency),
                    "amplitude": float(result.amplitude[freq_index, time_index]),
                    "power": float(result.power[freq_index, time_index]),
                }
            )
    return rows


def write_fft_csv(path: str | Path, result: FFTResult) -> None:
    _write_rows(path, fft_to_rows(result), ["series", "unit", "frequency_hz", "amplitude", "power"])


def write_stft_csv(path: str | Path, result: STFTResult) -> None:
    _write_rows(path, stft_to_rows(result), ["series", "unit", "time_s", "frequency_hz", "amplitude", "power"])


def write_fft_npz(path: str | Path, result: FFTResult) -> None:
    with atomic_output_path(path) as temporary_path:
        with temporary_path.open("wb") as handle:
            np.savez(
                handle,
                frequency_hz=result.frequency_hz,
                amplitude=result.amplitude,
                power=result.power,
                peak_frequency_hz=result.peak_frequency_hz,
                metadata_json=np.asarray(_metadata_json(result.metadata)),
                format_version=np.asarray(2, dtype=np.int64),
            )


def write_stft_npz(path: str | Path, result: STFTResult) -> None:
    with atomic_output_path(path) as temporary_path:
        with temporary_path.open("wb") as handle:
            np.savez(
                handle,
                time_s=result.time_s,
                frequency_hz=result.frequency_hz,
                amplitude=result.amplitude,
                power=result.power,
                metadata_json=np.asarray(_metadata_json(result.metadata)),
                format_version=np.asarray(2, dtype=np.int64),
            )


def _metadata_json(metadata: dict[str, Any]) -> str:
    """Serialize NPZ metadata without object arrays or pickle payloads."""

    return json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.item() if isinstance(value, np.generic) else str(value),
    )


def _decode_pcm(raw: bytes | bytearray | memoryview, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        return (np.frombuffer(raw, dtype=np.uint8).astype(float) - 128.0) / 128.0
    if sample_width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(float) / 32768.0
    if sample_width == 3:
        data = np.frombuffer(raw, dtype=np.uint8).reshape((-1, 3))
        signed = data[:, 0].astype(np.int32) | (data[:, 1].astype(np.int32) << 8) | (data[:, 2].astype(np.int32) << 16)
        signed = np.where(signed & 0x800000, signed - 0x1000000, signed)
        return signed.astype(float) / 8388608.0
    if sample_width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(float) / 2147483648.0
    raise ValueError(f"unsupported PCM sample width: {sample_width}")


def _preprocess_values(values: np.ndarray, time_s: np.ndarray, config: AnalysisConfig) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if config.detrend == "none":
        return values.copy()
    if config.detrend == "mean":
        return values - float(np.mean(values))
    if config.detrend == "linear":
        if values.size < 2:
            return values - float(np.mean(values))
        fit = np.polyfit(np.asarray(time_s, dtype=float), values, deg=1)
        return values - np.polyval(fit, time_s)
    raise ValueError(f"unsupported detrend mode: {config.detrend}")


def _window_values(name: str, size: int) -> np.ndarray:
    if name == "boxcar":
        return np.ones(size, dtype=float)
    if name == "hann":
        return np.hanning(size)
    if name == "hamming":
        return np.hamming(size)
    if name == "blackman":
        return np.blackman(size)
    raise ValueError(f"unsupported window: {name}")


def _resolve_sample_rate(series: SignalSeries, config: AnalysisConfig) -> float:
    if config.sample_rate_hz is not None and config.sample_rate_hz > 0:
        return float(config.sample_rate_hz)
    if series.sample_rate_hz > 0:
        return float(series.sample_rate_hz)
    inferred = _infer_sample_rate(series.time_s)
    if inferred <= 0:
        raise ValueError("sample rate must be positive or inferable from time_s")
    return inferred


def _infer_sample_rate(time_s: np.ndarray) -> float:
    time_s = np.asarray(time_s, dtype=float)
    if time_s.size < 2:
        return 0.0
    diffs = np.diff(time_s)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return 0.0
    return float(1.0 / np.median(diffs))


def _filter_frequency_range(
    frequency: np.ndarray,
    amplitude: np.ndarray,
    power: np.ndarray,
    config: AnalysisConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mask = _frequency_mask(frequency, config)
    return frequency[mask], amplitude[mask], power[mask]


def _frequency_mask(frequency: np.ndarray, config: AnalysisConfig) -> np.ndarray:
    mask = np.ones(frequency.shape, dtype=bool)
    if config.frequency_min_hz is not None:
        mask &= frequency >= float(config.frequency_min_hz)
    if config.frequency_max_hz is not None:
        mask &= frequency <= float(config.frequency_max_hz)
    return mask


def _peak_frequency(frequency: np.ndarray, amplitude: np.ndarray) -> float:
    if frequency.size == 0 or amplitude.size == 0:
        return 0.0
    candidates = np.arange(frequency.size)
    non_dc = candidates[frequency > 0]
    if non_dc.size > 0:
        index = int(non_dc[np.argmax(amplitude[non_dc])])
    else:
        index = int(np.argmax(amplitude))
    return float(frequency[index])


def _analysis_metadata(series: SignalSeries, sample_rate: float, config: AnalysisConfig) -> dict[str, Any]:
    return {
        "series": series.name,
        "unit": series.unit,
        "source_type": series.source_type,
        "source_metadata": dict(series.metadata),
        "sample_rate_hz": sample_rate,
        "window": config.window,
        "detrend": config.detrend,
        "frequency_min_hz": config.frequency_min_hz,
        "frequency_max_hz": config.frequency_max_hz,
    }


def _write_rows(path: str | Path, rows: list[dict[str, float | str]], fieldnames: list[str]) -> None:
    write_dict_csv(path, rows, fieldnames)


def _load_scipy_signal() -> Any | None:
    global _SCIPY_SIGNAL, _SCIPY_SIGNAL_IMPORT_ERROR
    if _SCIPY_SIGNAL is not None:
        return _SCIPY_SIGNAL
    if _SCIPY_SIGNAL_IMPORT_ERROR is not None:
        return None
    try:
        from scipy import signal
    except Exception as exc:
        _SCIPY_SIGNAL_IMPORT_ERROR = exc
        return None
    _SCIPY_SIGNAL = signal
    return _SCIPY_SIGNAL


def _scipy_error_message() -> str:
    detail = f" ({_SCIPY_SIGNAL_IMPORT_ERROR})" if _SCIPY_SIGNAL_IMPORT_ERROR else ""
    return "STFT requires scipy.signal. Install the science extra or scipy to enable STFT." + detail
