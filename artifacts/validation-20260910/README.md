# 普通原生视频流程与浅色背景回归

本轮仅运行普通 Qt 应用流程，无诊断动态库、运行时注入或私有方法替换。

## 当前证据

- 环境：macOS 26.6.1 arm64、Python 3.12.6、PySide6 6.11.1。
- 原生验证记录时间：2026-09-10 02:04:55 +08:00；Qt `cocoa`，1440×900 逻辑尺寸，Retina 2×，窗口 exposed。
- 输入：仓库夹具 `artifacts/experiment-videos/red-dot-tracking.mp4`，MPEG-4 Part 2（ffprobe `mpeg4`）、640×360、20 fps、72 帧、3.6 s。
- 输入 SHA-256：`aa5a51d2910d668bdc86eec55d443768f4b2884ea36f68b86f12f045db441353`；执行前后相同。
- 普通按钮操作完成追踪、拟合与 CSV/NPZ/Markdown 导出。72 帧均为 `ok`；CSV 和 NPZ 回读与原序列一致，NPZ 拟合预测一致且无 object array，Markdown 包含来源 revision。
- 新建测试项目保存后，通过后台读取重新打开；task ID、源序列和 FitResult 一致，草稿及项目均 clean。后续切换 Plot 会正常标记视图修改，因此 Plot 截图中的 Unsaved 不代表重开失败。
- 窗口均正常关闭，后台任务 idle，进程 exit 0。原生日志有一条 IMK mach-port 消息；它未阻止流程完成，不作为无障碍验收结论。

完整报告、导出文件、测试项目及 Fit/Plot 截图保存在：

`/Users/leo.xu/.codex/visualizations/2026/07/12/019f5664-24a7-7cb0-a53c-2d4c4b1f76b3/video-flow-20260910-cocoa-light-fix/`

报告记录基础提交 `cbe65aba663df8a2252a20913055315fac3fc723`，并保存本次运行时 `neo_tracker/`、`tests/` 的 `source.diff`；其 SHA-256 为 `5a2ee4cbad6bae6f2f273f30d7548b227549782bbebcb5ef31f28493194c06b7`。因此此处证据对应基础提交加补丁，不是未修改的基础提交。

## 背景修复

原生截图显示浅色界面中的深色文字搭配深色系统背景。新回归测试用深色应用调色板复现全部 7 个侧栏与 Fit 滚动区的 `#333333` 背景；修复后检查实际截图像素，8 个滚动区和状态栏均为 `#f5f5f7`，应用全局调色板不变。相邻字体继承、键盘焦点、1024 宽布局与 Physics 集成测试合计 13 项通过（5.028 s）。修复后的原生 Fit/Plot 截图也已逐张检查，背景和文字可读。

修改仅为现有样式表覆盖 QScrollArea/QStatusBar，以及在两处滚动容器构造后关闭内容区的自动背景填充；不改变全局或系统主题，不增加依赖。

最终全量 803 项测试通过（161.100 s、进程 exit 0），`compileall`、`pip check`、`git diff --check` 通过。

## 重跑与证据边界

从开发工作树执行 `video-physics-smoke.py`，传入一个尚不存在的输出目录；`QT_QPA_PLATFORM=cocoa` 使用普通原生窗口，`offscreen` 使用离屏窗口。脚本使用内存布局存储，仅读取夹具，并把项目与导出写入指定新目录。

此夹具的采集来源未经验证，不是定标实验真值。旧报告将它标为 H.264/真实采集的描述不能沿用。旧慢动作素材目录当前为空，本轮没有重新验证原有 HEVC 素材。此次短流程不覆盖长时采集、4K/高帧率矩阵、能耗温度、VoiceOver/完整 AX 层级或签名发布；这些验收仍未关闭。
