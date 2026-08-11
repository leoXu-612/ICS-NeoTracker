# Integration → Maintainers — Physics Analysis v0.3

更新时间：2026-08-11（Asia/Taipei）

## 结论

Engine、Analysis Workspace、Project Schema v3 与应用生命周期已完成集成；本地验收达到 `MERGE_READY`，真实科学数据集与发布资格仍为 `PARTIAL`。

## 分支

- Base: `a0e9d94aa50adf80d7a524171e6dbec33baa7c8e`
- Integration branch: `integration/physics-analysis-v0.3`
- Verified code SHA: `8f5f9d6`
- Engine source head: `ad17735`
- Workspace source head: `c0984a2`

## 已集成能力

- `TrackerResult.time_s` 驱动的 frame-aligned raw/filtered/filter-velocity series、VFR 一/二阶导数、显式 gap/edge 策略、Savitzky–Golay、线性/二次与可选非线性拟合、residual/units。
- Data/Plot/Fit 底部物理工作台、唯一 true-time SelectionSession、Video/Table/Plot/Fit 同步、Action Registry、Inspector、1024/Retina/键盘/AX 文本。
- Application 层后台 build/derive/smooth/fit/export；task UUID、results generation、source revision 三重陈旧校验；取消、close、queued-completion race 均为单一终态。
- Schema v3 只保存有界 analysis definitions、config、range、view 和 provenance；v1/v2 兼容迁移；重开会恢复可见 derivative/smoothing/fit，stale definition 保留但禁用。
- CSV/safe NPZ/Markdown 原子导出；不持久化 100k 派生数组、pickle/object array、Widget 或 Worker runtime。

## 最终验证

- Full suite: `668/668`, `119.740 s`, OK。
- `compileall`: exit 0；`pip check`: clean。
- Preview worker 生命周期：`100/100` 连续重复通过；最终全量后无新增 Python crash report。
- 100k project open ×3：heartbeat `35.701/49.397/52.741 ms`；payload/fingerprint 一致；physics apply `13.305–18.456 ms`。
- 100k engine hot pipeline P50/P95/Max：`77.584/91.437/92.977 ms`；RSS peak `276.766 MiB`；cancel max `2.792 ms`。
- 100k UI：plot prepare P95 `4.002 ms`；paint P95 `28.047 ms`；heartbeat max `31.954 ms`；table attach P95 `0.0028 ms`；0 helper。
- 真实 SloMo 当前 SHA：HEVC 1080p/240fps VFR、5,536 帧双跑同 digest；cancel `44 ms`；source replacement/truncation fail-closed；reopen 10/10；0 helper。

## 最终审计修复

- 修复 100k physics table 首次选中触发全表 `ResizeToContents` 扫描；列宽改为固定可调，避免惰性 O(N) hydration。
- Python-heavy snapshot/revision/series loop 增加协作让出；共享高代 GC guard 在所有终态恢复原阈值。
- 修复 coordinator cancel-after-completed 的零业务终态竞态。
- 修复 project reopen 未消费 fit/range/view/visible 的定义重放。
- 修复 build output revision 未绑定返回 series 的边界。
- 修复 Preview worker 在 QThread 停止后才销毁导致的间歇 `EXC_BAD_ACCESS`。

## 状态边界

```text
Synthetic numerical correctness: CLOSED
Engineering regression: CLOSED
VFR semantics: CLOSED
Project persistence: CLOSED
Real scientific dataset: PARTIAL
Release qualification: PARTIAL
```

当前真实视频没有定标 metadata 与 ground truth，只能关闭工程/VFR/生命周期回归，不能宣称所有实验已科学验证或产品已 production-ready。完整证据见 `artifacts/integration-v03/`。
