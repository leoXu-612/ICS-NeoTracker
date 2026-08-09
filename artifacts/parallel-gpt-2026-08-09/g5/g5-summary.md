# G5 Analysis and Review Response Coordination

## Result

- Added application-owned `AnalysisCoordinator` and immutable `AnalysisRequest`.
- Added application-owned `ReviewResponseCoordinator` with latest-only pending request coalescing and bounded LRU commit through `ReviewResponseService`.
- `NeoTrackerWindow` no longer constructs or assigns Analysis/Review Response `QThread`, Worker, Job, or pending-request state.
- `analysis.py` and Observation response computation were not modified.
- Analysis exports remain disabled while running, canceled, stale, or failed, and become enabled only after a context-valid `AnalysisRun` is accepted.

## Validation matrix

| Path | Gate |
| --- | --- |
| Analysis valid completion | owner token, source identity, and settings match frozen request |
| Analysis source mismatch | result discarded as stale |
| Analysis settings mismatch | result discarded as stale |
| Analysis owner/task mismatch | result discarded as stale |
| Analysis user cancel | late result discarded; supervisor released after thread exit |
| Analysis close | worker canceled; close gate released after thread exit |
| Review supersede | active request canceled; only latest pending request starts |
| Review result replacement | late response rejected by result object identity |
| Review completion | LRU commit occurs only after worker thread exits and validity is rechecked |
| Review close | pending request cleared; active worker canceled; cache cleared |

## Tests

- Coordinator and architecture tests: 15/15 passed.
- Existing `test_ui_main_window`: 143/143 passed.
- Analysis/Review/Application focused suite: 63/63 passed before the final added mismatch cases.
- Full suite: 495/495 passed in 68.267 seconds.
- `compileall`: passed.
- `pip check`: `No broken requirements found.`
- `git diff --check`: passed.

The full unittest output is recorded in `full-unittest.txt`.
