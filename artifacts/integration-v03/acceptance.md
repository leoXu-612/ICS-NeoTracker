# Physics Analysis v0.3 Integration Acceptance

Date: 2026-08-11 (Asia/Taipei)
Code SHA: `8f5f9d6`
Verified integration code/test SHA: `c92402c`
Branch: `integration/physics-analysis-v0.3`

## Conclusion

The Engine, Workspace, Schema v3, application coordinators, persistence replay, exports, and stale-result guards are integrated and `MERGE_READY`; the remote integration branch is published and its ref is verified against the exact local delivery SHA.

## Required gates

- Full regression: `674 tests / 121.656 s / OK`; zero FAIL and ERROR.
- Test ID union: Engine 595, Workspace 594, 645 unique across both; Integration contains all 645 with zero missing and adds 29 IDs.
- Static/dependencies: `compileall` exit 0; `pip check` reports no broken requirements.
- Integration tests: all four work-order suites are present and included in the full run.
- 100k project: three background opens preserve results, payload, and fingerprint; heartbeat max `52.741 ms`; no QThread lifecycle warning.
- 100k engine: hot pipeline P50/P95/Max `77.584/91.437/92.977 ms`; peak RSS `276.766 MiB`; max cancellation `2.792 ms`.
- 100k UI evidence inherited unchanged from Workspace SHA `c0984a2`: table attach P95 `0.0028 ms`, plot prepare P95 `4.002 ms`, paint P95 `28.047 ms`, heartbeat max `31.954 ms`, peak RSS `96.33 MiB`, zero helper.
- Real SloMo: current SHA reprocessed the 5,536-frame HEVC 1080p/240fps VFR source twice with the same digest; cancel `44 ms`; source replacement and truncation fail closed; reopen 10/10; zero residual helper.
- Persistence: v1/v2 migrate to v3; v3 stores bounded definitions/config/range/view/provenance only, never derived arrays or GUI/runtime objects.
- Export: CSV/NPZ/Markdown are atomic; spreadsheet formulas are neutralized; NPZ is pickle-free and contains no object dtype.

## Regression found during final acceptance

The first integrated 100k project-open run exposed a `ResizeToContents` scan on the 100k-row physics table and a close-time QThread leak. Fixed-column interactive sizing reduced physics apply from about `110–120 ms` to `13–18 ms`, and project heartbeat returned below `75 ms`. A later full run exposed Preview worker destruction after thread exit (`EXC_BAD_ACCESS` in `QObject::~QObject`); terminal signals now retire the worker before the QThread stops. The Preview lifecycle passed 100/100 repetitions. After the final Engine fixture continuation was merged, the expanded suite exposed a test cleanup that waited for legacy workers but not the new physics build/fit workers; the next window could deadlock against QThread destruction. A pre-fix targeted rerun generated `Python-2026-08-11-203841.ips` (`SIGABRT` in `QThread::~QThread`, worker still running), which is the expected signature of that cleanup defect. Cleanup now waits for both physics coordinators. The targeted test, adjacent two-test sequence, and subsequent 674-test full run exit cleanly without a report newer than 20:38:41 or a QThread warning.

## Alpha qualification

```text
Synthetic numerical correctness: CLOSED
Engineering regression: CLOSED
VFR semantics: CLOSED
Project persistence: CLOSED
Real scientific dataset: PARTIAL
Release qualification: PARTIAL
```

The real source proves decode/tracking/VFR/lifecycle behavior, not experimental accuracy: no calibration manifest or ground-truth trajectory was supplied. This release is not claimed production-ready or scientifically validated for all experiments.

## Evidence

- `benchmark-100k.json`
- `project-open-100k.json`
- `real-slomo-smoke.json`
- `test-union.json`
- `../v0.3.0-alpha/final-summary.md`
- `../parallel-deepseek-v03/d8/`
- `../parallel-gpt-v03/g7/`
