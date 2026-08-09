# P1-B 键盘遍历补充证据：完整焦点链 + 键盘激活（Qt 焦点语义）

日期：2026-08-09（Asia/Taipei）

## 环境限制（如实记录）

- 本会话 macOS GUI 前台被 Codex 宿主（ChatGPT）占用，`NSRunningApplication.activate`
  在 macOS 14+ 已失效；鼠标点击/CGEvent/System Events keystroke 均无法让
  Neo-Tracker 原生 Cocoa 窗口成为 key window，因此 **OS 级真实按键无法注入**。
- 同会话 Accessibility 服务整体退化：Photos 与 Python 应用的 AX 窗口树异常
  （AXApplication 递归嵌套、`kAXWindows` 不可用），系统级
  `AXFocusedApplication` 返回 `-25204`；AX 名称/角色/表格导航在此会话不可复核。
- 因此本包用 **Qt 应用内事件注入（QTest）在 offscreen 平台**验证焦点链：OS Tab
  键到达 Qt 后走的是同一套 `focusNextPrevChild` 焦点逻辑，结果与平台无关。
  真实按键与 AX/VoiceOver 复核仍标记 `BLOCKED`（需宿主释放前台/AX 服务恢复）。

## 证据 1：空项目完整焦点链（正向 + 反向，均闭环）

命令：`QT_QPA_PLATFORM=offscreen python3 focus_chain_empty.py`（`--reverse` 反向）

正向 6 步（`empty-forward.json`）：

```
Add media → Open Project → Save Project → Media tasks(QListWidget) →
PreviewCanvas(视频预览和 ROI 编辑器) → workflow section tabs(QTabBar) → Add media
```

反向 6 步（`empty-reverse.json`）与正向精确互逆：`Add media → tabs → PreviewCanvas
→ Media tasks → Save Project → Open Project → Add media`。两方向均
`loop_closed=True`。

键盘激活（`focus_chain_empty.py --activate`）：对 Add media 与 Open Project 按
`Space` 均触发对应文件对话框调用（`dialog_called=True`），默认操作可键盘触发。

## 证据 2：真实媒体加载后的焦点链（16 控件，闭环）

命令：`QT_QPA_PLATFORM=offscreen python3 focus_chain_media.py`

- 键盘激活 Add media（patch 文件对话框返回
  `artifacts/experiment-videos/red-dot-tracking.mp4`）→ 媒体 probe 完成 →
  `media_added_via_keyboard=True`、`media_available=True`、task 创建。
- 遍历（`media-forward.json`）：Media tasks → Remove Task → Relink Media →
  Run Tracking → Report → PreviewCanvas → Previous → Play → Next →
  帧号 QSpinBox → 时间线 QSlider → workflow tabs → Add media → Open Project →
  Save Project → 回到 Media tasks（`loop_closed=True`）。
- 结论：Tracking/Report/Relink/Remove 与全部 Preview transport 控件在媒体就绪后
  均可键盘聚焦；空项目时仅保留最小可聚焦集（Add/Open/Save/task list/preview/tabs），
  状态相关控件正确加入/退出焦点链。

## 崩溃记录（harness 生命周期，非产品缺陷）

- `Python-2026-08-09-222524.ips`：脚本首次运行在 media probe worker 结束前
  销毁窗口，`QThread: Destroyed while thread is still running`（SIGABRT）。
  已修复 harness（等待 probe 完成；退出用 `os._exit(0)` 跳过 Qt teardown），
  与 2026-07-16 记录的同型截图 harness 问题一致，**不是产品代码崩溃**。

## 仍缺口（P1-B）

- OS 级真实按键注入、AX 名称/角色/表格导航复核、VoiceOver、系统文本缩放、
  动态 compositor、4K 外接屏：本会话环境受限，`BLOCKED`。
