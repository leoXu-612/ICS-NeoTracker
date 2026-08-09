# ICS-NeoTracker — DeepSeek 开发执行交接

> 结论：当前代码已通过完整回归和大型项目交互门槛，但真实长时媒体、原生解码稳定性、整进程资源、原生 macOS 无障碍与部署环境仍缺证据；DeepSeek 应先关闭这些 P0/P1 风险，再继续功能扩展。

更新时间：2026-08-09（Asia/Taipei）  
任务状态：`IN_PROGRESS`  
权威工作区：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`

## 0. Codex 验收结论与当前工单（2026-08-09）

### 0.1 上一包 P0-A 验收：`PARTIAL ACCEPT`

已确认并接受：

- `pyproject.toml` 的 `media` extra 只声明 `opencv-python>=4.9`，不再主动安装未使用的 PyAV。
- `tests/test_media_dependencies.py` 的 3 项守卫具有实际约束：产品源码不得导入 PyAV、media extra 不得声明 PyAV、单独导入 `cv2` 不得传递加载 `av`。
- Codex 独立复跑：436 项 unittest 全部通过（64.627 秒）；`compileall`、`pip check` 通过；当前 397 项内容索引全部校验通过；Git 工作树在复测前为 clean，HEAD 为 `7deeee6f9c79c8661a46226390449204d7349b2e`。

不得扩大为以下结论：

- 当前解释器仍同时安装 OpenCV 4.13.0 与 PyAV 17.1.0；显式在同一进程导入二者仍稳定产生 `AVFFrameReceiver` / `AVFAudioReceiver` 重复实现警告。修复只关闭“本项目依赖集主动引入 PyAV”的风险，不证明历史 Python/Qt 原生退出根因已经修复。
- 10 万结果 heartbeat 的稳定门槛没有通过独立复核：同一命令三次 background run 为 35.55 / 72.84 / 153.45 ms，第三次超过 `<75 ms`，命令退出码为 1；payload 与 fingerprint 仍一致。不得继续写“heartbeat 全部通过”，必须先解释波动来源并用冷/热轮次分离的重复统计重验。
- `collab/FROM_DEEPSEEK.md` 写的是 `396/396`，当前索引实际为 `397/397`。
- 当前提交 `7deeee6` 的 diff 主要是文档、证据和索引；`pyproject.toml` 与依赖守卫测试已存在于初始提交 `f999fae`。因此不能把 `7deeee6` 单独描述为包含依赖修复代码的原子提交。

### 0.2 下一步任务：P1-A1 数据可信度、输入上限与导出原子性 `TODO`

目标：修复下面 4 个已由当前源码和一次性 `/tmp` 复现确认的问题；不做 UI 改版、功能扩展、真实媒体性能外推或无关重构。

#### A. 大媒体 sampled identity 被错误当作精确匹配（`MEDIUM`）

当前事实：大于 768 KiB 的文件只读取开头、中段、结尾各 256 KiB，但 `ProjectTaskController.assess_media_relink()` 把相同 sampled digest 返回为 `state="match"`。Codex 复现使用 2,000,044-byte WAV，在未采样的 600 KiB 偏移修改真实 PCM 样本后：

- 两文件 `MediaIdentity` 完全相同；
- 两文件 metadata 相同且均可打开；
- 解码样本由 `0000` 变为 `ff7f`；
- Relink assessment 仍为 `match / sampled / clear_results=False / can_apply=True`。

必须满足：sampled equality 不得静默证明“字节完全相同”，也不得在没有明确、可审计决策的情况下让旧 Results/Edits 与新内容继续绑定。优先方案是在隔离后台完成可持久化的 full-file identity；若选择显式人工确认方案，必须阻断 preview/tracking/export 对旧结果的误绑定，并清楚区分 exact match、sampled hint 与 user-approved risk。不得在 GUI 主线程全文件 hash，也不得把每帧热路径变成重复 hash。

#### B. task-level ROI 绕过 4096 点上限（`MEDIUM`）

`validate_roi_config()` 对 polygon / curve-band 只检查最小点数与有限数值；独立的 task `roi` 随后由 `apply_roi_config_to_task()` 全量复制并同步填充 Qt 表格，没有经过 `_points()` 的 `MAX_ROI_POINTS=4096` 限制。必须在反序列化/校验入口统一执行同一上限，4096 点允许、4097 点拒绝，拒绝应有稳定错误且不得部分应用任务。

#### C. WAV channel metadata 可驱动无界 UI/解码放大（`MEDIUM`）

`.wav` 走 stdlib 直接 probe，未复用 isolated-media 的 channel 上限；`available_sources()` 会按声明 channel 数创建一项一对象，`wav_signal_series()` 的 chunk 还会按 `frames × channels` 解码，而 workload gate 只按 frame count 估算。必须：

- 在任何 Qt/NumPy 分配前校验 WAV channels、sample width、sample rate、frame count 与合理的乘积/字节上限；
- 让 chunk 大小随 channel 数缩小，避免固定 1,048,576 frames 造成大块中间数组；
- workload 估算覆盖多通道解码成本；
- 用手工 header 构造的极端 channel WAV 做拒绝测试，不把损坏输入标记为 available。

#### D. 非 `.npz` 目标会发布空文件并遗留真实 sidecar（`LOW`）

`atomic_output_path()` 的临时文件沿用用户后缀，而 `np.savez(path)` 会在非 `.npz` 路径后自动追加 `.npz`：最终原子替换的是空临时文件，真实 archive 留在相邻隐藏 sidecar。必须把已安全创建的二进制 file object 交给 `np.savez`，或保证临时路径本身以 `.npz` 结尾；对 `.npz`、无后缀和其他后缀各补成功/失败清理测试。

### 0.3 实施与验收约束

1. 先补能在旧实现上失败的定向测试，再做最小修复；不得修改 `build/lib/`。
2. 每个问题都要记录 source → sink、失败前状态、修复后状态和反证；不要把“测试通过”写成安全证明。
3. 全量运行：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
   PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
   python3 -m pip check
   ```

