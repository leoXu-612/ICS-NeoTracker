# P1-A4 — helper IPC 有界化 + 保存/导出/插件/UI 数据保护审计

日期：2026-08-09（Asia/Taipei）
状态：P1-A 第 3–6 项全部闭环；整体任务 `IN_PROGRESS` / `PARTIAL`（真实媒体矩阵与
原生无障碍/部署证据仍未闭环）。

方法：按信任边界审计；第 3 项发现并修复可复现缺口（红 4 → 绿 4），第 4–6 项审计未发现
新再现缺陷，补齐缺失的回归矩阵（保存/导出 10 项、插件 config-diff 3 子项）。

## P1-A 第 3 项：helper IPC 长度/批次上界（已修复）

### 失败前状态（复现）

- `tracking_worker` 子进程→父进程消息用裸 `connection.send/recv()`（pickle 流），
  `recv()` 无 maxlength，子进程若异常发送超大消息可无限占用父进程内存；隔离媒体
  helper 已用 `send_bytes/recv_bytes(maxlength=…)` 有界信封，两处不一致。
- `_accept_isolated_results` 不限制结果批次数量：17 个连续帧结果（超过
  `checkpoint_frames=16`）会被全部接受。
- terminal 消息未做形状校验，畸形消息只在通用异常路径报“failed unexpectedly”。

### 修复

- 新增 `TRACKING_IPC_MAX_MESSAGE_BYTES = 128 MiB`（覆盖 64 MiB debug-history 留存上限
  + 单帧重响应 48 MiB 上限 + 余量）；子进程 `_encode_isolated_message`（pickle 后限长），
  父进程 `_receive_isolated_message`（`recv_bytes(maxlength=…)` + `pickle_loads`）。
- 子进程消息超限即抛错 → 运行以明确失败终止（不静默丢 checkpoint）；父进程 `OSError`
  （超长/断管）置 EOF 并强制 cancel/terminate/kill。
- `_accept_isolated_results` 校验 `isinstance(list)` 且 `len ≤ checkpoint_frames`。
- `_validate_isolated_terminal` 校验 7 元组 + dict 终态；畸形终态直接 emit failed。

### 反证

- 真实 spawn 全链路 30 项 tracking_worker 测试（含取消/挂起/崩溃/EOF）通过；
  正常 100k 项目与既有检查点协议不受影响。

## P1-A 第 4 项：保存/导出原子性与失败清理（审计 + 回归矩阵）

审计结论：`atomic_text_writer`/`atomic_output_path`/`project.save`/`write_fft_npz` 均
遵循同目录临时文件 → flush/fsync → `os.replace` → 父目录 fsync → finally 清理临时文件；
写入/fsync/replace 任一步失败都保留既有目标；CSV 表头与单元格统一走
`spreadsheet_safe_cell`（`=`/`+`/`-`/`@` 前缀转义）；NPZ metadata 为 JSON 字符串、无
pickle/object 数组。未发现新的可复现缺陷。

新增 10 项回归测试（此前该区域零覆盖）：

- 文本/二进制原子写成功发布且无临时残留；
- 写失败/fsync 失败（模拟 ENOSPC）/目标为目录：既有目标逐字节保留、临时文件清理；
- CSV 公式注入：`=`, `+`, `-`, `@`、空白前缀全部转义，表头同样转义，普通值不变；
- CSV 失败保留既有文件；
- NPZ 导出失败（模拟 ENOSPC）保留既有文件并清理临时文件；
- 项目保存失败（模拟 os.replace ENOSPC）保留既有文件并清理临时文件。

## P1-A 第 5 项：插件/配置信任边界（审计 + 锁定测试）

审计结论：pipeline config 反序列化是**白名单分派**（ROI/坐标/观测/状态/运动/滤波/
优化器按字符串 type 构造硬编码类），无 importlib/exec/getattr 任意类实例化；模板尺寸、
ROI 点数、观测工作量均有上限；项目载入时 `apply_pipeline_config` 后执行
`first_expected_config_diff`，未知模块类型 → config 未精确应用 → **项目载入失败**（非
静默降级）。第三方 pipeline 适配器只走进程内路径，`process_isolation=True` 时
`_builtin_pipeline_supports_process_isolation` 判否 → fail-closed（既有测试覆盖）。

