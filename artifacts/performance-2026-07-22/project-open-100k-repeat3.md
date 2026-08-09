# 100k project-open repeated heartbeat gate

Date: 2026-07-22 HKT

Command:

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75
```

Fixture: one synthetic `.ntproj`, 100,000 tracking results, 44.398 MiB. The same
window opens the project three times so the second and third runs also exercise
replacement cleanup of the previous 100,000-result task.

| Run | Return | Fully usable | Max heartbeat | GUI apply |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1.566 ms | 5,898.644 ms | 35.000 ms | 32.730 ms |
| 2 | 0.481 ms | 3,850.171 ms | 47.633 ms | 43.954 ms |
| 3 | 0.676 ms | 3,752.339 ms | 53.570 ms | 51.277 ms |

The synchronous reference took 1,870.131 ms and stopped the heartbeat for
1,873.936 ms. All background runs preserved the exact result count, canonical
payload, 64-character SHA-256 fingerprint, prepared Review diagnostics, and
Analysis sources. Both deferred views were fully hydrated when timing stopped.

The gate exits non-zero if any repeated background heartbeat exceeds 75 ms.
This offscreen synthetic result does not replace real long-duration camera,
hardware decoder, cold-storage, whole-process RSS, Retina, or VoiceOver tests.