4. 10 万结果 benchmark 至少运行 2 轮、每轮 5 次 background open；分别报告每次值、median、p95/max、超门槛次数和 `max_gap_phase_before/after`。任一轮出现 `>75 ms` 就保持 `PARTIAL`，不得只选最好结果。
5. 更新 `collab/FROM_DEEPSEEK.md`、`PROJECT_INDEX.md`、`交接.md` 与 `PROJECT_FILE_INDEX.sha256`；报告实际索引条目数。
6. 完成后只提交一个范围清晰的 commit；先用 `git diff --cached --stat` 确认源码、测试和证据都在同一提交中。若工作树含非本工单改动，停止并报告，不得覆盖。
7. 回复固定包含：`Conclusion / Findings / Changes Made / Files Modified / Testing / Performance and Runtime Evidence / Remaining Risks / Suggested Commit Message`。

## 1. 执行授权与目标

你被要求直接审计、修改并验证当前 ICS-NeoTracker 源码和测试。目标不是增加表面功能，而是继续完成以下长期任务：

1. 代码 Review：寻找会造成崩溃、数据错误、数据丢失、资源泄漏或安全边界失效的问题，并修复已复现的问题。
2. 数据采集性能：验证并优化真实视频的 probe、Preview、Tracking、取消与关闭路径。
3. UI 质量：提高真实 macOS 窗口中的可理解性、响应性、键盘操作与无障碍质量。
4. 投产门槛：只有性能、安全、正确性、操作感和部署证据均闭环，才可建议结束任务。

不要写入以下旧目录：

- `/Users/leo.xu/Desktop/Codex/Codex_Neo-Tracker`
- `/Users/leo.xu/Desktop/Codex/ICS-Project-/Codex_Neo-Tracker`

源码权威目录是 `neo_tracker/`；`build/lib/neo_tracker/` 是历史构建产物，不得作为修改来源。

## 2. 当前可复现基线

以下结果于 2026-08-09 在当前工作区重新运行，不是旧交接记录的推断：

| 维度 | 当前证据 | 状态 |
| --- | --- | --- |
| 完整回归 | 436 项 unittest，64.627 秒 | PASS |
| Python 编译 | `compileall` | PASS |
| Python 依赖 | `pip check` 返回 `No broken requirements found` | PASS |
| 内容完整性 | 当前 397 个稳定文件全部通过 SHA-256 校验 | PASS |
| 10 万结果项目打开 | 三次 background open 3.861/3.905/4.167 秒 | PASS |
| 10 万结果 UI heartbeat | 35.55/72.84/153.45 ms，门槛 `<75 ms` | **FAIL / FLAKY** |
| 10 万结果 GUI apply | 33.51/69.12/61.80 ms | PARTIAL |
| 大项目正确性 | payload、results、fingerprint、Review diagnostics、Analysis sources 一致 | PASS |
| Git | `main` @ `7deeee6`，复测前工作树 clean | PASS |

