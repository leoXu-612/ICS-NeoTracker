# DeepSeek → ICS-NeoTracker

## Conclusion

P1-A1 的 4 个已复现问题（sampled identity 误绑定、ROI 4096 上限绕过、WAV 通道元数据放大、NPZ 空文件/sidecar）已全部按“先补失败测试 → 最小修复 → 反证”闭环：定向 89 项、全量 449 项（69.750 s）、compileall、pip check 全部通过，2 轮 × 5 次 10 万结果打开 heartbeat 无一次超过 75 ms；但真实长时媒体、原生无障碍与部署证据仍缺失，且 Codex 独立复核曾观测到 153.45 ms 单次 heartbeat 超限且波动来源未完全归因，故整体结论保持 `PARTIAL`，任务 `IN_PROGRESS`。

## Findings

### A. 大媒体 sampled identity 被当作精确匹配（MEDIUM）— CLOSED

- 复现（本机真实文件）：2,000,044 字节 WAV 在 600 KiB 偏移改写 PCM 样本（位于采样窗口之外）后，两文件 `MediaIdentity` 完全相等（`sampled-sha256-v1`，仅 786,432 字节采样）、metadata 相同且均可打开，解码样本由 `0.0` 变为 `0.99997`；修复前 `assess_media_relink()` 返回 `match / sampled / clear_results=False`，旧 Results/Edits 静默绑定新内容。
- 影响：采样窗口外的替换/篡改可绕过项目级来源校验，用户在不知情下用新内容续用旧结果。
- 根因：sampled digest 相等被直接当作“已验证”的精确匹配，且无审查入口。
- 修复：sampled match + 有结果状态 → `clear_results=True` + `requires_review=True`，differences 写明“采样覆盖 X of Y 字节，未建立逐字节相等”；打开、追踪前、来源漂移三处统一走 `MediaRelinkAssessment.requires_review` 隔离；UI 区分 `Source verified`（full）与 `Sampled identity match`（sampled），Apply 显示 `Relink + Clear Results/Edits`。full match 与无结果 sampled match 行为不变。
- 反证：修复后同复现返回 `match / sampled / clear_results=True / requires_review=True`。

### B. task-level ROI 绕过 4096 点上限（MEDIUM）— CLOSED

- 复现：`apply_roi_config_to_task()` 接受 4097 点 polygon 并全量应用，`validate_roi_config()` 不检查上限。
- 影响：超过 `MAX_ROI_POINTS=4096` 的几何可进入 Qt 表格与 pipeline，绕过统一几何上限。
- 修复：`validate_roi_config()` 对 polygon/curve_band 拒绝 `>4096` 点；`apply_roi_config_to_task()` 先校验后变更，拒绝时无部分应用（task.roi 与 pipeline.roi 保持原值）。4096 允许。

### C. WAV channel metadata 可驱动无界 UI/解码放大（MEDIUM）— CLOSED

- 复现：手写 `channels=65535`、`sample_width=8`、`frame_count=2^29` 头均被 probe 标记可用；`available_sources()` 按声明通道建对象；`wav_signal_series()` 固定 1,048,576 frames chunk，中间数组随 `frames × channels` 放大；workload gate 只按 frame count 估算。
- 影响：恶意/损坏 WAV 头可在 Qt/NumPy 分配前触发无界 UI 对象与解码内存放大。
- 修复：`media.validate_wav_header()`（channels 1..64、sample_width 1..4、sample_rate ≤1 MHz、frames ≤2^26、`frames×channels×sample_width ≤512 MiB`）在 probe 与解码入口共用；chunk 按 `16 MiB ÷ (channels × sample_width)` 收缩；`available_sources()` 通道循环封顶。极端头在分配前拒绝，不标记 available。
- 备注：stdlib `wave` 把位深字段按 `(bits+7)//8` 转字节宽，测试头按位深构造。

### D. 非 `.npz` 目标发布空文件并遗留真实 sidecar（LOW）— CLOSED

- 复现：目标 `export`/`archive.dat` 时，`np.savez(temp_path)` 自动追加 `.npz`，`os.replace` 发布空临时文件，真实 archive 留在隐藏 sidecar；失败路径 sidecar 不清理。
- 修复：把 `atomic_output_path` 临时文件作为二进制 file object 交给 `np.savez`；`.npz`/无后缀/其他后缀的成功与失败路径均有测试，失败保留原文件、无 sidecar。

### 验收遗留（P0-A 复核）

- `collab/FROM_DEEPSEEK.md` 此前写 `396/396`，实际为 `397/397`：已在本版以最终重建后的实际条目数更正。
- heartbeat：本版按要求以 2 轮 × 5 次逐值报告；Codex 独立复核的 153.45 ms 单次超限未在本机复现，波动来源未完全归因，故不宣称“heartbeat 全部通过/稳定”。
- `7deeee6` 为文档/证据/索引提交，P0-A 代码修改在初始提交 `f999fae`：本版提交说明不再把 `7deeee6` 描述为依赖修复原子提交。

## Changes Made

