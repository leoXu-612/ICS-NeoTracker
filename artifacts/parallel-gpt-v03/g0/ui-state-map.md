# G0 — Analysis Workspace UI State Map

## Baseline

- Branch: `feature/gpt-analysis-workspace`
- Base / contract SHA: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`
- Baseline full suite: `544` tests, `0` failures/errors, `69.509 s`.
- Baseline 100k project open: background call `0.422 ms`, batch `3938.578 ms`, max heartbeat `59.536 ms`, apply `57.281 ms`; synchronous/background payloads and fingerprints matched.
- `NeoTrackerWindow` owns coordinators and immutable view state, while worker/thread lifecycle lives under `neo_tracker/application/**`.

## Existing control paths

| Concern | Source | Current route | Constraint for v0.3 |
| --- | --- | --- | --- |
| Task identity | `NeoTrackerWindow._task_changed` | task list -> `current_task` -> all existing renderers | Reset or remap analysis selection once; reject the prior task revision. |
| Video frame | `_preview_frame_selected_by_user` / `_preview_frame_changed` | spin/slider/playback -> task preview index -> preview + Review selection | Publish one selection transaction carrying frame and true time. |
| Results row | `_result_selection_changed` | tracking row -> source frame -> preview render | Route through `SelectionSession`; retain exact/nearest provenance. |
| Diagnostic point | `_jump_to_diagnostic_frame` | diagnostic plot -> preview frame -> Review row | Route as diagnostic-origin selection without a signal loop. |
| Review table | `ResultsTableModel` | virtual `QAbstractTableModel` over `TrackerResult` | Keep intact; the physics table is a separate model over immutable `SampleSeries`. |
| Review diagnostics | `ReviewDiagnosticsPanel` | prepared arrays + bounded diagnostic plot | Reuse its QPainter discipline, not its tracking-specific semantics. |
| Signal | `AnalysisController` + `AnalysisCoordinator` | source snapshot -> background FFT/STFT -> stale/context check | Preserve independently as the bottom `Signal` page; do not merge numerical paths. |
| Commands | `ApplicationShell` + `ActionRegistry` + `ViewStateStore` | one QAction per command, buttons mirror action state | Add physics actions to the same registry; no button-only handlers. |
| Close/cancel | `TaskSupervisor` + coordinators | cancel -> thread terminal -> owner/revision validation | Physics fit jobs must follow the same single-terminal-state pattern. |

## Unified selection state

```text
SelectionSession
  task_id / result_identity / source_revision
  frame_index <-> time_s
  series_id <-> sample_index
  fit_id
  match = exact | nearest | unavailable
  revision (strictly increasing)
```

```text
Video ----------> SelectionSession ----------> Table
  ^                       |                       |
  |                       v                       |
  +--------------------- Plot <------------------+
                          ^
                          |
                     Diagnostic
```

Invariant: a transaction is normalized once, committed once, and broadcast once. Consumers may render it but must not re-submit the same revision. A task/source revision mismatch is stale and produces no commit. Missing frames map to the nearest valid sample with an explicit `nearest` match; equal-distance ties choose the earlier source sample for determinism.

## Workspace state

```text
collapsed <-> expanded(page, height) <-> canvas-focus
                     |
                     +-> selection(range, cursor, visible series)
                     +-> fit(id, status, source revision)
```

- Collapse preserves selected page and last non-zero height.
- Canvas Focus temporarily hides the bottom workspace and restores its prior state on exit.
- Workspace layout state is bounded presentation data only; numerical arrays and workers never enter `ViewState`.
- At 1024x768 the bottom workspace remains reachable, the preview minimum is reduced by splitter allocation rather than hiding controls, and the right sidebar is not used for 100k-row physics data.

## Planned ownership map

- `neo_tracker/ui/selection_session.py`: UI-independent selection state and normalization.
- `neo_tracker/ui/workspaces/physics_workspace.py`: bottom shell and page ownership.
- `neo_tracker/ui/series_table_model.py`: virtual physical-series rows.
- `neo_tracker/ui/physics_plot.py`: bounded prepared plot data and QPainter widget.
- `neo_tracker/ui/fit_panel.py`: request/result presentation only.
- `neo_tracker/ui/analysis_workspace_controller.py`: immutable requests, background lifecycle, stale/cancel/close.
- `neo_tracker/ui/inspectors/physics_inspector.py`: textual provenance/quality presentation.
- `neo_tracker/ui/shell/**`, `action_registry.py`, `view_state.py`, `main_window.py`: narrow integration seams.

## G0 conclusion

The existing shell already has the correct command and background-lifecycle primitives, but selection is duplicated across Preview, Review, and Diagnostics and the fixed 450 px sidebar is the wrong container for analysis data. The v0.3 line will add one selection authority and one bottom analysis region while preserving every existing workflow page.
