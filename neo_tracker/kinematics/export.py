from __future__ import annotations

"""Atomic, reproducible CSV/NPZ/Markdown export for one analysis series."""

import csv
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from neo_tracker.atomic_io import fsync_parent_directory
from neo_tracker.csv_utils import spreadsheet_safe_cell

from .protocols import CancellationProbe
from .runtime import check_cancelled
from .types import FitResult, FitStatus, SampleSeries


CSV_FIELDS = (
    "frame",
    "time_s",
    "series_id",
    "series_name",
    "value",
    "unit",
    "valid",
    "source_kind",
    "processing_chain",
    "fit_prediction",
    "residual",
)


def _target_path(path: str | Path) -> Path:
    target = Path(path).expanduser()
    if not target.name or target.name in {".", ".."}:
        raise ValueError("export path must name a file")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def _atomic_destination(path: str | Path) -> Iterator[tuple[Path, Path]]:
    target = _target_path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        yield target, temporary
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        fsync_parent_directory(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _validate_fit(series: SampleSeries, fit: FitResult | None) -> None:
    if fit is None:
        return
    if not isinstance(fit, FitResult):
        raise TypeError("fit must be a FitResult or None")
    if fit.status is not FitStatus.OK:
        raise ValueError("only successful fit results can be exported")
    if fit.series_id != series.series_id:
        raise ValueError("fit result series_id does not match the exported series")
    if fit.source_revision != series.source_revision:
        raise ValueError("fit result source revision is stale")
    if len(fit.predicted) != len(series) or len(fit.residuals) != len(series):
        raise ValueError("fit result must remain frame aligned with the exported series")


def _csv_text(value: str) -> str:
    text = value.replace("\x00", "")
    return str(spreadsheet_safe_cell(text))


def _numeric_cell(value: float) -> str:
    return format(float(value), ".17g") if np.isfinite(value) else ""


def export_csv(
    path: str | Path,
    series: SampleSeries,
    fit: FitResult | None = None,
    *,
    cancellation: CancellationProbe | None = None,
) -> Path:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    _validate_fit(series, fit)
    check_cancelled(cancellation, phase="CSV export")
    chain = json.dumps(
        [step.to_dict() for step in series.processing_chain],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with _atomic_destination(path) as (target, temporary):
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise")
            writer.writeheader()
            for index in range(len(series)):
                if index % 1_024 == 0:
                    check_cancelled(cancellation, phase="CSV export")
                fit_valid = bool(fit is not None and fit.valid_mask[index])
                writer.writerow(
                    {
                        "frame": str(int(series.frame_indices[index])),
                        "time_s": _numeric_cell(series.time_s[index]),
                        "series_id": _csv_text(series.series_id),
                        "series_name": _csv_text(series.name),
                        "value": _numeric_cell(series.values[index]),
                        "unit": _csv_text(series.unit),
                        "valid": "true" if series.valid_mask[index] else "false",
                        "source_kind": _csv_text(series.source_kind),
                        "processing_chain": _csv_text(chain),
                        "fit_prediction": (
                            _numeric_cell(fit.predicted[index]) if fit_valid and fit is not None else ""
                        ),
                        "residual": (
                            _numeric_cell(fit.residuals[index]) if fit_valid and fit is not None else ""
                        ),
                    }
                )
            handle.flush()
            os.fsync(handle.fileno())
        check_cancelled(cancellation, phase="CSV export")
    return target


def _plain_json(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _series_metadata(series: SampleSeries) -> dict[str, object]:
    return {
        "schema": "neo-tracker.kinematics-export/v1",
        "series_id": series.series_id,
        "series_name": series.name,
        "unit": series.unit,
        "source_kind": series.source_kind,
        "source_revision": series.source_revision,
        "processing_chain": [step.to_dict() for step in series.processing_chain],
        "metadata": _plain_json(series.metadata),
    }


def _fit_metadata(fit: FitResult) -> dict[str, object]:
    return {
        "model": fit.model.value,
        "parameter_names": list(fit.parameter_names),
        "parameter_units": list(fit.parameter_units),
        "rmse": fit.rmse,
        "r_squared": fit.r_squared,
        "sample_count": fit.sample_count,
        "range_start_s": fit.range_start_s,
        "range_end_s": fit.range_end_s,
        "source_revision": fit.source_revision,
        "status": fit.status.value,
        "message": fit.message,
    }


def _json_scalar(value: dict[str, object]) -> np.ndarray:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return np.asarray(encoded, dtype=np.str_)


def export_npz(
    path: str | Path,
    series: SampleSeries,
    fit: FitResult | None = None,
    *,
    cancellation: CancellationProbe | None = None,
) -> Path:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    _validate_fit(series, fit)
    check_cancelled(cancellation, phase="NPZ export")
    arrays: dict[str, np.ndarray] = {
        "frame_indices": np.asarray(series.frame_indices, dtype=np.int64),
        "time_s": np.asarray(series.time_s, dtype=np.float64),
        "values": np.asarray(series.values, dtype=np.float64),
        "valid_mask": np.asarray(series.valid_mask, dtype=bool),
        "metadata_json": _json_scalar(_series_metadata(series)),
    }
    if fit is not None:
        arrays.update(
            {
                "fit_parameters": np.asarray(fit.parameters, dtype=np.float64),
                "fit_standard_errors": np.asarray(fit.standard_errors, dtype=np.float64),
                "fit_covariance": np.asarray(fit.covariance, dtype=np.float64),
                "fit_predicted": np.asarray(fit.predicted, dtype=np.float64),
                "fit_residuals": np.asarray(fit.residuals, dtype=np.float64),
                "fit_valid_mask": np.asarray(fit.valid_mask, dtype=bool),
                "fit_metadata_json": _json_scalar(_fit_metadata(fit)),
            }
        )
    if any(array.dtype.hasobject for array in arrays.values()):
        raise TypeError("NPZ export refuses object arrays")
    with _atomic_destination(path) as (target, temporary):
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        check_cancelled(cancellation, phase="NPZ export")
        with np.load(temporary, allow_pickle=False) as archive:
            if set(archive.files) != set(arrays):
                raise ValueError("NPZ verification found missing or unexpected arrays")
            for name, expected in arrays.items():
                actual = archive[name]
                if actual.shape != expected.shape or actual.dtype != expected.dtype:
                    raise ValueError(f"NPZ verification failed for {name}")
                if actual.dtype.hasobject:
                    raise TypeError("NPZ verification found an object array")
    return target


def _markdown_text(value: object) -> str:
    return (
        str(value)
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "&#96;")
    )


def _markdown_code(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    longest = 0
    current = 0
    for character in text:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def export_markdown(
    path: str | Path,
    series: SampleSeries,
    fit: FitResult | None = None,
    *,
    limitations: Sequence[str] = (),
    cancellation: CancellationProbe | None = None,
) -> Path:
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    _validate_fit(series, fit)
    check_cancelled(cancellation, phase="Markdown export")
    valid_count = int(np.count_nonzero(series.valid_mask))
    valid_times = series.time_s[series.valid_mask]
    data_range = (
        f"{float(valid_times[0]):.17g} s to {float(valid_times[-1]):.17g} s"
        if valid_times.size
        else "unavailable"
    )
    unit = series.unit or "unit unavailable"
    lines = [
        "# NeoTracker Kinematics Analysis",
        "",
        "## Source",
        "",
        f"- Series: {_markdown_code(series.series_id)} ({_markdown_text(series.name)})",
        f"- Source kind: {_markdown_code(series.source_kind)}",
        f"- Source revision: {_markdown_code(series.source_revision)}",
        f"- Unit: {_markdown_code(unit)}",
        f"- Data range: {data_range}",
        f"- Valid samples: {valid_count} / {len(series)}",
        "",
        "## Processing chain",
        "",
    ]
    if series.processing_chain:
        lines.extend(
            f"{index}. {_markdown_code(step)}"
            for index, step in enumerate(series.processing_chain, 1)
        )
    else:
        lines.append("No processing steps recorded.")
    if fit is not None:
        lines.extend(
            [
                "",
                "## Fit",
                "",
                f"- Model: {_markdown_code(fit.model.value)}",
                f"- Range: {fit.range_start_s:.17g} s to {fit.range_end_s:.17g} s",
                f"- RMSE: {fit.rmse:.17g} {_markdown_text(unit)}",
                f"- R²: {fit.r_squared:.17g}",
                f"- Samples: {fit.sample_count}",
                "",
                "| Parameter | Value | Unit | Standard error |",
                "| --- | ---: | --- | ---: |",
            ]
        )
        for name, value, parameter_unit, error in zip(
            fit.parameter_names,
            fit.parameters,
            fit.parameter_units,
            fit.standard_errors,
            strict=True,
        ):
            error_text = f"{float(error):.17g}" if np.isfinite(error) else "unavailable"
            lines.append(
                f"| {_markdown_text(name)} | {float(value):.17g} | "
                f"{_markdown_text(parameter_unit or 'unit unavailable')} | {error_text} |"
            )
    lines.extend(["", "## Limitations", ""])
    default_limitations = (
        "Unknown source units are preserved as unavailable and are not inferred.",
        "Derived arrays are reproducible outputs, not persisted project source data.",
    )
    all_limitations = tuple(limitations) or default_limitations
    lines.extend(f"- {_markdown_text(item)}" for item in all_limitations)
    text = "\n".join(lines) + "\n"
    with _atomic_destination(path) as (target, temporary):
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        check_cancelled(cancellation, phase="Markdown export")
    return target


def export_bundle(
    paths: Mapping[str, str | Path],
    series: SampleSeries,
    fit: FitResult | None = None,
    *,
    cancellation: CancellationProbe | None = None,
) -> tuple[Path, ...]:
    """Prepare all formats before publishing; roll back a failed publication.

    Cancellation ends at publication. A returned bundle is fully published.
    Independent paths cannot be one crash-atomic filesystem transaction.
    """

    exporters = (("csv", export_csv), ("npz", export_npz), ("markdown", export_markdown))
    if not isinstance(paths, Mapping) or set(paths) != {name for name, _writer in exporters}:
        raise ValueError("export bundle requires csv, npz, and markdown paths")
    if not isinstance(series, SampleSeries):
        raise TypeError("series must be a SampleSeries")
    _validate_fit(series, fit)
    check_cancelled(cancellation, phase="export bundle")
    targets = tuple(_target_path(paths[name]) for name, _writer in exporters)
    targets = tuple(path.parent.resolve() / path.name for path in targets)
    if len(set(targets)) != len(targets):
        raise ValueError("export bundle paths must be distinct")
    directories: list[Path] = []
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    keep_backups = False
    try:
        for (_name, writer), target in zip(exporters, targets, strict=True):
            check_cancelled(cancellation, phase="export bundle preparation")
            directory = Path(tempfile.mkdtemp(prefix=".neo-tracker-export-", dir=target.parent))
            directories.append(directory)
            staged[target] = directory / "new"
            writer(staged[target], series, fit, cancellation=cancellation)
        for target in targets:
            check_cancelled(cancellation, phase="export bundle backup")
            if target.exists() or target.is_symlink():
                if not target.is_symlink() and not target.is_file():
                    raise ValueError("export target must be a regular file or symlink")
                backup = staged[target].parent / "previous"
                try:
                    os.link(target, backup, follow_symlinks=False)
                except (OSError, NotImplementedError):
                    shutil.copy2(target, backup, follow_symlinks=False)
                if not backup.is_symlink():
                    if not backup.is_file():
                        raise ValueError("export target must be a regular file or symlink")
                    with backup.open("rb") as handle:
                        os.fsync(handle.fileno())
                fsync_parent_directory(backup)
                backups[target] = backup
        check_cancelled(cancellation, phase="export bundle publication")
        # ponytail: three renames with rollback; a single container is needed for crash atomicity.
        for target in targets:
            os.replace(staged[target], target)
            published.append(target)
            fsync_parent_directory(target)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for target in reversed(published):
            try:
                if target in backups:
                    os.replace(backups[target], target)
                else:
                    target.unlink()
                fsync_parent_directory(target)
            except BaseException as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            keep_backups = True
            recovery = "; ".join(
                f"{target} -> {backup}"
                for target, backup in backups.items()
                if backup.exists() or backup.is_symlink()
            )
            raise OSError(
                "Export rollback incomplete. Recovery backups retained: "
                f"{recovery or 'no original files existed'}. Errors: {'; '.join(rollback_errors)}"
            ) from exc
        raise
    finally:
        if not keep_backups:
            for directory in directories:
                shutil.rmtree(directory, ignore_errors=True)
    return targets


__all__ = ["CSV_FIELDS", "export_bundle", "export_csv", "export_markdown", "export_npz"]
