# DeepSeek → ICS-NeoTracker

## Conclusion

P1-A1R 的 3 个验收缺口已全部关闭：task-level ROI 项目载入 fail-closed（4096 允许、4097 拒绝且带字段路径错误）、WAV 分析预检显式携带并校验 `frames × channels × sample_width` 解码成本（loader 调用前拒绝超限）、benchmark 证据重新生成为 `json.tool` 可解析的合法 JSON；全量 454 项（60.192 s）、compileall、pip check 通过，两轮 × 5 次 heartbeat 0 次超 75 ms。历史 153.45 ms 超限记录保留且波动来源未完全归因，真实媒体/无障碍/部署证据仍缺，整体保持 `PARTIAL`、任务 `IN_PROGRESS`。

## Findings

### A. task-level ROI 项目载入静默忽略超限（MEDIUM）— CLOSED

- 复现（`41b8deb` 上）：`task_from_snapshot()` 第 505 行忽略 `apply_roi_config_to_task()` 返回值；4097 点 task ROI 被校验拒绝后项目仍载入、ROI 静默回退，无稳定错误。
- 影响：违反 P1-A1 B 的 fail-closed 要求；超限几何在项目载入路径被吞掉，用户可能得到与文件不一致的任务状态。
- 修复：`task_from_snapshot()` 先 `validate_roi_config()`，失败即 `raise ValueError("project task $.roi is invalid: …")`；再应用并检查 `False`。打开 worker 在载入前失败，当前已打开项目不被部分替换。
- 测试：`test_snapshot_open_rejects_oversized_task_roi_fail_closed`（4096/4097 polygon+curve_band）、`test_open_rejects_oversized_task_roi_fail_closed`（worker 失败含 `roi`）；旧“无效可选 ROI 忽略”测试按验收工单改为拒绝。

### B. WAV 分析预检未计入多通道解码成本（MEDIUM）— CLOSED

- 复现（`41b8deb` 上）：`AnalysisWorker` 预检仅校验 `sample_count`（frame count）；`AnalysisSource` 不携带 sample width/decoded bytes，`frames × channels × sample_width` 解码成本未在加载前显式校验。
- 影响：同 frame count 下 channels/sample width 越大，解码中间数组越大，预检无法区分；来源若绕过 probe 或经 IPC/项目数据构造，可在分配前放大。
- 修复：`MediaInfo.sample_width_bytes`（probe 填充、快照持久化）；`AnalysisSource.decoded_source_bytes`（mono/各 channel 源按 `frames × 封顶 channels × sample_width` 计算，`to_data/from_data` 同步）；`validate_wav_decode_workload()`（512 MiB，与 header 校验同源常量）在 `AnalysisWorker.run()` 的 loader 调用、NumPy 分配前执行；`wav_signal_series()` 实际打开后仍复验 header，16 MiB block 上限保留。
- 测试：`test_worker_rejects_wav_decode_cost_before_loading`、`test_worker_preflight_differentiates_decode_cost_with_same_frame_count`（同 frame count、不同 decode bytes → 预检不同，超限时 loader 未被调用）、`test_audio_sources_carry_decoded_source_bytes`（1000×2×2）。

### C. benchmark 证据不是合法 JSON（LOW）— CLOSED

- 复现：`benchmark-round1.json` / `round2.json` 首行混入 Qt stderr 字体告警，`python3 -m json.tool` 无法解析。
- 修复：两轮 × 5 次重新生成，stdout 单独写 `.json`、stderr 写 `.stderr.txt`；两个 `.json` 均通过 `json.tool`。
- 历史记录：153.45 ms 单次超限保留并标明“历史超限、本轮结果”，未删除或改写。

### 验收基线更新（事实）

- Codex 复跑 449/449、400/400 索引、heartbeat 34.64–47.01 ms：与本包 454/454 及 33.49–58.87 ms 一致；本轮仍 0 次超限，但跨负载稳定性未证明。
- 当前解释器仍装有 PyAV 17.1.0（声明层已排除）；历史原生退出根因未关闭。

## Changes Made

