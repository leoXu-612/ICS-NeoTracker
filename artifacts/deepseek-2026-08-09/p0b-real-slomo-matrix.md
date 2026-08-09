# P0-B 真实 SloMo 素材矩阵（VFR HEVC 1080p 240fps 原采集）

日期：2026-08-09（Asia/Taipei）
状态：**VFR 原采集与真实 HEVC 1080p 高帧率长时运行闭环**；4K 原采集、
单会话 ≥10 分钟、功耗/温度仍 `BLOCKED`。

## 素材来源

用户从照片“PHY-EE实验视频”相簿提供（桌面 `PHY-EE-导出/`），4 个独特
iPhone SloMo 原片（同名 `(1)` 为大小一致的重复副本，按大小去重）：

| 文件 | 编码 | 分辨率 | nominal fps | avg fps | VFR | 帧数 | 时长 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PMR00054.mov | HEVC | 1920×1080 | 240 | 240.18 | 是 | 26,926 | 112.1 s |
| PMR00055.mov | HEVC | 1920×1080 | 240 | 240.23 | 是 | 36,029 | 150.0 s |
| PMR00056.mov | HEVC | 1920×1080 | 240 | 240.26 | 是 | 5,536 | 23.0 s |
| PMR00057.mov | HEVC | 1920×1080 | 240 | 240.06 | 是 | 23,797 | 99.1 s |

应用 `probe_media` 与 ffprobe 的 fps/frame_count 完全一致；4 素材共
92,288 帧（≈6.4 分钟真实画面）。

## 运行结果（`benchmark-slomo-round1.json`，脚本
`benchmarks/benchmark_real_slomo_matrix.py`）

| 素材 | 完成帧/帧数 | 耗时 s | 吞吐 fps | CPU peak % | parent/child RSS peak KB | 确定性 |
| --- | --- | --- | --- | --- | --- | --- |
| PMR00054 | 26,926/26,926 | 135.5 | 198.7 | 33.1 | 219,169/510,592 | 是 |
| PMR00055 | 36,029/36,029 | 175.9 | 204.8 | 32.8 | 219,169/543,008 | 是 |
| PMR00056 | 5,536/5,536 | 26.2 | 211.3 | 31.2 | 219,169/462,096 | 是 |
| PMR00057 | 23,797/23,797 | 105.1 | 226.4 | 31.0 | 219,169/583,088 | 是 |

- 每素材 run1/run2 result digest 相同（确定性）；0 失败；每轮后无残留进程。
- 总处理帧数 184,576（两轮）；真实 HEVC 1080p 240fps 解码 + tracking 稳定运行
  约 14 分钟，无崩溃、无单调 RSS 增长迹象（每素材独立进程，parent RSS 恒定
  219 MB）。
- Cancel（0.5 s 请求，最短素材）：终态延迟 **52 ms**、56 帧 checkpoint、无残留。
- 来源运行中替换：`TRACKING_SOURCE_CHANGED` fail-closed 检测。
- 字节截断（60%）：probe fail-closed（`Could not open media file`）。
- 连续重开 ×10：全部成功、无残留进程。

## 门槛状态

- **VFR 原采集**：CLOSED（真实 SloMo VFR，avg fps 240.06–240.26 ≠ nominal）。
- **HEVC 1080p 原采集**：CLOSED（真实相机编码，非转码）。
- **长时运行**：单素材最长 150 s；4 素材连续两轮 184,576 帧 / 约 14 分钟
  真实处理无崩溃/漂移/孤儿进程。**单会话 ≥10 分钟仍 `BLOCKED`**（无更长素材）。
- **4K 原采集**：`BLOCKED`（素材为 1080p）。
- **功耗/温度**：`BLOCKED`（`powermetrics` 需 root）；CPU 已按轮采样。

## 命令

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_real_slomo_matrix.py \
  --media-dir "/Users/leo.xu/Desktop/PHY-EE-导出"
```

stdout → `benchmark-slomo-round1.json`，stderr → `.stderr.txt`；JSON 通过
`python3 -m json.tool`。swap/truncated 夹具写入 `/tmp/nt-slomo-matrix/`
（不入库，可按需重建）。
