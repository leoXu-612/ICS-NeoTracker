# Codex ↔ Claude ↔ GPT 协作信箱

## 权威路径

- 自 2026-07-11 起，项目权威工作区为：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`。
- 原路径 `/Users/leo.xu/Desktop/Codex/Codex_Neo-Tracker` 仅作为迁移回退副本保留，不应继续写入新改动。
- 开始任务前先确认当前工作目录位于新路径，并先读根目录 `PROJECT_INDEX.md` 与 `交接.md`。

## 信箱约定

- Codex 把消息/任务追加写入 `FROM_CODEX.md`（整文件覆盖或追加均可，修改即视为新消息）。
- Claude 监听该文件，收到后处理并把回复写入 `FROM_CLAUDE.md`。
- GPT 接手 DeepSeek 工单（2026-08-10 起）：进度与未完成事项见
  `FROM_GPT.md`，后续进展由 GPT 继续追加；DeepSeek 的交付历史在
  `FROM_DEEPSEEK.md`。
- 每条消息建议以 `## [时间] 标题` 开头，正文说明任务、涉及文件和验收标准。
- 状态标记：`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED`，写在标题行末尾。
