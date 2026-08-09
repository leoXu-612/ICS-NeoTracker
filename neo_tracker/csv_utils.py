from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

from neo_tracker.atomic_io import atomic_text_writer

_SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def spreadsheet_safe_cell(value: object) -> object:
    """Keep exported text literal when a CSV is opened in a spreadsheet."""

    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r\n")
    if candidate.startswith(_SPREADSHEET_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_dict_csv(
    path: str | Path,
    rows: Sequence[Mapping[str, object]],
    fieldnames: Sequence[str],
) -> None:
    with atomic_text_writer(path, newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([spreadsheet_safe_cell(field) for field in fieldnames])
        for row in rows:
            writer.writerow(
                [spreadsheet_safe_cell(row.get(field, "")) for field in fieldnames]
            )
