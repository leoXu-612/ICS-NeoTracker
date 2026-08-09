# P1-A1 — 数据可信度、输入上限与导出原子性

日期：2026-08-09（Asia/Taipei）
状态：4/4 CLOSED（源码 + 定向测试 + 全量回归闭环）；工单整体保持 `PARTIAL`（真实媒体/无障碍/部署证据缺失）。

方法：每个问题先补能在旧实现上失败的定向测试（红），再做最小生产修改，再重跑（绿），并记录 source → sink、失败前/修复后状态与反证。

## A. 大媒体 sampled identity 被当作精确匹配（MEDIUM）

### Source → Sink

来源：`media.probe_media_identity()` 对 >768 KiB 文件只摘要开头/中段/结尾各 256 KiB（`sampled-sha256-v1`）→ 项目快照持久化 `MediaIdentity` → 打开/Relink/追踪前 `ProjectTaskController.assess_media_relink()` 用 sampled digest 比较 → UI 决策（保留或清除 Results/Edits）。

### 失败前状态

真实复现（本机重跑，2,000,044 字节 WAV，在 600 KiB 偏移写入 `0x7FFF`，位于采样窗口之外）：

```text
identity_a == identity_b: True | strategy: sampled-sha256-v1 | sampled_bytes: 786432
probe available: True True
decoded samples at modified offset: a = [0.0, 0.0] | b = [0.999969482421875, 0.0]
assessment.state: match | identity_state: sampled
assessment.clear_results: False   ← 旧行为：静默保留旧 Results/Edits 绑定新内容
```

### 修复后状态

```text
assessment.state: match | identity_state: sampled
assessment.clear_results: True
assessment.requires_review: True
differences: ('Sampled digest covers 786,432 of 2,000,044 bytes; exact byte equality is not established.',)
empty-task clear_results: False | requires_review: False
```

### 修改

- `MediaRelinkAssessment.requires_review` 属性：sampled match 且存在结果状态时强制审查（打开、追踪前、来源漂移隔离三个调用点统一使用）。
- `assess_media_relink()`：sampled match + `has_result_state` → `clear_results=True` 并在 differences 中写明采样覆盖范围与“未建立逐字节相等”。
- `media_relink_panel`：区分标题 `Source verified`（full）与 `Sampled identity match`（sampled），Apply 按钮显示 `Relink + Clear Results/Edits`。

### 反证

- full identity match：仍 `clear_results=False`、`requires_review=False`（精确匹配不受影响）。
- 无结果状态的 sampled match：不要求审查、不清理（无可丢失数据）。
- 未在 GUI 主线程做全文件 hash；无每帧重复 hash 热路径。

## B. task-level ROI 绕过 4096 点上限（MEDIUM）

### Source → Sink

来源：`apply_roi_config_to_task()`（task 级 ROI 复制并同步 Qt 表格）→ `validate_roi_config()` 只查最小点数/有限数值 → 4097 点 polygon/curve_band 可绕过 `_points()` 的 `MAX_ROI_POINTS=4096`。

### 失败前状态

`validate_roi_config(polygon_4097)` 返回 `None`；`apply_roi_config_to_task(task, polygon_4097)` 返回 `True` 并全量填充 task/pipeline。

### 修复后状态

- `validate_roi_config()` 对 polygon / curve_band 增加 `len(points) > MAX_ROI_POINTS` 拒绝（4096 允许、4097 拒绝，稳定错误文案）。
- `apply_roi_config_to_task()` 先校验后变更：4097 返回 `False`，task.roi 与 pipeline.roi 均保持原值，无部分应用。

### 反证

- 4096 点 polygon 仍可应用；`apply_pipeline_config`（`_points()` 路径）行为不变。
- 定向测试红/绿：`test_validate_roi_config_enforces_max_roi_points`、`test_apply_roi_config_rejects_oversized_polygon_without_partial_application`。

## C. WAV channel metadata 驱动无界 UI/解码放大（MEDIUM）

### Source → Sink

来源：`.wav` 头（stdlib `wave` 直接 probe，无 isolated-media 通道上限）→ `probe_wav_media()` 的 `MediaInfo.channels` → `available_sources()` 每通道建一个 AnalysisSource → `wav_signal_series()` 按 `frames × channels` 解码，chunk 固定 1,048,576 frames，workload gate 只按 frame count。

### 失败前状态

