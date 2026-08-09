# D0–D8 Engine Acceptance

## Status

`ENGINE_READY`

## Verification

```text
Baseline: 544 tests / 70.092 s / OK
Kinematics targeted: 65 tests / 0.771 s / OK
Full branch: 588 tests / 63.116 s / OK
compileall: exit 0
pip check: No broken requirements found.
100k series benchmark: passed=true, exit_code=0
100k derivative benchmark: passed=true, exit_code=0
100k fit benchmark: passed=true, exit_code=0
100k end-to-end benchmark: passed=true, exit_code=0
```

The full suite contains every baseline test plus 44 new engine tests. Contract
files (`types.py`, `protocols.py`, `validation.py`, `__init__.py`) have no diff
from `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`.

## Scope boundary

- Synthetic correctness: CLOSED.
- VFR and missing-gap semantics: CLOSED.
- Safe export: CLOSED.
- 100k engine performance: CLOSED against recorded gates.
- Real scientific dataset validation: PARTIAL (not executed on this branch).
- Project schema and UI integration: integration-owner work, not modified here.
