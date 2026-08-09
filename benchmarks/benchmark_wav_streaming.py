from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
from time import perf_counter
import tracemalloc
import wave

import numpy as np


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.analysis import SignalSeries, _decode_pcm, wav_signal_series


def _previous_wav_signal_series(
    path: Path,
    channel: int | str,
    *,
    chunk_frames: int,
) -> SignalSeries:
    with wave.open(str(path), "rb") as wav:
        channels = int(wav.getnchannels())
        sample_width = int(wav.getsampwidth())
        sample_rate = float(wav.getframerate())
        frame_count = int(wav.getnframes())
        remaining = frame_count
        raw = bytearray()
        chunk_size = max(1, int(chunk_frames))
        while remaining > 0:
            block = wav.readframes(min(chunk_size, remaining))
            if not block:
                break
            raw.extend(block)
            decoded_frames = len(block) // max(1, channels * sample_width)
            if decoded_frames <= 0:
                break
            remaining -= decoded_frames
    samples = _decode_pcm(raw, sample_width)
    if samples.size % channels != 0:
        samples = samples[: samples.size - (samples.size % channels)]
    samples = samples.reshape((-1, channels))
    if channel == "mono":
        values = samples.mean(axis=1)
        channel_label = "mono"
    else:
        channel_index = int(channel)
        values = samples[:, channel_index]
        channel_label = str(channel_index)
    time_s = np.arange(values.size, dtype=float) / sample_rate
    return SignalSeries(
        name=f"{path.stem}:{channel_label}",
        time_s=time_s,
        values=values.astype(float),
        sample_rate_hz=sample_rate,
        unit="amplitude",
        source_type="audio",
        metadata={"path": str(path), "channels": channels, "channel": channel_label},
    )


def _measure(operation, rounds: int) -> tuple[SignalSeries, float, int]:
    warm = operation()
    del warm
    elapsed: list[float] = []
    for _ in range(max(3, int(rounds))):
        gc.collect()
        started = perf_counter()
        result = operation()
        elapsed.append((perf_counter() - started) * 1000.0)
        del result
    gc.collect()
    tracemalloc.start()
    result = operation()
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, median(elapsed), int(peak)


def _write_fixture(path: Path, *, frame_count: int, chunk_frames: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        remaining = int(frame_count)
        offset = 0
        while remaining > 0:
            count = min(int(chunk_frames), remaining)
            sample_index = np.arange(offset, offset + count, dtype=np.int32)
            left = ((sample_index * 97) % 65_536 - 32_768).astype("<i2")
            right = ((sample_index * 193 + 17) % 65_536 - 32_768).astype("<i2")
            wav.writeframesraw(np.column_stack((left, right)).tobytes())
            offset += count
            remaining -= count


def _case(path: Path, *, channel: int | str, chunk_frames: int, rounds: int) -> dict[str, object]:
    previous, previous_ms, previous_peak = _measure(
        lambda: _previous_wav_signal_series(path, channel, chunk_frames=chunk_frames),
        rounds,
    )
    current, current_ms, current_peak = _measure(
        lambda: wav_signal_series(path, channel=channel, chunk_frames=chunk_frames),
        rounds,
    )
    return {
        "channel": channel,
        "previous_median_ms": previous_ms,
        "current_median_ms": current_ms,
        "speedup": previous_ms / current_ms,
        "time_reduction_percent": 100.0 * (previous_ms - current_ms) / previous_ms,
        "previous_python_peak_bytes": previous_peak,
        "current_python_peak_bytes": current_peak,
        "python_peak_reduction_percent": 100.0 * (previous_peak - current_peak) / previous_peak,
        "peak_reduction_ratio": previous_peak / current_peak,
        "parity": {
            "values_exact": bool(np.array_equal(previous.values, current.values)),
            "time_axis_exact": bool(np.array_equal(previous.time_s, current.time_s)),
            "metadata_exact": previous.metadata == current.metadata,
            "current_values_owns_allocation": current.values.base is None,
        },
    }


def benchmark(frame_count: int, chunk_frames: int, rounds: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "streaming-stereo.wav"
        _write_fixture(path, frame_count=frame_count, chunk_frames=chunk_frames)
        return {
            "frame_count": int(frame_count),
            "channels": 2,
            "sample_width_bytes": 2,
            "sample_rate_hz": 48_000,
            "chunk_frames": int(chunk_frames),
            "rounds": max(3, int(rounds)),
            "cases": {
                "mono": _case(path, channel="mono", chunk_frames=chunk_frames, rounds=rounds),
                "channel_1": _case(path, channel=1, chunk_frames=chunk_frames, rounds=rounds),
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark whole-file WAV accumulation against bounded streaming decode."
    )
    parser.add_argument("--frames", type=int, default=2_000_000)
    parser.add_argument("--chunk-frames", type=int, default=65_536)
    parser.add_argument("--rounds", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.frames, args.chunk_frames, args.rounds), indent=2))


if __name__ == "__main__":
    main()
