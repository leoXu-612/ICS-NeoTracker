# P1-A2 — `.ntproj` JSON 完整性（重复键、版本/字段类型混淆）

日期：2026-08-09（Asia/Taipei）
状态：3/3 CLOSED；整体任务 `IN_PROGRESS` / `PARTIAL`（基准第 2 轮出现 1 次 >75 ms 超限）。

方法：按 P1-A 第 1 项（`.ntproj`：重复键/类型混淆/严格顺序/原子性）审计，先在 `6cd9fa0` 上补失败测试（红），再做最小修复（绿）。

## A. 重复 JSON 键被静默接受（last-wins）

### Source → Sink

来源：`.ntproj` 文本 → `NeoTrackerProject.load()` / 打开 worker `_read_project_json_for_stage()` 的 `json.loads` → 解析后 dict（后键覆盖前键）→ 模型校验。

### 失败前状态

```text
dup keys: ACCEPTED name = b   （"name":"a","name":"b" → name=b）
```

### 修复后状态

- 新增 `project.no_duplicate_json_keys`（`object_pairs_hook`），任一对象出现重复键即 `ValueError("project JSON contains duplicate key …")`。
- 拒绝只在不可信文件解析边界生效：`NeoTrackerProject.load()` 与 worker 隔离子进程文件解析（`reject_duplicate_keys=True`）。
- GUI 侧 stage 记录解码保持默认（应用自生成记录，无重复键来源），避免 object_pairs_hook 的每对象 Python 回调拖慢 QThread 心跳。

### 反证

- 正常保存→加载 roundtrip 不变；worker 隔离子进程对重复键文件报错。

## B. `version` 字段类型混淆（bool/float/str 被 int() 接受）

### 失败前状态

```text
version=True: ACCEPTED as 2
version=1.9: ACCEPTED as 2
version='1': ACCEPTED as 2
```

### 修复后状态

`_migrate_to_current()` 要求 `version` 为真 int 且在 1..2；bool/float/str 拒绝（`project version must be an integer`），越界拒绝（`unsupported Neo-Tracker project version`）。

## C. `preview_frame_index` 字段类型混淆（bool/float/str/负数被 int() 接受）

### 失败前状态

```text
preview_frame_index=True: ACCEPTED -> 1
preview_frame_index=1.5: ACCEPTED -> 1
preview_frame_index='7': ACCEPTED -> 7
preview_frame_index=-3: ACCEPTED -> -3
```

### 修复后状态

`ProjectTaskSnapshot.from_dict()` 用 `_frame_index(..., label="project task preview_frame_index")`：非负真 int 才接受，bool/float/str/负数拒绝。

## 红/绿证据

- 红（`6cd9fa0` 上）：10 项失败（重复键 ×2、version 类型 ×4、preview_frame_index ×4）。
- 绿：定向 33 项 OK；全量 458 tests / 64.489 s OK；compileall、pip check 通过。

## Benchmark（4 轮 × 5 次，stdout/stderr 分离，合法 JSON）

原始文件：`benchmark-p1a2-round{1..4}.json`（`json.tool` 通过）+ `.stderr.txt`。

| 轮次 | heartbeat 逐值 (ms) | median | p95 | max | >75ms | 退出码 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 37.02 / 53.23 / 49.34 / 47.74 / 57.91 | 49.34 | 53.23 | 57.91 | 0 | 0 |
| 2 | 36.39 / 46.00 / 88.62 / 50.81 / 58.86 | 50.81 | 58.86 | 88.62 | 1 | 1 |
| 3 | 36.18 / 47.92 / 56.02 / 46.23 / 69.31 | 47.92 | 56.02 | 69.31 | 0 | 0 |
| 4 | 38.71 / 57.19 / 50.26 / 49.36 / 68.67 | 50.26 | 57.19 | 68.67 | 0 | 0 |

- `payload_equal=True`、fingerprint `d9c2dd5992cc…` 不变；阶段均为 baseline 校验→workspace 应用。
- 第 2 轮超限运行对应 batch 4,511 ms（其余轮次 batch 3,700–3,900 ms），与系统负载一致；object_pairs_hook 已从 GUI 侧解码路径移除（GUI 侧解码应用自生成记录，重复键在隔离子进程文件解析边界已拒绝），移除前同轮 4/5 超限、移除后 1/5 超限。
- 结论：本轮 3/4 轮通过；第 2 轮单次 88.62 ms 超限未归因为代码改动，但按验收规则保持 `PARTIAL`，不宣称跨负载稳定。

## 限制

- 重复键拒绝只覆盖本项目 JSON 解析入口；`pipelines`/`media_info` 等嵌套 dict 的深层类型校验仍沿用现有 `apply_pipeline_config`/模型校验。
