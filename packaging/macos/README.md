# Neo-Tracker 本机 Desktop App

构建：2026-10-05，深色专业工作台版（`20261005.1`）。沿用已有核心功能和独立打包入口。

## 使用

双击同目录中的 `Neo-Tracker.app`。Python、PySide6、OpenCV、NumPy 和 SciPy 已包含在应用里，无须另装 Python 或从源码启动。可将整个 `.app` 移到自己的“应用程序”文件夹；不要单独移动 `Contents` 内文件。

如果旧版本还在运行，请先保存项目并正常退出旧版，再打开新版。旧版下载目录未被覆盖。

默认简体中文。菜单栏“语言 / Language”可选择英文，下次启动生效。顶部是导入、追踪/取消、专注画面和导出菜单；打开/保存位于“文件”菜单或右侧“素材”检查器。左侧选择素材与追踪方式，中央查看视频，右侧分类选择器切换定标、检查、信号等参数。底部横向显示图表与光标附近数据，更多诊断/运行/修订入口收在“更多分析”。PNG 图表仍导出为白底。高级配置标识、导出字段和部分底层诊断原文保持不变。

本包针对这台 Apple Silicon Mac 的 macOS 27.0 制作，最低系统版本也限定为 27.0；没有声明兼容 Intel 或旧版系统。

本包是本地 ad-hoc 签名，完整性检查通过，但没有 Developer ID 签名或 Apple 公证。它已在本机启动并完成包内功能检查；这不等于 Gatekeeper 分发审查通过。如果系统阻止打开，请保留提示并联系开发者，不要关闭系统保护或删除隔离属性。

现有功能限制不因打包改变：大于 768 KiB 的视频项目重开后，来源复核的恢复按钮会明确清除 Results/Edits 后重追踪。需要保留人工修订时不要确认清除，先保留原项目文件。没有用真实定标真值验证测量准确性。

## 从开发工作树重建

需要 Python 3.12 与项目的 desktop/media/science 依赖，以及 PyInstaller 6.21.0、pyinstaller-hooks-contrib 2026.6。本次使用的运行库为 PySide6 6.11.1、OpenCV 4.13.0.92、SciPy 1.17.0；精确本机依赖版本见验证记录。

```bash
./script/build_and_run.sh --build-only
./script/build_and_run.sh --verify
```

无参数时构建并打开 `dist/macos/Neo-Tracker.app`，Codex 的 Run 动作使用同一脚本。可通过 `PYTHON_BIN` 指定解释器。脚本不会强制结束可能含有未保存项目的旧实例；重建前请正常关闭构建目录中的应用。

`--verify` 运行应用内显式 `--self-test` 模式，使用临时合成视频，覆盖隔离探测/预览/追踪、拟合、CSV/NPZ/Markdown 导出、项目保存与隔离重开，以及 SciPy 功能；不读取 Photos 或用户项目。测试后清理自己的临时媒体，JSON 报告写到 `build/macos/bundle-smoke.json`。这是打包兼容性检查，不重复科学准确性或全部 UI 验收。
