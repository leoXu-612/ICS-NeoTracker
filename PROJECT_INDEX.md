# ICSTracker Project Index

更新时间：2026-08-13（Asia/Taipei）

## 权威工作区

- 新路径：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`
- 旧路径：`/Users/leo.xu/Desktop/Codex/Codex_Neo-Tracker`
- 迁移原则：新路径验证通过后作为唯一可写工作区；旧路径暂时保留为回退副本，不再继续开发。

## 快速入口

- `README.md`：产品功能、安装、运行与路线图。
- `交接.md`：Codex 与 Claude 的当前开发状态、已修复问题和未完成目标。
- `FORDEEPSEEK.md`：交给 DeepSeek 的当前执行任务、优先级、工程不变量、验证门槛与交付格式。
- `collab/PROTOCOL.md`：协作信箱与路径约定。
- `collab/FROM_CODEX.md`：Codex 任务和迁移通知。
- `collab/FROM_CLAUDE.md`：Claude 的独立审查记录。
- `collab/FROM_INTEGRATION_V03.md`：Physics Analysis v0.3 集成状态、验收数字、风险与分支交接。
- `artifacts/integration-v03/`：当前集成 SHA 的 100k、项目打开、真实 SloMo 与验收证据。
- `artifacts/v0.3.0-alpha/final-summary.md`：Physics Analysis v0.3 工单最终交付报告。
- `PROJECT_FILE_INDEX.sha256`：当前 599 个稳定文件的内容索引（排除 `.DS_Store`、`*.pyc`、`__pycache__`、`.git/` 与索引自身），可用于完整性校验。
- `MIGRATION_RECORD.md`：本次目录迁移、备份和验证记录。

## 代码索引

```text
neo_tracker/
  __main__.py         桌面程序入口
  core.py             TrackingPipeline、逐帧状态流与重型 Review 数据帧数/字节双限额、增量留存 owner/usage accounting 和显式线性 rebuild 边界
  roi.py              ROI 模型、只读缓存、广播坐标与几何包围盒有界 mask 构建
  coordinates.py      像素、世界、极坐标、环形和路径映射
  observations.py     观测模型、低临时数组 uint8 RGB 预处理、融合 Fire response、Travelling Flame 直接环形/缓存线性像素索引采样、单项只读几何缓存/非重复 polar-theta 证据、Template 内容校验的只读预处理/FFT 核缓存、Color/Brightness/Template/EdgeFront ROI 窗口计算、ROI 内归一化、公开全帧与 Tracking ROI/full-frame Color 紧凑响应存储、全局候选还原、自适应连通域包围盒/稀疏 active-only/稠密全局 OpenCV component weighting、EdgeFront 单轴梯度/代表点、NumPy 向量化连通域与有界块 FFT NCC
  states.py           状态模型
  motion.py           运动先验
  filters.py          追踪滤波器
  media.py            视频与音频媒体层、精确 seek 落点验证/重开顺序恢复、完整/有界采样 source identity、metadata/digest 同文件版本 stat guard，以及 MediaReader 打开后/逐读取/重开前后的来源版本守卫（TOCTOU fail-closed）
  atomic_io.py        同目录临时文件、flush/fsync、原子替换与失败回滚的通用持久化边界
  csv_utils.py        电子表格公式注入防护与统一 CSV 单元格净化
  analysis.py         有界分块 WAV PCM 解码、一次式 tracking source metadata 发现、低复制/cancel-aware dense-sparse 选中序列、FFT/STFT 与信号分析
  project.py          Schema v3、v1/v2 迁移、64 MiB/100,000-result 有界 .ntproj 原子序列化、稳定 task UUID/results generation、bounded analysis definitions 与 canonical fingerprint
  kinematics/         immutable SampleSeries、VFR/gap-aware derivative、Savitzky–Golay、线性/二次/可选非线性 fit、residual/unit、atomic CSV/NPZ/Markdown export 与 cancellation runtime
  application/kinematics_workspace_coordinator.py 后台 series/derivative/smoothing/export、task/generation/revision 陈旧校验、单终态与 close gate
  application/kinematics_coordinator.py 后台 fit protocol、取消/陈旧结果拒绝与单终态
  application/qt_worker_lifecycle.py terminal worker 迁回主事件循环、QThread 退出与延迟销毁的共享边界
  visualization.py    叠加层和环形时序热图
  ui/main_window.py   PySide6 桌面界面、1024×768 响应式 Preview/固定高度工具栏、Add/Open/Save 后台准备与原子提交、O(1) dirty 标记、Tracking 来源绑定/漂移隔离/恢复入口、取消/关闭终态与运行生命周期编排
  ui/shell/configuration_protection_mixin.py 配置变更前的 Pipeline JSON 草稿与 Results/Edits 双阶段保护、fail-closed 取消语义
  ui/shell/review_editing_mixin.py Review 点修正/Mark Lost 的会话内一步撤销、结果 generation 守卫与精确 dirty 状态恢复
  ui/playback_controller.py elapsed wall time 到源帧的播放时钟、低 FPS 间隔与 preview skip 计数
  ui/background_tasks.py 后台任务 generation、当前 token 与窗口关闭门控
  ui/isolated_media.py spawn-owned probe/preview decoder、受限 JSON+raw-bytes IPC、4K 工作集上限、来源版本复验与有界 terminate/kill
  ui/preview_decode_worker.py 最新请求优先的 Preview QThread 监控、显式 session 所有权与取消/过期提交隔离
  ui/media_probe_worker.py 可取消的 QThread 顺序编排；生产视频 metadata/source identity 探测委托隔离进程
  ui/project_open_worker.py subprocess 隔离项目 JSON 读取、受限 JSONL 流式重建、digest/顺序/计数验证、媒体去重核验与分阶段提交
  ui/project_save_worker.py 不可变项目快照的后台原子保存、revision/path 守卫与过期完成隔离
  ui/analysis_controller.py metadata-only Signal 数据源发现、未变化结果的紧凑来源索引缓存/显式失效、usable/total/omitted 质量摘要、选中序列构建、FFT/STFT 状态和导出
  ui/analysis_worker.py QThread 后台 WAV/tracking snapshot/FFT/STFT、preparation/processing 阶段与取消
  ui/project_controller.py 项目任务创建、保守 source identity/legacy relink 判定、ROI 与定标状态转换
  ui/media_relink_panel.py 离线媒体候选身份比对、来源变化/未验证状态、显式 Apply 与结果保护状态
  ui/task_actions_panel.py 任务影响摘要、二次确认与单步撤销状态
  ui/project_status_panel.py 项目身份、Saved/Unsaved、未 Apply 草稿、Full/Rerun 结果替换与防丢失对话框
  ui/review_controller.py Review 格式/选择、编辑历史 superseded 状态、二分定位与静态叠加缓存
  ui/results_table_model.py 保留完整行语义、按需格式化、有界行缓存与单行 copy-on-write/summary 增量更新的虚拟 Results table model
  ui/preview_canvas.py 预览、ROI/定标交互、Applied/Editable 标签分层、节点编辑、追踪叠加与物理显示像素有界的 Response RGBA 缓存组件
  ui/roi_geometry_editor.py 五类 ROI 数值字段、Qt C++ 原生节点表、节点中点插入、画布双向选择与 Apply/Revert
  ui/calibration_editor.py 两点定标的长度/单位草稿、+X 端点反转、Apply/Revert 与不重画修正
  ui/run_history_panel.py 运行历史筛选、终态最新记录选择、持久 runtime backend/吞吐/Input/Compute/prefetch overlap/Review peak 摘要、缓存目标说明、Qt C++ 原生双选比较表格与可见记录导出请求
  ui/edit_history_panel.py 编辑历史类型筛选、superseded 灰显/计数、原序号、跳帧与可见记录导出请求
  ui/review_diagnostics.py Review 诊断模式、单次结果索引、单位化 velocity selector、静态 series LRU 与单结果/字段消失增量失效
  ui/confidence_plot.py 可切换、完整目标可点击且按显示像素保留端点/选中/极值/语义点的紧凑 Review 诊断图组件
  ui/review_response.py ROI-local 响应按需完整展开、历史响应图恢复与全局有界 LRU
  ui/review_response_worker.py QThread 后台历史响应图重算与取消
  ui/tracking_worker.py QThread 监控与 spawn 解码/追踪子进程、单次 child-input 序列化、128 MiB 有界 IPC 信封与批次/终态校验、异常 EOF 有界失败、严格一帧预取、16 帧结果 checkpoint、0.35 s 取消宽限后的有界 terminate/kill、运行前后来源复核、插件隔离 fail-closed、终态性能样本
  ui/selection_session.py Video/Data/Plot/Fit 唯一 true-time selection transaction、source revision 守卫、nearest/tie 与防回环
  ui/workspaces/physics_workspace.py Data/Plot/Fit 底部实验台、100k 虚拟表、Action Registry 路由、响应式/键盘/AX 文本
  ui/physics_plot.py VFR true-time 绘图、有界 envelope decimation、gap、fit/residual、Retina 导出与键盘选择
