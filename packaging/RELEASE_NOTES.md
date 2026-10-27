Neo-Tracker 的首个独立桌面 Release，基于当前应用架构重构后的 v0.2 开发基线。已包含 Python、PySide6、NumPy、OpenCV 和 SciPy，无需另装 Python。

下载与安装：

- Apple Silicon Mac：下载 `macOS-arm64.dmg`，将 Neo-Tracker 拖入 Applications；也可使用 ZIP。
- Intel Mac：下载 `macOS-x64.dmg`，将 Neo-Tracker 拖入 Applications；也可使用 ZIP。
- Windows x64：解压 `Windows-x64.zip`，保留完整目录，运行其中的 `Neo-Tracker.exe`。

本版包含视频追踪、ROI/定标、结果检查与修正、项目保存/打开、FFT/STFT、CSV 与报告导出，以及应用生命周期协调层重构。打包适配覆盖项目加载助手和 multiprocessing spawn 子进程。

验证：本机 524 项源码回归测试通过。每个平台的实际打包程序均检查了 Qt 窗口启动、视频探测、预览解码、12 帧隔离追踪、含结果项目保存/打开和 SciPy STFT。构建记录 `*-build.json` 包含源码提交、依赖版本和检查结果；`*-SHA256SUMS.txt` 用于校验下载文件。自动检查不代表完整人工验收。

macOS 包仅为 ad-hoc 签名，未经过 Apple Developer ID 签名或公证；系统可能阻止首次打开，可在“系统设置 → 隐私与安全性”中允许打开。Windows 包未做 Authenticode 签名，可能显示 SmartScreen 提示。仍属早期版本。
