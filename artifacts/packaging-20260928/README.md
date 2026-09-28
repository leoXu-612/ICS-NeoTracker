# 本机 macOS 应用封装记录

产品代码基线：`b953ee2`。本次未修改 `neo_tracker/`，新增 `packaging/macos/`、单一构建入口 `script/build_and_run.sh` 和 Codex Run 配置；主仓库未合并、未推送。既有 AX 诊断脚本未运行、未修改、未打包。

交付目录：`/Users/leo.xu/Downloads/Neo-Tracker-local-20260928/`。

构建工具：Python 3.12.6、PyInstaller 6.21.0、hooks 2026.6。构建完成退出码 0；Apple Silicon arm64，`LSMinimumSystemVersion=27.0`，沿用项目推荐 ICSTrackerWaveROIPoint 图标。应用包含自身 Python 和运行依赖，不依赖开发工作树。见 `build.log`、`bundle-smoke.json` 和 `bundle-details.txt`。

打包入口先执行 `multiprocessing.freeze_support()`，随后只将既有 `-m neo_tracker.ui.project_open_worker` 参数路由到该固定模块的原 CLI，最后才启动桌面界面。保留全部隔离与 IPC 校验，不把普通应用启动误当作 helper，也不开放任意模块执行入口。

实际下载副本从 `/tmp` 启动，取消 PYTHONPATH/PYTHONHOME/VIRTUAL_ENV，PATH 仅含系统工具；`bundle-smoke.json` 为 `frozen=true`、`passed=true`，各依赖路径均在 `.app/Contents/Frameworks/`。Cocoa 窗口暴露，24 帧合成视频探测、追踪、坐标/时间核验、拟合与导出、项目保存重开、SciPy STFT/优化器全部通过；退出码 0，未残留测试 helper，临时媒体已清理。

随后在 Finder 双击下载副本，系统登记 `org.ics.neotracker` 为运行中，实际进程来自下载目录；应用留给用户继续使用。没有扫描 Neo-Tracker 的 AX 控件树，没有注入或修改系统设置。

签名状态为 ad-hoc，`codesign --verify --deep --strict` 通过；`spctl --assess --type execute` 返回 rejected，故不宣称 Developer ID/Gatekeeper 分发信任或公证已通过。没有使用本机开发证书、没有上传 Apple 公证，也没有绕过安全提示。包内测试和本机普通启动成功与上述分发限制并存。

本轮只验证封装新增的运行边界，复用原有产品验证；不重跑 817 项、不下载 Photos 视频。大文件来源复核、科学真值、完整无障碍、跨平台和公开发布边界仍与核心交接记录相同。