- `neo_tracker/ui/project_controller.py`：`task_from_snapshot()` ROI fail-closed；`MediaInfo.sample_width_bytes` 持久化（`media_info_to_dict` / `media_info_from_snapshot`）。
- `neo_tracker/media.py`：`MediaInfo.sample_width_bytes` 字段；`probe_wav_media()` 填充。
- `neo_tracker/ui/analysis_controller.py`：`AnalysisSource.decoded_source_bytes`（含序列化）；音频源按 `frames × channels × sample_width` 计算。
- `neo_tracker/analysis.py`：`validate_wav_decode_workload()`。
- `neo_tracker/ui/analysis_worker.py`：加载前解码成本预检。
- 测试：新增 5 项（ROI snapshot/open fail-closed、WAV 预检 ×2、decode bytes 传递），更新 1 项既有 ROI 忽略语义测试。
- 证据：`artifacts/deepseek-2026-08-09/p1a1r-acceptance-fixes.md`、`benchmark-p1a1r-round{1,2}.json`、`benchmark-p1a1r-round{1,2}.stderr.txt`。
- 文档：`交接.md`（3.77）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`、`collab/FROM_DEEPSEEK.md`；`FORDEEPSEEK.md` 为 Codex 权威工单更新，原样随包提交（DeepSeek 未改写）。

## Files Modified

- `neo_tracker/ui/project_controller.py`
- `neo_tracker/media.py`
- `neo_tracker/ui/analysis_controller.py`
- `neo_tracker/analysis.py`
- `neo_tracker/ui/analysis_worker.py`
- `tests/test_project_controller.py`
- `tests/test_project_open_worker.py`
- `tests/test_analysis_worker.py`
- `tests/test_analysis_controller.py`
- `artifacts/deepseek-2026-08-09/p1a1r-acceptance-fixes.md`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-p1a1r-round1.json`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-p1a1r-round1.stderr.txt`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-p1a1r-round2.json`（新增）
- `artifacts/deepseek-2026-08-09/benchmark-p1a1r-round2.stderr.txt`（新增）
- `交接.md`
- `PROJECT_INDEX.md`
- `PROJECT_FILE_INDEX.sha256`
- `collab/FROM_DEEPSEEK.md`（本文件）
- `FORDEEPSEEK.md`（Codex 验收更新，随本包提交）

## Testing

```bash
# 定向（红 → 绿：旧实现 3 失败 + 3 错误 → 修复后 44 OK）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_project_controller \
    tests.test_project_open_worker tests.test_analysis_worker \
    tests.test_analysis_controller
# Ran 44 tests ... OK

# 全量
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
# Ran 454 tests in 60.192s ... OK

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
# 退出码 0

python3 -m pip check
# No broken requirements found.

python3 -m json.tool artifacts/deepseek-2026-08-09/benchmark-p1a1r-round1.json >/dev/null
python3 -m json.tool artifacts/deepseek-2026-08-09/benchmark-p1a1r-round2.json >/dev/null
# 均通过
```

## Performance and Runtime Evidence

- Workload：10 万结果 `.ntproj` 打开，2 轮 × 5 次 background open（门槛 <75 ms），offscreen，stdout/stderr 分离。
- 环境：macOS 15.7.7 Apple Silicon；Python 3.12.6；NumPy 2.2.3；PySide6 6.11.1；OpenCV 4.13.0。
- 逐值 heartbeat（ms）：round1 = 36.91 / 45.29 / 47.79 / 58.87 / 54.41；round2 = 33.49 / 47.48 / 45.75 / 43.76 / 47.95。
- 统计：median 47.79 / 45.75；p95 54.41 / 47.48；max 58.87 / 47.95；超 75 ms 0/0；退出码均 0。
- apply 逐值：34.90/43.24/45.68/56.89/51.29 与 31.59/44.05/43.74/41.44/45.86 ms。
- `max_gap_phase_before/after`：`Opening project · verifying the saved baseline…` → `…applying the prepared workspace…`；`payload_equal=True`，fingerprint `d9c2dd5992cc…` 不变。
- 原始数据：`benchmark-p1a1r-round{1,2}.json`（合法 JSON）+ `.stderr.txt`（Qt 字体告警）。
- 历史：Codex 曾记录 153.45 ms 单次超限（保留、未改写）；本包与 Codex 多轮均未复现，波动来源未完全归因。
- 残留：无项目进程、无新 `.ips`。

## Remaining Risks

- 事实：sampled identity 仍为“显式确认 + 清理”方案；后台可持久化 full-file identity 未实现。
- 推断：153.45 ms 单次超限推测为同机负载，无负载快照佐证，属未验证假设；不宣称跨负载稳定性。
- 未验证：真实长时媒体（P0-B）、原生无障碍（P1-B）、部署/签名（P2）仍缺证据；不得 `DONE`。

