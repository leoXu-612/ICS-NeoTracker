from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from neo_tracker.atomic_io import atomic_write_text
from neo_tracker.core import TrackerResult, TrackingPipeline
from neo_tracker.csv_utils import write_dict_csv
from neo_tracker.project import TrackingRunRecord, pipeline_config_digest


def results_to_rows(results: Iterable[TrackerResult]) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    for result in results:
        row: dict[str, float | int | str] = {
            "frame_index": result.frame_index,
            "time_s": result.time_s,
            "confidence": result.confidence,
            "status": result.status,
        }
        for key, value in result.state.items():
            row[f"raw_{key}"] = value
        for key, value in result.filtered_state.items():
            row[key] = value
        rows.append(row)
    return rows


def write_csv(
    path: str | Path,
    results: Iterable[TrackerResult],
    state_units: dict[str, str] | None = None,
) -> None:
    rows = results_to_rows(results)
    path = Path(path)
    if not rows:
        atomic_write_text(path, "", encoding="utf-8")
        return
    fieldnames = _ordered_result_columns(rows)
    if state_units is None:
        output_fieldnames = fieldnames
        output_rows = rows
    else:
        output_fieldnames = [_column_label(key, state_units) for key in fieldnames]
        output_rows = [{_column_label(key, state_units): row.get(key, "") for key in fieldnames} for row in rows]
    write_dict_csv(path, output_rows, output_fieldnames)


def result_summary(results: Iterable[TrackerResult]) -> dict[str, Any]:
    result_list = list(results)
    if not result_list:
        return {
            "frames": 0,
            "duration_s": 0.0,
            "average_confidence": 0.0,
            "min_confidence": 0.0,
            "max_confidence": 0.0,
            "status_counts": {},
            "state_ranges": {},
            "filter_shift": {},
            "attention_frames": [],
        }
    confidences = [float(result.confidence) for result in result_list]
    status_counts: dict[str, int] = {}
    state_values: dict[str, list[float]] = {}
    filter_deltas: dict[str, list[float]] = {}
    attention_frames: list[dict[str, Any]] = []
    for result in result_list:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
        for key, value in result.filtered_state.items():
            state_values.setdefault(key, []).append(float(value))
        for key, raw_value in result.state.items():
            if key not in result.filtered_state:
                continue
            try:
                delta = abs(float(result.filtered_state[key]) - float(raw_value))
            except Exception:
                continue
            if np.isfinite(delta):
                filter_deltas.setdefault(key, []).append(float(delta))
        if result.status != "ok" or result.confidence < 0.4:
            attention_frames.append(
                {
                    "frame_index": result.frame_index,
                    "time_s": result.time_s,
                    "confidence": result.confidence,
                    "status": result.status,
                }
            )
    state_ranges = {
        key: {
            "min": float(np.min(values)),
            "max": float(np.max(values)),
        }
        for key, values in state_values.items()
        if values
    }
    filter_shift = {
        key: {
            "mean_abs": float(np.mean(values)),
            "max_abs": float(np.max(values)),
        }
        for key, values in filter_deltas.items()
        if values
    }
    return {
        "frames": len(result_list),
        "duration_s": float(result_list[-1].time_s - result_list[0].time_s) if len(result_list) > 1 else 0.0,
        "average_confidence": float(np.mean(confidences)),
        "min_confidence": float(np.min(confidences)),
        "max_confidence": float(np.max(confidences)),
        "status_counts": status_counts,
        "state_ranges": state_ranges,
        "filter_shift": filter_shift,
        "attention_frames": attention_frames,
    }


