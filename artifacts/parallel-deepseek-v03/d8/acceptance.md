# D0–D8 Engine Acceptance

## Status

`ENGINE_READY`

## Verification

```text
Baseline: 544 tests / 70.092 s / OK
Kinematics targeted: 66 tests / 0.824 s / OK
Full branch: 589 tests / 62.188 s / OK
compileall: exit 0
pip check: No broken requirements found.
100k series benchmark: passed=true, exit_code=0
100k derivative benchmark: passed=true, exit_code=0
100k fit benchmark: passed=true, exit_code=0
100k end-to-end benchmark: passed=true, exit_code=0
```

The full suite contains every baseline test plus 45 new engine tests. Contract
files (`types.py`, `protocols.py`, `validation.py`, `__init__.py`) have no diff
from `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`.

The post-readiness adversarial audit is closed: no-initial sinusoid fitting now
centers both frequency-candidate scoring and the nonlinear objective before
restoring the physical offset. Dedicated `1e9` and `1e12` DC-offset cases pass;
the 100k 256-candidate scan remains below its 5 s gate at 185.682 ms median and
1.304 ms cancellation latency.

## Scope boundary

- Synthetic correctness: CLOSED.
- VFR and missing-gap semantics: CLOSED.
- Safe export: CLOSED.
- 100k engine performance: CLOSED against recorded gates.
- Large-DC sinusoid stability: CLOSED for offsets through `1e12` in the
  adversarial test matrix.
- Real scientific dataset validation: PARTIAL (not executed on this branch).
- Project schema and UI integration: integration-owner work, not modified here.
