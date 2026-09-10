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

## 游标时间标签边界修复（2026-09-10）

原生末帧截图中的游标时间文本被右侧裁切。共享绘制代码固定使用 124×18 文本框，且只限制左边界。回归在 320、640、1440 像素宽度、12/24 pt 字体及首帧/内部/末帧共 18 个组合中复现 12 个失败。修复改为用当前字体测量文本框，并同时限制左右位置；完整六位小数、单位、选中样本和游标真实时间坐标保持不变。未新增依赖、图表框架或运行时补丁。

定向 21 项通过（1.404 s）；最终全量 804 项通过（193.690 s、进程 exit 0）。`compileall`、`pip check`、`git diff --check` 通过。几何回归覆盖上述 18 个组合，不代表任意极窄视口或任意字体下的完整可读性验收。

最终原生验证时间为 2026-09-10 02:30:11 +08:00：普通 `cocoa`、1440×900、窗口 exposed、实际 2× 缩放。72 帧追踪、拟合、三格式导出回读及项目保存/重开再次通过；第 71 个样本（零起始）的 `3.550000 s` 在窗口截图和 2788×360 的 PNG 导出中均完整可见。窗口正常关闭、后台 idle、进程 exit 0，输入 SHA-256 前后不变。原生日志仍有一条 IMK mach-port 消息，未阻止流程完成。

最终报告、截图、导出及运行时补丁：

`/Users/leo.xu/.codex/visualizations/2026/07/12/019f5664-24a7-7cb0-a53c-2d4c4b1f76b3/video-flow-20260910-cocoa-cursor-verified/`

报告基础提交为 `98e805b48517c3ac1fa61321f04aa968fd1b796d`；`source.diff` 包含源码、测试及本验证脚本，SHA-256 为 `d004d0038b746b2d8970385bb25fe013b4f808ac09705adf7a3443b9d6610d60`。首次普通原生运行记录到 1×，保存在相邻 `video-flow-20260910-cocoa-cursor-fix/`；不要把两次窗口缩放混为同一条证据。

剩余观察：窗口内嵌 Plot 底部的 `true time (s)` 轴标题仍不可见，而独立 PNG 正常；容器布局原因尚未诊断。本次只关闭游标时间标签裁切，不关闭整页布局、原生文本缩放/VoiceOver、科学数据集或发布验收。

## 内嵌 Plot 容器裁切修复（2026-09-10）

