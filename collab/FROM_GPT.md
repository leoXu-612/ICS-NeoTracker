# DeepSeek → GPT（ICS-NeoTracker 工单交接）

更新时间：2026-08-10（Asia/Taipei）
状态：`IN_PROGRESS`（`PARTIAL`）——代码侧可实施工作已全部完成并留痕；
剩余门槛全部依赖外部条件，解锁后由 GPT 继续执行。

## 0. 一句话结论

`FORDEEPSEEK.md` 第 8 节 8 项完成门槛中 5 项已闭环，3 项被外部条件阻塞
（真实 4K/≥10 分钟素材、功耗/温度 root 权限、VoiceOver/系统文本缩放授权）；
未满足前不得 `DONE`。完整逐项审计见 `collab/FROM_DEEPSEEK.md` 末尾
“完成度审计”节与本文件第 4 节。

## 1. 权威路径与仓库状态

- 权威工作区：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`
  （`pwd -P` 验证）。旧目录 `/Users/leo.xu/Desktop/Codex/Codex_Neo-Tracker`
  与 `.../ICS-Project-/Codex_Neo-Tracker` 均为历史副本，禁止写入。
- Git：`main` HEAD = `f95c8fe`（2026-08-10），`origin/main` 已同步，工作树
  干净。共 12 个工单提交，均含可复现证据。
- 内容完整性：`PROJECT_FILE_INDEX.sha256` 已排除 `.git/` 元数据（索引命令
  修正见 `FORDEEPSEEK.md` 第 7 节），481 个内容文件校验全部通过。
- `FORDEEPSEEK.md` 是 Codex 发放任务的权威文件，**不得改写**；后续进展写入
  `collab/FROM_DEEPSEEK.md` 与 `collab/FROM_GPT.md`，证据放
  `artifacts/deepseek-YYYY-MM-DD/`。

## 2. 已完成并验证（勿重做，除非有新证据）

### P1-A 安全与数据可信度（全部 CLOSED）

1. `.ntproj` 严格解析：重复键/类型混淆拒绝、NaN/Infinity 处理、64 MiB 上限、
   100,000 result 上限、受限 JSONL 重建校验。
2. MediaReader：symlink/regular-file、TOCTOU stat/version guard、VFR 帧数
   校正、有界采样 identity（>768 KiB 只摘要头/中/尾各 256 KiB）。
3. helper IPC：128 MiB 有界信封、shape×dtype 溢出防护、EOF/重复终态/过期
   generation/进程回收；Rerun prefix pickle 由项目上限约束（性能成本点，
   非信任缺口）。
4. 保存/导出：同目录临时文件 + fsync + `os.replace` 原子替换；CSV 公式注入
   防护；NPZ 无 pickle；ENOSPC/权限失败路径清理测试。
5. 插件：config-diff fail-closed；第三方 subprocess contract。
6. UI 数据保护：Open/Relink/Full/Rerun/Discard/Close 取消与失败保留原状态；
   来源漂移时丢弃新结果并恢复旧状态（`TRACKING_SOURCE_CHANGED`）。

### P0-A 崩溃归因（CLOSED）

- 所有新鲜 `Python-*.ips` 已按堆栈归因：交互期 SIGSEGV 均为 Codex 宿主工具
  子进程（parentPid=1394，`sleep 45` 对照也复现），非本应用；`QThread
  destroyed while running` 为测试 harness 生命周期问题，已修复脚本。
- 依赖：`pyproject.toml` media extra 已移除 PyAV（仅 `opencv-python`），源码
  无 `import av`；当前解释器仍同时装 cv2 4.13 + av 17.1 会打印重复类警告，
  属部署环境问题，干净 venv 无此冲突。

### P0-B 真实媒体矩阵（PARTIAL，见第 4 节）

- H.264 640×360 真实素材 72/72 帧 digest 一致 ×2 轮；取消 41–53 ms；
  来源替换/截断 probe fail-closed；重开 ×10 无残留。
- **真实 SloMo 原片**（桌面 `PHY-EE-导出/`，8 GB 不入库）：4 个 iPhone
  SloMo HEVC 1920×1080 nominal 240 fps、avg 240.06–240.26（VFR 确认）；
  共 92,288 帧 ≈6.4 分钟。`benchmarks/benchmark_real_slomo_matrix.py`
  跑 4 素材 ×2 轮 184,576 帧 ≈14 分钟：确定性 digest、0 失败、无孤儿、
  吞吐 198.7–226.4 fps、CPU peak 31–33%、parent RSS 恒定 219 MB。
  证据：`artifacts/deepseek-2026-08-09/benchmark-slomo-round1.json`、
  `p0b-real-slomo-matrix.md`。
- CPU 采样：`benchmark-p0b-cpu-round{1,2}.json`（Full Run peak 4.2–37.3%）。

### P1-B 原生 UI 与无障碍（PARTIAL，见第 4 节）

- 原生窗口 Retina 2× 截图（1024×768 / 1280×808 / 1440×900），AX resize、
  名称/描述、Tab 焦点前进、AXPress 激活 Add media、优雅关闭。
- Qt 焦点链正/反向闭环：空项目 6 控件、媒体态 16 控件、来源漂移态 14 控件
  + Run Tracking 禁用保护；Space 激活按钮；Cancel Import 可达。
  证据：`artifacts/deepseek-2026-08-09/p1b-keyboard-traversal/`。
- **AX 服务部分恢复**（2026-08-10）：按 PID 应用级 AX 树完整可读
  （system-wide `AXFocusedApplication` 仍 `-25204`）；AXPress Add media →
  NSOpenPanel → AXPress Cancel 关闭恢复；截图 OCR 验证全部 UI 文本。
  证据：`artifacts/deepseek-2026-08-10/nt-ax-tree.json`、
  `nt-ax-window.png`、`nt-ax-verify.md`。

### Heartbeat 门槛（正常负载 CLOSED）

- 正常负载（load 3.6–4.8）**8 组连续三次 <75 ms（24/24，max 59.18 ms）**，
  results/payload/fingerprint 一致；round12 = 36.58/49.59/46.32 ms。
- 宿主满载（load >7）轮次 1/2/5 出现 76–155 ms 调度延迟超限，属 OS 调度
  延迟而非 GUI 长阻塞；跨负载稳定性 `PARTIAL`，不得宣称满载稳定。
- 证据：`artifacts/deepseek-2026-08-09/heartbeat-rounds/`（round1..12 +
  README；原始 round9 现为 round11，内容与 HEAD 逐字节一致）。

### 其他

- 干净环境：全新 venv（`.[desktop,media,science]`）479 tests / 61.252 s OK、
  pip check 干净、原生窗口启动/退出正常（`p0b-cleanenv-and-heartbeat.md`）。
- 部署范围已在 README 写明：**内部 Python 工具，非签名/notarized 分发 App**
  （无 `.app` 打包、codesign、notarization、自动更新）。满足 P2 范围声明要求。
- 索引修复：`find` 命令排除 `.git/`，提交/推送后不再伪失败。

## 3. 必跑基线（每次修改前后）

```bash
cd /Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker
pwd -P
git status -sb
LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256

PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q   # 当前 479 tests / ~68 s OK

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
python3 -m pip check

PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75
```

SloMo 矩阵（真实素材，约 14 分钟，fixture 默认 `/tmp/nt-slomo-matrix`）：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_real_slomo_matrix.py \
  --media-dir "/Users/leo.xu/Desktop/PHY-EE-导出"
```

完成后重建索引（含 `! -path './.git/*'`）并校验、更新
`collab/FROM_DEEPSEEK.md` / `collab/FROM_GPT.md` / `交接.md` /
`PROJECT_INDEX.md` 后提交推送（GitHub 偶发超时，用 6 次重试循环：
`for i in 1..6; do git push && break; sleep 8; done`）。

## 4. 未完成事项与解锁条件（GPT 的下一步）

### 4.1 阻塞项（需用户/系统提供条件，已连续多轮无变化）

| # | 事项 | 状态 | 解锁条件 |
| --- | --- | --- | --- |
| B1 | 4K 原采集、单会话 ≥10 分钟真实素材 | `BLOCKED` | 用户提供素材（放到桌面任意目录并告知路径）。拼接/循环不符合“真实单会话”口径，不可用来冒充 |
| B2 | 功耗/温度证据 | `BLOCKED` | 需要 root 运行 `powermetrics`；当前无权限 |
| B3 | VoiceOver / 系统文本缩放 | `BLOCKED` | 用户明确授权启用（会改变系统辅助功能状态） |
| B4 | AX 写入/窗口几何操作 | `PARTIAL` | AX 服务进一步恢复（当前 set size `-25201`、AXRaise `-25206`、System Events 窗口枚举 `-1719`）；恢复后复测窗口 resize |
| B5 | 满载 heartbeat 跨负载稳定性 | `PARTIAL` | 宿主负载自然回落并出现持续正常窗口，补“满载下连续三次 <75 ms”或按正常负载口径验收 |

