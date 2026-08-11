# ICS-NeoTracker v0.3.0-alpha

Date: 2026-08-11 (Asia/Taipei)

## Conclusion

- Engine status: `ENGINE_READY`.
- Workspace status: `WORKSPACE_READY`.
- Integration status: `MERGE_READY`.
- Scientific validation status: `PARTIAL`; current real media has no calibration manifest or ground-truth trajectory.
- Release qualification: `PARTIAL`; this report does not claim production readiness or validation for every experiment class.

## Git

- v0.2 base tag: `v0.2.0-alpha.1`, peeled commit `aa3a2eb4a25a154e96f1f7f2bc9d1b315b8b7186`.
- Contract seed: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`.
- GPT Workspace head: `c0984a246cf8c65cbbf606b69e6d4154fe4d8c03`.
- DeepSeek Engine head: `8b91e5903e0f27b23de9818ec5d69d28994dcc64`.
- Verified integration code/test head: `c92402c241c06756cb2c1176850678968caeeab2`.
- Delivery branch: `origin/integration/physics-analysis-v0.3`; the final remote ref is verified equal to local `HEAD` at handoff.
- Tag: no v0.3 tag created; tagging/main merge remains a maintainer release action.

## Architecture

- Kinematics modules: immutable contracts plus series construction, nonuniform derivatives, cadence-strict smoothing, linear/quadratic/nonlinear fitting, residuals, units, cancellation runtime, and safe exports under `neo_tracker/kinematics/`.
- Coordinators: Application-owned background build/derive/smooth/fit/export jobs; QWidget code does not implement numerical algorithms.
- SelectionSession: one true-time transaction synchronizes Video, Data, Plot, and Fit with exact/nearest and source-revision semantics.
- Workspace: collapsible bottom Data/Plot/Fit tray within the existing Shell; table virtualization and bounded true-time plot decimation avoid full 100k formatting/painting.
- Project schema: version 3 adds stable task UUID, result generation, and bounded analysis definitions while retaining v1/v2 migration.

## Numerical Correctness

- Linear 100k parameter max absolute error: `6.366462912410498e-12`.
- Quadratic 100k parameter max absolute error: `1.1641532182693481e-10`; fitted quadratic coefficient and physical acceleration `2a` remain distinct.
- VFR finite-difference max absolute error: `9.432842432044453e-06`; true `time_s` and frame mapping are preserved.
- Sinusoidal no-initial error: `1.687538997430238e-14`; adversarial offsets through `1e12` have dedicated recovery tests.
- Exponential no-initial error: `2.233768725545815e-13`.
- Missing segments split processing domains and retain invalid masks; Savitzky–Golay rejects materially nonuniform cadence rather than silently resampling.
- The deterministic analytic catalog covers all 14 required fixture classes, including constant, insufficient-sample, duplicate-time, and very-small-`dt` boundaries.

## UI

- Data: virtual model, no cell widgets, explicit raw/filtered/smoothed/derived labels, validity, frame, true time, value, unit, and provenance.
- Plot: true-time axes, invalid-gap separation, bounded dense-trace preparation, fit/residual layers, keyboard selection, and 2× export.
- Fit: explicit source/model/range/initial/bounds request; parameters, units, errors, R², RMSE, residual, sample count, cancellation, and export states.
- Selection sync: Video/Table/Plot/Fit share one source-revision-guarded session without duplicate broadcasts.
- Responsive/accessibility: 1024×768, 1280×808, 1440×900, Retina 2×, Tab/Shift+Tab, focus mode, reversible tray collapse, visible focus, and non-color status text are covered.

## Testing

- Contract: `21/21`, OK.
- Engine branch: `595/595`, 64.957 s, OK.
- Workspace branch: `594/594`, 66.022 s, OK.
- Integration suites: the four required kinematics workspace, persistence, VFR, and export modules are present and included.
- Full integration: `674/674`, 121.656 s, OK; zero FAIL/ERROR and no QThread warning.
- Test union: Engine/Workspace contain 645 unique IDs; integration contains all 645 with zero missing and adds 29 integration-only IDs. Machine evidence: `../integration-v03/test-union.json`.
- Preview lifecycle stress: `100/100`, OK. A pre-fix targeted cleanup rerun produced `Python-2026-08-11-203841.ips` (`SIGABRT`, `QThread::~QThread`, worker still running); after the cleanup fix, the targeted test, adjacent sequence, and final full run produced no report newer than 20:38:41 and no orphan helper.
- Compile/dependencies: `compileall` exit 0; `pip check` reports no broken requirements.

## Performance

- 100k series build P50/P95/Max: `70.756/71.220/71.330 ms`; detached snapshot: `541.408 ms`.
- 100k derivative P50/P95/Max: `2.430/2.575/2.605 ms`.
- 100k plot prepare P50/P95/Max: `3.103/4.002/4.799 ms`; paint P95 `28.047 ms`.
- 100k table attach P50/P95/Max: `0.0019/0.0028/0.0090 ms`.
- 100k linear fit P50/P95/Max: `6.330/6.699/6.747 ms`.
- 100k CSV/NPZ/Markdown one-run exports: `587.044/157.293/0.866 ms`.
- GUI heartbeat: project-open max `52.741 ms`; dense-plot loop max `31.954 ms`.
- Peak RSS: `276.766 MiB`; five-repeat growth `0.031 MiB`.
- Engine cancellation max: `2.792 ms`; real-video cancellation: `44 ms`.
- All performance values are local measured gates, not deployment guarantees.

## Persistence

- v1/v2 compatibility: both migrate to schema v3; unknown or invalid analysis records fail closed.
- v3 save/open: only bounded definitions, config, range, view, visibility, and provenance persist; 100k derived arrays, FFT/STFT copies, pickle/object arrays, QWidget, worker, and runtime state do not.
- Stale analysis: task UUID, result generation, source revision, owner, range, and model are checked before output commit; stale definitions remain inspectable but disabled.
- Atomicity: project and CSV/NPZ/Markdown outputs use same-directory temporary files, flush/fsync, atomic replace, and parent-directory fsync; CSV formula injection and NPZ object/pickle payloads are rejected.

## Real Experiments

- Available fixture: read-only copy `PMR00056.mov`, HEVC 1920×1080, nominal 240 fps, average 240.262 fps VFR, 5,536 frames, 23.0415 s.
- Ground truth: unavailable; no calibration metadata or reference trajectory was supplied.
- Results: two complete current-code runs processed all 5,536 frames with identical digest `87b77eb574719f3d2f6bae6693bce1c257977965eb1d4b91b157554d5e4a9a5a`; source replacement and truncation fail closed; reopen 10/10; zero residual helper.
- Missing coverage: calibrated uniform motion, acceleration, free fall, projectile, pendulum, spring, circular motion, and wavefront experiment matrices.

## Limitations

- Uncertainty: no complete uncertainty propagation or ground-truth error budget.
- Multi-track: explicitly outside v0.3 scope.
- 4K/long session: no original 4K, single-session ≥10-minute, thermal, power, or sustained hardware-decode qualification.
- Accessibility: offscreen/AX-text and Retina evidence does not replace native VoiceOver or OS text-scaling validation.
- Distribution: no signing, notarization, packaging, or production release qualification.

## Files Changed

- Engine: `neo_tracker/kinematics/`, analytical fixtures, engine tests, and 100k benchmarks.
- Application/UI: kinematics coordinators, SelectionSession, Physics Workspace/Data/Plot/Fit/Inspector, Action Registry integration, and lifecycle tests.
- Persistence/integration: `neo_tracker/project.py`, project open/controller adapters, four required integration suites, schema tests, and acceptance evidence.
- Project documentation: README, project index, stable file hashes, collaboration handoffs, and this report.

## Commits

- Contract: `a0e9d94`.
- Engine line: `1d29884` through `8b91e59`.
- Workspace line: `f6f41c6` through `c0984a2`.
- Integration merges/adapters: `1dceac3`, `b1862cb`, `770f6c1`, `c3849f9`, `7afc945`, `8f5f9d6`.
- Final fixture union/lifecycle audit: Engine continuation merged, then `c92402c` waits for physics workers during window cleanup.

## Suggested Next Version

- `v0.4 Editing Reliability`: global editing reliability/undo scope, then calibrated scientific dataset qualification before broader release claims.

```text
Synthetic numerical correctness: CLOSED
Engineering regression: CLOSED
VFR semantics: CLOSED
Project persistence: CLOSED
Real scientific dataset: PARTIAL
Release qualification: PARTIAL
```
