# 中文桌面界面改版 · 2026-09-29

基线：`5fbeab2`，开发分支 `codex/preview-selection-draft-protection-v04`。没有合并主仓库、推送、改动照片原片或覆盖昨天的 App。

## 设计与实现

依照用户指定的 apple-design：参考 Finder 的工作区导航和素材管理、IINA 的内容优先，采用左侧导航/素材、中央画面、右侧参数、底部分析的稳定分区。统一浅色表面、单一蓝色强调、系统字体和即时按下反馈；删掉追踪页重复的模块列表，详细流程保留在独立“流程”页。没有加入模糊、弹簧动画或新 UI 框架。

`language.py`/`translations_zh.py` 只负责显示文字。正常启动默认简体中文，菜单可选英文并在下次启动生效；Qt 标准对话框的中文翻译文件也打入包内。项目字段、分析页键、模型值、单位、CSV/NPZ 字段保持原样。分析页改为用 tabData 保存英文稳定键，拟合模型用 currentData 判断类型，避免翻译破坏持久化或非线性参数入口。

核心控件、状态、数据保护确认和主要图表标签已中文化；底层错误、部分高级参数说明和配置 JSON 保留原文，不声称所有文字或原生无障碍已完整本地化。

## 验证范围

- `main-window-direct-ownership.log`：157 项完整执行，无原生崩溃；156 通过，1 项仍断言旧焦点颜色。更新该外观预期后，`chinese-final-regression.log` 的定向 20 项全部通过（0.996 s），包括中文导航、稳定页键、模型值、保留结果为默认按钮、导出字段不变、Review 模型和可见焦点像素。
- `focused-regression.log`：63 项中 62 通过，1 项字体检查早期错误；后续单独字体检查通过，并由上述 157 项完整批次再次覆盖。未以此旧日志宣称全部通过。
- `chinese-protection-sequence.log`：中文检查与数据保护顺序共 30 项通过（3.404 s）。
- 早期批量测试的原生崩溃记录保留在 `ui-regression.log`、`main-window-regression.log`、`main-window-final.log` 等文件。初版把已加入旧布局的控件再次移到导航栏；改为直接加入最终布局、移除重复列表后，157 项能完整结束。测试清理的实验性 deleteLater 改动未采用。该证据不表示一般 Qt/AX 问题已全部解决。
- `preview.py` 生成源代码界面快照，覆盖七个工作区及 1024×768；图中没有用户媒体。
- 最终独立 App 的 `bundle-smoke.json` 和 `bundle-smoke.png`：从 `/tmp`、无开发 PYTHONPATH 的环境运行下载副本，Cocoa 中文窗口、24 帧合成视频的隔离解码/追踪、拟合、导出、项目保存重开、SciPy 均通过。快照由实际 App 生成，不是设计稿。没有重复下载真实视频，也没有重跑全仓库 817 项。
- 最终 Cocoa 运行退出码为 0；控制台仍有字体别名、IMK mach-port 及 `Called accessibilityLabel on invalid object: 0` 平台诊断。因此只确认本次功能检查与正常退出，不声称原生无障碍诊断已清零。没有执行 AX 层级扫描或安全绕过。

## 本机包

`/Users/leo.xu/Downloads/Neo-Tracker-zh-20260929/Neo-Tracker.app`，Apple Silicon / macOS 27.0，本地 ad-hoc 签名。新旧包分开保留；不修改运行中的旧 App。构建与签名校验见 `build-final.log`，ZIP 摘要见 `archive.sha256`。

本次是 UI 与语言改版，不改变大文件来源复核时的清除/重追踪限制，也不增加科学真值验证、Developer ID 公证、真实 2×/VoiceOver 验收或跨平台承诺。
