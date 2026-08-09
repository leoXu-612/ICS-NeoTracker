# P1-A3 — MediaReader 来源版本守卫（TOCTOU / symlink / VFR）

日期：2026-08-09（Asia/Taipei）
状态：P1-A 第 2 项（媒体信任边界）中“MediaReader 打开后无版本守卫”的 TOCTOU 缺口已关闭；
symlink 与 VFR/错误 frame count 行为已测试固化；整体任务 `IN_PROGRESS` / `PARTIAL`
（真实媒体矩阵与原生无障碍/部署证据仍未闭环）。

方法：在 `9ccc14b` 上先补失败测试（红：5 项 TOCTOU 测试失败），再做最小修复（绿：
定向 26 OK，全量 464 OK）。不修改 `build/lib/`。

## Finding 1（MEDIUM，已修复）：MediaReader 构造后不再校验来源版本

### Source → Sink

媒体路径 → `MediaReader.__init__` 的 `_media_file_version()`（结果被丢弃）→
`VideoCapture` 打开（fd 绑定旧 inode）→ `_read_frame` / `_reopen_capture`
（无任何 stat/版本校验）→ 返回解码帧或静默重开新来源。

### 失败前状态（复现）

- 构造 reader 后 `os.replace` 替换同一路径为新文件（新 inode）：`read_frame(0)` 仍返回
  旧 fd 的帧；若 seek 被拒绝触发重开，`_reopen_capture` 会静默打开新文件并返回其帧，
  一次运行可能混入新旧两种来源。
- 同一 inode 就地重写（size/mtime/ctime 变化）：后续读取不受影响，可能混读新旧内容。
- 打开期间路径被换成 FIFO 或删除：读取不感知，进程可能阻塞或读到不一致状态。
- 构造 stat 与 `VideoCapture` 打开之间的替换窗口没有任何复验。

### 影响

数据可信度：来源被替换时，运行/预览结果与项目记录的 source identity 不一致，且无任何
错误提示；跟踪子进程可能把新旧来源的帧作为同一次运行结果提交。

### 修复

- 构造时保存 `self._source_version`，`VideoCapture` 打开成功后立即复验；不一致时释放
  capture 并抛 `RuntimeError`（关闭构造期 TOCTOU 窗口）。
- `_read_frame` 每次解码前调用 `_verify_source_unchanged()`：路径被替换（新 inode）、
  就地重写（size/mtime/ctime）、换成非 regular 文件或消失时均 fail-closed 抛错。
- `_reopen_capture` 在释放旧 capture 前与打开新 capture 后各复验一次；不一致时释放并
  抛错，绝不静默切换来源。
- 调用方语义：跟踪子进程外层 `except Exception` 将运行记录为失败；注入式预览路径
  `except Exception` 显示解码失败信息；隔离 helper 已有请求级版本守卫，语义一致。

## Finding 2（LOW，测试固化）：symlink 目标替换

行为：`_media_file_version` 的 `S_ISREG` 检查作用于 `stat()` 解析后的目标（指向常规
文件的 symlink 允许；指向 FIFO/设备的拒绝；悬空 symlink 报不存在）。symlink 目标被
替换时通过解析后 inode 变化被版本守卫捕获 → fail-closed。新增测试覆盖
“允许常规文件 symlink + 目标替换后拒绝读取”。

## Finding 3（LOW，测试固化）：VFR / 错误 frame count

`CAP_PROP_FRAME_COUNT` 为近似值；实际可解码帧早于报告长度结束（VFR、截断或错误
count）时，`read_frame_for_processing` 抛 `EndOfMediaError`，不重复末帧、不返回垃圾。
已有 seek 落点验证/重开顺序恢复保留；新增 VFR 表征测试：报告 40 帧、实际 21 帧、
请求第 30 帧 → `EndOfMediaError(frame_index=30)`。

## Changes Made

- `MediaReader`：保存构造期来源版本；打开后、每次读取、每次重开前后均复验；不一致即
  fail-closed 抛 `RuntimeError`（错误信息含 “Media source changed … retry”）。
- `tests/test_media.py`：新增 6 项测试（5 项 TOCTOU 红→绿 + 1 项 VFR 表征）。

## Files Modified

- `neo_tracker/media.py`
- `tests/test_media.py`
- `交接.md`（3.79）、`PROJECT_INDEX.md`、`PROJECT_FILE_INDEX.sha256`、
  `collab/FROM_DEEPSEEK.md`、本文件

## Testing

```bash
# 红：旧实现上 5 项失败（RuntimeError not raised）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_media -v
# FAILED (failures=5)

# 绿：定向
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_media -q
# Ran 26 tests ... OK

# 全量
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
# Ran 464 tests in 72.181s ... OK

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
# 退出码 0
python3 -m pip check
# No broken requirements found.
```

## Performance and Runtime Evidence

- 每次读取新增一次 `stat`：本机实测 `Path.stat` 约 1.70 µs/次、`os.stat` 约 1.66 µs/次
  （200,000 次采样），相对视频解码 ms 级成本可忽略；不改动解码与 seek 主路径逻辑。
- 全量 464 tests / 72.181 s OK；compileall、pip check 通过；`pgrep` 无残留项目进程。
- project-open 基准（100,000 results / 44.398 MiB，与历史同命令）：两轮 × 3 次逐值
  heartbeat 46.62 / 64.04 / 48.71 ms（median 48.71，max 64.04）与 59.75 / 58.46 /
  51.35 ms（median 58.46，max 59.75），0 次超 75 ms，退出码 0；`results_exact=True`、
  `payload_equal=True`、fingerprint `d9c2dd5992cc…` 前后一致、`deferred_state` 一致。
  原始 JSON：`benchmark-p1a3-round{1,2}.json` + `.stderr.txt`（`json.tool` 通过）。
- 历史 heartbeat 波动记录（P1-A2 第 2 轮 88.62 ms、更早 153.45 ms）保留，整体仍
  `PARTIAL`，不宣称跨负载稳定。

## Remaining Risks

- stat 版本守卫检测“替换/重写/换类型/消失”；不承诺检测刻意保留相同 inode、size、
  mtime 且无 ctime 变化的内容篡改（真实写入必然改变 ctime，该场景仅剩根权限级攻击）。
- 大文件 sampled identity（>768 KiB 只采样三块）仍是采样而非全文件校验，未变。
- 真实 H.264/HEVC、CFR/VFR 相机素材的长时间来源替换矩阵仍需 P0-B 素材才可闭环。
