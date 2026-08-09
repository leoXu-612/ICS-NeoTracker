# G3 Media Import and Project IO Evidence

## Conclusion

Media import and project open/save worker ownership now lives in Application-layer coordinators. The existing staged open, atomic UI apply boundary, cancellation, save snapshot revision guard, and close-during-save behavior remain covered, with no 100,000-result benchmark regression.

## Tests

- Red phase: coordinator imports and both Window ownership checks failed before implementation.
- Coordinator and architecture: 11/11 passed.
- Full suite: 477/477 passed in 77.659 seconds.
- `compileall`: passed.
- `pip check`: passed (`No broken requirements found.`).
- The media-probe responsiveness fixture now uses a synthetic WAV so it does not accidentally start an unrelated isolated video-preview lifecycle during Qt teardown. Real video preview/isolated-decoder tests remain unchanged.

## 100,000-result Project Open

Command:

```text
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python3 benchmarks/benchmark_project_open_ui.py --results 100000 --repeat-background 5 --max-heartbeat-ms 75
```

| Metric | Values (ms) | P50 | P95 | Max |
| --- | --- | ---: | ---: | ---: |
| Heartbeat | 38.21 / 50.87 / 52.27 / 52.38 / 53.69 | 52.27 | 53.69 | 53.69 |
| GUI apply | 36.01 / 48.63 / 50.00 / 48.58 / 51.39 | 48.63 | 51.39 | 51.39 |
| Fully usable | 3974.59 / 4000.90 / 3993.12 / 4032.11 / 4038.62 | 4000.90 | 4038.62 | 4038.62 |

- Exit code: 0.
- Heartbeat samples above 75 ms: 0/5.
- `payload_equal=true`; `results_exact=true`.
- Synchronous/background fingerprint: `d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`.
- Deferred diagnostics and analysis were both complete.
- Baseline heartbeat was 91.43 / 69.25 / 70.85 / 77.16 / 51.15 ms (2/5 above 75 ms); the G3 distribution has no stable greater-than-20% regression. One five-run sample is not evidence of cross-load performance improvement.

Raw JSON, separated stderr, exit code, compile output, and dependency output are stored beside this summary.

## Compatibility

- Project format and limits: unchanged.
- `ProjectTaskController` domain rules: unchanged.
- Open failure/cancellation: current project remains unchanged.
- Save during newer edits: completed disk snapshot updates the saved baseline while the live project remains dirty.
- Close during save: the non-cancelable atomic save is allowed to finish; close proceeds only after its task token is released.
