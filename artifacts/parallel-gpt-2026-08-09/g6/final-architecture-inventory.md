# NeoTrackerWindow Architecture Inventory at G6

## Structural comparison

| Metric | G0 | G6 | Change |
| --- | ---: | ---: | ---: |
| `main_window.py` lines | 7,311 | 6,822 | -489 (-6.7%) |
| `NeoTrackerWindow` methods/properties | 303 | 290 | -13 (-4.3%) |
| Job dataclasses defined in Window module | 7 | 0 | -7 |
| Major Worker/QThread constructors in Window | 7 lifecycle groups | 0 | all removed |
| Window-owned worker pending/session fields | preview and review response | 0 | all removed |

The remaining legacy field names are read-only properties in
`CoordinatorCompatibilityMixin`; they are test/injection adapters over coordinator-owned
state and are not storage owned by the Window.

## Final responsibility map

| Owner | Responsibility |
| --- | --- |
| `TaskSupervisor` | generations, active task kinds, close gate |
| `PreviewCoordinator` | decode request coalescing, stale rejection, decoder session and worker lifecycle |
| `PlaybackCoordinator` | source-time playback clock and timer lifecycle |
| `MediaImportCoordinator` | media probe batch, progress, cancel, terminal state |
| `ProjectIOCoordinator` | staged project open and revision-guarded project save workers |
| `TrackingCoordinator` | Full/Rerun snapshots, first-frame replacement, progress, cancellation, restoration, run records |
| `AnalysisCoordinator` | FFT/STFT worker, stage state, immutable owner/source/settings validation |
| `ReviewResponseCoordinator` | latest-only response recompute, validity checks, and bounded LRU commit |
| `ActionRegistry` | canonical QActions and button/menu command routing |
| `ViewStateStore` | immutable enabled/text/tooltip state for primary commands |
| `ApplicationShell` | command bindings and ViewState projection to Qt surfaces |
| `NeoTrackerWindow` | widget construction, rendering, request composition, file dialogs, and top-level confirmations |

## Canonical action surface

Sixteen commands are registered once and shared by buttons and menus:

```text
media.add
project.open
project.save
tracking.run
tracking.export_csv
tracking.export_report
analysis.run
analysis.export_csv
analysis.export_npz
playback.previous
playback.toggle
playback.next
review.correct
review.mark_lost
review.rerun
review.jump
```

Run/Cancel, Play/Pause, project-open cancellation, media-import cancellation, Save
presentation, and every listed command's enabled state update through the shared action
and immutable ViewState. No shortcuts, layout migration, or QSS rewrite were added.
