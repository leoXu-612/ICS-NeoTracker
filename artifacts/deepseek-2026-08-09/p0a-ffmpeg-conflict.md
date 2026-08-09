# P0-A — 原生意外退出 / 依赖冲突最小复现与修复

日期：2026-08-09（Asia/Taipei）

## 复现（一次性隔离子进程，原始输出）

```bash
python3 -c 'import cv2; print("cv2", cv2.__version__)'
# cv2 4.13.0

python3 -c 'import av; print("av", av.__version__)'
# av 17.1.0

python3 -c 'import cv2; import av; print("both cv2-first ok")'
# objc[1945]: Class AVFFrameReceiver is implemented in both
#   .../cv2/.dylibs/libavdevice.61.3.100.dylib and
#   .../av/.dylibs/libavdevice.62.3.101.dylib (0x...)
#   This may cause spurious casting failures and mysterious crashes.
#   One of the duplicates must be removed or renamed.
# objc[1945]: Class AVFAudioReceiver is implemented in both ... (同上)
# both cv2-first ok

python3 -c 'import av; import cv2; print("both av-first ok")'
# objc[1946]: 与上面相同的 AVFFrameReceiver / AVFAudioReceiver 重复类警告
# both av-first ok
```

结论：单独导入任一模块无警告；同进程同时导入必触发 macOS objc 重复类警告（OpenCV 携带 FFmpeg 61，PyAV 携带 FFmpeg 62）。

## 使用点审计

```bash
rg -n 'import cv2|from cv2' . --glob '*.py' --hidden -g '!build/**'
# neo_tracker/media.py:462（懒加载）
# neo_tracker/observations.py:48（懒加载）
# benchmarks/benchmark_preview_media.py:18
# artifacts/code-review-ui-2026-07-22/audit_ui.py:11

rg -n -i 'pyav|\bav\.|import av|from av' neo_tracker tests benchmarks
# 无任何命中

rg -n '\bav\b' pyproject.toml
# media = ["opencv-python>=4.9", "av>=12"]   ← 唯一声明
```

事实：产品源码、测试、基准对 PyAV 零使用；`av` 只出现在 `pyproject.toml` 的 media extra 与自动生成的 `neo_tracker.egg-info/` 陈旧元数据中。

## 修复（最小方案）

- `pyproject.toml`：media extra 由 `["opencv-python>=4.9", "av>=12"]` 改为 `["opencv-python>=4.9"]`。
- 新增 `tests/test_media_dependencies.py`（3 项守卫测试）：
  1. 静态扫描 `neo_tracker/`：禁止出现 `import av` / `from av`；
  2. `pyproject.toml` 的 media extra 不得声明 PyAV；
  3. 子进程中 `import cv2` 后断言 `av` 不在 `sys.modules`（运行时守卫：即使环境中安装了 PyAV，GUI 进程也不得同时持有两套 FFmpeg）。
- 重新生成 `neo_tracker.egg-info/`（`pip install -e . --no-deps`）：requires.txt 与 PKG-INFO 均不再含 `av`。

不选择"强制 backend 分进程"方案：产品当前只使用 OpenCV，且无 PyAV 使用点；保留未使用依赖只会在 GUI 进程引入冲突风险。

## Clean environment 安装验证

```bash
tmpd=$(mktemp -d /tmp/neotracker-venv-XXXXXX)
python3 -m venv "$tmpd"
"$tmpd/bin/pip" install -q '/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker[media]'
"$tmpd/bin/python" -c 'import cv2; print("venv cv2", cv2.__version__)'   # venv cv2 5.0.0
"$tmpd/bin/python" -c 'import av'                                          # ModuleNotFoundError: No module named 'av'
"$tmpd/bin/python" -m unittest tests.test_media_dependencies -v            # 3 tests OK
```

事实：按声明安装 media extra 后 `av` 不会被安装，冲突无法由依赖集产生。注意干净环境解析到 opencv-python 5.0.0，与当前解释器 4.13.0 不同；两环境均通过导入探针。

## 验收

- 定向测试：`tests.test_media_dependencies` 3/3 通过（当前解释器与 venv 各跑一次）
- 全量：436 tests，64.652 s，OK；compileall OK；pip check OK
- 10 万结果 heartbeat <75 ms，payload 一致
- 无残留进程、无新 `.ips`

## 状态与限制

- 状态：CLOSED（依赖声明层面）。当前解释器环境中仍装有 PyAV 17.1.0（历史安装），本次改动不卸载任何已安装包；运行时守卫测试证明 `import cv2` 不会传递加载 `av`，但若第三方代码显式 `import av` 仍会触发旧警告。
- 区分：这是部署风险（依赖集可避免），不是既有产品崩溃的已证明根因；2026-07-14/15 Accessibility/AppKit 与 2026-07-16 QThread 生命周期问题未见新 `.ips`，本包未重新归因它们。