新增测试：`test_snapshot_unknown_pipeline_module_fails_closed_via_config_diff`
（observation/state/motion 三类未知 type 均拒绝载入，3 个子项）。

## P1-A 第 6 项：UI 数据保护（审计，未发现新缺口）

- Open：token/generation 门控，worker 完成/失败/取消回调先校验
  `_background_tasks.is_current(job.token)`；`_apply_project` 先在临时列表构建全部
  task（失败时关闭 reader 并保留当前工作区），成功后才原子切换。
- Save：revision/path 守卫，`_project_content_revision` 与 job 比对，过期快照不标记
  clean；保存失败提示且不丢数据。
- Full/Rerun：失败/取消/来源漂移恢复 `job.previous_results`/edit_history/outcome；
  来源漂移时进入 relink review 并禁用导出；结果替换仅提交一次。
- Close：`BackgroundTaskCoordinator.closing` 门控，worker 全部停止后才真正关闭；
  Cancel/Close 均有终态与恢复入口。
- 既有覆盖：`tests/test_ui_main_window.py` 143 项（open/save/close/cancel/relink/
  restore）、`tests/test_project_controller.py` 19 项、`tests/test_tracking_worker.py`
  30 项。

## Changes Made / Files Modified

- `neo_tracker/ui/tracking_worker.py`：有界 IPC 信封、批次数量上限、终态校验。
- `tests/test_tracking_worker.py`：+4 项（1 行为红 → 绿，3 项新协议契约）。
- `tests/test_atomic_export.py`：新增 10 项保存/导出安全回归矩阵。
- `tests/test_project_controller.py`：+1 项（3 子项）未知配置模块 fail-closed 锁定。
- `collab/FROM_DEEPSEEK.md`、`交接.md`（3.80）、`PROJECT_INDEX.md`、
  `PROJECT_FILE_INDEX.sha256`、本文件。

## Testing

```bash
# 红：批次上界行为失败（17>16 被接受）+ 3 项 ImportError
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest tests.test_tracking_worker.TrackingWorkerTests.test_isolated_results_batch_count_is_bounded \
  tests.test_tracking_worker.TrackingWorkerTests.test_isolated_message_encode_rejects_oversized_payload \
  tests.test_tracking_worker.TrackingWorkerTests.test_isolated_receive_rejects_oversized_frame \
  tests.test_tracking_worker.TrackingWorkerTests.test_isolated_terminal_validation_rejects_malformed
# FAILED (failures=1, errors=3)

# 绿：tracking_worker 30 项、atomic_export 10 项、project_controller 19 项
# 全量：Ran 479 tests in 62.192s OK
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q neo_tracker tests benchmarks  # 0
python3 -m pip check   # No broken requirements found.
pgrep -fl "neo_tracker|isolated_media|benchmark_project"  # 无残留
```

## Performance and Runtime Evidence

- IPC 长度上限为 128 MiB，覆盖 debug-history 64 MiB 上限 + 单帧 48 MiB 帧字节上限；
  正常检查点（每批 ≤16 结果、重数组仅存最近 4 帧）远低于上限，不改变吞吐语义。
- 全量 479 tests / 62.192 s OK；compileall、pip check 通过；无残留进程。
- 本批不涉及 project-open UI 路径；历史 heartbeat 波动记录保留，整体 `PARTIAL`。

## Remaining Risks

- `_isolated_child_input`（Rerun prefix）pickle 大小由 100,000-result 项目上限约束，
  未设独立字节上限；大项目 Rerun 的 spawn 参数序列化仍是性能/内存成本点（非信任缺口）。
- 保存/导出审计依赖模拟 ENOSPC/权限失败；真实磁盘满、跨设备与目录替换的实机矩阵
  建议在 P0-B 真实环境中补跑。
