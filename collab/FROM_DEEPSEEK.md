# DeepSeek → ICS-NeoTracker

## Conclusion

P0-A（OpenCV/PyAV 双 FFmpeg 栈冲突）已按最小方案关闭：`media` extra 移除零使用点的 `av` 依赖并补齐 3 项守卫测试，436 项回归、compileall、pip check、内容索引与 10 万结果 heartbeat 全部通过；真实长时媒体、原生 macOS 无障碍与部署证据仍缺失，任务保持 `IN_PROGRESS`，整体结论为 `PARTIAL`。

## Findings

### P0-A：`media` extra 同时声明 OpenCV 与 PyAV，同进程导入触发 FFmpeg 61/62 冲突

- 复现：`python3 -c 'import cv2; import av'` 与 `python3 -c 'import av; import cv2'`（一次性子进程，任意顺序）均输出 macOS objc 警告：`AVFFrameReceiver`/`AVFAudioReceiver` 在两个 `libavdevice` 中重复实现（cv2 携带 FFmpeg 61.3.100，av 携带 FFmpeg 62.3.101），提示可能出现类型转换失败与神秘崩溃；单独导入任一模块无警告。原始输出见 `artifacts/deepseek-2026-08-09/p0a-ffmpeg-conflict.md`。
- 影响：部署风险。若安装 `[media]` 后任何代码（或第三方依赖）在 GUI 进程同时导入两者，可能产生原生崩溃；且该依赖集与"GUI 进程不加载两套 FFmpeg"不变量冲突。
- 根因：`pyproject.toml` media extra 声明了 `av>=12`，但 `neo_tracker/`、`tests/`、`benchmarks/` 对 PyAV 零使用；`neo_tracker.egg-info/` 中的 `av>=12` 为自动生成的陈旧元数据。
- 状态：CLOSED（依赖声明层面）。修复与验证见 "Changes Made" / "Testing"。

### P0-A 关联：Qt accessibility、QThread 生命周期、OpenCV/FFmpeg 旧崩溃报告

- 2026-08-09 复核：`~/Library/Logs/DiagnosticReports` 最近 7 天无 python/Qt/neo_tracker 相关 `.ips`（仅有 CodexProviderSwitcher 与 Retired/xdvipdfmx，与产品无关），433→436 项回归无异常退出。
- 结论：旧报告（2026-07-14/15 Accessibility/AppKit、2026-07-16 QThread）无新复现证据，本包不重新归因；依赖冲突已从依赖声明层面关闭，但"环境显式导入 PyAV"仍可能触发警告（见 Remaining Risks）。

### P0-B：真实长时媒体矩阵

- 状态：BLOCKED。工作区 `artifacts/experiment-videos/` 仅含 72 帧红点短视频等 synthetic/短样本，无目标相机 H.264/HEVC、CFR/VFR、1080p/4K 真实素材；未提供目标硬件、典型会话时长与通过门槛，故未用 synthetic 替代结论。
- 已记录待办：首帧/稳态 Preview latency、Tracking 吞吐与 overlap、whole-process RSS、fd/helper 残留、Cancel/Close 时延、结果/fingerprint/provenance、热/功耗证据；优化前需保存可复现 baseline。

### P1-A：安全与数据可信度 Review

- 状态：PARTIAL。工程不变量（原子保存、IPC 上限、来源复验、取消终态、无 pickle）由现有 436 项回归覆盖，本次未发现新的可复现缺陷；未按信任边界重跑完整攻击面审计（`.ntproj` 深度/NaN、media TOCTOU、helper IPC 溢出、导出失败路径等），需独立工作包逐项产出带严重度与精确复现的 findings。

### P1-B：原生 macOS UI 与无障碍

- 状态：BLOCKED（权限/工具）。本环境仅能提供 offscreen 证据；原生 Retina 2×、系统文本缩放、全键盘焦点、VoiceOver/Accessibility Inspector、动态 compositor 残留均需真实窗口会话与辅助功能权限，未在本包执行。

### P2：产品能力缺口

- 状态：NOT STARTED。二维 `radius×angle` 原始采样可视化、外部 pipeline config 安全反序列化、安装/打包/签名/notarization 部署路径均未动工；按工单要求，不在 P0/P1 稳定前扩大插件/API 表面积。

## Changes Made

- `pyproject.toml`：`media` extra 由 `["opencv-python>=4.9", "av>=12"]` 改为 `["opencv-python>=4.9"]`（移除零使用点的 PyAV）。
- 新增 `tests/test_media_dependencies.py`（3 项守卫测试）：
  1. `neo_tracker/` 静态扫描禁止 `import av` / `from av`；
  2. media extra 不得声明 PyAV；
  3. 子进程 `import cv2` 后断言 `av` 不在 `sys.modules`（运行时守卫）。
