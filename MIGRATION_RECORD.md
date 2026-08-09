# ICSTracker Workspace Migration Record

迁移日期：2026-07-11（Asia/Shanghai）

## 路径

- 原工作区：`/Users/leo.xu/Desktop/Codex/Codex_Neo-Tracker`
- 新工作区：`/Users/leo.xu/Desktop/Codex/ICS-Project-/ICS-NeoTracker`
- 备份目录：`/Users/leo.xu/Desktop/Codex/ICS-Project-/_backups`

## 迁移策略

1. 在原工作区生成 `PROJECT_FILE_INDEX.sha256`。
2. 对原工作区创建完整压缩备份，包含源码、测试、实验视频、设计资产和历史构建产物。
3. 使用保留时间戳和权限的方式复制到新工作区。
4. 在新工作区校验 SHA-256 文件索引并运行全部测试。
5. 新工作区验证通过后设为唯一权威工作区；原目录暂时保留，不自动删除。

## 当前基线

- 迁移前目录大小：约 96 MB。
- 迁移前文件数：274（新增迁移文档和索引前）。
- 自动化测试：80 项。
- 当前工作区不是 Git 仓库，因此本次迁移使用压缩备份和 SHA-256 内容索引保证可恢复性与完整性。

## 验证记录

迁移命令、备份文件名、索引校验和新目录测试结果将在执行后补充到本节。
