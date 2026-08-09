# G6 Application Shell Evidence

## Conclusion

The primary Window command surface now uses canonical QActions, a testable immutable
ViewState, and a dedicated Shell binding layer; no major Worker or QThread is constructed
by `NeoTrackerWindow`.

## Validation

- G6 Action Registry, ViewState, Shell, and architecture tests: 13/13 passed.
- Final full suite: 502/502 passed in 60.590 seconds.
- `compileall`: passed.
- `pip check`: passed (`No broken requirements found.`).
- `git diff --check`: passed before staging.
- Structural evidence is in `final-architecture-inventory.md`.

## 100,000-result project open

### Primary five runs

| Metric | Values (ms) | P50 | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 38.16 / 76.56 / 50.01 / 45.60 / 51.50 | 50.01 | 76.56 | 76.56 |
| GUI apply | 35.85 / 74.30 / 45.62 / 43.36 / 49.21 | 45.62 | 74.30 | 74.30 |
| Fully usable | 3941.31 / 4100.47 / 3961.65 / 4084.47 / 4060.90 | 4060.90 | 4100.47 | 4100.47 |

- Exit code: 1; heartbeat samples above 75 ms: 1/5.
- The 76.56 ms sample spans `verifying the saved baseline` to `finished`.

### Confirmation five runs

| Metric | Values (ms) | P50 | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 40.34 / 45.02 / 50.02 / 45.23 / 46.62 | 45.23 | 50.02 | 50.02 |
| GUI apply | 31.72 / 42.72 / 47.74 / 42.85 / 43.04 | 42.85 | 47.74 | 47.74 |
| Fully usable | 3964.75 / 3841.52 / 3787.30 / 4007.69 / 3790.01 | 3841.52 | 4007.69 | 4007.69 |

- Exit code: 0; heartbeat samples above 75 ms: 0/5.
- Across both G6 runs, 1/10 samples exceeded 75 ms. The outlier is retained in the
  report and is not treated as resolved.
- Both runs report `payload_equal=true`, `results_exact=true`, no pending deferred views,
  and fingerprint `d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`.
- Relative to G0 (heartbeat P50 70.85 ms, GUI apply P50 66.41 ms, fully usable P50
  4207.75 ms), neither G6 run shows a systematic greater-than-20% regression.

Raw unittest, dependency, benchmark JSON, stderr, and exit-code evidence is stored beside
this report.