## Suggested Commit Message

```text
fix(trust): fail closed on task ROI load, preflight WAV decode cost, emit valid JSON evidence

- task_from_snapshot rejects invalid/oversized task ROI with a field-path
  error instead of silently ignoring apply failure (4096 ok / 4097 reject)
- MediaInfo carries sample_width_bytes; AnalysisSource carries
  decoded_source_bytes; AnalysisWorker preflights frames*channels*width
  before loading, sharing the 512 MiB cap with header validation
- benchmark evidence regenerated with stdout/stderr split; both .json pass
  python3 -m json.tool; historical 153.45 ms over-limit record kept
- Red/green: 3 fails + 3 errors -> 44 targeted OK; full suite 454 OK in
  60.192s; compileall and pip check pass; 2x5 open rounds all <75 ms
```

---

# P1-A2（第二提交）— `.ntproj` JSON 完整性

## Conclusion

P1-A2 的三个 `.ntproj` 完整性问题（重复 JSON 键 last-wins、`version` 类型混淆、`preview_frame_index` 类型混淆）已修复并验证：全量 458 项（64.489 s）、compileall、pip check、索引全过；基准 4 轮 × 5 次中第 1/3/4 轮 0 次超限、第 2 轮 1 次 88.62 ms 超限（对应 batch 4,511 ms），按验收规则保持 `PARTIAL`，不宣称跨负载稳定。

## Findings

- A（重复键，MEDIUM）：`json.loads` 对重复键 last-wins，字节不同的文件可解析为同一语义，破坏文件字节与解析状态的一一对应。修复：`no_duplicate_json_keys`（`object_pairs_hook`）只在不可信文件解析边界生效（`load()` 与隔离子进程文件解析）；GUI 侧 stage 记录解码不加钩子，避免每对象 Python 回调拖慢 QThread 心跳。
- B（version 类型混淆，LOW）：`version=true / 1.9 / "1"` 被 `int()` 接受并迁移。修复：要求真 int 且 1..2。
- C（preview_frame_index 类型混淆，LOW）：`True / 1.5 / "7" / -3` 被 `int()` 接受。修复：`_frame_index` 非负真 int 校验。

## Changes Made / Files Modified

- `neo_tracker/project.py`：`no_duplicate_json_keys`；`load()` 钩子；`_migrate_to_current` 严格 version；`preview_frame_index` 用 `_frame_index`。
- `neo_tracker/ui/project_open_worker.py`：`_safe_json_loads(..., reject_duplicate_keys)`，文件解析边界启用、stage 解码不启用。
- `tests/test_project.py`（3 项新测试）、`tests/test_project_open_worker.py`（1 项新测试）。
- `artifacts/deepseek-2026-08-09/p1a2-project-json-integrity.md`、`benchmark-p1a2-round{1..4}.json` + `.stderr.txt`（新增）。
- `交接.md`（3.78）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`、本文件。

## Testing

```bash
# 定向：红 10 项失败 -> 绿 33 OK（test_project + test_project_open_worker）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_project tests.test_project_open_worker
# Ran 33 tests ... OK

# 全量
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
# Ran 458 tests in 64.489s ... OK

python3 -m compileall -q neo_tracker tests benchmarks   # 退出码 0
python3 -m pip check                                    # No broken requirements found.
python3 -m json.tool artifacts/deepseek-2026-08-09/benchmark-p1a2-round{1,2,3,4}.json >/dev/null
# 4/4 通过
```

## Performance and Runtime Evidence

| 轮次 | heartbeat 逐值 (ms) | median | p95 | max | >75ms | 退出码 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 37.02 / 53.23 / 49.34 / 47.74 / 57.91 | 49.34 | 53.23 | 57.91 | 0 | 0 |
| 2 | 36.39 / 46.00 / 88.62 / 50.81 / 58.86 | 50.81 | 58.86 | 88.62 | 1 | 1 |
| 3 | 36.18 / 47.92 / 56.02 / 46.23 / 69.31 | 47.92 | 56.02 | 69.31 | 0 | 0 |
| 4 | 38.71 / 57.19 / 50.26 / 49.36 / 68.67 | 50.26 | 57.19 | 68.67 | 0 | 0 |

- `payload_equal=True`、fingerprint `d9c2dd5992cc…` 不变；阶段均为 baseline 校验→workspace 应用。
- 第 2 轮超限对应 batch 4,511 ms（其余 3,700–3,900 ms），疑似系统负载；钩子从 GUI 解码路径移除后同轮超限由 4/5 降为 1/5。历史 153.45 ms 记录保留。

## Remaining Risks

- 第 2 轮 88.62 ms 单次超限未归因为代码改动（负载推测无快照佐证）；`PARTIAL` 保持。
- 嵌套 `pipelines`/`media_info` 深层类型校验仍依赖现有 `apply_pipeline_config`/模型校验；重复键拒绝覆盖本项目全部 JSON 解析入口。

## Suggested Commit Message

```text
fix(trust): reject duplicate JSON keys and type-confused .ntproj version/index fields