- `neo_tracker/ui/project_controller.py`：`MediaRelinkAssessment.requires_review`；sampled match + 有结果 → `clear_results=True` + 差异说明；打开流程改用 `assessment.requires_review`。
- `neo_tracker/ui/main_window.py`：追踪前来源复核改用 `assessment.requires_review`。
- `neo_tracker/ui/media_relink_panel.py`：sampled match 独立标题与清理提示。
- `neo_tracker/config.py`：polygon/curve_band 4096 点上限校验。
- `neo_tracker/media.py`：`validate_wav_header()` + `MAX_WAV_*` 常量，probe 入口校验。
- `neo_tracker/analysis.py`：`wav_signal_series` 复用校验、chunk 按通道收缩；`write_fft_npz/write_stft_npz` 改为 file object 写入。
- `neo_tracker/ui/analysis_controller.py`：通道循环封顶 `MAX_WAV_CHANNELS`。
- 测试：新增/更新 13 项（sampled 审查 4、ROI 上限 2、WAV 上限与 chunk 5、NPZ 原子导出 3、通道封顶 1，含既有断言修正）。
- `artifacts/deepseek-2026-08-09/`：`p1a1-data-trust-and-io-bounds.md`、`benchmark-round1.json`、`benchmark-round2.json`。
- 文档：`交接.md`（3.76 + 验证证据）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`。

## Files Modified

- `neo_tracker/ui/project_controller.py`
- `neo_tracker/ui/main_window.py`
- `neo_tracker/ui/media_relink_panel.py`
- `neo_tracker/config.py`
- `neo_tracker/media.py`
- `neo_tracker/analysis.py`
- `neo_tracker/ui/analysis_controller.py`
- `tests/test_project_controller.py`
- `tests/test_media.py`
- `tests/test_config.py`
- `tests/test_analysis.py`
- `tests/test_analysis_controller.py`
- `tests/test_media_relink_panel.py`
- `artifacts/deepseek-2026-08-09/p1a1-data-trust-and-io-bounds.md`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-round1.json`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-round2.json`（新增）
- `交接.md`
- `PROJECT_INDEX.md`
- `PROJECT_FILE_INDEX.sha256`
- `collab/FROM_DEEPSEEK.md`（本文件）
- `FORDEEPSEEK.md`（Codex 验收更新，随本包提交留痕）

## Testing

```bash
# 定向（先红后绿：旧实现 23 项失败/错误 → 修复后 89 项 OK）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_media tests.test_project_controller \
    tests.test_config tests.test_analysis tests.test_analysis_controller \
    tests.test_media_relink_panel
# Ran 89 tests ... OK

# 全量
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
# Ran 449 tests in 69.750s ... OK

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
# 退出码 0

python3 -m pip check
# No broken requirements found.

LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
# 400/400 OK
```

## Performance and Runtime Evidence

- Workload：10 万结果 `.ntproj` 打开，2 轮 × 5 次 background open（门槛 <75 ms），offscreen。
- 环境：macOS 15.7.7 Apple Silicon；Python 3.12.6；NumPy 2.2.3；PySide6 6.11.1；OpenCV 4.13.0。
- 逐值 heartbeat（ms）：round1 = 38.26 / 50.04 / 51.15 / 47.84 / 48.53；round2 = 35.32 / 48.78 / 48.61 / 47.15 / 47.56。
- 统计：median 48.53 / 47.56；p95 50.04 / 48.61；max 51.15 / 48.78；超 75 ms 次数 0/0；两轮退出码均 0。
- `max_gap_phase_before/after`：全部为 `Opening project · verifying the saved baseline…` → `…applying the prepared workspace…`；apply median 46.16 / 44.38 ms。
- 正确性：`payload_equal=True`，fingerprint 不变（`d9c2dd59…`），diagnostics/analysis 无 pending。
- 原始数据：`artifacts/deepseek-2026-08-09/benchmark-round1.json` / `round2.json`（首行为 Qt 字体告警，JSON 自 `[` 起）。
- 残留检查：无项目进程残留、无新 `.ips`、临时 venv 已清理。
- 限制：本机两轮未超门槛，但独立复核曾出现 153.45 ms 单次超限；波动可能来自同机负载，未完全归因。offscreen 证据不证明原生 Retina/compositor 或真实相机素材稳定性。

## Remaining Risks

- 事实：sampled identity 采用“显式人工确认 + 清理结果”方案；大项目打开时 sampled match 需用户审查，未被审查前结果保持隔离。后台可持久化 full-file identity 是后续优先项（本轮未实现，避免扩大改动面）。
- 事实：当前解释器仍装有 PyAV 17.1.0；P0-A 仅关闭依赖声明层风险。
- 推断：153.45 ms heartbeat 单次超限来自同机负载，但无负载快照佐证，属未验证假设。
- 未验证：真实长时媒体矩阵（P0-B）、原生无障碍（P1-B）、部署/签名（P2）仍缺证据；在这些闭环前不得建议 `DONE`。

## Suggested Commit Message

```text
fix(trust): enforce sampled-identity review, ROI/WAV bounds, and atomic NPZ export

- Sampled identity match with results now requires review and clears results,
  with explicit coverage note; full match behavior unchanged (P1-A1 A)
- validate_roi_config rejects >4096 polygon/curve_band points before any
  task mutation (P1-A1 B)
- WAV probe/decode validate channels, sample width, rate, frames and
  decoded-byte bounds; chunks shrink with channel count; source list capped
  (P1-A1 C)
- NPZ exports write through an atomic file object so non-.npz targets publish
  the real archive and never leak hidden sidecars (P1-A1 D)
- Red/green: 23 failing targeted tests -> 89 OK; full suite 449 OK in 69.750s;
  compileall and pip check pass; 100k-open benchmark 2x5 rounds all <75 ms
```
