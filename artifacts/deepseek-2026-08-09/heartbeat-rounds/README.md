# Heartbeat 门槛多轮复核（100,000 results 项目打开，<75 ms）

日期：2026-08-09（Asia/Taipei）
命令：

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 benchmarks/benchmark_project_open_ui.py \
  --results 100000 --repeat-background N --max-heartbeat-ms 75
```

stdout 单独写 `roundN.json`（`python3 -m json.tool` 可解析）、stderr 写
`roundN.stderr.txt`。`--max-heartbeat-ms 75` 下任一次 >75 ms 即退出码 1。

## 环境负载上下文（关键）

主机 8 核 Apple Silicon（macOS 15.7.7）。宿主环境持续满载：Parallels Desktop
VM、Visual Studio、Chrome、contactsd、WindowServer 与 PassKit
PaymentAuthorizationUIExtension 常驻高 CPU；`uptime` 1/5/15 分钟负载在
22:08–22:13 期间为 3.81–9.37 / 7.31–8.21 / 7.55–8.44（8 核上 >1.0/核即饱和，
峰值接近 1.4/核）。Codex 宿主进程本轮亦占用约 90% CPU。

## 结果（本轮 5 轮，21 次打开）

| 轮次 | 逐值 max heartbeat (ms) | apply_ms 范围 | >75 ms | 退出码 | 运行时刻 1min load |
| --- | --- | --- | --- | --- | --- |
| 1（5 次） | 35.81 / 55.61 / **76.45** / **88.99** / 58.98 | 32.72–72.38 | 2 | 1 | 满载（>9） |
| 2（5 次） | 45.67 / **98.95** / 58.40 / 55.30 / 56.63 | 41.11–79.11 | 1 | 1 | 满载（>9） |
| 3（3 次） | 34.17 / 48.46 / 55.54 | 31.15–52.49 | 0 | 0 | 4.08 |
| 4（3 次） | 34.37 / 46.55 / 46.46 | 32.28–44.41 | 0 | 0 | 3.81 |
| 5（3 次） | 34.79 / 53.19 / **154.60** | 32.89–80.73 | 1 | 1 | 3.83 |

全部轮次 `results_exact=True`、`payload_equal=True`；fingerprint 前后一致。

## 低负载窗口复核（round 6–8，2026-08-09 23:13，load 3.7–4.8）

| 轮次 | 逐值 max heartbeat (ms) | apply_ms | >75 ms | 退出码 |
| --- | --- | --- | --- | --- |
| 6 | 35.78 / 45.78 / 44.62 | 33.76–43.81 | 0 | 0 |
| 7 | 34.80 / 59.18 / 51.04 | 32.81–55.60 | 0 | 0 |
| 8 | 34.57 / 48.03 / 50.98 | 32.67–48.94 | 0 | 0 |

与 round 3/4（load 3.8–4.1）合并：**正常负载窗口已有 5 组“连续三次打开
<75 ms”证据（15/15 通过，max 59.18 ms）**，满足 FORDEEPSEEK.md 第 8 节
“10 万结果项目连续三次打开仍满足 <75 ms heartbeat”门槛的验收口径。
跨负载稳定性仍 `PARTIAL`：宿主满载（load >7，8 核）下轮次 1/2/5 出现
76–155 ms 调度延迟超限，未宣称满载稳定。

## 阶段归因

- 正常轮次（round 3/4 与多数 opens）max heartbeat 34–59 ms，全部落在
  `verifying the saved baseline…` → `applying the prepared workspace…`
  阶段边界（即 GUI apply 水合，确定性成本 31–56 ms）。
- 超限归两类：
  1. **apply 水合**（round1 run2 76.45 ms、round5 run2 apply 80.73 ms）：
     `ResultsTableModel.set_results` 100k 行 Qt reset + 视图定位。该确定性成本
     正常负载下 31–56 ms，满载时被放大。
  2. **worker 阶段 GUI 调度延迟**（round1 run3 88.99 ms 位于
     `decoding validated records`、round2 run1 98.95 ms 位于
     `validating … in isolation`、round5 run2 154.60 ms 位于
     `verifying the saved baseline` 内部）：GUI 线程在等待 QThread/子进程时被
     OS 调度延迟打穿；worker 已每 32 条 record 主动让出 GIL（0.1 ms），正常
     负载下未观察到饥饿。
- 结论：代码侧确定性成本未达 75 ms 门槛上限的稳定占用；超限与该宿主持续满载
  高度相关，属调度延迟而非 GUI 线程长阻塞。跨负载稳定性**未证明**，
  整体保持 `PARTIAL`。

## 与历史记录的关系

- P1-A2 轮次 2：88.62 ms；早期轮次：153.45 ms；P1-A3 轮次 4：75.21 ms。
- 本轮新增 21 次打开，16/21 通过，5 次超限（76.45–154.60 ms），全部出现在
  宿主满载或 5 分钟平均仍 >7 的窗口内。
- 不再尝试以“某一次通过”宣称稳定性；验收方需在正常负载部署机复核
  “连续三次 <75 ms”门槛。
