from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
from time import perf_counter

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker import media


def _median_ms(operation, rounds: int) -> float:
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000.0)
    return median(samples)


def _prepare_sampled_file(path: Path, size_bytes: int) -> None:
    chunk_bytes = media.MEDIA_IDENTITY_CHUNK_BYTES
    positions = (0, (size_bytes - chunk_bytes) // 2, size_bytes - chunk_bytes)
    with path.open("wb") as source:
        source.truncate(size_bytes)
    with path.open("r+b") as source:
        for index, position in enumerate(positions, start=1):
            source.seek(position)
            source.write(bytes([index]) * chunk_bytes)


def benchmark(source: Path, rounds: int, virtual_gib: float) -> dict[str, float | int | str]:
    if not source.exists():
        raise FileNotFoundError(source)

    identity = media.probe_media_identity(source)
    if identity is None:
        raise RuntimeError(f"Could not calculate source identity: {source}")
    source_identity_ms = _median_ms(lambda: media.probe_media_identity(source), rounds)

    original_identity_probe = media.probe_media_identity
    try:
        media.probe_media_identity = lambda _path: None  # type: ignore[assignment]
        probe_without_identity_ms = _median_ms(lambda: media.probe_media(str(source)), rounds)
    finally:
        media.probe_media_identity = original_identity_probe  # type: ignore[assignment]
    probe_with_identity_ms = _median_ms(lambda: media.probe_media(str(source)), rounds)

    large_size = max(
        media.MEDIA_IDENTITY_FULL_LIMIT_BYTES + 1,
        int(float(virtual_gib) * 1024**3),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        large_path = Path(tmpdir) / "sampled-large-media.bin"
        _prepare_sampled_file(large_path, large_size)
        large_identity = media.probe_media_identity(large_path)
        large_identity_ms = _median_ms(lambda: media.probe_media_identity(large_path), rounds)
    if large_identity is None:
        raise RuntimeError("Could not calculate sampled large-file identity")

    return {
        "source": str(source),
        "source_size_bytes": source.stat().st_size,
        "source_identity_strategy": identity.strategy,
        "source_identity_ms": source_identity_ms,
        "probe_without_identity_ms": probe_without_identity_ms,
        "probe_with_identity_ms": probe_with_identity_ms,
        "probe_identity_overhead_ms": probe_with_identity_ms - probe_without_identity_ms,
        "virtual_large_size_bytes": large_size,
        "virtual_large_strategy": large_identity.strategy,
        "virtual_large_sampled_bytes": large_identity.sampled_bytes,
        "virtual_large_identity_ms": large_identity_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark bounded media source identity work.")
    parser.add_argument(
        "--source",
        type=Path,
        default=WORKSPACE_ROOT / "artifacts/experiment-videos/red-dot-tracking.mp4",
    )
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--virtual-gib", type=float, default=4.0)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.source, args.rounds, args.virtual_gib), indent=2))


if __name__ == "__main__":
    main()