- 手写头 `channels=65535`：`probe_wav_media` 返回 `available=True`；`available_sources` 创建 65,536 个对象；`wav_signal_series` 不拒绝。
- `sample_width=8`（位深 64）：不拒绝。
- 16 通道 2 字节采样：chunk 请求 600,000 frames（固定上限 1,048,576 未随通道收缩）。

### 修复后状态

- `media.validate_wav_header()`：channels ∈ [1,64]、sample_width ∈ [1,4]、sample_rate ∈ (0,1 MHz]、frame_count ≤ 2^26、`frames × channels × sample_width ≤ 512 MiB`；probe 与 `wav_signal_series` 共用，损坏/极端头返回 `available=False` 或 `ValueError`，不再标记可用。
- `wav_signal_series`：chunk 大小按 `16 MiB // (channels × sample_width)` 收缩。
- `available_sources`：通道循环按 `min(channels, MAX_WAV_CHANNELS)` 封顶。

### 反证

- 正常 4 通道 WAV 解码语义不变；16 通道 chunk 请求 ≤ 524,288 frames（`test_wav_signal_series_chunk_scales_with_channel_count`）。
- 极端头拒绝测试：`test_wav_probe_rejects_extreme_channel_or_size_metadata`、`test_wav_signal_series_rejects_extreme_channel_metadata`、`test_audio_sources_are_capped_at_max_channels`。
- 注意：stdlib `wave` 将位深字段按 `(bits+7)//8` 转字节宽，测试头按位深构造。

## D. 非 `.npz` 目标发布空文件并遗留真实 sidecar（LOW）

### Source → Sink

来源：`analysis.write_fft_npz()/write_stft_npz()` → `atomic_output_path()` 临时文件沿用用户后缀 → `np.savez(path)` 对非 `.npz` 路径自动追加 `.npz` → `os.replace` 发布空临时文件，真实 archive 留在隐藏 sidecar。

### 失败前状态

目标 `export`（无后缀）或 `archive.dat`：目标文件为空（0 字节），`.<name>.*.npz` sidecar 残留真实 archive；失败路径下 sidecar 也不清理。

### 修复后状态

`write_fft_npz()/write_stft_npz()` 改为把 `atomic_output_path` 临时文件作为二进制 file object 交给 `np.savez`，不再依赖路径后缀：

```python
with atomic_output_path(path) as temporary_path:
    with temporary_path.open("wb") as handle:
        np.savez(handle, ...)
```

### 反证

- `.npz`、无后缀、其他后缀三种目标：内容可 `np.load(allow_pickle=False)` 加载，目录无隐藏 sidecar。
- 失败路径（savez 抛错）：原目标文件字节保持不变，无 sidecar 残留。
- 定向测试：`test_npz_export_atomic_for_all_suffixes`、`test_npz_write_failure_cleans_sidecars_for_all_suffixes`、`test_npz_write_failure_preserves_existing_export`。

## 红/绿证据

- 红（旧实现）：23 项失败/错误，覆盖 A/B/C/D 全部入口（见本包执行记录）。
- 绿（修复后）：定向 89 项 OK；全量 449 项 OK（69.750 s）；compileall OK；pip check OK。

## Benchmark（验收约束 0.3.4：2 轮 × 5 次）

原始 JSON：`benchmark-round1.json`、`benchmark-round2.json`（首行为 Qt 字体告警，数据从 `[` 起）。

| 轮次 | heartbeat 逐值 (ms) | median | p95 | max | >75ms 次数 | apply median/max | payload_equal |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 38.26 / 50.04 / 51.15 / 47.84 / 48.53 | 48.53 | 50.04 | 51.15 | 0 | 46.16 / 47.54 | True |
| 2 | 35.32 / 48.78 / 48.61 / 47.15 / 47.56 | 47.56 | 48.61 | 48.78 | 0 | 44.38 / 46.62 | True |

- `max_gap_phase_before/after` 均为 `Opening project · verifying the saved baseline…` → `…applying the prepared workspace…`；fingerprint 不变（`d9c2dd59…`）。
- 退出码：两轮均 0（门槛 75 ms）。
- 限制：本机两轮未超门槛，但 Codex 独立复核曾出现 153.45 ms 单次（退出码 1）；波动可能来自同机负载，尚未被完全归因，因此不宣称 heartbeat 稳定性已证明，任务保持 `PARTIAL`。
