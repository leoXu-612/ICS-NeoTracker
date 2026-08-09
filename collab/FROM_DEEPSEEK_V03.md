## 2026-08-10 D0–D8 — DONE

### Base
- Branch: `feature/ds-kinematics-engine`
- Base SHA: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`
- Implementation Head SHA: `bebfe51`

### Conclusion
- Status: `ENGINE_READY`.
- D0–D8 are implemented within the Engine ownership boundary.
- Synthetic correctness, VFR/gap semantics, exports, full regression, and 100k gates are closed.
- Real scientific dataset validation remains `PARTIAL` by work-order scope.

### Numerical Semantics
- Base series preserve every frame and true `TrackerResult.time_s`; lost/non-finite values remain aligned and invalid.
- Raw state, filtered state, and filter velocity remain explicitly separate series.
- Nonuniform first/second derivatives split at gaps and support explicit invalid/one-sided edges.
- Savitzky–Golay runs only on cadence accepted by the contract and never resamples silently.
- Linear/quadratic fits use centered/scaled time and transform coefficients/covariance back to original-time semantics.
- Optional bounded exponential/sinusoidal fits normalize sinusoidal parameters and reject overflow/nonconvergence explicitly.
- RMSE/R², residual series, coefficient units, and quadratic `a` versus physical `2a` semantics remain distinct.

### Accuracy
- 100k VFR finite difference max absolute error: `9.432842432044453e-06`.
- 100k SavGol derivative max absolute error: `8.583068846768072e-07`.
- 100k linear parameter max absolute error: `6.366462912410498e-12`.
- 100k quadratic parameter max absolute error: `1.1641532182693481e-10`.
- Exponential with no supplied initial parameters: `2.233768725545815e-13`.
- Sinusoidal with no supplied initial parameters: `1.687538997430238e-14`.
- No-initial `1e9 + 2 sin(1.7t + 0.4)` recovery: RMSE `1.1921e-08`.
- No-initial `1e12 + 2 sin(1.7t + 0.4)` recovery: RMSE `1.4183e-05`.
- High-DC R² and sinusoid fitting, positive/negative exponential rate, missing gaps, VFR, tiny-dt, overflow, stale, unavailable, failed, and cancelled terminals have dedicated tests.

### Files Changed
- Engine: `series.py`, `derivatives.py`, `smoothing.py`, `fitting.py`, `residuals.py`, `models.py`, `units.py`, `export.py`, `runtime.py`.
- Tests: new `tests/test_kinematics_*.py` except the unchanged contract test.
- Fixtures/benchmarks: `tests/fixtures/kinematics/**`, `benchmarks/kinematics_fixtures.py`, four `benchmark_kinematics_*.py` programs.
- Evidence: `artifacts/parallel-deepseek-v03/**`.
- Contract, UI/Application, project/schema, package metadata, indexes, old evidence, and media were not modified.

### Tests
- Baseline before implementation: 544 tests, 70.092 s, OK.
- Kinematics targeted: 66 tests, 0.824 s, OK.
- Full branch: 589 tests, 62.188 s, OK (0 FAIL, 0 ERROR).
- `python3 -m compileall -q neo_tracker tests benchmarks`: exit 0.
- `python3 -m pip check`: no broken requirements.

### Performance
- Base series build 100k: median `97.292 ms`, P95 `99.173 ms`, max `99.513 ms`.
- VFR first+second derivative 100k: median `1.887 ms`, P95 `1.949 ms`, max `1.958 ms`.
- SavGol first+second derivative 100k: median `4.067 ms`, P95 `4.618 ms`, max `4.732 ms`.
- Linear+quadratic fits 100k: median `6.841 ms`, P95 `7.014 ms`, max `7.017 ms`.
- 256-frequency sinusoid guess 100k: median `185.682 ms`; cancellation `1.304 ms`.
- End-to-end hot compute pipeline 100k: median `75.939 ms`, P95 `76.867 ms`, max `76.970 ms`.
- End-to-end `timing_ms` excludes the one-time snapshot and exports; their times are reported separately under `stage_timing_ms`.
- CSV 100k: `641.019 ms`; NPZ 100k: `145.208 ms`.
- End-to-end peak RSS: `272.047 MiB`; 5-repeat RSS growth: `7.766 MiB`.
- Maximum end-to-end cancellation latency: `2.853 ms`.

### Public API Compatibility
- `KinematicsEngineRuntime` satisfies contract `SeriesBuilder`, `DerivativeOperator`, and `FitOperator` protocols without Qt/UI dependencies.
- Contract-owned `neo_tracker/kinematics/__init__.py` was intentionally not changed.
- Integration must choose and expose package-root symbols; direct module APIs are stable: `TrackingSeriesBuilder`, `snapshot_tracker_results`, `derive_series`, `smooth_series`, `fit_series`, `residual_series`, `export_csv`, `export_npz`, `export_markdown`, and `KinematicsEngineRuntime`.
- SciPy remains optional: linear/quadratic work without it; nonlinear fits return `UNAVAILABLE` when absent.

### Remaining Risks
- The row-wise immutable 100k snapshot adds approximately 55–78 MiB RSS depending on available state/velocity keys; it is below the 128 MiB snapshot gate but remains a future columnar-optimization candidate.
- Real experiment ground truth has not been run; no claim of broad scientific validation is made.
- Project schema v3 persistence, UI background coordination, and stale-result presentation must be completed by the integration owner.
- Polynomial fits intentionally reject bounds; bounded fitting is implemented for nonlinear models only.

### Integration Notes
- Merge this Engine branch before the Workspace branch.
- Inject calibrated units into `TrackingSeriesBuilder(units=...)`; unknown units intentionally remain empty.
- Call Engine operations on workers for 100k workloads even though recorded compute times are bounded; cancellation and revision checks are already available.
- Export functions atomically replace targets, fsync the parent directory, neutralize spreadsheet formulas, and write NPZ through a binary file object readable with `allow_pickle=False`.
- Evidence and exact result digests are under `artifacts/parallel-deepseek-v03/d8/`.
