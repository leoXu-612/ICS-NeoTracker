# D0 Baseline and Contract Map

- Branch: `feature/ds-kinematics-engine`
- Contract/base SHA: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`
- Baseline full suite: 544 tests in 70.092 s, 0 failures/errors.
- Contract source: `docs/kinematics-contract-v0.3.md`
- Contract files remained read-only: `types.py`, `protocols.py`, `validation.py`, `__init__.py`.

## API mapping

- `TrackerResult.state` → `state:<key>`
- `TrackerResult.filtered_state` → `filtered_state:<key>`
- `TrackerResult.debug.filter.velocity` → `filter_velocity:<key>`
- `TrackerResult.frame_index/time_s/status` → aligned frame/time/mask provenance.
- `KinematicsEngineRuntime` implements `SeriesBuilder`, `DerivativeOperator`, and `FitOperator`.
- Cancellation raises before partial Series/Derivative/Export publication; Fit returns one explicit terminal result.
- No nominal FPS reconstruction or implicit gap interpolation/resampling is used.
Fixture catalog: 14/14 work-order cases (`uniform_linear`,
`uniform_quadratic`, `vfr_linear`, `vfr_quadratic`, `sinusoidal`,
`exponential`, `missing_segments`, `outlier_samples`, `angular_wrap`,
`path_distance`, `constant_series`, `insufficient_samples`,
`duplicate_time`, `very_small_dt`) in `benchmarks/kinematics_fixtures.py`
and `tests/fixtures/kinematics/manifest.json`; covered by
`tests/test_kinematics_fixtures.py` (6 tests).