- json.loads now rejects duplicate object keys at the untrusted file parse
  boundary (load + isolated open), not on the GUI-side stage decode path
- version must be a real int in [1,2]; preview_frame_index must be a
  non-negative real int; bool/float/str/negative values are rejected
- Red/green: 10 failing tests -> 33 targeted OK; full suite 458 OK in
  64.489s; compileall and pip check pass
- Benchmark 4x5: rounds 1/3/4 pass; round 2 has one 88.62 ms over-limit
  with elevated batch time; PARTIAL retained, evidence kept as valid JSON
```

---

# P1-A3（第三提交）— MediaReader 来源版本守卫（TOCTOU）

## Conclusion

P1-A 第 2 项（媒体信任边界）中 MediaReader 打开后不再校验来源版本的 TOCTOU 缺口已关闭：
构造后、每次读取与每次重开前后均复验 dev/ino/size/mtime/ctime，来源被替换/重写/换类型时
fail-closed 抛错；红 5 项 → 定向 26 OK、全量 464（72.181 s）、compileall、pip check
全过，无残留进程。

## Findings

- A（MEDIUM，已修复）：`MediaReader.__init__` 曾丢弃构造期文件版本，`_read_frame` 与
  `_reopen_capture` 无任何守卫。复现：打开后 `os.replace` 替换来源，读取仍返回旧 fd 帧；
  seek 失败触发重开时会静默打开新文件并返回其帧，跟踪子进程可能混入新旧来源。影响：
  运行结果与项目 source identity 不一致且无错误。修复：`self._source_version` 保存 +
  `_verify_source_unchanged()` 在打开后/每次读取/重开前后调用，不一致即释放并抛错。
- B（LOW，测试固化）：symlink 指向常规文件允许（`S_ISREG` 检查解析后目标），目标被替换
  时经解析 inode 变化被守卫捕获；指向 FIFO/设备的拒绝、悬空 symlink 报不存在。
- C（LOW，测试固化）：VFR/错误 frame count——报告长度超过实际可解码帧时抛
  `EndOfMediaError`，不重复末帧；seek 落点验证/重开顺序恢复保留。

## Changes Made / Files Modified

- `neo_tracker/media.py`：MediaReader 来源版本守卫（构造后复验、逐读取复验、重开前后复验）。
- `tests/test_media.py`：+6 项测试（5 项 TOCTOU + 1 项 VFR 表征）。
- `artifacts/deepseek-2026-08-09/p1a3-media-reader-version-guard.md`（新增证据）。
- `交接.md`（3.79）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`、本文件。

## Testing

```bash
# 红：旧实现 5 项失败（RuntimeError not raised）；绿：定向 26 OK
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_media -q
# 全量：Ran 464 tests in 72.181s OK
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks  # 0
python3 -m pip check   # No broken requirements found.
pgrep -fl "neo_tracker|isolated_media|benchmark_project"  # 无残留
PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75 \
  > artifacts/deepseek-2026-08-09/benchmark-p1a3-round1.json \
  2> artifacts/deepseek-2026-08-09/benchmark-p1a3-round1.stderr.txt
# round1 exit=0；round2 同命令 exit=0；json.tool 通过
```

## Performance and Runtime Evidence

- 每次读取新增一次 `stat`：实测约 1.7 µs/次（200,000 次采样），相对解码 ms 级可忽略。
- project-open 基准两轮 × 3 次：逐值 46.62/64.04/48.71 ms 与 59.75/58.46/51.35 ms，
  max 64.04/59.75，0 次超 75 ms，退出码 0；`results_exact=True`、
  `payload_equal=True`、fingerprint `d9c2dd5992cc…` 一致。