- 重新生成 `neo_tracker.egg-info/`（`pip install -e . --no-deps`），requires.txt/PKG-INFO 同步移除 `av`。
- `artifacts/deepseek-2026-08-09/`：`baseline.md`（环境/备份/回归/基准/残留）与 `p0a-ffmpeg-conflict.md`（复现原始输出/审计/修复/clean-env 验证/限制）。
- 文档：`README.md` 安装节说明 media extra 仅含 OpenCV 及不引入 PyAV 的原因；`交接.md` 新增 3.75 节与 2026-08-09 复核基线；`PROJECT_INDEX.md` 更新测试数与索引语义（排除 `.git/`）。
- `PROJECT_FILE_INDEX.sha256` 重新生成并校验。

说明：`build/` 为历史构建产物，`pip install -e .` 曾短暂重生成其内容，已从开工前备份定向还原，未以 `build/lib/` 作为任何修改来源。

## Files Modified

- `pyproject.toml`
- `tests/test_media_dependencies.py`（新增）
- `neo_tracker.egg-info/requires.txt`、`neo_tracker.egg-info/PKG-INFO`（重新生成）
- `artifacts/deepseek-2026-08-09/baseline.md`（新增）
- `artifacts/deepseek-2026-08-09/p0a-ffmpeg-conflict.md`（新增）
- `README.md`
- `交接.md`
- `PROJECT_INDEX.md`
- `PROJECT_FILE_INDEX.sha256`
- `collab/FROM_DEEPSEEK.md`（本文件，新增）

## Testing

```bash
# 定向（当前解释器与全新 venv 各一次）
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest tests.test_media_dependencies -v
# Ran 3 tests ... OK

# 全量回归
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -q
# 修改前 433 tests / 61.976 s；修改后 436 tests / 64.652 s ... OK

PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
# 退出码 0，无输出

python3 -m pip check
# No broken requirements found.

LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
# 396/396 OK

# 10 万结果项目打开
PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75
# max_heartbeat_ms 46.46/48.87（修改前 42.37/50.61/56.88），门槛 <75 ms；payload_equal true

# Clean environment（全新 venv）
python3 -m venv "$tmpd" && "$tmpd/bin/pip" install -q '.[media]'
"$tmpd/bin/python" -c 'import cv2; print(cv2.__version__)'   # 5.0.0
"$tmpd/bin/python" -c 'import av'                            # ModuleNotFoundError
```

## Performance and Runtime Evidence

- Workload：10 万结果 `.ntproj` 打开 ×3（修改前后各一轮），offscreen，`repeat-background 3`。
- 环境：macOS 15.7.7 Apple Silicon；Python 3.12.6；NumPy 2.2.3；PySide6 6.11.1；OpenCV 4.13.0（解释器）/ 5.0.0（venv）；PyAV 17.1.0（环境中仍安装，未使用）。
- 结果：heartbeat 46.46/48.87 ms（修改前 42.37/50.61/56.88），均低于 `<75 ms`；apply 44.40/46.49 ms；payload/results/fingerprint/diagnostics/analysis 一致；fingerprint `d9c2dd59…`。
- RSS/进程：本次未做 whole-process RSS 与热/功耗测量（P0-B 记录为缺口）；回归与基准后无残留项目进程、无新 `.ips`。
- 限制：offscreen/合成证据不证明原生 Retina、compositor、真实相机素材或长时会话稳定性。

## Remaining Risks

- 事实：当前解释器环境中仍装有 PyAV 17.1.0；本次仅清理依赖声明，未卸载任何已装包。运行时守卫证明 `import cv2` 不传递加载 `av`，但若第三方代码显式 `import av`，旧 objc 重复类警告仍会出现。
- 推断：旧崩溃报告（Accessibility/AppKit、QThread）与 FFmpeg 冲突无新复现证据，未做堆栈归因；不能由"无新 `.ips`"推断这些路径已修复。
- 未验证假设：真实相机素材的长时稳定性、原生无障碍与部署签名/notarization 均缺证据；在这些闭环前不得建议 `DONE`。
- 待办：P0-B 需用户提供目标素材与硬件/时长门槛；P1-A 需按信任边界逐项审计；P1-B 需真实窗口与辅助功能权限。

## Suggested Commit Message

```text
fix(media): drop unused PyAV from media extra to avoid FFmpeg 61/62 conflict

- Remove av>=12 from pyproject media extra (zero usage in source/tests/benchmarks)
- Add tests guarding product source against PyAV imports and declaring media extra
- Regenerate egg-info metadata; record baseline, repro, and clean-env verification
- Full suite: 436 tests OK; heartbeat <75 ms on 100k-result open; index 396/396 OK
```

Git 备注：仓库由用户于 2026-08-09 16:51 初始化（Initial commit `f999fae`），本包修改已在初始提交之后的工作树中，随后由 DeepSeek 提交留痕。
