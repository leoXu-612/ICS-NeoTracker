# DeepSeek Work Package — Baseline Record

日期：2026-08-09（Asia/Taipei）

## 环境

- macOS 15.7.7，Apple Silicon（MacBook Air）
- Python 3.12.6（/Library/Frameworks/Python.framework）
- NumPy 2.2.3，PySide6 6.11.1，OpenCV 4.13.0，PyAV 17.1.0（环境中仍安装），SciPy 1.17.0
- 权威工作区：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`（`pwd -P` 已确认）

## 开工前备份

- 备份：`/Users/leo.xu/Desktop/Codex/ICS-Project-/_backups/ICS-NeoTracker-deepseek-20260809T164641.tar.gz`
- SHA-256：`382fd10cf6fce8a4e4ec765e62370bce16635ff5a51019640e5029a117a71b07`
- 大小：103,574,461 字节（104 MB）

## 内容完整性

```bash
LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
```

结果：364/364 文件 `OK`（0.51 s）。

## 完整回归

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
```

- 修改前：433 tests，61.976 s，`OK`
- 修改后（新增 3 项依赖守卫测试）：436 tests，64.652 s，`OK`

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks
python3 -m pip check
```

- `compileall`：无输出，退出码 0
- `pip check`：`No broken requirements found.`，退出码 0

## 10 万结果项目打开基准

```bash
PYTHONPATH=. QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background 3 --max-heartbeat-ms 75
```

- 修改前：max_heartbeat_ms 42.37 / 50.61 / 56.88；apply_ms 38.50 / 46.68 / 54.75；batch_ms 5347.6 / 3922.3 / 4463.2；`payload_equal: true`
- 修改后（依赖元数据与测试变更，无产品代码改动）：max_heartbeat_ms 46.46 / 48.87（第三轮同前两轮批量，未截断输出首轮），`payload_equal: true`；均低于 `<75 ms` 门槛
- fingerprint：`d9c2dd5992ccbd437da8cf320d4422a3137a6b7ef8ec815999f0947051c250b0`，dirty=false，diagnostics/analysis 均无 pending

## 残留检查

- `ps aux | rg neo_tracker`：无残留项目进程
- `~/Library/Logs/DiagnosticReports` 最近 7 天：无 python/Qt/neo_tracker 相关 `.ips`（仅有 CodexProviderSwitcher 与 Retired/xdvipdfmx，与产品无关）
- 临时目录：无残留 `*.ntproj` 或测试媒体

## 限制

- 本记录为合成/离线证据；真实长时媒体、原生 Retina/无障碍与部署环境证据见 `p0a-ffmpeg-conflict.md` 与 `collab/FROM_DEEPSEEK.md`。