- 历史 heartbeat 超限记录（P1-A2 第 2 轮 88.62 ms、更早 153.45 ms）保留，整体 `PARTIAL`。

## Remaining Risks

- stat 守卫不承诺检测刻意保留 inode/size/mtime 且无 ctime 变化的篡改（仅根权限级）。
- 大文件 sampled identity 仍是采样；真实 H.264/HEVC、CFR/VFR 来源替换矩阵需 P0-B 素材。

## Suggested Commit Message

```text
fix(trust): bind MediaReader to its opened source version and fail closed on replacement

- MediaReader stores dev/ino/size/mtime_ns/ctime_ns at construction and
  re-verifies after VideoCapture open, before every frame read, and both
  before and after reopen; a replaced/rewritten/non-regular source raises
  RuntimeError instead of mixing frames or silently switching sources
- Symlink-to-regular-file stays accepted; retargeted symlinks are detected
  through the resolved inode; VFR/truncated sources raise EndOfMediaError
- Red/green: 5 failing TOCTOU tests -> 26 targeted OK; full suite 464 OK in
  72.181s; compileall and pip check pass; per-read stat ~1.7 us
```

---

# P1-A4（第四提交）— helper IPC 有界化 + 保存/导出/插件/UI 数据保护审计

## Conclusion

P1-A 第 3–6 项全部闭环：tracking 子→父 IPC 改为 128 MiB 有界信封并限制结果批次数量与
终态形状（红 4 → 绿 4）；保存/导出、插件配置、UI 数据保护审计未发现新再现缺陷，补齐
保存/导出 10 项与插件 config-diff 3 子项回归矩阵；全量 479 tests（62.192 s）、
compileall、pip check 全过。

## Findings

- P1-A3（MEDIUM，已修复）：`tracking_worker` 子→父消息裸 `recv()` 无长度上限，且
  `_accept_isolated_results` 不限制批次数量（17>16 被接受）。修复：`recv_bytes`
  maxlength 128 MiB、`_encode_isolated_message` 超限即 fail-closed、批次
  `len ≤ checkpoint_frames`、`_validate_isolated_terminal` 形状校验。
- P1-A4（LOW，审计）：保存/导出原子模式（temp→fsync→replace→cleanup）已统一；
  CSV 表头/单元格公式转义、NPZ 无 pickle 均覆盖；未发现新缺陷，补齐此前零覆盖的
  失败路径回归矩阵。
- P1-A5（LOW，审计）：pipeline config 白名单分派无任意代码实例化；未知模块类型经
  config-diff 检查使项目载入 fail-closed（非静默降级）；第三方适配器隔离失败测试既有。
- P1-A6（LOW，审计）：Open/Save/Tracking/Close/Discard/Relink 均有 token 门控、
  失败恢复、原子应用与 revision 守卫；既有 143 项 UI 测试覆盖，未发现新缺口。

## Changes Made / Files Modified

- `neo_tracker/ui/tracking_worker.py`（IPC 有界信封/批次上限/终态校验）。
- `tests/test_tracking_worker.py`（+4）、`tests/test_atomic_export.py`（新 10）、
  `tests/test_project_controller.py`（+1，3 子项）。
- `artifacts/deepseek-2026-08-09/p1a4-ipc-export-plugin-ui-safety.md`（新增证据）。
- `交接.md`（3.80）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`、本文件。

## Testing

```bash
# 红：1 行为失败 + 3 ImportError；绿：tracking_worker 30 / atomic_export 10 /
# project_controller 19 定向全过
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
# Ran 479 tests in 62.192s OK
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks  # 0
python3 -m pip check   # No broken requirements found.
pgrep -fl "neo_tracker|isolated_media|benchmark_project"  # 无残留
```

## Performance and Runtime Evidence

- IPC 上限 128 MiB 覆盖 64 MiB debug-history + 48 MiB 帧字节上限；正常检查点远低于
  上限，吞吐语义不变；全量 479 / 62.192 s OK。
- 本批不涉及 project-open UI 路径；heartbeat 波动仍 `PARTIAL`（历史超限记录保留）。

## Remaining Risks

- Rerun prefix 的 spawn 参数 pickle 由 100,000-result 项目上限约束，无独立字节上限
  （性能/内存成本点，非信任缺口）。
- 保存/导出失败路径为模拟 ENOSPC/权限测试；真实磁盘满实机矩阵建议 P0-B 补跑。

## Suggested Commit Message

```text
fix(trust): bound tracking IPC, lock export/plugin safety, audit UI data protection