当前验证环境：

- macOS 15.7.7，Apple Silicon
- Python 3.12.6
- NumPy 2.2.3
- PySide6 6.11.1
- OpenCV 4.13.0
- PyAV 17.1.0
- SciPy 1.17.0

这些是当前机器的事实，不是跨平台兼容性承诺。

## 3. 已完成且不得回退的能力

- Tracking 的 decoder、单帧预取和 pipeline 在可终止的 `spawn` 子进程运行；QThread 只监控 IPC。
- Probe、Preview 和项目 JSON 读取均有隔离进程、输入上限、来源复验和有界 terminate/kill。
- 项目打开在完整成功后才原子切换；取消或失败必须保留当前工作区。
- 项目保存使用同目录临时文件、flush/fsync 和 `os.replace`；后台保存受 revision/path 守卫保护。
- Full/Rerun 默认保护既有 Results/Edits，来源漂移时丢弃新结果并恢复旧状态。
- `.ntproj` 有 64 MiB 文件上限、100,000-result 上限及受限 JSONL 重建校验。
- CSV 具有公式注入防护；NPZ metadata 不允许 pickle。
- Review 大表、诊断图、Response cache、Signal source 和项目 fingerprint 已做有界/增量处理。
- 1024×768 offscreen 截图未见整窗裁切或错误 reflow；这只证明合成环境，不证明原生 Retina、compositor 或辅助功能。

历史 `collab/FROM_CLAUDE.md` 的 2026-07-11 findings 已被后续实现覆盖。不得仅依据旧行号重新报同一问题；必须先在当前代码复现。

## 4. 不可破坏的工程不变量

任何改动都必须同时满足：

1. 相同输入与配置产生相同 frame/time、状态、候选、滤波结果和项目 fingerprint 语义。
2. GUI 主线程不执行长时 I/O、解码、项目解析、全量结果扫描或不可中断计算。
3. Cancel、Close、helper crash、EOF 和 timeout 均只产生一个明确终态，不留下孤儿进程、活线程或半提交结果。
4. 打开、保存、Relink、Full Run 和 Rerun 失败时不丢失用户已有项目、Results、Edits 或来源证据。
5. 任何来自项目文件、媒体、插件或子进程的数据在分配 NumPy/Qt 对象前都必须有类型、尺寸、数量和顺序上限。
6. 不把 sampled media identity 描述为完整文件验证；大于 768 KiB 的媒体当前只摘要开头、中段和结尾各 256 KiB。
7. 不在项目文件或外部 IPC 中接受不可信 pickle；现有 child-input 序列化只能是父进程对内存中已验证对象生成的私有通道数据。
8. 不以盲目 timeout 代替资源所有权；超时后必须确认 terminate/kill、pipe/session/reader 清理和最终状态。
9. 不修改 `build/lib/` 来替代源码修复，也不把旧 benchmark 数字当作当前结果。

## 5. 按优先级执行的路线

### P0-A：定位原生 Python 意外退出与解码依赖冲突

2026-08-09 验收更新：依赖声明层面的 P0-A 已关闭；历史 Accessibility/AppKit、QThread 与其他原生退出根因仍未关闭。当前优先执行上面的 P1-A1 工单。

当前发现：单独导入 `cv2` 或 `av` 无警告；同一解释器同时导入时，macOS 报告 `AVFFrameReceiver` 和 `AVFAudioReceiver` 被两套 `libavdevice` 重复实现：OpenCV 携带 FFmpeg 61，PyAV 携带 FFmpeg 62，并警告可能出现异常崩溃。

同时必须区分：

- 当前产品源码会懒加载 OpenCV，但没有发现 PyAV 导入，因此上述冲突是部署风险，不是现有产品崩溃根因的证明。
- 2026-07-14/15 的已知报告位于 Accessibility → AppKit/libqcocoa 原生调用链，曾出现 `EXC_BAD_ACCESS`/`SIGBUS`。
- 2026-07-16 的一次 `QThread::~QThread()` SIGABRT 来自测试截图 harness 在 worker 结束前销毁窗口，不等同于正常产品关闭。
- 2026-08-09 的 433 项回归没有异常退出。

执行顺序：

