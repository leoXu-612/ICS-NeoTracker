# GPT Application Shell Final Audit

## Conclusion

The G0-G6 architecture work package is complete on
`refactor/gpt-application-shell`.  The final audit found and fixed one
order-dependent Qt test-harness abort, restored the missing G5/G6 handoff
entries, and re-ran every final acceptance command without changing product
behavior, project format, or scientific results.

## Audit correction

Running the coordinator and shell tests in the explicit work-package order
initially terminated Python with exit code 134:

```text
QWidget: Cannot create a QWidget without QApplication
```

`test_preview_coordinator` and `test_playback_coordinator` created a bare
`QCoreApplication`.  A later widget-based module then could not upgrade that
process to `QApplication`, so Qt aborted before unittest could report a
failure.  Both modules now bootstrap `QApplication`; the same 12-module
command passes in arbitrary order.

## Current validation (2026-08-10 Asia/Taipei)

- Coordinator/Application Shell order regression: 44/44 passed in 1.665 s.
- Full suite: 502/502 passed in 63.586 s.
- `compileall`: passed.
- `pip check`: passed (`No broken requirements found.`).
- `git diff --check`: passed.
- Forbidden/Integrator/DeepSeek-owned paths in the branch diff: none.

## 100,000-result project open

The final five-run check ran at load average 3.96 on 8 logical CPUs:

| Metric | Values (ms) | Median | Max |
| --- | --- | ---: | ---: |
| Heartbeat | 63.45 / 49.08 / 48.58 / 48.85 / 47.40 | 48.85 | 63.45 |
| GUI apply | 59.89 / 46.81 / 44.97 / 45.27 / 45.06 | 45.27 | 59.89 |
| Fully usable | 3860.66 / 3837.36 / 3861.27 / 3910.11 / 3810.41 | 3860.66 | 3910.11 |

- Exit code: 0; heartbeat samples above 75 ms: 0/5.
- `payload_equal=true`, `results_exact=true`, and both deferred views were
  complete after every run.
- Fingerprint remained
  `d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`.
- The earlier G6 76.56 ms sample and G4/G0 outliers remain in their original
  evidence.  This passing run is not presented as proof of all-load
  scheduling stability.

## Delivery boundary

- Product source changed by this final audit: none.
- Test files changed: `tests/test_preview_coordinator.py` and
  `tests/test_playback_coordinator.py`.
- Shared integration files changed: none.
- Main branch merge performed: no, as required by the work package.
