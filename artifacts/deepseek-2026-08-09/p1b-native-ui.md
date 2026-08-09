# P1-B — 原生 macOS UI 与无障碍（真实窗口证据）

日期：2026-08-09（Asia/Taipei）
状态：**`PARTIAL`** — 真实窗口、Retina 2×、键盘 Tab 焦点、AX 名称/描述/按钮激活、
优雅关闭均已取证；VoiceOver、系统文本缩放、完整焦点顺序、动态 compositor 逐项证据
未闭环（需交互式会话/权限），且记录到一次未归因的原生 PySide6/Shiboken 崩溃。

## 环境

- macOS 15.7.7，Apple Silicon，内置 Liquid Retina 2880×1864（2×）。
- `python3 -m neo_tracker` 原生 Cocoa 启动（无 offscreen 覆盖），窗口标题
  `Neo-Tracker`，默认 1280×808 点。
- 无障碍自动化：System Events `UI elements enabled = true`（已授权）；AX 交互通过
  一次性 Swift 工具（CGWindowList / AXUIElement / CGEvent）。

## 证据

### 窗口与 Retina

| 逻辑尺寸（点） | 截图物理像素（Retina 2× + 阴影） | 文件 |
| --- | --- | --- |
| 1280×808（默认） | 2784×1840 | `p1b-native-ui/nt-native-1280x808.png` |
| 1024×768（AX resize 应用成功） | 2184×1672 | `p1b-native-ui/nt-native-1024x768.png` |
| 1440×900（AX resize 应用成功） | 3016×1936 | `p1b-native-ui/nt-native-1440x900.png` |

- AX 读取 applied size 与请求一致（1024.0×768.0、1440.0×900.0），截图物理像素约为
  逻辑尺寸 ×2（Retina）+ 窗口阴影，未见整窗裁切（截图尺寸与窗口+阴影一致）。

### 键盘与无障碍（AX 树）

- 初始焦点：`AXButton` “Add media”，AXDescription “Add video or WAV files to the
  current project.”（名称/描述完整）。
- 连续 2 次 Tab（应用置前 + key window 后发送）：焦点移动到
  `AXStaticText`（描述 “No media loaded / Add a video or WAV file from the Media tab.”）
  ——键盘焦点顺序可前进。
- `AXPress` 激活 “Add media” 成功（result=0），随后应用日志出现
  `NSOpenPanel` 运行时消息（原生文件对话框实例化），按 Escape 关闭后应用继续运行。
- 应用在 resize/focus/press/close 全程稳定；点击关闭按钮优雅退出，无残留进程。

### 崩溃记录（已归因：工具宿主环境产物，非本应用）

- 交互期间观察到 PySide6/Shiboken QThread-QObject 生命周期 SIGSEGV（堆栈
  `Shiboken::Object::clearReferences/destroy → QObjectWrapper 析构 →
  sendPostedEvents → QThread`，EXC_BAD_ACCESS）。
- **归因实验**：全部 10 个 `Python-*.ips` 的 `parentPid=1394`（ChatGPT/Codex 应用宿主
  进程）；纯 `sleep 45`（不启动本应用、无 AX 交互）对照运行 45 秒即新增 5 个同父进程
  同类崩溃；受控本应用实例（pid 20530/21405/21449/21495/21725）全部存活并优雅退出。
  结论：该崩溃由 Codex 宿主自身的 Python+PySide6 子进程产生，**与 ICS-NeoTracker 无关**，
  不作为本应用原生崩溃归因；本应用在此会话中无新崩溃。

## 缺口（如实记录）

- VoiceOver：未启用（启用会改变用户系统状态）；AX 名称/描述/角色已取证，VOO 行为未闭环。
- 系统文本缩放：未改动系统设置（影响用户桌面），未取证。
- 完整焦点顺序/快捷键：仅验证 Tab 前进 2 步；全键盘遍历未闭环。
- 动态 compositor 闪烁/旧帧残影：静态截图无法证明；需交互式播放观察。
- 4K/外接显示器：仅本机 Retina 2× 证据。