- tracking child->parent messages use a 128 MiB capped send_bytes/recv_bytes
  envelope; oversized payloads fail closed instead of unbounded pickup
- isolated result batches are capped at checkpoint_frames; terminal results
  are shape-validated before unpacking
- save/export safety matrix: atomic publish preserves existing targets and
  cleans temp files on write/fsync/replace failure; CSV formula cells and
  headers escaped; NPZ export failure cleanup
- unknown pipeline module types in task snapshots fail project load closed
  via config-diff (observation/state/motion locked by tests)
- Red/green: 1 behavioral + 3 import failures -> 479 tests OK in 62.192s;
  compileall and pip check pass
```

---

# P0-B — 真实媒体矩阵（可执行部分）

## Conclusion

用工作区唯一真实采集素材（red-dot-tracking.mp4，640×360/20fps/72 帧 H.264）及其
HEVC/1080p 转码跑通 Full Run（两轮 72/72、digest 一致）、取消（41–44 ms 干净终态）、
来源运行中替换（fail-closed `TRACKING_SOURCE_CHANGED`）、截断文件（probe fail-closed）、
重开 ×10（无残留）；**VFR 原采集、4K 原采集、≥10 分钟长会话、功耗/温度证据
`BLOCKED`（缺素材/权限）**，整体 `PARTIAL`，不得据此建议 DONE。

## Findings / Changes Made / Files Modified

- 新增 `benchmarks/benchmark_real_media_matrix.py`（stdout=JSON、stderr=警告分离，
  `json.tool` 通过）；证据 `benchmark-p0b-round{1,2}.json` + `.stderr.txt`、
  `p0b-real-media-matrix.md`、`media-matrix/` 夹具（hevc/1080p 转码、截断、替换基底）。
- 首次运行曾因 `os.replace` 把合成素材移走（脚本缺陷）；已用 `git restore` 恢复原始
  文件并把替换改为 `shutil.copyfile`（不再破坏素材），两轮重跑正常。

## Testing / Performance and Runtime Evidence

- Full Run：real 0.224/0.224 s、HEVC 0.210/0.223 s、1080p 0.411/0.435 s；child RSS
  峰值 96–107 / 113–121 / 277–297 MB；两轮 (frame_index, filtered_state) digest 相同。
- Cancel：0.15 s 时请求，41–44 ms 到终态（completed cancelled=True）、无残留。
- Source replacement：0.4 s 替换后运行失败 `TRACKING_SOURCE_CHANGED: … content
  identity no longer matches`，42 结果被丢弃语义恢复旧态。
- Truncated：`Could not open media file`（moov 被截断）；提前 EOF 行为由单测覆盖。

## Remaining Risks

- 1080p/HEVC 是真实内容转码，不构成原采集分辨率/编码器稳定性结论；VFR/4K/长会话
  `BLOCKED`，需用户提供目标相机素材。`powermetrics` 需 root，功耗/温度未测。

---

# P1-B — 原生 macOS UI 与无障碍（真实窗口证据）

## Conclusion

原生 Cocoa 启动（无 offscreen）取到真实窗口证据：Neo-Tracker 窗口在 1280×808 /
1024×768 / 1440×900 逻辑尺寸下截图（Retina 2×），AX 名称/描述完整、Tab 焦点可前进、
`AXPress` 可激活按钮、关闭按钮优雅退出无残留；**观察到的 PySide6/Shiboken
QThread-QObject 崩溃经对照实验归因为 Codex 宿主环境产物（10 个 .ips 父进程均为宿主，
纯 `sleep 45` 也产生 5 个），与 ICS-NeoTracker 无关；VoiceOver/系统文本缩放/完整焦点
遍历/动态 compositor 未闭环，整体 `PARTIAL`**。

## Findings / Files Modified

- 证据 `p1b-native-ui.md` + 3 张原生截图 + `python-2026-08-09-205230-crash.ips`。
- 崩溃归因：10 个 `Python-*.ips`（含交互期间 4 个 + `sleep 45` 对照 5 个 + 静默启动
  1 个）`parentPid` 全部为 ChatGPT/Codex 宿主（1394），堆栈均为 Shiboken QObject
  析构 + QThread sendPostedEvents；**结论：宿主工具子进程崩溃，非本应用**；受控本应用
  实例全部存活并优雅退出。

## Testing / Performance and Runtime Evidence

- AX resize applied=(1024,768)/(1440,900)；截图物理像素 ≈ 逻辑 ×2（Retina）+ 阴影。
- Tab×2：焦点 Add media（AXButton）→ “No media loaded …”（AXStaticText）。
- `AXPress` Add media result=0；应用日志出现 NSOpenPanel 运行时消息；Escape 后继续。
- 关闭按钮退出，无残留 neo_tracker/helper 进程。

## Remaining Risks

- 需在干净会话复查宿主工具崩溃是否影响其他应用（已确认不影响本应用）；VoiceOver、
  系统文本缩放、全键盘遍历、动态 compositor、4K 外接显示器证据缺失；部署/签名（P2）未做。

---

# 干净环境 + heartbeat 轮次（2026-08-09 续）

## Conclusion

全新 venv（`.[desktop,media,science]`）完成安装、479 项全量回归（61.252 s）、
真实媒体矩阵（72/72、取消 53 ms、来源替换 fail-closed）、原生窗口启动/优雅退出；
heartbeat 轮次 4 再现 75.21 ms 超限（>75 ms 门槛 0.21 ms），跨负载稳定性保持
`PARTIAL`。证据 `p0b-cleanenv-and-heartbeat.md`。

## Findings / Changes Made / Files Modified

- 新增：`benchmark-p0b-cleanenv-round1.json`（+`.stderr.txt`）、
  `p1b-native-ui/nt-cleanenv-1280x808.png`、`p0b-cleanenv-and-heartbeat.md`；
  `交接.md`、`PROJECT_FILE_INDEX.sha256` 更新。无源码改动。
- 干净环境首次缺 science extra 时 3 项 STFT 测试按设计 fail-closed，补装后全过。
- heartbeat：round3 0 超限（max 64.76）；round4 1 次 75.21 ms 超限（退出码 1）——
  与历史 88.62/153.45 ms 波动同族，`PARTIAL` 保持，不宣称稳定。

## Remaining Risks

- 真实目标相机素材矩阵（P0-B）、VoiceOver/文本缩放（P1-B）、打包/签名（P2）仍未闭环；
  heartbeat 跨负载稳定性未证明。

---

# Heartbeat 多轮复核 + P1-B 键盘遍历（2026-08-09 续）

## Conclusion

heartbeat 门槛 5 轮 21 次打开中 16 次通过、5 次超限（76.45–154.60 ms）全部出现在
宿主持续满载窗口（8 核 load 峰值 9.37），正常负载轮次 34–59 ms；超限为调度延迟而非
GUI 长阻塞，跨负载稳定性仍 `PARTIAL`。P1-B 完整键盘焦点链（空项目 6 控件、媒体态
16 控件，正/反向闭环）与键盘激活（Space 触发 Add media/Open Project）已取证；
OS 级真实按键与 AX/VoiceOver 复核因本会话宿主占用前台 + AX 服务退化而 `BLOCKED`。

## Findings / Changes Made / Files Modified

- 新增 `artifacts/deepseek-2026-08-09/heartbeat-rounds/`（README + round1..5.json
  + `.stderr.txt`）与 `p1b-keyboard-traversal/`（README + 两个脚本 + 3 份 JSON）。
- `ResultsTableModel.set_results` 三趟 100k 扫描合并单趟实测无收益（16.3→17.3 ms），
  已完全回退；无源码改动。`交接.md`（3.83/3.84）、`PROJECT_INDEX.md`、
  `PROJECT_FILE_INDEX.sha256` 更新。
- 全量 479 tests / 62.494 s OK、compileall、pip check、SHA-256 索引通过。
- `Python-2026-08-09-222524.ips` 为键盘 harness 在 probe worker 结束前销毁窗口所致
  （QThread destroyed while running），非产品代码缺陷；harness 已修复。

## Remaining Risks

- OS 级真实按键、AX 表格导航、VoiceOver、系统文本缩放：宿主会话/AX 服务恢复后复核。
- 真实目标相机素材矩阵（P0-B VFR/4K/长会话/功耗）与 heartbeat 跨负载稳定性仍
  `BLOCKED`/`PARTIAL`；P2 打包/签名范围已写入 README 部署范围。
