# Kinematics Accuracy Summary

All values below come from deterministic analytic fixtures and strict JSON benchmark outputs.

| Gate | Maximum absolute error | Result |
| --- | ---: | --- |
| 100k VFR finite difference | 0.000009432842432044453 | PASS |
| 100k Savitzky–Golay derivative | 8.583068846768072e-7 | PASS |
| 100k linear parameters | 6.366462912410498e-12 | PASS |
| 100k quadratic parameters | 1.1641532182693481e-10 | PASS |
| Exponential, no initial parameters | 2.233768725545815e-13 | PASS |
| Sinusoidal, no initial parameters | 1.687538997430238e-14 | PASS |
| End-to-end 100k maximum | 2.2737367544343885e-9 | PASS |

Missing segments, VFR, positive/negative exponential rate, angular units, large-DC R²,
overflow, stale revision, nonconvergence, cancellation, and optional-SciPy absence
are covered by deterministic unit tests.