1. 以时间戳和堆栈检查新的 `.ips`，分别标记 Qt accessibility、QThread 生命周期、OpenCV/FFmpeg、PyAV/FFmpeg或其他签名。
2. 在隔离子进程中复现最小导入、probe、Preview、Tracking、Cancel 和 Close；不要让一次 native crash 杀死审计主进程。
3. 审计 `pyproject.toml`：`media` extra 当前同时安装 `opencv-python` 与 `av`，但源码没有 PyAV 使用点。先确认后选择最小方案：移除未使用依赖，或强制不同 backend 只存在于各自 helper 进程。不要在 GUI 进程同时加载两套 FFmpeg。
4. 为选定方案补充回归/环境检查，并验证 clean environment 安装。
5. 只有“稳定复现 → 堆栈归因 → 修复 → 同路径反证”闭环后，才能声称关闭某一崩溃原因。

### P0-B：真实长时数据采集与资源证据

现有短视频、synthetic、warm-process 和 `tracemalloc` 结果不能证明投产稳定性。使用真实目标相机素材建立矩阵，至少覆盖实际会投入使用的：

- H.264 与 HEVC；
- CFR 与 VFR；
- 目标 1080p/4K 分辨率及实际帧率；
- 正常文件、损坏/截断文件、来源运行中替换；
- Preview、Full Run、Rerun、Cancel、Close 和再次打开。

先在测试记录中定义目标硬件、典型会话时长和通过门槛；如果用户没有提供真实素材或目标时长，明确标记 `BLOCKED`，不得用 synthetic 素材代替结论。

每轮必须记录：

- 首帧与稳态 Preview latency；
- Tracking 总吞吐、Input/Compute overlap 和 GUI heartbeat；
- 主进程加全部子进程的 RSS 峰值与稳定趋势，而非只看 Python `tracemalloc`；
- 文件描述符、helper 数量、退出后残留进程；
- Cancel/Close 请求到 UI 可操作及 helper 消失的时间；
- 结果数量、状态、fingerprint、source identity/provenance；
- CPU、功耗、温度或热降频证据；若权限或工具不足，明确记录缺口。

优化前先保存可复现 baseline；优化后使用相同素材、顺序和运行轮次对照。拒绝只报告最好一次或只报告微基准。

### P1-A：安全与数据可信度 Review

按信任边界而不是按文件数量审查：

1. `.ntproj`：嵌套深度、数量/字节上限、重复键/类型混淆、NaN/Infinity、迁移、严格顺序、取消和失败原子性。
2. 媒体：symlink/regular-file、TOCTOU、stat/version guard、采样 identity 限制、seek 落点、VFR/错误 frame count。
3. helper IPC：header/body 长度、shape×dtype 溢出、EOF、重复终态、过期 generation、异常退出和进程回收。
4. 保存/导出：目录替换、权限失败、磁盘满、临时文件清理、CSV 公式注入、NPZ 无 pickle。
5. 插件：反序列化、任意代码加载、不可 pickle pipeline、第三方 subprocess contract 和 fail-closed 行为。
6. UI 数据保护：Open/Relink/Full/Rerun/Discard/Close 的取消与失败路径。

先输出带严重度、精确复现和影响的 findings，再修改已确认问题。不要以“测试通过”替代攻击面验证。

### P1-B：原生 macOS UI 与无障碍

在真实窗口而不是只用 `QT_QPA_PLATFORM=offscreen` 验证：

- 1024×768、1440×900 和 Retina 2×；
- 系统文本缩放、长路径/长标题、空/加载/完成/取消/失败/来源漂移状态；
- 全键盘焦点顺序、可见焦点、快捷键、默认与危险操作；
- VoiceOver 和 Accessibility Inspector 的名称、角色、值、状态变化与表格导航；
- Preview/处理帧语义、进度、Cancel 反馈、恢复入口和错误可操作性；
- 动态 compositor 是否存在闪烁、透明标签遗漏或旧帧残影。

当前 PySide6 6.11.1 环境曾与 Accessibility/AppKit 崩溃关联。无障碍工具必须在隔离、可恢复的运行中逐步验证；不要直接重复会触发全层级崩溃的自动化。若需升级 Qt/PySide，先建立前后最小复现和完整回归。

UI 改动必须同时提供原生截图、键盘/无障碍结果和测试；截图无裁切不等于操作完成。

### P2：产品能力缺口

在 P0/P1 稳定后再处理：

