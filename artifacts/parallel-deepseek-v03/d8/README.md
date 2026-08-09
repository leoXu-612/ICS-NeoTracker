# D8 Performance and Accuracy Evidence

Generated: 2026-08-09T19:15:15Z

## Conclusion

All four 100k benchmark programs returned strict JSON with `passed=true` and
`exit_code=0`. Benchmark stderr was empty.

## Commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/benchmark_kinematics_series.py --samples 100000 --iterations 5 --enforce
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/benchmark_kinematics_derivatives.py --samples 100000 --iterations 5 --enforce
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/benchmark_kinematics_fits.py --samples 100000 --iterations 5 --sinusoid-guess-samples 100000 --enforce
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/benchmark_kinematics_100k.py --samples 100000 --iterations 5 --enforce
```

## Key measurements

| Workload | Median | P95 | Max |
| --- | ---: | ---: | ---: |
| Base series build | 97.292 ms | 99.173 ms | 99.513 ms |
| VFR first+second derivative | 1.887 ms | 1.949 ms | 1.958 ms |
| SavGol first+second derivative | 4.067 ms | 4.618 ms | 4.732 ms |
| Linear+quadratic fits | 5.740 ms | 6.081 ms | 6.165 ms |
| 256-candidate sinusoid guess | 160.903 ms | 161.249 ms | 161.288 ms |
| End-to-end compute pipeline | 74.957 ms | 75.561 ms | 75.628 ms |
| CSV export | 588.978 ms | 588.978 ms | 588.978 ms |
| NPZ export | 139.294 ms | 139.294 ms | 139.294 ms |

- End-to-end peak RSS: 275.781 MiB.
- RSS after 5 repeated pipelines grew 0.047 MiB.
- Live input → immutable snapshot RSS: 141.859 → 197.109 MiB.
- Final aligned series RSS: 224.609 MiB.
- Maximum cancellation latency: 2.783 ms.
- 100k sinusoid guess cancellation latency: 0.895 ms.
- Result digests are recorded in each JSON file.

## Scope

Synthetic numerical correctness is closed for D0–D8. Real scientific dataset
validation remains outside this engine branch and is PARTIAL by work-order rule.
