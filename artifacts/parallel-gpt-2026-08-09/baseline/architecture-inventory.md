# NeoTrackerWindow Architecture Inventory at G0

Base: `9ccc14baedaf19ad5160efdc325fb151d49c3199`

## Current responsibility map

| Current owner in `main_window.py` | Lifecycle surface | Target owner |
| --- | --- | --- |
| `BackgroundTaskCoordinator` and `_background_tasks` | active kinds, generation tokens, closing gate | G1 `TaskSupervisor`, with compatibility for `BackgroundTaskCoordinator` |
| `MediaProbeJob`, thread, worker | start, progress, cancel, terminal staging, thread cleanup | G3 `MediaImportCoordinator` |
| `ProjectOpenJob`, thread, worker | staged load/probe, progress, cancel, atomic apply handoff, deferred heavy views | G3 `ProjectIOCoordinator` |
| `ProjectSaveJob`, thread, worker | immutable snapshot, revision/path guard, terminal cleanup | G3 `ProjectIOCoordinator` |
| `PreviewDecodeJob`, pending request, decoder session, thread, worker | request coalescing, stale rejection, session ownership, close | G2 `PreviewCoordinator` |
| `PlaybackClock`, play timer callbacks | source-time clock, skips, end/failure state | G2 `PlaybackCoordinator` |
| `TrackingJob`, thread, worker | Full/Rerun snapshots, replacement commit, progress, cancel, source drift, terminal restoration, run history | G4 `TrackingCoordinator` |
| `AnalysisJob`, thread, worker | context snapshot, stages, cancel, stale result rejection, terminal cleanup | G5 `AnalysisCoordinator` |
| `ReviewResponseJob`, pending request, thread, worker | request merge, cancel, stale owner/revision rejection, cache commit | G5 `ReviewResponseCoordinator` |
| button/menu signal connections and enablement spread across render/busy methods | command entry and testable enabled state | G6 `ActionRegistry` and `ViewState` |
| widget construction, dialogs, visual rendering | QWidget ownership and top-level user confirmations | G6 `NeoTrackerWindow` UI shell |

## Job dataclasses to extract in G1

| Dataclass | Current mutable terminal fields | Target coordinator |
| --- | --- | --- |
| `TrackingJob` | completed/cancelled/failed/ended_early, result replacement state, previous result/edit/analysis state, performance metrics, source identity | Tracking |
| `AnalysisJob` | cancelled and cancel presentation state | Analysis |
| `MediaProbeJob` | cancelled/completed/failure detail | Media import |
| `ProjectOpenJob` | cancelled/completed/failure detail | Project IO |
| `ProjectSaveJob` | completed/failure detail plus content revision/path | Project IO |
| `ReviewResponseJob` | cancelled/completed/failure detail | Review response |
| `PreviewDecodeJob` | cancelled/result/failure detail plus decoder session | Preview |

`PipelineStep` is presentation data, not a background job. It remains outside the lifecycle extraction until G6 decides whether it belongs in view state.

## Window-owned lifecycle fields at G0

```text
_tracking_thread / _tracking_worker / _tracking_job
_analysis_thread / _analysis_worker / _analysis_job
_media_probe_thread / _media_probe_worker / _media_probe_job
_project_open_thread / _project_open_worker / _project_open_job
_project_save_thread / _project_save_worker / _project_save_job
_review_response_thread / _review_response_worker / _review_response_job
_pending_review_response
_preview_decode_thread / _preview_decode_worker / _preview_decode_job
_pending_preview_decode / _preview_decode_cache / _preview_decoder_session
_background_tasks
```

## Method clusters and planned migration

| Lines at G0 | Current methods | Planned stage |
| --- | --- | --- |
| 1899–2121 | media probe start/progress/terminal/cancel/thread finish | G3 |
| 2143–2304 | project open start/progress/terminal/cancel/thread finish | G3 |
| 2421–2570 | open command, save start/terminal/thread finish | G3/G6 |
| 2778–3030 | prepared project atomic apply and deferred heavy views | G3, preserving UI render callbacks in shell |
| 4375–4587 | preview request/session/start/cancel/terminal/stale validation | G2 |
| 4644–4800 | review response queue/start/cancel/terminal/stale validation | G5 |
| 4861–5006 | playback start/stop/tick/status | G2 |
| 5536–6318 | source verification, Full/Rerun start, progress, cancel, terminal restoration/commit | G4 |
| 6705–7215 | analysis source/context, start, stage, cancel, terminal, export | G5; exports/rendering remain UI-facing |
| 7222–7286 | coordinated close/resume/close event | G1 then each coordinator's idempotent `close()` |

## Required compatibility seams

- Tests currently inspect window compatibility attributes such as `_tracking_job`, `_analysis_job`, and `_close_when_workers_stop`; G1 must preserve intentional read-only adapters while ownership moves.
- Worker factories/loaders (`project_loader`, `project_saver`, injected preview reader paths) are test seams and must remain injectable.
- Coordinators may emit semantic results but may not retain QWidget instances or call render methods.
- UI confirmations remain in the window; requests passed inward should be immutable snapshots.
- The existing task/session objects and `.ntproj` schema are unchanged by this work package.
