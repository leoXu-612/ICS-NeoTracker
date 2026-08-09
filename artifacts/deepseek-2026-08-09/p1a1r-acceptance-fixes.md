# P1-A1R — 验收修正（ROI fail-closed、WAV 解码预检、合法 JSON 基准）

日期：2026-08-09（Asia/Taipei）
状态：3/3 CLOSED；整体任务保持 `IN_PROGRESS` / `PARTIAL`。

方法：针对 Codex 验收列出的 3 个缺口，先在 `41b8deb` 上补失败测试（红），再做最小修复（绿），并保留历史证据可追溯。

## A. task-level ROI 项目载入必须 fail closed

### 失败前状态（在 `41b8deb` 上复现）

`task_from_snapshot()` 第 505 行 `self.apply_roi_config_to_task(task, snapshot.roi)` 忽略返回值：4097 点 task-level ROI 被 `validate_roi_config()` 拒绝（返回 `False`），但项目仍载入、ROI 静默回退，不产生稳定错误。

### 修复后状态

```python
if snapshot.roi:
    roi_error = validate_roi_config(snapshot.roi)
    if roi_error is not None:
        raise ValueError(f"project task $.roi is invalid: {roi_error}")
    if not self.apply_roi_config_to_task(task, snapshot.roi):
        raise ValueError("project task $.roi could not be applied")
```

- 4096 点 polygon / curve_band 允许并应用；4097 点拒绝。
- 错误带字段路径 `project task $.roi is invalid: …`。
- 打开 worker 在任务载入前失败 → 当前已打开项目不被部分替换（worker 原子切换契约不变）。
- 旧测试 `test_invalid_optional_roi_and_calibration_are_ignored` 改为 `test_invalid_snapshot_roi_fails_closed_and_invalid_calibration_ignored`：无效 snapshot ROI 现在拒绝；无效 calibration 仍保持忽略语义。

### 反证与测试

- 红：`test_snapshot_open_rejects_oversized_task_roi_fail_closed`（polygon/curve_band 各 4097/4096）、`test_open_rejects_oversized_task_roi_fail_closed`（worker 报错含 `roi`）。
- 绿：上述测试 + 全量回归通过。

## B. WAV 分析预检携带并计算解码成本

### 失败前状态

`AnalysisWorker` 预检只调用 `validate_analysis_sample_count(sample_count)`；`AnalysisSource` 不携带 sample width/decoded bytes，`frames × channels × sample_width` 的多通道解码成本未在加载前校验（header 与 16 MiB block 上限已生效，但预检项未闭环）。

### 修复后状态

1. `MediaInfo` 新增 `sample_width_bytes`，`probe_wav_media()` 填充，并随项目快照持久化（`media_info_to_dict` / `media_info_from_snapshot`）。
2. `AnalysisSource` 新增 `decoded_source_bytes`（`to_data` / `from_data` 同步）；`available_sources()` 按 `frame_count × 封顶 channels × sample_width_bytes` 计算并附到 mono/各 channel 源。
3. `analysis.validate_wav_decode_workload(decoded_source_bytes)`（上限 512 MiB，与 `validate_wav_header` 同源常量）。
4. `AnalysisWorker.run()` 在 `series_loader` 调用、NumPy 分配与长时读取前先校验 `decoded_source_bytes`（>0 时），再校验分析工作集。
5. `wav_signal_series()` 仍在实际 `wave.open()` 后重新校验 header（probe 与读取之间来源替换防护），512 MiB 总量与 16 MiB block 上限未移除。

### 反证与测试

- 红：`test_worker_rejects_wav_decode_cost_before_loading`、`test_worker_preflight_differentiates_decode_cost_with_same_frame_count`（相同 frame count、不同 decode bytes → 预检结果不同，超限时 loader 未被调用）、`test_audio_sources_carry_decoded_source_bytes`（`1000 × 2 × 2`）。
- 绿：以上测试 + 全量回归通过；`wav_signal_series` 通道相关 chunk 上限测试继续通过。

## C. 性能证据必须是合法 JSON

### 失败前状态

`benchmark-round1.json` / `benchmark-round2.json` 首行混入 Qt stderr 字体告警，`python3 -m json.tool` 无法解析。

### 修复后状态

重新生成两轮各 5 次，stdout 与 stderr 分离：

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 5 --max-heartbeat-ms 75 \
  > artifacts/deepseek-2026-08-09/benchmark-p1a1r-round1.json \
  2> artifacts/deepseek-2026-08-09/benchmark-p1a1r-round1.stderr.txt
```

`python3 -m json.tool` 两个 `.json` 均通过；stderr 内容单独存 `.stderr.txt`。

| 轮次 | heartbeat 逐值 (ms) | median | p95 | max | >75ms | apply 逐值 | payload_equal |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 36.91 / 45.29 / 47.79 / 58.87 / 54.41 | 47.79 | 54.41 | 58.87 | 0 | 34.90/43.24/45.68/56.89/51.29 | True |
| 2 | 33.49 / 47.48 / 45.75 / 43.76 / 47.95 | 45.75 | 47.48 | 47.95 | 0 | 31.59/44.05/43.74/41.44/45.86 | True |

- 退出码两轮均 0；`max_gap_phase_before/after` 均为 `Opening project · verifying the saved baseline…` → `…applying the prepared workspace…`；fingerprint `d9c2dd5992cc…` 不变。
- 历史记录保留：Codex 独立复核曾出现 153.45 ms 单次超限（历史超限、本轮结果），未删除或改写；DeepSeek 与 Codex 多轮均未复现，但波动来源未完全归因，故不宣称跨负载稳定性。

## 全量验证

```text
定向：44 tests OK（project_controller / project_open_worker / analysis_worker / analysis_controller）
全量：454 tests in 60.192s OK
compileall：退出码 0
pip check：No broken requirements found
残留：无项目进程、无新 .ips
```

## 限制

- 本次只关闭验收 1–3；真实长时媒体（P0-B）、原生无障碍（P1-B）、部署（P2）证据仍缺。
- `AnalysisSource.decoded_source_bytes` 为预检防护；实际解码仍以 `wav_signal_series` 的 header 复验为准。
