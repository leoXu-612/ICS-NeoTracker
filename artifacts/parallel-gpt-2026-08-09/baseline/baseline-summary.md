# G0 Architecture Baseline

Date: 2026-08-09 (Asia/Taipei)

## Base

- Branch: `refactor/gpt-application-shell`
- Base SHA: `9ccc14baedaf19ad5160efdc325fb151d49c3199`
- Worktree: `/Users/leo.xu/Desktop/Codex/ICS-Project-/_worktrees/ICS-NeoTracker-gpt`
- Source worktree was clean before this evidence directory was created.

## Environment

- Python 3.12.6
- NumPy 2.2.3
- PySide6 6.11.1
- OpenCV 4.13.0
- SciPy 1.17.0

## Verification

### Lifecycle-focused tests

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest \
    tests.test_background_tasks \
    tests.test_playback_controller \
    tests.test_media_probe_worker \
    tests.test_tracking_worker \
    tests.test_analysis_worker \
    tests.test_project_open_worker \
    tests.test_review_response_worker \
    tests.test_ui_main_window -v
```

Result: 199 tests passed in 62.558 seconds. Raw output: `targeted-lifecycle.txt`.

### Full suite

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
```

Result: 458 tests passed in 63.508 seconds. Raw output: `full-unittest.txt`.

### Buildability and dependencies

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
python3 -m pip check
```

Result: both exited 0; pip reported `No broken requirements found.` Raw output: `compileall.txt` and `pip-check.txt`.

## 100,000-result Project Open Baseline

Command:

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
    --results 100000 --repeat-background 5 --max-heartbeat-ms 75
```

| Metric | Values (ms) | Median | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 91.43 / 69.25 / 70.85 / 77.16 / 51.15 | 70.85 | 91.43 | 91.43 |
| GUI apply | 42.70 / 66.69 / 66.41 / 74.93 / 47.93 | 66.41 | 74.93 | 74.93 |
| Fully usable | 5527.65 / 4823.05 / 4207.75 / 3965.49 / 3835.96 | 4207.75 | 5527.65 | 5527.65 |

- Exit code: 1 because 2/5 heartbeat samples exceeded 75 ms.
- `payload_equal=True`; `results_exact=True`.
- Synchronous and background fingerprint: `d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`.
- Deferred diagnostics and analysis were both complete.
- First outlier phase was `finished → finished`; the 77.16 ms outlier was `verifying the saved baseline → applying the prepared workspace`.
- This is a truthful flaky baseline, not evidence of cross-load stability. Later stages must report regressions relative to the distribution and retain both outliers.
- Raw evidence: `project-open-100k.json`, `project-open-100k.stderr.txt`, and `project-open-100k.exit-code.txt`. The JSON passes `python3 -m json.tool`.

## Structural Baseline

- `neo_tracker/ui/main_window.py`: 7,311 lines.
- Top-level classes plus class methods detected by source scan: 303.
- Background Job dataclasses in the window module: 7.
- Window-owned primary worker/thread pairs: media probe, project open, project save, preview decode, tracking, analysis, and review response.
- Window-owned pending/session state: preview pending request/session/cache and review-response pending request.

See `architecture-inventory.md` for the source-to-target responsibility map.
