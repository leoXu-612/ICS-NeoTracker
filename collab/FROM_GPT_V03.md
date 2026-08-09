# GPT → Integration — Analysis Workspace v0.3

更新时间：2026-08-10（Asia/Taipei）

状态：`WORKSPACE_READY`

## Conclusion

GPT 的 G0–G7 Analysis Workspace 线已完成：Data/Plot/Fit、统一 SelectionSession、后台 Fit coordinator、Inspector/Action Registry、1024/HiDPI/键盘/AX 文本与 100k UI 门禁均通过；不得把此状态解释为 Engine 或 Integration 已完成。

## Branch and commits

- Worktree: `/Users/leo.xu/Desktop/Codex/ICS-Project-/_worktrees/ICS-NeoTracker-analysis-gpt`
- Branch: `feature/gpt-analysis-workspace`
- Required base: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`
- Clean code head before this evidence commit: `ef55e43`
- 17 logical/support commits from G0 through the final plot performance and whitespace fixes; do not squash away boundary/cancellation regressions during integration.

Key commit sequence:

```text
f6f41c6 docs(ui): map physics workspace baseline and design
9a65d14 feat(app): add unified selection session for physics analysis
d0b0fa8 feat(ui): add virtualized physical series table
0a6d8d1 feat(ui): add bounded interactive physics plot
06a467c feat(ui): introduce bottom physics data workspace
4034e34 feat(ui): mount physics workspace in application shell
f07e447 fix(ui): publish one canonical plot selection
8ce5063 feat(app): coordinate background kinematics and fitting jobs
ed5a586 feat(ui): add physics fit request and result panel
e74c5ad feat(ui): connect background fits to physics workspace
e19853d feat(ui): add engine action requests and physics inspector
6efb965 fix(ui): reject ambiguous analysis results
00aa065 feat(ui): unify physics actions and inspection
a3c3c36 test(ui): harden physics workspace accessibility
36a98b8 refactor(ui): keep physics integration within shell bounds
30c8268 perf(ui): batch dense physics plot traces
ef55e43 style(ui): normalize physics shell whitespace
```

## Integration API

1. Call `NeoTrackerWindow.set_physics_series(series, owner=task, result_identity=...)` with immutable `SampleSeries` values from one source revision. Duplicate `series_id`, mixed revision, and stale owner inputs are rejected.
2. Inject an Engine `FitOperator` through `set_kinematics_fit_operator(...)`. The Application coordinator owns QThread/cancel/stale/close; no QWidget performs fitting.
3. Connect `NeoTrackerWindow.physicsOperationRequested` to the engine/application service. Requests are immutable and identify operation, series, source revision, and explicit configuration:
   - derivative order 1/2 with nonuniform/gap-split policy;
   - Savitzky–Golay smoothing with no implicit resampling;
   - CSV/safe-NPZ/Markdown export with the successful fit snapshot.
4. Replace the branch-local task/result identity adapters with Integration's stable `task_id` and `results_generation`. Do not weaken `source_revision` checks or SelectionSession's single-revision broadcast invariant.

## Validation

- Final full suite: **594 tests / 66.022 s / 0 FAIL / 0 ERROR**.
- Compileall: exit 0.
- Pip check: no broken requirements.
- 100k project-open background ×3: heartbeat max 34.847–46.265 ms; payload/fingerprint equal.
- 100k UI: table attach P95 0.0028 ms; plot prepare P95 4.002 ms; paint P95 28.047 ms; paint-loop heartbeat max 31.954 ms; 2,427 prepared points; zero cell widgets; 96.33 MiB peak RSS; zero helpers.
- G7: 1024×768, 1280×808, 1440×900, Retina 2×, Tab/Shift+Tab, plot keyboard, sync, focus/collapse/restore, close/unsubscribe, readable error/validity/provenance states.

Raw evidence:

- `artifacts/parallel-gpt-v03/g0/ui-state-map.md`
- `artifacts/parallel-gpt-v03/g0/current-layout.md`
- `artifacts/parallel-gpt-v03/g7/100k-project-open.json`
- `artifacts/parallel-gpt-v03/g7/100k-ui-benchmark.json`
- `artifacts/parallel-gpt-v03/g7/workspace-validation.md`

## Remaining integration risks

- Real derivative/smoothing/export execution is not wired here; this branch emits engine-owned requests only.
- Stable task IDs/results generations and project v3 persistence belong to Integration.
- Non-OK fit results are intentionally text-only and never enable plot/residual/export.
- Offscreen HiDPI/AX-text evidence is not native VoiceOver or system text-scaling evidence.
- No project/schema/engine/contract/index/README/video files were modified by this line.

## Status

`WORKSPACE_READY` — yes.

`ENGINE_READY` — not asserted by GPT.

`MERGE_READY` — not asserted; Integration must run the merged contract/engine/schema/export/real-media matrix.
