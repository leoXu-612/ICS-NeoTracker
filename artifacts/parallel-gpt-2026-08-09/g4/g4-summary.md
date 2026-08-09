# G4 Tracking Coordinator Evidence

## Conclusion

`TrackingCoordinator` now owns Tracking Job snapshots, QThread/Worker lifecycle, cancellation, progress metrics, first-valid-frame replacement, Full/Rerun terminal determination, zero-frame/source-drift restoration, superseded edits, and run records. `NeoTrackerWindow` retains confirmation, source-review UI, semantic progress rendering, and terminal presentation.

## Validation

- Red phase: coordinator import and Window ownership checks failed before implementation.
- Coordinator/architecture focused tests: passed.
- TrackingWorker + Coordinator + existing MainWindow lifecycle matrix: 178/178 passed before the two final explicit Rerun matrix cases were added.
- Final full suite: 485/485 passed in 68.187 seconds; raw evidence is in `full-unittest.txt` and its exit-code file.
- `compileall`: passed.
- `pip check`: passed (`No broken requirements found.`).
- Detailed state-to-test coverage is in `tracking-state-matrix.md`.

## 100,000-result Project Open (required non-Tracking regression gate)

### Primary five runs

| Metric | Values (ms) | P50 | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 35.16 / 50.61 / 52.23 / 51.71 / 111.84 | 51.71 | 111.84 | 111.84 |
| GUI apply | 33.07 / 46.58 / 49.94 / 48.98 / 55.88 | 48.98 | 55.88 | 55.88 |
| Fully usable | 3875.18 / 3886.67 / 3836.15 / 3837.20 / 4733.12 | 3875.18 | 4733.12 | 4733.12 |

- Exit code: 1; heartbeat samples above 75 ms: 1/5.
- The 111.84 ms outlier occurred entirely inside `decoding validated records`.

### Confirmation five runs

| Metric | Values (ms) | P50 | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 36.59 / 108.09 / 54.87 / 45.95 / 47.40 | 47.40 | 108.09 | 108.09 |
| GUI apply | 31.66 / 44.32 / 50.90 / 43.69 / 43.74 | 43.74 | 50.90 | 50.90 |
| Fully usable | 4148.20 / 4063.65 / 4076.59 / 4036.19 / 3936.14 | 4063.65 | 4148.20 | 4148.20 |

- Exit code: 1; heartbeat samples above 75 ms: 1/5.
- The 108.09 ms outlier occurred after the benchmark reported `finished → finished`.
- Across all 10 G4 samples, 2/10 exceeded 75 ms. Median behavior did not regress versus G0 or G3, but recurring isolated outliers remain and are not hidden or labeled solved.
- Both runs have `payload_equal=true`, `results_exact=true`, no pending deferred views, and fingerprint `d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`.

## Compatibility and Residual Risk

- Project format, scientific pipeline configuration, checkpoints, spawn process isolation, prefetch, cancel grace, terminate/kill escalation, and source identity rules are unchanged.
- The Window retains read-only compatibility properties and the `TrackingWorker` symbol so existing injection tests remain valid; it no longer constructs or stores their lifecycle state.
- Offscreen tests do not establish native VoiceOver/Retina behavior or real-camera decoder thermal stability.