普通 Qt 几何检查确认：工作区固定的 `minimumHeight=38` 覆盖布局的内容最小尺寸，展开时分隔栏可分配 280 或 120 像素，Plot 却仍需至少 180 像素，加上标题、标签栏与按钮行后超出父容器。新增回归在默认布局和已保存的 120 像素布局、三种窗口尺寸及打开/压缩/折叠恢复的 18 个组合中全部复现裁切。Qt 的最小尺寸覆盖规则见 [QWidget 官方文档](https://doc.qt.io/qt-6/qwidget.html#minimumSize-prop)。

最终修复移除这项覆盖，并把 Export PNG 移到已有工作区标题栏，取消它独占的按钮行；通过已有布局状态信号，仅在 Plot 展开时显示按钮。图表 180 像素最小高度、按钮原生尺寸、页面留白、绘图和持久化语义保持不变。仅缩小留白的中间方案在载入项目的 Cocoa 窗口中仍要求 795 像素高；标签栏 corner widget 的中间方案则被按钮上缘裁切回归否决。两者均不是最终实现。

最终定向 42 项通过（1.981 s），包括 18 个几何组合、按钮显隐、尺寸和焦点，以及原有 Fit 滚动、工作区恢复与绘图测试。附加普通 Tab/Shift+Tab 检查确认标题栏导出按钮双向可达；不要求它紧接在 Plot 后方。最终全量 805 项通过（180.791 s、进程 exit 0）；`compileall`、`pip check`、`git diff --check` 通过。

最终普通原生运行：2026-09-10 11:09:07 +08:00，macOS 26.6.1 arm64、Python 3.12.6、PySide6 6.11.1、Cocoa、实际 1×、窗口 exposed。1024×768、1280×808、1440×900 的压缩与折叠恢复共六个场景均保持请求的窗口尺寸，Plot 及导出按钮的完整矩形在所有 Qt 父容器内。工作区实际高 284 像素；对应 Plot 为 978/1234/1394×191。1024×768 与 1440×900 窗口截图及 1394×191 独立 PNG 已逐张查看，底部 `true time (s)` 和末帧 `3.550000 s` 均完整可见。

同一原生流程再次完成 72 帧追踪、拟合、CSV/NPZ/Markdown 回读、项目保存/重开；源序列与拟合结果一致，输入 SHA-256 不变，所有窗口关闭、后台 idle、进程 exit 0。日志有一条 IMK mach-port 消息，未阻止流程完成。完整输出：

`/Users/leo.xu/.codex/visualizations/2026/07/12/019f5664-24a7-7cb0-a53c-2d4c4b1f76b3/video-flow-20260910-cocoa-plot-header-final/`

报告基础提交为 `744a626f34dcb41186d99c2d7517d88aa21e70c5`；运行时 `source.diff` 包含源码、测试与验证脚本，SHA-256 为 `551be1fdb0215dd7ee1bd6b4d3821405fddaddc455b31c18994891c870dd4556`，已与当前补丁复核一致。名称含 `plot-layout` 的早期输出是中间方案，不得替代本次最终证据。

本节关闭此前记录的内嵌 Plot 轴标题裁切，不关闭任意字体/极窄视口、当前补丁的真实 Retina 2×、VoiceOver/完整 AX、科学定标、长时媒体或发布验收。未执行既有原生诊断脚本，未注入、替换私有运行时或修改系统设置。

## 原生键盘选择与显示倍率复核（2026-09-10）

前节提及的附加 Tab/Shift+Tab 按钮可达性来自 **offscreen**，不代表 Cocoa 原生焦点链。本轮在同一代码和仓库视频上增加双向焦点链记录、平台 Tab 策略、屏幕/窗口实际倍率，以及 Data/Plot 方向键的共享选择断言。没有修改系统键盘设置或焦点策略。

最终 Cocoa 运行于 2026-09-10 11:24:52 +08:00，平台 `tabFocusBehavior=3`；Plot 页媒体状态的焦点链只有 Plot、Media tasks、PreviewCanvas、Frame spin 四项，正反向均闭环，但 **Export PNG 不在 Tab 焦点链中**。offscreen 对照运行于 11:23:42，策略为 `TabFocusAllControls`，同状态正反向均为 21 个控件，Export PNG 可达。因此不能把离屏可达性外推为原生完整键盘验收。[Qt 官方文档](https://doc.qt.io/qt-6/qstylehints.html#tabFocusBehavior-prop) 将该只读值定义为平台 Tab 焦点行为。

两个平台均通过三次方向键操作：Data `Down` 到样本/帧 36（1.8 s），Plot `Right` 到 37（1.85 s），`Left` 返回 36（1.8 s）。每次 canonical selection revision 只增加 1，Data 选中行、Plot 样本、视频帧号与保存的 true time 一致，源序列和 FitResult 保持不变。验证现等待对应帧的解码缓存与 `PreviewCanvas.has_frame()`，不把 Loading frame 截图算作预览完成；最终原生 `keyboard-plot-selection.png` 已查看，真实画面、帧号 36、游标 1.800000 s 与焦点边框同时可见。

对照还复现载入项目后的 offscreen 最小窗口为 1024×769，旧 1024×768 空/简单序列测试不足以覆盖它。产品修复仅将工作区标题栏上下留白各从 5 改为 4，按钮与 Plot 最小尺寸不变；完整视频流程在两个平台的 1024×768、1280×808、1440×900 压缩/恢复场景均通过，离屏 1024×768 截图已查看。

`system_profiler SPDisplaysDataType` 只读检查显示内建 Liquid Retina、2880×1864；但当前 Qt 屏幕和窗口实际报告 1×，普通 `show/raise/activateWindow` 后仍为 1×（active/exposed 均为 true）。未更改 Python.app 的 Info.plist、显示设置或 Qt 缩放环境变量，也未使用强制倍率来替代真实 Retina 2× 验收。

最终输出分别保存在以下共同父目录下：

`/Users/leo.xu/.codex/visualizations/2026/07/12/019f5664-24a7-7cb0-a53c-2d4c4b1f76b3/`

- `video-flow-20260910-cocoa-keyboard-final/`：最终普通原生流程、实际 1×。
- `video-flow-20260910-offscreen-keyboard-final/`：最终离屏对照。

两份报告均记录基础提交 `dc3e15e8979f4d801d9a3c72b5febd1f0f87d70e` 加 `source.diff`，补丁 SHA-256 均为 `4f796dd52eb81785d2c281cd3c71f426478f35401f30b02e08fb3d11d50d3cf3`；72 帧追踪、拟合、三格式回读、项目保存/重开及输入哈希不变均再次通过，窗口正常关闭、后台 idle、进程 exit 0。原生日志仍有一条 IMK mach-port 消息。无 `-final` 的早期输出不包含完整画面等待，不得代替最终证据。

定向 42 项通过（1.801 s）；最终全量 805 项通过（186.275 s、进程 exit 0），`compileall`、`pip check`、`git diff --check` 通过。流程报告的 `passed=true` 表示其断言完成，**不表示完整原生键盘/Retina/无障碍验收通过**：原生按钮 Tab 可达性、真实 2×、物理 OS 按键、VoiceOver 与系统文本缩放仍未关闭。此处 QTest 事件不等同于物理 OS 按键，亦未执行 AX 层级扫描。

## PNG 导出失败保护（2026-09-10）

基础提交 `9aac8b20946973e0649ded6449c705c3752597c2` 的 `PhysicsPlot.export_image` 直接把目标路径交给 `QPixmap.save`，未使用项目已有的原子写入边界。[Qt 官方 API](https://doc.qt.io/qt-6/qpixmap.html#save) 返回成功/失败；不能把失败值当作旧文件未改写的保证。临时目录中的单元测试模拟写入部分内容后返回 false，复现新目标留下半成品、既有目标内容被改写。

按 Ponytail 复用 `atomic_output_path`：先写同目录临时 PNG，写完并同步后才替换目标；编码返回 false 或文件操作抛出 OSError 时返回 false，并由原有窗口状态栏显示失败。绘图、尺寸、像素倍率及布尔返回约定不变，不增加依赖或新的持久化机制。

新增一项回归覆盖新建/覆盖两种目标的部分写入与最终替换失败，共四个子场景；修复前四项失败，修复后均保留旧内容或保持目标不存在，且无临时文件残留。原有成功导出回归补验覆盖旧文件、QImage 回读有效及目录清理。定向 `tests.test_physics_plot tests.test_analysis_workspace tests.test_analysis_workspace_responsiveness tests.test_atomic_export` 合计 53 项通过（1.991 s）。源码与测试补丁 SHA-256 为 `0d60fd73c99c7c2ea348c6b25ff166036260998322d6d83d7327c290dfedf9ac`。

最终全量 806 项通过（175.425 s、进程 exit 0），`compileall`、`pip check`、`git diff --check` 通过。测试使用 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`，`PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen`，命令为 `-m unittest discover -s tests -q`。

这些是普通 offscreen 测试中的公开保存 API/文件操作故障模拟，不是实际磁盘耗尽或断电实验。本轮未运行原生 AX 诊断、未改系统设置，亦未重新验证原生 UI、真实 2×、科学数据或长时媒体；前述验收边界保持未关闭。

## 经用户授权的系统键盘导航对照（2026-09-10）

用户明确允许临时启用系统“键盘导航”。通过系统设置的键盘页确认原值为 off，切换为 on；验证结束后恢复 off，并再次查看该页确认。没有开启 VoiceOver、辅助功能中的其他键盘功能，或修改应用焦点策略。

两次普通 Cocoa 流程均对应干净提交 `bafed13bc017b235beac65caca0731015acc9fc1`，运行时 `source.diff` 均为空（SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`）。开启时的 11:52:23 +08:00 记录为 `TabFocusAllControls`，加载项目的 Plot 页正反向焦点链均有 21 个控件且 Export PNG 可达；恢复关闭后的 11:53:48 记录为 `TabFocusBehavior.3`，正反向均为 4 个控件且 Export PNG 被跳过。这将该场景的按钮 Tab 差异定位到系统设置，无需新增应用焦点逻辑。

两轮均完成 72 帧追踪、拟合、CSV/NPZ/Markdown 回读、项目保存/重开、PNG 导出、三种窗口尺寸的布局检查及 Data/Plot 方向键与已解码视频帧同步；输入 SHA-256 不变，所有自建窗口关闭、后台 idle、进程 exit 0。开启时的 `keyboard-plot-selection.png` 已查看，画面、帧号 36、1.800000 s 游标及轴标题可见。两轮均为实际 1×，各有一条 IMK mach-port 消息，未阻止流程完成。

完整输出位于共同父目录 `/Users/leo.xu/.codex/visualizations/2026/07/12/019f5664-24a7-7cb0-a53c-2d4c4b1f76b3/` 下的 `video-flow-20260910-cocoa-keyboard-navigation-on/` 与 `video-flow-20260910-cocoa-keyboard-navigation-restored/`。

本节仅关闭“开启系统键盘导航时，已加载项目 Plot 页的双向 Tab 可达性”这一条件性检查；按键仍由 Qt QTest 发送，不是物理 OS 按键，也未覆盖其他所有工作流状态、按钮键盘激活、VoiceOver 或完整 AX。真实 2×、系统文本缩放、真实科学数据与长时媒体验收仍未关闭。本轮按 Ponytail 只补验证记录，没有修改业务代码或验证脚本，未重复运行全量测试。

## PNG 按钮空格键激活（2026-09-10）

沿用 `MainWindowPhysicsWorkspaceTests.test_plot_image_export_is_reachable_from_workspace`，将原来的直接 `.click()` 改为可见、启用且已聚焦按钮的 Qt Space 按下/松开：按下时不调用文件选择器，松开后恰好调用一次；实际 PNG 写入后可由 QImage 回读，状态栏报告成功，canonical selection 保持不变。测试窗口显式注入现有内存布局存储，避免 Cocoa 运行写入用户应用偏好。

最终普通 Cocoa 单项通过（0.725 s、exit 0）；offscreen 的 Plot、Workspace、响应式和原子导出相关 53 项通过（1.957 s、exit 0），`git diff --check` 通过。对应基础提交 `313db4559299ed57de98632e9a1587edf6a0666b` 加测试补丁 SHA-256 `5388f3d50c280f49a8f27c291587efc39cbdba098d590a0bf8b47c4be484940b`。保留原 Test ID，没有增加重复测试或业务代码，未重跑全量测试。

这是程序先设置焦点、再发送 Qt 按键的按钮激活检查；文件选择器沿用原测试替身返回临时文件路径，不代表原生文件对话框或物理 OS 键盘操作已验收。系统键盘导航保持上一轮恢复后的状态，本轮没有修改系统设置或运行 AX 诊断；其他原生无障碍与真实实验边界不变。
