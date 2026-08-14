# DeepSeek → ICS-NeoTracker（V04-CONFIG-RESULT-PROTECTION）

## 结论

在基线 `1ad69c976ee3cf9d4c869d5697ed73f3deb80d1f` 上新建独立 worktree / 分支
`feature/ds-config-result-protection-v04`，实现“配置变更前保护当前结果”闭环并原子提交
`fix(ui): protect results before configuration changes`。任务状态：`READY_FOR_CODEX_REVIEW`。

保护覆盖 preset、Apply JSON、ROI apply/draw/commit/reset、calibration apply/reset、
marker color/tolerance/candidate limit 共 9 个失效入口；拒绝路径零永久 mutation（默认按钮、
Escape、关闭均安全取消，拒绝后控件经 signal blocker 恢复、草稿保留），接受路径只失效一次、
Runs 保留、result generation 只递增一次，同值/no-op 不提示不失效。全量 692 tests
（124.373 s）、定向 162 tests（61.680 s）、compileall、pip check、`git diff --check`
全部通过；工作树 clean，main 未改动。

## 修改文件

- `neo_tracker/ui/main_window.py`：新增局部保护入口 `_confirm_config_result_replacement()`，
  并为 9 个配置失效入口前置确认；marker 三个数字控件关闭 keyboard tracking。
- `neo_tracker/ui/project_status_panel.py`：新增 `build_config_result_protection_dialog()`
  （复用 Full Run 对话框风格，默认/Escape 均为“Keep Current Results”）。
- `tests/test_ui_main_window.py`：新增 `ConfigResultProtectionTests`（9 项），并给 7 个既有
  验收用例注入 `_confirm_config_result_replacement = True` 以保留其“接受”语义。
- `tests/test_project_status_panel.py`：新增对话框结构测试（2 项）。
- `collab/FROM_DEEPSEEK.md`：本交付报告。

## 各入口的保护方式

| 入口 | 触发方法 | 保护与拒绝行为 |
| --- | --- | --- |
| preset 切换 | `_preset_changed` | 先收齐“丢弃草稿”与“替换结果”两个肯定答复再 mutation；任一取消都恢复 combo、保留草稿/pipeline |
| Apply JSON | `_apply_advanced_config` | 解析后同值/no-op 直接返回；否则确认，拒绝不改 pipeline/results/edits |
| ROI draw/commit | `_commit_roi_config` | 同值/no-op 不清结果；否则确认，拒绝不 apply、不清结果 |
| ROI reset | `_reset_roi_to_preset` | 已为 preset（`task.roi is None`）即 no-op；否则确认，拒绝保持原 ROI |
| calibration apply | `_apply_calibration_rod` | 同值/no-op 直接返回；否则确认，拒绝不 apply |
| calibration reset | `_reset_calibration` | 无有效 rod 即 no-op；否则确认，拒绝保持原 rod/坐标 |
| marker color | `_color_sample_selected` | 同色/no-op 直接返回；否则确认，拒绝不写入 sample_rgb |
| marker tolerance | `_color_tolerance_changed` | 同值/no-op 直接返回；拒绝时 `_render_marker_controls` 恢复 spin |
| marker candidates | `_color_candidate_settings_changed` | 同值/no-op 直接返回；拒绝时 `_render_marker_controls` 恢复两个 spin |

保护条件统一复用既有 `_task_has_tracking_result_state()`（Results / Edits /
tracking_outcome / tracking_note 任一存在即触发；完全空状态不触发）。拒绝路径在
`_confirm_config_result_replacement()` 内统一给出状态栏提示，不写 task 配置、不替换
pipeline、不清 Results/Edits、不递增 generation、不标 dirty。marker 数字控件已
`setKeyboardTracking(False)`，只在 Enter/焦点离开时提交一次，避免逐字符重复弹窗。

## 测试结果

```bash
# 定向（§6 命令）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_project_status_panel tests.test_ui_main_window -q
# Ran 162 tests in 61.680s ... OK（exit 0）

# 全量
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
# Ran 692 tests in 124.373s ... OK（exit 0）

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests   # exit 0
python3 -m pip check                                                  # No broken requirements found.
git diff --check 1ad69c976ee3cf9d4c869d5697ed73f3deb80d1f..HEAD        # 无空白告警
git status --short                                                    # 空（clean）
```

新增测试 11 项：对话框默认/Escape/accessible 安全取消（2）、全入口拒绝零 mutation 表驱动（1）、
marker 拒绝恢复控件值且单次提交单次确认（1）、marker keyboard tracking 关闭（1）、
接受仅失效一次且 Runs 保留（1）、result-only/edit-only/outcome-only 触发而空状态不触发（1）、
同值/no-op 不提示不失效（1）、preset 多确认顺序（2，含“先接受丢弃草稿后拒绝结果仍保留草稿”）。

## 未解决风险与范围外事项

- 保护入口只覆盖现有失效 handler；未新增配置功能、未重构无关 handler（按 §3.B 边界）。
- 对话框默认/Escape/关闭安全取消通过对话框结构测试与 `_confirm_config_result_replacement`
  fail-closed 逻辑证明；未做真实原生窗口的手动模态点击验证（offscreen 测试注入代替）。
- marker 拒绝恢复依赖 `_render_marker_controls`（无信号递归，内部已 blockSignals）。
- 本工单不改 `PROJECT_FILE_INDEX.sha256`、README、路线文档与 `build/lib/`；索引与路线
  文档由 Codex 最终集成时统一处理。
- main_window.py 行数控制在 7000 以下（6997），满足应用壳架构守卫。

## Suggested Commit Message

```text
fix(ui): protect results before configuration changes

- Add a fail-closed confirmation gate before any configuration change
  (preset, JSON, ROI, calibration, marker) would invalidate current
  Results/Edits; default/Escape/close keep results and cancel the change
- Reject path performs zero mutation and restores transient widget values
  via signal blockers; preset collects both draft-discard and result-replace
  answers before mutating; marker spins disable keyboard tracking
- Same-value/no-op changes neither prompt nor invalidate; accept path
  invalidates once, preserves Runs, and increments generation once
- Tests: 11 new protection tests (162 targeted / 692 full, all green),
  compileall and pip check pass, git diff --check clean
```