### 4.2 解锁后的执行顺序（建议）

1. **新素材到位**：`ls /Users/leo.xu/Desktop/PHY-EE-导出/` 或用户新路径；
   4K/长素材用 `benchmark_real_slomo_matrix.py`（需适配分辨率/ROI）或
   `benchmark_real_media_matrix.py` 跑矩阵，补 `benchmark-<name>-round1.json`
   证据；无新素材则跳过。
2. **B3 授权后**：启用 VoiceOver 截取真实窗口 AX 朗读/焦点证据，系统文本
   缩放（如 150%）截图验证 reflow 无裁切；完成后恢复系统设置。
3. **B2 授权后**：`sudo powermetrics` 采样 Full Run 与空闲对照，记录功耗/
   温度/降频。
4. **AX 恢复复查**：`swift /tmp/axsyswide.swift`；若 `-25204` 消失，补窗口
   resize、AX 焦点导航、AXPress 全链证据，更新 `nt-ax-verify.md`。
5. 每批完成：定向测试 → 全量 → compileall/pip check → 索引 → 更新文档 →
   提交推送。

### 4.3 全部门槛闭环条件（第 8 节原文要点）

- 全量测试/compileall/pip check/索引通过；失败/取消/恢复回归齐备。
- 100k 项目连续三次 <75 ms + 一致性（正常负载已满足，8 组 24/24）。
- 真实媒体矩阵长时运行：无未归因崩溃、结果漂移、单调 RSS 增长、孤儿
  helper、不可恢复 Cancel/Close（VFR/HEVC 已满足；4K/≥10 分钟未满足）。
- 新鲜 `.ips` 归因（已满足）。
- 原生 Retina、系统文本缩放、键盘、VoiceOver/Accessibility Inspector
  实际证据（Retina/键盘/AX 已满足；文本缩放/VoiceOver 未满足）。
- 打开/保存/Relink/Full/Rerun/导出/异常恢复无数据丢失（已满足）。
- 安装与部署范围明确 + 干净环境启动/核心工作流（已满足）。
- 无未关闭 P0/P1 finding；剩余限制写明影响与接受者。

## 5. 环境限制（本会话实测，GPT 执行时先复查）

- 宿主（Codex/ChatGPT）长期占用 GUI 前台：`NSRunningApplication.activate`
  在 macOS 14+ 失效、鼠标/CGEvent/System Events keystroke 均无法注入
  Neo-Tracker → OS 级真实按键 `BLOCKED`；键盘证据用 Qt 应用内事件注入
  （QTest）取得。
- AX 服务退化/部分恢复：system-wide `-25204`；按 PID 读树与 AXPress 可用，
  写入与窗口几何不可用。
- 系统负载波动大（2.8–9.4，8 核）；低负载窗口（<5）适合跑 benchmark。
- 素材 8 GB 在桌面 `PHY-EE-导出/`，不入库；`.gitignore` 含
  `ICS-NeoTracker-PHY-EE-Test-Videos/`，禁止 `git add -A` 误提交大文件。

## 6. 交付格式（沿用 FORDEEPSEEK.md 第 9 节）

每次回复固定包含：`Conclusion / Findings / Changes Made / Files Modified /
Testing / Performance and Runtime Evidence / Remaining Risks /
Suggested Commit Message`。
