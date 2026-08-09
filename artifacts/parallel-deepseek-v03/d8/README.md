# D8 Performance and Accuracy Evidence

Generated: 2026-08-10

## Conclusion

All four 100k benchmark programs returned strict JSON with `passed=true` and
`exit_code=0`. The post-readiness high-DC sinusoid regression is closed without
weakening the performance or cancellation gates.

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
| Linear+quadratic fits | 6.841 ms | 7.014 ms | 7.017 ms |
| 256-candidate sinusoid guess | 185.682 ms | 197.813 ms | 199.161 ms |
| End-to-end compute pipeline | 75.939 ms | 76.867 ms | 76.970 ms |
| CSV export | 641.019 ms | 641.019 ms | 641.019 ms |
| NPZ export | 145.208 ms | 145.208 ms | 145.208 ms |

- End-to-end `timing_ms` measures the hot series-build + first-derivative +
  linear-fit pipeline. It excludes the one-time live-result snapshot and all
  exports; those operations remain separately reported in `stage_timing_ms`.
- End-to-end peak RSS: 272.047 MiB.
- RSS after 5 repeated pipelines grew 7.766 MiB.
- Live input → immutable snapshot RSS: 145.156 → 199.641 MiB.
- Final aligned series RSS: 242.875 MiB.
- Maximum cancellation latency: 2.853 ms.
- 100k sinusoid guess cancellation latency: 1.304 ms.
- Result digests are recorded in each JSON file.

## Adversarial numerical check

With no supplied initial parameters and 2,000 samples of
`offset + 2 sin(1.7t + 0.4)`, the fit recovered:

- `offset=1e9`: `[2.0000000009, 1.6999999999, 0.4000000018, 1e9]`,
  RMSE `1.1921e-08`.
- `offset=1e12`: `[1.9999992412, 1.6999998406, 0.4000023335, 1e12]`,
  RMSE `1.4183e-05`.

Candidate scoring and nonlinear optimization now operate on centered observed
values, with the physical offset restored only after optimization.

## Scope

Synthetic numerical correctness is closed for D0–D8. Real scientific dataset
validation remains outside this engine branch and is PARTIAL by work-order rule.