def markdown_report(
    title: str,
    pipeline: TrackingPipeline,
    results: Iterable[TrackerResult],
    media_path: str | None = None,
    media_info: Any | None = None,
    roi: dict[str, Any] | None = None,
    calibration_rod: dict[str, Any] | None = None,
    edit_history: list[dict[str, Any]] | None = None,
    run_history: list[TrackingRunRecord] | None = None,
    project_path: str | None = None,
    notes: str = "",
    include_absolute_paths: bool = False,
    trusted_notes_markdown: bool = False,
) -> str:
    result_list = list(results)
    summary = result_summary(result_list)
    rows = [
        f"# {_markdown_text(title)}",
        "",
        "## Experiment",
        "",
        f"- Media: {_markdown_text(_report_path(media_path, include_absolute_paths) or 'not set')}",
        f"- Project: {_markdown_text(_report_path(project_path, include_absolute_paths) or 'not saved')}",
        f"- Pipeline: {_markdown_text(pipeline.name)}",
    ]
    if media_info is not None:
        if getattr(media_info, "kind", "video") == "audio":
            rows.extend(
                [
                    f"- Media type: audio",
                    f"- Sample rate: {getattr(media_info, 'sample_rate_hz', getattr(media_info, 'fps', 0.0)):.6g} Hz",
                    f"- Samples: {getattr(media_info, 'frame_count', 0)}",
                    f"- Channels: {getattr(media_info, 'channels', 0)}",
                    f"- Duration: {getattr(media_info, 'duration_s', 0.0):.6g} s",
                ]
            )
        else:
            rows.extend(
                [
                    f"- Media type: video",
                    f"- FPS: {getattr(media_info, 'fps', 0.0):.6g}",
                    f"- Frames: {getattr(media_info, 'frame_count', 0)}",
                    f"- Resolution: {getattr(media_info, 'width', 0)} x {getattr(media_info, 'height', 0)}",
                    f"- Duration: {getattr(media_info, 'duration_s', 0.0):.6g} s",
                ]
            )
    rows.extend(
        [
            "",
            "## Calibration And ROI",
            "",
            f"- ROI: {_markdown_text(_format_dict_inline(roi or pipeline.roi.to_config()))}",
            f"- Calibration rod: {_markdown_text(_format_dict_inline(calibration_rod)) if calibration_rod else 'not set'}",
            "",
            "## Tracking Summary",
            "",
            f"- Result frames: {summary['frames']}",
            f"- Result duration: {summary['duration_s']:.6g} s",
            f"- Average confidence: {summary['average_confidence']:.6g}",
            f"- Confidence range: {summary['min_confidence']:.6g} - {summary['max_confidence']:.6g}",
            f"- Status counts: {_markdown_text(_format_dict_inline(summary['status_counts']))}",
        ]
    )
    state_ranges = summary["state_ranges"]
    state_units = pipeline.state_model.units()
    if state_units:
        rows.extend(["", "### State Units", ""])
        for key, unit in sorted(state_units.items()):
            rows.append(f"- {_markdown_text(key)}: {_markdown_text(unit)}")
    if state_ranges:
        rows.extend(["", "### State Ranges", ""])
        for key, values in sorted(state_ranges.items()):
            unit = state_units.get(key, "")
            unit_suffix = f" {unit}" if unit else ""
            rows.append(
                f"- {_markdown_text(key)}: {values['min']:.6g} to {values['max']:.6g}"
                + (_markdown_text(unit_suffix) if unit_suffix else "")
            )
    filter_shift = summary["filter_shift"]
    if filter_shift:
        rows.extend(["", "### Filter Shift", ""])
        rows.append("| State key | Unit | Mean abs shift | Max abs shift |")
        rows.append("| --- | --- | ---: | ---: |")
        for key, values in sorted(filter_shift.items()):
            rows.append(
                f"| {_markdown_cell(key)} | {_markdown_cell(state_units.get(key, ''))} | "
                f"{values['mean_abs']:.6g} | {values['max_abs']:.6g} |"
            )
    attention_frames = summary["attention_frames"]
    rows.extend(["", "### Attention Frames", ""])
    if attention_frames:
        rows.append("| Frame | Time (s) | Confidence | Status |")
        rows.append("| ---: | ---: | ---: | --- |")
        for item in attention_frames[:40]:
            rows.append(
                f"| {item['frame_index']} | {item['time_s']:.6g} | {item['confidence']:.6g} | "
                f"{_markdown_cell(item['status'])} |"
            )
        if len(attention_frames) > 40:
            rows.append(f"| ... | ... | ... | {len(attention_frames) - 40} more frames omitted |")
    else:
        rows.append("No low-confidence, lost, or manually marked frames.")
    if result_list:
        rows.extend(["", "### Result Preview", ""])
        preview_rows = results_to_rows(result_list[:8])
        rows.extend(_markdown_table(preview_rows))
    rows.extend(["", "## Tracking Run History", ""])
    run_records = list(run_history or [])
    if run_records:
        report_run_rows = tracking_run_rows(run_records)
        if not include_absolute_paths:
            for row in report_run_rows:
                row["source_path"] = _report_path(str(row.get("source_path", "")), False) or ""
        rows.extend(_tracking_run_table(report_run_rows))
        current_digest = pipeline_config_digest(pipeline.to_config())
        historical_configs: dict[str, dict[str, Any]] = {}
        for record in run_records:
            if record.pipeline_digest != current_digest:
                historical_configs.setdefault(record.pipeline_digest, record.pipeline_config)
        if historical_configs:
            rows.extend(["", "### Historical Pipeline Configurations", ""])
            for digest, config in historical_configs.items():
                rows.extend([f"#### Config `{digest}`", "", *_fenced_code(json.dumps(config, indent=2), "json"), ""])
    else:
        rows.append("No tracking runs recorded.")
    rows.extend(["", "## Edit History", ""])
    history_rows = edit_history_rows(edit_history or [])
    if history_rows:
        rows.extend(_edit_history_table(history_rows))
    else:
        rows.append("No manual edits recorded.")
    rows.extend(["", "## Pipeline JSON", "", *_fenced_code(json.dumps(pipeline.to_config(), indent=2), "json")])
    if notes:
        safe_notes = notes if trusted_notes_markdown else "\n".join(_markdown_text(line) for line in notes.splitlines())
        rows.extend(["", "## Notes", "", safe_notes])
    rows.append("")
    return "\n".join(rows)


