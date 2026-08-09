# P0-B 补充：干净环境安装/启动/核心工作流 + heartbeat 轮次证据

日期：2026-08-09（Asia/Taipei）
状态：干净环境（全新 venv）安装/全量回归/原生窗口/真实媒体核心工作流全部通过；
heartbeat 轮次 3 通过、轮次 4 出现 75.21 ms 超限（门槛 75 ms），跨负载稳定性继续
保持 `PARTIAL`。

## 干净环境（clean environment）

```bash
python3 -m venv /tmp/nt-clean-venv
/tmp/nt-clean-venv/bin/python -m pip install -e ".[desktop,media,science]"
```

| 项 | 结果 |
| --- | --- |
| 全量 unittest（venv） | 479 tests in 61.252s OK |
| 首次缺 science extra | 3 项 STFT 测试按设计 fail-closed（提示安装 science extra），补装后全过 |
| `pip check` | No broken requirements found |
| P0-B 真实媒体矩阵（venv） | 72/72 帧 ×3 素材；取消 53 ms；来源替换检测 True；截断 probe fail-closed；重开 OK |
| 原生窗口（venv 启动） | 窗口 1280×808（Retina 2× 截图 2784×1840），AX 关闭按钮优雅退出 |

证据：`benchmark-p0b-cleanenv-round1.json`（+ `.stderr.txt`）、
`p1b-native-ui/nt-cleanenv-1280x808.png`。

## heartbeat 轮次 3–4（100,000 results 项目打开，与历史同命令）

| 轮次 | 逐值 (ms) | median | max | >75 ms | 退出码 |
| --- | --- | --- | --- | --- | --- |
| 3 | 36.61 / 52.21 / 64.76 | 52.21 | 64.76 | 0 | 0 |
| 4 | 44.15 / **75.21** / 51.13 | 51.13 | 75.21 | 1 | 1 |

- `results_exact=True`、`payload_equal=True`、fingerprint `d9c2dd5992cc…` 前后一致。
- 轮次 4 单次 75.21 ms 超限（高于门槛 0.21 ms）再次复现历史波动（P1-A2 第 2 轮
  88.62 ms、更早 153.45 ms）；跨负载稳定性**未证明**，整体 `PARTIAL`，不宣称通过。

## 结论

- 干净环境安装/启动/核心工作流门槛（FORDEEPSEEK.md 第 8 节第 7 项）已有首轮证据；
  剩余：真实目标相机素材矩阵（P0-B）、VoiceOver/文本缩放（P1-B）、打包/签名范围
  （P2）仍未闭环。
