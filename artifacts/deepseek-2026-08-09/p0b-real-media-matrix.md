# P0-B — 真实媒体矩阵（可执行部分）

日期：2026-08-09（Asia/Taipei）
状态：**`PARTIAL` — 可执行部分已闭环；HEVC/1080p 为真实内容转码；VFR 原采集、
4K 原采集、长会话时长与热/功耗证据 `BLOCKED`（缺素材/权限）。**

## 目标与门槛（按工单定义）

- 目标硬件：macOS 15.7.7 Apple Silicon（本机 M 系列）、Python 3.12.6、OpenCV 4.13.0。
- 典型会话：≥10 分钟连续 1080p 跟踪（无素材，`BLOCKED`）；本机可用素材为 72 帧
  （约 3.6 s）真实采集片段，证据范围如实标注。
- 通过门槛：
  1. Full Run 完成全部帧、结果数一致、两轮 digest 相同（确定性）；
  2. Cancel/Close 到终态 <2 s 且无残留 helper/进程；
  3. 来源运行中替换 → fail-closed（`TRACKING_SOURCE_CHANGED`）且不混入新旧结果；
  4. 截断/损坏文件 probe/读取 fail-closed；
  5. 连续重开不泄漏进程；
  6. RSS 峰值有记录（无长会话趋势结论）。

## 素材矩阵

| 素材 | 来源 | 规格 | 性质 |
| --- | --- | --- | --- |
| real-h264 | `artifacts/experiment-videos/red-dot-tracking.mp4` | 640×360 / 20fps / 72 帧 / H.264 | **真实采集** |
| hevc-transcode | 上述素材 libx265 转码 | 640×360 / 20fps / 72 帧 / HEVC | 真实内容、重编码 |
| upscale-1080p | 上述素材 scale=1920:1080 | 1920×1080 / 20fps / 72 帧 / H.264 | 真实内容、重编码/放大 |
| truncated | 上述素材前 60% 字节 | 损坏（moov 丢失） | 真实内容、人为截断 |
| source-swap | 上述素材副本 | 运行中被 `synthetic-red-marker.mp4` 整体替换 | 替换测试夹具 |

命令：`PYTHONPATH=. QT_QPA_PLATFORM=offscreen python3 benchmarks/benchmark_real_media_matrix.py`
（stdout → `.json`，stderr → `.stderr.txt`；`json.tool` 校验通过）。

## 两轮结果（round1 / round2）

### Full Run（隔离 spawn 子进程，identity 绑定）

| 素材 | 轮次 | 耗时 s | 完成帧 | 结果数 | 失败 | child RSS KB | 确定性 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| real-h264 | 1 / 2 | 0.224 / 0.224 | 72 | 72 | 0 | 96,192 / 106,752 | digest 相同 |
| hevc-transcode | 1 / 2 | 0.210 / 0.223 | 72 | 72 | 0 | 120,816 / 113,376 | digest 相同 |
| upscale-1080p | 1 / 2 | 0.411 / 0.435 | 72 | 72 | 0 | 277,440 / 296,736 | digest 相同 |

- 结果 digest 对 (frame_index, filtered_state) 两轮逐位一致；无失败、无残留进程。
- 说明：640×360 ROI 较小，uint8 快速路径下 300+ fps；1080p 全帧 ROI 约 170 fps。

### Cancel（1080p 全帧 ROI，0.15 s 后取消）

| 轮次 | 取消延迟 s | 终态 | 结果数 | 残留进程 |
| --- | --- | --- | --- | --- |
| 1 | 0.041 | completed(cancelled=True) | 0 | 0 |
| 2 | 0.044 | completed(cancelled=True) | 0 | 0 |

- 协作式取消在帧间边界干净停止；terminate/kill 路径由 `tests/test_tracking_worker.py`
  既有挂起解码器测试覆盖。

### 来源运行中替换（0.4 s 时把 1080p 源替换为合成视频）

| 轮次 | 检测 | 失败消息 | 结果数 | 残留进程 |
| --- | --- | --- | --- | --- |
| 1 | True | `TRACKING_SOURCE_CHANGED: … content identity no longer matches` | 42（随后被 UI 丢弃语义恢复旧态） | 0 |
| 2 | True | 同上 | 42 | 0 |

- fail-closed：运行以 source-changed 失败终止，不会把新旧来源帧混为一次运行结果。

### 截断文件

- probe：`Could not open media file`（字节截断移除 moov）→ fail-closed。
- 说明：截断但保留 moov/尾部帧数据的“提前 EOF”路径由 `tests/test_media.py` 的
  VFR/截断表征测试覆盖（报告 count 超实际可解码帧 → `EndOfMediaError`）。

### 连续重开 ×10（真实素材）

- 10/10 成功读帧；无残留进程。

## CPU 采样（2026-08-09 补充，主进程 `ps %cpu`）

矩阵脚本新增 `_CpuSampler`（0.05 s 间隔采样主进程 CPU%，记录 peak/median/样本数）；
两轮 JSON：`benchmark-p0b-cpu-round{1,2}.json`（+ `.stderr.txt`）。

| 场景 | 轮次 | CPU peak / median（主进程） | 采样数 | parent/child RSS peak KB |
| --- | --- | --- | --- | --- |
| real-h264 Full Run | 1 / 2 | 37.3% / 37.3% · 36.4% / 34.7% | 4 / 4 | 111,559/93,568 · 110,330/108,624 |
| hevc-transcode Full Run | 2 | 4.2% / 4.2% | 4 | 113,181/104,160 |
| upscale-1080p Full Run | 2 | 23.0% / 5.95% | 8 | 137,773/291,392 |
| Cancel（0.6 s 请求） | 1 / 2 | 4.5% / 3.35% · 5.4% / — | 4 / 4 | — |
| 来源替换检测 | 1 / 2 | 29.8% / 4.8% · 25.5% / — | 8 / 8 | — |

- 72 帧片段运行仅 0.2 s，Full Run 采样点 4–8 个；CPU% 为满载宿主（8 核 load
  峰值 >9）下的观测值，含系统噪声，仅作为运行期资源占用证据。
- 功耗/温度/热降频仍需 root `powermetrics`，`BLOCKED`；长会话 CPU/RSS 趋势
  随真实素材闭环。

## 残留进程与资源证据

- 每轮每测试后 `residual_processes == []`；`multiprocessing.active_children()` 空。
- child RSS 峰值：640×360 约 96–107 MB、HEVC 约 113–121 MB、1080p 全帧约
  277–297 MB（ps RSS KB）；parent `ru_maxrss` 见 JSON。
- 72 帧片段不足以做单调 RSS 增长趋势结论；长会话 `BLOCKED`。

## 缺口（如实记录）

- **VFR 原采集**：`BLOCKED`（工作区无真实 VFR 相机素材；ffmpeg CFR→VFR 重定时在
  本机构建下不稳定，且合成 VFR 不能替代原采集结论）。
- **4K 原采集**：`BLOCKED`；1080p 为真实内容放大转码，不构成原采集分辨率证据。
- **长会话（≥10 min）**：`BLOCKED`（缺素材）。
- **功耗/温度/热降频**：`powermetrics` 需 root，未测量；CPU 采样见 JSON。
- 来源替换、取消、截断、重开等**行为闭环**已在本轮真实内容上验证。