def write_markdown_report(
    path: str | Path,
    title: str,
    pipeline: TrackingPipeline,
    results: Iterable[TrackerResult],
    media_path: str | None = None,
    media_info: Any | None = None,
    roi: dict[str, Any] | None = None,
    calibration_rod: dict[str, Any] | None = None,
    edit_history: list[dict[str, Any]] | None = None,
    run_history: list[TrackingRunRecord] | None = None,
    project_path: str | None = None,
    notes: str = "",
    include_absolute_paths: bool = False,
    trusted_notes_markdown: bool = False,
) -> None:
    atomic_write_text(
        path,
        markdown_report(
            title=title,
            pipeline=pipeline,
            results=results,
            media_path=media_path,
            media_info=media_info,
            roi=roi,
            calibration_rod=calibration_rod,
            edit_history=edit_history,
            run_history=run_history,
            project_path=project_path,
            notes=notes,
            include_absolute_paths=include_absolute_paths,
            trusted_notes_markdown=trusted_notes_markdown,
        ),
        encoding="utf-8",
    )


def _format_dict_inline(data: dict[str, Any] | None) -> str:
    if not data:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in data.items())


def edit_history_rows(
    edit_history: Iterable[dict[str, Any]],
    *,
    sequence_numbers: Iterable[int] | None = None,
) -> list[dict[str, str | int]]:
    entries = list(edit_history)
    sequences = list(sequence_numbers) if sequence_numbers is not None else list(range(1, len(entries) + 1))
    if len(sequences) != len(entries):
        raise ValueError("edit history sequence count must match entry count")
    rows: list[dict[str, str | int]] = []
    for index, entry in zip(sequences, entries):
        if not isinstance(index, int) or isinstance(index, bool) or index <= 0:
            raise ValueError("edit history sequence numbers must be positive integers")
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        rows.append(
            {
                "index": index,
                "time": str(entry.get("time", "")),
                "type": str(entry.get("type", "edit")),
                "frame_index": "" if entry.get("frame_index") is None else int(entry["frame_index"]),
                "state": "superseded" if entry.get("superseded_at") else "current",
                "superseded_at": str(entry.get("superseded_at", "")),
                "superseded_by_rerun_start_frame": (
                    ""
                    if entry.get("superseded_by_rerun_start_frame") is None
                    else int(entry["superseded_by_rerun_start_frame"])
                ),
                "details": _format_dict_inline(details),
            }
        )
    return rows