- 二维 `radius×angle` 原始 `polar_samples` 与模型特定、带物理单位的残差可视化；
- 外部自定义 pipeline config 的安全反序列化与 subprocess contract；
- 安装、打包、启动、升级和回滚路径。若目标是分发式 macOS App，还需签名/notarization；若仅为内部 Python 工具，必须把该部署范围写清楚。

不要在未关闭稳定性与数据可信度风险前扩大插件/API 表面积。

## 6. 标准执行循环

### 开工前

1. 执行 `pwd -P`，确认处于权威工作区。
2. 依次阅读 `PROJECT_INDEX.md`、`交接.md`、`README.md` 和本文件。
3. 先运行 SHA-256 校验；当前目录没有 Git，因此在任何写入前创建带时间戳的完整备份并记录其 SHA-256。
4. 运行完整 baseline，记录环境、命令、退出码和耗时。

### 每个工作包

1. 只选择一个可复现风险或一个量化瓶颈。
2. 先补 reproducer、测试或 benchmark，再做最小生产修改。
3. 同时检查成功、失败、取消、关闭、来源替换和重复执行。
4. 运行定向测试，再运行全量测试和相关性能门槛。
5. 检查是否有项目 Python 进程、helper、线程或新 `.ips` 残留。
6. 将命令、原始数据、环境、结论和限制写入 `artifacts/deepseek-YYYY-MM-DD/`。
7. 更新 `README.md`、`交接.md`、`PROJECT_INDEX.md` 和内容索引；旧数字必须保留日期或被当前复测替换。

不要同时做大规模重构、UI 重设计和 decoder 更换；否则无法归因正确性、性能或崩溃变化。

## 7. 必跑命令

```bash
cd /Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker
pwd -P

LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256

PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
python3 -m pip check

PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75
```

依赖冲突最小探针应分别在一次性子进程运行：

```bash
python3 -c 'import cv2'
python3 -c 'import av'
python3 -c 'import cv2; import av'
python3 -c 'import av; import cv2'
```

完成所有修改与验证后，重新生成索引：

```bash
find . -type f \
  ! -name '.DS_Store' \
  ! -name '*.pyc' \
  ! -path '*/__pycache__/*' \
  ! -name 'PROJECT_FILE_INDEX.sha256' \
  -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 \
  > PROJECT_FILE_INDEX.sha256

LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
```

## 8. 完成门槛

只有同时满足以下条件，才可建议 `DONE`：

- 当前全量测试、`compileall`、`pip check` 和内容索引全部通过；新增行为有失败/取消/恢复回归。
- 10 万结果项目连续三次打开仍满足 `<75 ms` heartbeat，并保持 payload/results/fingerprint/diagnostics/analysis 一致。
- 目标真实媒体矩阵完成长时运行；无未归因崩溃、结果漂移、单调 RSS 增长、孤儿 helper 或无法恢复的 Cancel/Close。
- 新鲜 `.ips` 已按堆栈归因；不能把 Qt accessibility、QThread cleanup 和 FFmpeg 冲突混为一个原因。
- 原生 Retina、系统文本缩放、键盘、VoiceOver/Accessibility Inspector 有实际证据。
- 项目打开、保存、Relink、Full/Rerun、导出和异常恢复均没有数据丢失。
- 安装与部署范围明确，并在干净环境完成启动和核心工作流。
- 没有未关闭的 P0/P1 finding；剩余限制已写明影响和接受者，不以“暂未复现”冒充修复。

缺少真实媒体、目标硬件、无障碍权限或热/功耗测量能力时，结论必须是 `BLOCKED` 或 `PARTIAL`，不是 `DONE`。

## 9. 交付格式

将独立结果写入 `collab/FROM_DEEPSEEK.md`，并使用以下结构：

```markdown
# DeepSeek → ICS-NeoTracker

## Conclusion
一句证据化结论。

## Findings
按 P0/P1/P2 排序；每项包含复现、影响、根因和状态。

## Changes Made
只列已实施且有验证的修改。

## Files Modified
列出所有源码、测试、文档和 artifact。

## Testing
列出精确命令、退出码、测试数和耗时。

## Performance and Runtime Evidence
列出 workload、环境、median/p95/max、正确性、RSS、进程和限制。

## Remaining Risks
区分事实、推断和未验证假设。

## Suggested Commit Message
即使当前无 Git，也提供一条可审计摘要。
```

第一份回复先给出：当前理解、P0-A 最小复现结果、准备修改的精确文件和验收方式；随后直接执行，不要重做已通过且没有新证据的问题。