tests/                 681 项 unittest 回归测试（含 14 类解析 fixture、Engine/Workspace/Schema v3/四组 Integration、media/source identity、项目数据保护、后台生命周期、100k、VFR、导出与 UI 回归）
benchmarks/            可重复的 kinematics 100k/derivative/fit/export、Physics UI/plot、项目打开 heartbeat，以及 tracking/media/ROI/Review/Signal/WAV 工作集和语义对照基准
                        P0-B 真实媒体矩阵（real H.264/HEVC/1080p Full Run、取消、来源替换、截断、重开；主进程 CPU/RSS 采样；stdout JSON / stderr 分离）
assets/                App 图标与封面资产
artifacts/             UI 审查截图、实验视频与可复现性能记录
build/                 历史构建产物，不是源码权威来源
```

最新证据状态（2026-08-11）：Physics Analysis v0.3 的 Engine、Workspace、
Schema v3 与 Application Integration 已在 `integration/physics-analysis-v0.3`
闭环，远端同名分支已发布并按交付 SHA 验证。本地全量 `674/674`
（121.656 s），双方 645 个唯一 Test ID 缺失 0 个，`compileall`/`pip check` 通过；
100k 项目连续三次 heartbeat 最大 `52.741 ms`，payload/fingerprint 一致；
100k engine hot pipeline P50/P95/Max 为 `77.584/91.437/92.977 ms`，
peak RSS `276.766 MiB`，cancel max `2.792 ms`。当前 SHA 的真实 HEVC
1080p/240fps VFR 原片 5,536 帧双跑 digest 一致，取消 44 ms，来源替换/
截断 fail-closed，重开 10/10，无残留 helper。工程、VFR 和持久化为
`CLOSED`；真实科学数据集与发布资格仍为 `PARTIAL`，因为尚无定标 manifest、
ground truth、完整实验类型矩阵、原生 VoiceOver/文本缩放与发行验证。完整证据见
`artifacts/integration-v03/` 与 `collab/FROM_INTEGRATION_V03.md`。

增量证据（2026-08-13）：`codex/editing-reliability-v0.4` 增加 Review
会话内一步撤销；任何后续 Results mutation 都会使撤销失效。Application QThread
统一在终态先调度 worker 退休、再退出线程，修复已复现的 Qt/GIL 析构死锁。
最终 `676/676`（121.742 s），`compileall`、`pip check`、`git diff --check`
通过；未发现新增 Python crash report、QThread warning或遗留 unittest 进程。

定标轴方向增量（2026-08-13）：二维两点定标复用已有
`LinearWorldCoordinate.y_positive`，在 Calib 提供相对 `+X` 左/右侧选择，并在
Preview 绘制 `+X/+Y`；方向随项目保存并兼容旧文件。全量压力复现同时暴露
QThread 终态的 Python wrapper 析构竞态；全部 Application coordinator 现先把
terminal worker 迁回主事件循环、再退出 QThread，最后在主线程延迟删除。Analysis
生命周期修复前循环稳定原生退出，修复后 200/200；最终 `679/679`
（125.662 s）通过，15:08 后无新 `.ips`。

定标轴控制增量（2026-08-13）：Calib 的 `Reverse +X` 只交换现有两点标尺的
草稿端点，Preview 立即更新，但 task、Results 和坐标模型在 Apply 前保持不变；
Revert 精确恢复基线，非二维坐标模型不显示该操作。1024×768 无横向滚动；最终
`680/680`（120.680 s）、`compileall`、`pip check` 与 `git diff --check` 通过。

结果状态失效增量（2026-08-13）：配置变化不再只用非空 Results 判断旧数据；
合法的 edit-only/outcome-only 状态也会统一清理 Edits/outcome/note、重置 pipeline、
递增 results generation 并失效 Signal/Physics 缓存，Runs 审计保持不变。最终
`681/681`（121.051 s）、`compileall`、`pip check` 与 `git diff --check` 通过。

跨编辑器草稿保护（2026-08-24）：ROI、Calibration 与 Marker 操作在同步 Advanced
JSON 前先保护未 Apply 的 Pipeline JSON；第一阶段同意丢弃、第二阶段取消 Results/Edits
替换时仍保持原草稿。no-op 不提示也不覆盖草稿，preset combo 使用 `QSignalBlocker`
恢复进入 handler 前的信号状态。最终 `705/705`、`compileall`、`pip check` 与
`git diff --check` 通过。

## 常用命令

```bash
cd /Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker
python3 -m neo_tracker
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
```

源码权威目录始终是 `neo_tracker/`。`build/lib/neo_tracker/` 只用于保留历史构建结果，发布前应从新工作区重新构建。
