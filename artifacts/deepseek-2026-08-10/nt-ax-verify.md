# P1-B 真实窗口 AX 复核（2026-08-10，AX 服务部分恢复）

## Conclusion

2026-08-10 按 PID 的应用级 AX 树恢复可读（system-wide
`AXFocusedApplication` 仍 `-25204`）：真实原生窗口（1280×808，Retina 2×
物理 2560×1616）完整暴露窗口内 AX 层级——按钮名称/描述/启用状态、
tab 组、列表、步进器、滑块均正确；`AXPress` 激活 “Add media” 成功
（返回 0），NSOpenPanel 实例化且对话框 Cancel 按钮经 AX 关闭后主窗口
恢复；应用经 SIGTERM 优雅退出、无残留进程、无新增 `.ips`。AX 写入类
操作（窗口 resize/AXRaise）仍受环境限制（`-25201/-25206`），本轮无法
重复 2026-08-09 的窗口 resize 证据；AX 树与按钮交互为新增的当前实况证据。

## Findings

- AX 树完整可读：`AXApplication → AXWindow("Neo-Tracker") → 12 个直接
  子元素`，含 `AXButton`（Add media / Open Project / Save Project /
  Run tracking / Export CSV / Report 等）、`AXTabGroup`（Media /
  Tracking / Review / Signal / Calib / Flow / JSON）、`AXList`（Media
  tasks）、`AXIncrementor`（Preview frame number）、`AXSlider`
  （Preview frame timeline）；空项目下 Run/Export/Report/Preview 控件
  `enabled=false`，Add/Open/Save `enabled=true`，状态语义与 UI 一致。
- `AXPress` 交互成功：Add media 返回 0 → 应用日志出现 NSOpenPanel
  实例化；对话框 AX 树（Cancel enabled / Open disabled 未选文件）
  经 `AXPress Cancel` 关闭，主窗口恢复（`AXWindow title=Neo-Tracker`）。
- 环境限制：AX 服务为**部分恢复**——按 PID 读树可用，但窗口几何/动作
  属性读取与写入失败（`AXActions` 不可用、`AXSize/AXPosition` nil、
  set size `-25201`、`AXRaise` `-25206`）；System Events 也无法枚举
  窗口（`-1719`）。因此本会话无法复测窗口 resize/前台切换，该部分以
  2026-08-09 证据为准，并如实标注仍受环境限制。
- 无新崩溃：本会话真实窗口启动、AX 交互、关闭全程无 `.ips` 新增；
  未发现残留 helper/进程。

## Evidence Files

- `nt-ax-tree.json`：完整窗口 AX 层级（roles/titles/descriptions/values/
  enabled，深度 6）。
- `nt-ax-window.png`：真实窗口截图（物理 2560×1616，Retina 2×）；
  Vision OCR 确认标题、按钮、tabs、状态文本全部渲染。
- `nt-ax-window-copy.png` 为默认 1280×808 窗口截图（另存副本）
  （resize 未成功），不当作 1024×768 证据；见上方环境限制。

## Testing

- `swift /tmp/axprobe.swift <pid>`：app role=AXApplication、children、
  windows 可读。
- `swift /tmp/axdump.swift <pid> 6` → `nt-ax-tree.json`，`json.tool` 通过。
- `swift /tmp/axact.swift <pid> click "Add media"` → press 返回 0；
  应用日志 `NSOpenPanel` 运行时消息。
- `swift /tmp/axact.swift <pid> clickrole AXButton "Cancel"` → 返回 0，
  主窗口恢复。
- `screencapture -o -x -l <wid>` → PNG 2560×1616；Vision OCR 读取
  完整 UI 文本。
- 关闭：SIGTERM → 无残留进程；DiagnosticReports 无 2026-08-10 新增。

## Remaining Risks

- AX 写入/窗口几何操作与 VoiceOver 复核仍受环境限制；系统文本缩放、
  4K 外接屏、动态 compositor 闪烁复核未闭环；OS 级真实按键注入仍
  `BLOCKED`（宿主占用前台 + AX 服务不完整）。
