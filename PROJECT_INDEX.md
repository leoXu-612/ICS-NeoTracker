# ICSTracker Project Index

更新时间：2026-08-09 20:15（Asia/Taipei）

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
- `PROJECT_FILE_INDEX.sha256`：当前 414 个稳定文件的内容索引（排除 `.DS_Store`、`*.pyc`、`__pycache__`、`.git/` 与索引自身），可用于完整性校验。
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
  project.py          64 MiB/100,000-result 有界 .ntproj 原子序列化、record-framed canonical JSON 指纹、终态 runtime/backend/source provenance 指标与有界运行历史
  visualization.py    叠加层和环形时序热图
  ui/main_window.py   PySide6 桌面界面、1024×768 响应式 Preview/固定高度工具栏、Add/Open/Save 后台准备与原子提交、O(1) dirty 标记、Tracking 来源绑定/漂移隔离/恢复入口、取消/关闭终态与运行生命周期编排
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
  ui/calibration_editor.py 两点定标的长度/单位草稿、Apply/Revert 与不重画修正
  ui/run_history_panel.py 运行历史筛选、终态最新记录选择、持久 runtime backend/吞吐/Input/Compute/prefetch overlap/Review peak 摘要、缓存目标说明、Qt C++ 原生双选比较表格与可见记录导出请求
  ui/edit_history_panel.py 编辑历史类型筛选、superseded 灰显/计数、原序号、跳帧与可见记录导出请求
  ui/review_diagnostics.py Review 诊断模式、单次结果索引、单位化 velocity selector、静态 series LRU 与单结果/字段消失增量失效
  ui/confidence_plot.py 可切换、完整目标可点击且按显示像素保留端点/选中/极值/语义点的紧凑 Review 诊断图组件
  ui/review_response.py ROI-local 响应按需完整展开、历史响应图恢复与全局有界 LRU
  ui/review_response_worker.py QThread 后台历史响应图重算与取消
  ui/tracking_worker.py QThread 监控与 spawn 解码/追踪子进程、单次 child-input 序列化、异常 EOF 有界失败、严格一帧预取、16 帧结果 checkpoint、0.35 s 取消宽限后的有界 terminate/kill、运行前后来源复核、插件隔离 fail-closed、终态性能样本
tests/                 464 项 unittest 回归测试（含 media 依赖守卫、sampled identity 审查、ROI 上限与载入 fail-closed、WAV 通道/解码预检、NPZ 原子导出、`.ntproj` 重复键/类型混淆拒绝、MediaReader TOCTOU/symlink/VFR）
benchmarks/            可重复的 tracking input/compute、严格一帧预取、持久 Preview decoder session、进度 cadence、Add/Open/Save GUI heartbeat、100,000-result 项目流式打开与 Review 增量编辑、Color/Template/Edge/Annular/ROI/Review/Signal/WAV 工作集和语义对照基准
assets/                App 图标与封面资产
artifacts/             UI 审查截图、实验视频与可复现性能记录
build/                 历史构建产物，不是源码权威来源
```

## 常用命令

```bash
cd /Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker
python3 -m neo_tracker
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
LC_ALL=en_US.UTF-8 shasum -a 256 -c PROJECT_FILE_INDEX.sha256
```

源码权威目录始终是 `neo_tracker/`。`build/lib/neo_tracker/` 只用于保留历史构建结果，发布前应从新工作区重新构建。