def write_edit_history_csv(
    path: str | Path,
    edit_history: Iterable[dict[str, Any]],
    *,
    sequence_numbers: Iterable[int] | None = None,
) -> None:
    rows = edit_history_rows(edit_history, sequence_numbers=sequence_numbers)
    destination = Path(path)
    if not rows:
        atomic_write_text(destination, "", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    write_dict_csv(destination, rows, fieldnames)


def tracking_run_rows(
    run_history: Iterable[TrackingRunRecord],
    *,
    sequence_numbers: Iterable[int] | None = None,
) -> list[dict[str, str | int]]:
    records = list(run_history)
    sequences = list(sequence_numbers) if sequence_numbers is not None else list(range(1, len(records) + 1))
    if len(sequences) != len(records):
        raise ValueError("tracking run sequence count must match record count")
    rows: list[dict[str, str | int]] = []
    for index, record in zip(sequences, records):
        if not isinstance(index, int) or isinstance(index, bool) or index <= 0:
            raise ValueError("tracking run sequence numbers must be positive integers")
        frame_range = (
            "-"
            if record.end_frame is None
            else f"{record.start_frame}-{record.end_frame}"
        )
        rows.append(
            {
                "index": index,
                "started_at": record.started_at,
                "mode": record.mode,
                "outcome": record.outcome,
                "compute_backend": record.compute_backend,
                "frame_range": frame_range,
                "processed_frames": record.processed_frames,
                "result_count": record.result_count,
                "duration_s": f"{record.duration_s:.3f}",
                "tracking_elapsed_s": (
                    f"{record.tracking_elapsed_s:.3f}" if record.has_performance_metrics else ""
                ),
                "throughput_fps": f"{record.throughput_fps:.3f}" if record.has_performance_metrics else "",
                "input_ms_per_frame": (
                    f"{record.input_ms_per_frame:.3f}" if record.has_performance_metrics else ""
                ),
                "processing_ms_per_frame": (
                    f"{record.processing_ms_per_frame:.3f}" if record.has_performance_metrics else ""
                ),
                "prefetch_frames": record.prefetch_frames,
                "stage_overlap_ms_per_frame": (
                    f"{record.stage_overlap_ms_per_frame:.3f}"
                    if record.has_performance_metrics and record.prefetch_frames > 0
                    else ""
                ),
                "peak_debug_bytes": record.peak_debug_bytes if record.peak_debug_bytes > 0 else "",
                "pipeline_digest": record.pipeline_digest,
                "note": record.note,
                "source_path": record.source_path,
                "source_identity_strategy": (
                    record.source_identity.strategy if record.source_identity is not None else ""
                ),
                "source_identity_sha256": (
                    record.source_identity.sha256 if record.source_identity is not None else ""
                ),
                "source_size_bytes": (
                    record.source_identity.size_bytes if record.source_identity is not None else ""
                ),
            }
        )
    return rows


def write_tracking_run_csv(
    path: str | Path,
    run_history: Iterable[TrackingRunRecord],
    *,
    sequence_numbers: Iterable[int] | None = None,
) -> None:
    rows = tracking_run_rows(run_history, sequence_numbers=sequence_numbers)
    destination = Path(path)
    if not rows:
        atomic_write_text(destination, "", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    write_dict_csv(destination, rows, fieldnames)


def _tracking_run_table(rows: list[dict[str, str | int]]) -> list[str]:
    table = [
        "| # | Started (UTC) | Mode | Outcome | Compute backend | Frames | Processed | Results | Duration (s) | Tracking (s) | FPS | Input (ms/f) | Compute (ms/f) | Prefetch (frames) | Stage overlap (ms/f) | Review cache peak (bytes) | Config | Note | Source path | Identity strategy | Identity SHA-256 | Source size (bytes) |",
        "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | ---: |",
    ]
    for row in rows:
        table.append(
            "| "
            + " | ".join(
                _markdown_cell(row.get(key, ""))
                for key in (
                    "index",
                    "started_at",
                    "mode",
                    "outcome",
                    "compute_backend",
                    "frame_range",
                    "processed_frames",
                    "result_count",
                    "duration_s",
                    "tracking_elapsed_s",
                    "throughput_fps",
                    "input_ms_per_frame",
                    "processing_ms_per_frame",
                    "prefetch_frames",
                    "stage_overlap_ms_per_frame",
                    "peak_debug_bytes",
                    "pipeline_digest",
                    "note",
                    "source_path",
                    "source_identity_strategy",
                    "source_identity_sha256",
                    "source_size_bytes",
                )
            )
            + " |"
        )
    return table


def _markdown_cell(value: object) -> str:
    return _markdown_text(value).replace("|", "\\|")


def _markdown_text(value: object) -> str:
    text = html.escape(str(value).replace("\r", " ").replace("\n", " "), quote=True)
    text = text.replace("\\", "\\\\")
    for character in ("`", "*", "[", "]", "{", "}", "(", ")", "#", "+", "!", ">"):
        text = text.replace(character, "\\" + character)
    return text


def _report_path(value: str | None, include_absolute_paths: bool) -> str | None:
    if not value:
        return value
    return value if include_absolute_paths else Path(value).name


def _fenced_code(content: str, language: str) -> list[str]:
    maximum_run = 0
    current_run = 0
    for character in content:
        if character == "`":
            current_run += 1
            maximum_run = max(maximum_run, current_run)
        else:
            current_run = 0
    fence = "`" * max(3, maximum_run + 1)
    return [f"{fence}{language}", content, fence]


def _edit_history_table(rows: list[dict[str, str | int]]) -> list[str]:
    table = [
        "| # | Time | Frame | Type | State | Superseded by rerun | Details |",
        "| ---: | --- | ---: | --- | --- | ---: | --- |",
    ]
    for row in rows:
        table.append(
            "| "
            + " | ".join(
                _markdown_cell(row.get(key, ""))
                for key in (
                    "index",
                    "time",
                    "frame_index",
                    "type",
                    "state",
                    "superseded_by_rerun_start_frame",
                    "details",
                )
            )
            + " |"
        )
    return table


def _markdown_table(rows: list[dict[str, float | int | str]]) -> list[str]:
    if not rows:
        return ["No results."]
    columns = _ordered_result_columns(rows)
    table = [
        "| " + " | ".join(_markdown_cell(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        table.append("| " + " | ".join(_markdown_cell(row.get(column, "")) for column in columns) + " |")
    return table


def _ordered_result_columns(rows: list[dict[str, float | int | str]]) -> list[str]:
    keys = {key for row in rows for key in row.keys()}
    priority = ["frame_index", "time_s"]
    state_priority = ["x_px", "y_px", "x_world", "y_world", "theta", "theta_unwrapped", "r", "s", "offset"]
    columns = [key for key in priority if key in keys]
    columns.extend(key for key in state_priority if key in keys and key not in columns)
    raw_keys = sorted(key for key in keys if key.startswith("raw_") and key not in columns)
    columns.extend(raw_keys)
    columns.extend(key for key in ["confidence", "status"] if key in keys and key not in columns)
    columns.extend(sorted(keys.difference(columns)))
    return columns


def _column_label(key: str, state_units: dict[str, str]) -> str:
    if key == "time_s":
        return "time_s (s)"
    if key.startswith("raw_"):
        base_key = key[4:]
        unit = state_units.get(base_key, "")
    else:
        unit = state_units.get(key, "")
    return f"{key} ({unit})" if unit else key
