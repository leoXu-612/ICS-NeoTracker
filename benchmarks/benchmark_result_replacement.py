from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from sys import getsizeof
import sys
from time import perf_counter

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from neo_tracker.ui.review_controller import ReviewController


def _median_ms(operation, rounds: int) -> float:
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000.0)
    return median(samples)


def _median_ms_with_setup(setup, operation, rounds: int) -> float:
    samples: list[float] = []
    for _ in range(max(3, int(rounds))):
        value = setup()
        started = perf_counter()
        operation(value)
        samples.append((perf_counter() - started) * 1000.0)
    return median(samples)


def benchmark(result_count: int, edit_count: int, rounds: int) -> dict[str, float | int]:
    results = [object() for _ in range(max(0, int(result_count)))]
    edits = [
        {"type": "manual_correction", "frame_index": index, "details": {"x": float(index)}}
        for index in range(max(0, int(edit_count)))
    ]

    def backup():
        return list(results), [dict(entry) for entry in edits]

    saved_results, saved_edits = backup()
    rerun_start_frame = max(0, len(edits) // 2)

    def release() -> None:
        result_backup = list(saved_results)
        edit_backup = list(saved_edits)
        result_backup.clear()
        edit_backup.clear()

    def count_affected() -> int:
        return ReviewController.active_manual_edit_count_from_frame(edits, rerun_start_frame)

    def supersede(history: list[dict[str, object]]) -> int:
        return ReviewController.supersede_manual_edits_from_frame(
            history,
            rerun_start_frame,
            superseded_at="2026-07-14T00:00:00Z",
        )

    return {
        "result_count": len(results),
        "edit_count": len(edits),
        "backup_median_ms": _median_ms(backup, rounds),
        "release_median_ms": _median_ms(release, rounds),
        "result_pointer_list_bytes": getsizeof(saved_results),
        "edit_list_bytes": getsizeof(saved_edits),
        "rerun_start_frame": rerun_start_frame,
        "affected_active_edits": count_affected(),
        "rerun_confirmation_scan_median_ms": _median_ms(count_affected, rounds),
        "rerun_commit_scan_median_ms": _median_ms_with_setup(
            lambda: [dict(entry) for entry in edits],
            supersede,
            rounds,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark current-result backup and one-shot rerun edit superseding."
    )
    parser.add_argument("--results", type=int, default=20_000)
    parser.add_argument("--edits", type=int, default=20)
    parser.add_argument("--rounds", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.results, args.edits, args.rounds), indent=2))


if __name__ == "__main__":
    main()
