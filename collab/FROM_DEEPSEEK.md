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
