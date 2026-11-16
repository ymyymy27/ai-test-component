# CLI 与数据契约（一期）

> 状态：2026-11-16 设计基线（D2）。实现依据：[轻量 Trae 插件修改计划](../../../修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md)；需求见[需求规格](../02-需求/02-需求规格.md)。
> 本册定义命令、字段、状态与错误码；扩展与 Python 都按本册实现，不另造第二套。

## 1 通用约定

### 1.1 调用

```bash
python -m aitest.cli <command> --workspace <dir> [--input <json-file>] [--confirm]
```

- 输入：`--input` 文件或 stdin JSON，二者等价；
- 输出：stdout 一行 JSON；日志写 stderr；
- 退出码：`0` 成功；`1` 业务拒绝；`2` 参数或环境错误；
- 所有命令幂等：同一 `request_id` 重复提交返回同一结果，不重复执行有副作用的动作。

### 1.2 信封

请求：

```json
{"command": "project.add", "request_id": "uuid", "payload": {}}
```

成功：

```json
{"ok": true, "request_id": "uuid", "data": {}}
```

失败：

```json
{"ok": false, "request_id": "uuid", "error": {"code": "INVALID_PATH", "message": "目录不可写", "next_step": "换一个可写目录或修正权限"}}
```

### 1.3 路径与时间

- 路径一律绝对路径进入 CLI，落盘时项目内用相对路径；
- 时间用带时区 ISO 8601，如 `2026-11-16T14:20:00+08:00`；
- ID 用前缀 + UUID/短哈希，如 `proj_`、`task_`、`plan_`、`run_`、`issue_`、`report_`。

## 2 错误码

| code | 含义 | next_step |
| --- | --- | --- |
| `INVALID_REQUEST` | 信封或字段不合法 | 按字段提示修正 |
| `INVALID_PATH` | 目录不存在或不可写 | 换目录或修权限 |
| `WORKSPACE_LOCKED` | 业务文件被另一进程持有 | 等待或关闭另一实例 |
| `PROJECT_NOT_FOUND` | 项目不存在 | 先建项目或检查 ID |
| `TASK_NOT_FOUND` | 任务不存在 | 先建任务 |
| `PLAN_NOT_PUBLISHED` | 计划未发布 | 先 `plan.publish` |
| `SCOPE_EMPTY` | 范围为空的 | 至少选一条验收项 |
| `CONFIRMATION_REQUIRED` | 缺执行前确认 | 在面板确认或加 `--confirm` |
| `RUN_ACTIVE` | 同一项目已有运行 | 等待、取消或查询现有运行 |
| `RUN_NOT_FOUND` | 运行不存在 | 检查运行 ID |
| `RUN_STATE_CONFLICT` | 当前状态不允许该操作 | 先查询状态 |
| `MODEL_UNAVAILABLE` | 模型未配置或不可达 | 配置密钥或检查网络 |
| `MODEL_OUTPUT_INVALID` | 模型输出不合格 | 重试或改用人工 |
| `GITHUB_UNAVAILABLE` | `gh` 缺失、未登录或读取失败 | 安装/登录后重试 |
| `LINK_CONFIG_MISSING` | 缺日志或查询配置 | 补项目配置 |
| `DEPENDENCY_MISSING` | 缺运行依赖 | 在确认页授权安装或手动安装 |
| `SCRIPT_REJECTED` | 生成脚本含危险操作 | 检查脚本或改用手写 |
| `SCHEMA_UNSUPPORTED` | 旧数据格式 | 备份后转换或重建 |
| `BACKUP_INVALID` | 备份损坏或校验失败 | 换备份 |
| `CLEANUP_FAILED` | 清理命令未配置、无法执行或退出码非零 | 检查项目配置与项目根目录 |
| `DISK_FULL` | 磁盘不足 | 清理磁盘后重试 |
| `INTERNAL` | 未预期错误 | 附 request_id 反馈 |

## 3 枚举

### 3.1 运行状态

`running / completed / cancelled / interrupted / execution_error`

### 3.2 业务结论

`passed / failed / incomplete / not_applicable`

### 3.3 用例结果

`passed / failed / blocked / error / not_run / manual_pending`

### 3.4 Mock 真实性

`real / mock / mixed / unknown`

### 3.5 问题状态与处置

状态：`pending / in_progress / retest / closed`
处置：`fixed / non_defect / duplicate / deferred`

### 3.6 计划状态

`draft / published`

## 4 命令

### 4.1 项目与模块

#### `project.add`

```json
{"root": "/abs/path", "name": "订单系统", "goal": "下单改版验证"}
```

返回：`{"project_id": "proj_...", "revision": 1, "modules": []}`

规则：目录必须存在且可写；同一根目录已登记则返回现有项目；根目录与已登记项目重叠时返回 `INVALID_REQUEST` 并列出冲突项目。

#### `project.list` / `project.show`

- `list` 返回 `{"projects": [{"project_id", "name", "root", "updated_at"}]}`；
- `show` 入参 `{"project_id"}`，返回项目、模块、绑定与缺口。

#### `project.update`

入参 `{"project_id", "patch": {...}, "expected_revision": 3}`；返回新修订号。版本冲突返回 `RUN_STATE_CONFLICT`。

#### `project.config`

入参 `{"project_id": "proj_...", "action": "get|update", "patch": {"services": {...}}}`。`action` 省略时为 `get`，只返回白名单段；`update` 按段合并写回并返回合并结果，`patch` 中某段为 `null` 时删除该段。允许的段为 `model / services / link / runners / evidence / l1 / data`；未知段、非对象段或含密钥字段返回 `INVALID_REQUEST`。读取时也会剔除凭据样式的键，密钥不写入该文件（见第 6 节）。

#### `module.add` / `module.update` / `module.list`

```json
{"project_id": "proj_...", "name": "订单", "responsibility": "下单与查询", "owner": "张三", "status": "开发中", "deps": [{"to": "payment", "type": "calls", "note": "支付"}]}
```

`type`：`calls / interface / shared_data`。

### 4.2 目标与任务

#### `task.suggest`

入参 `{"project_id": "proj_..."}`。

返回：

```json
{"candidates": [{"title": "验证订单创建流程", "reason": "order 模块近三次提交", "source": "git"}], "fallback": "验证项目能否启动并跑通主流程"}
```

无信号时 `candidates` 为空、只给 `fallback`；AI 不可用时返回 `ok: true` 并标 `ai_generated: false`。

#### `task.add`

```json
{
  "project_id": "proj_...",
  "title": "订单改版验证",
  "goal": "上线前验证下单流程",
  "scope": "下单、库存",
  "inputs": "订单页面",
  "outputs": "订单记录",
  "preconditions": ["测试库可用"],
  "executor": "李四",
  "reviewer": "王五",
  "acceptance_items": [
    {"text": "下单后订单列表出现该订单", "observable": true, "source": "任务描述"},
    {"text": "系统要稳定", "observable": false, "source": "任务描述"}
  ]
}
```

返回：`{"task_id": "task_...", "revision": 1, "acceptance_items": [{"item_id": "ai_...", "status": "active|pending"}]}`。

`observable=false` 的项标 `pending`，不进入范围。

#### `task.update` / `task.list` / `task.show`

`update` 生成新修订；`show` 返回任务、验收项与关联计划。

### 4.3 交付说明

#### `delivery.save`

```json
{
  "task_id": "task_...",
  "done": ["下单接口"],
  "not_done": ["退款"],
  "changed_modules": ["order"],
  "interface_changes": ["POST /orders 增加 coupon 字段"],
  "run_commands": ["make run"],
  "test_data": "测试库",
  "dependencies": ["payment-api"],
  "mocks": ["payment-api"],
  "self_test_evidence": ["截图 2 张"],
  "commit": "abc123"
}
```

返回：`{"delivery_id": "dlv_...", "revision": 1, "complete": true}`。缺 `run_commands` 或 `commit` 时 `complete: false`，不阻止保存。

#### `delivery.show`

入参 `{"task_id"}`；无记录时返回 `data: null`。

### 4.4 规则

#### `rules.save`

```json
{"project_id": "proj_...", "name": "接口检查", "version": "2", "steps_md": "...", "ai_rules": ["..."], "evidence_requirements": ["日志", "响应"]}
```

返回 `{"rules_version_id": "rule_...", "version": "2"}`；新版本不覆盖历史。

#### `rules.list` / `rules.show` / `rules.bind`

- `bind` 入参 `{"project_id", "rules_version_id"}`；
- `show` 返回规则正文与绑定项目。

#### `rules.import` / `rules.export`

入参 `{"path": "/abs/file.md", "project_id": "..."}`；导入产生新版本，导出写 Markdown。

### 4.5 计划

#### `plan.generate`

```json
{
  "task_id": "task_...",
  "scope": {"preset": "all_applicable", "module_ids": [], "acceptance_item_ids": []},
  "rules_version_id": "rule_..."
}
```

`preset`：`all_applicable / selected`。`selected` 时必须给 `acceptance_item_ids`。

返回：

```json
{"plan_id": "plan_...", "revision": 1, "scope": {"preset": "selected", "acceptance_item_ids": ["ai_1", "ai_3"], "coverage": {"selected": 2, "applicable": 8}}, "cases": [], "status": "published", "ai_generated": true}
```

规则：每个选中验收项至少一条用例；预期必须带 `expectation_source`；生成后自动发布为 `published`。

#### `plan.update`

入参 `{"plan_id", "patch": {"cases": [...]}, "expected_revision": 1}`；生成新修订并重新发布；旧运行仍引用旧计划。

#### `plan.show` / `plan.publish`

`show` 返回计划、用例、范围与覆盖；`publish` 用于把 `draft` 发布，已发布计划返回 `RUN_STATE_CONFLICT`。

### 4.6 执行

#### `run.start`

```json
{"plan_id": "plan_...", "acceptance_item_ids": ["ai_1"]}
```

`acceptance_item_ids` 可省略；省略时使用计划范围。返回：

```json
{"run_id": "run_...", "status": "running", "scope": {"selected": 2, "applicable": 8}}
```

规则：必须有确认记录或 `--confirm`；同一项目已有运行返回 `RUN_ACTIVE`；范围为空返回 `SCOPE_EMPTY`。

#### `run.status` / `run.list`

- `status` 入参 `{"run_id"}`，返回运行、用例状态、证据引用、结论（若已结束）；
- `list` 入参 `{"project_id", "limit": 20}`，按时间倒序。

#### `run.cancel`

入参 `{"run_id"}`；写取消标志，返回 `{"cancel_requested": true}`；不立即改状态。

#### `run.manual`

```json
{"run_id": "run_...", "case_id": "case_...", "step": "打开订单列表", "result": "passed", "note": "", "evidence": ["evidence/shot-1.png"]}
```

返回该用例当前状态；未记录完的步骤保持 `manual_pending`。

#### `data.cleanup`

入参 `{"run_id": "run_...", "dry_run": true}`；`dry_run=false` 时需 `--confirm`。返回 `{"rows": 12, "conditions": "...", "executed": false}`。

#### `evidence.add`

```json
{"run_id": "run_...", "case_id": "case_...", "path": "/abs/shot.png", "media_type": "image/png", "note": "人工补证"}
```

返回证据引用 `{"evidence_ref": "evidence/..."}`。

#### `evidence.list`

入参 `{"run_id": "run_..."}`；返回引用、摘要、大小与是否截断。

### 4.7 问题

#### `issue.add` / `issue.list` / `issue.update` / `issue.close`

```json
{"project_id": "proj_...", "run_id": "run_...", "case_id": "case_...", "title": "下单生成两条订单", "severity": "P1"}
```

- 同一失败签名返回现有 `issue_id` 并追加发生记录；
- `update` 入参 `{"issue_id", "status"|"disposition", "note"}`；
- `close` 需 `{"issue_id", "run_id", "evidence_ref"}`，无新运行证据返回 `RUN_STATE_CONFLICT`。

### 4.8 报告

#### `report.generate`

入参 `{"run_id"}`；返回 `report_id`、`outcome`、`counts`、`coverage`、`changed_modules`、`unmapped_files`。

#### `report.export`

入参 `{"report_id", "path": "/abs/report.md"}`；返回导出路径与摘要。

#### `report.opinion`

```json
{"report_id": "rep_...", "by": "张三", "text": "A3 已复测通过，可以上线"}
```

返回 `{"opinion": {"by": "张三", "at": "...", "text": "..."}}`；不改变结论。

### 4.9 AI 分析

#### `analysis.run`

入参 `{"run_id"}`；返回：

```json
{"status": "generated|not_generated|invalid", "analysis": {"observed": "...", "evidence_refs": [], "candidate_causes": [{"cause": "...", "confidence": "medium"}], "unverified": ["..."], "next_steps": ["..."]}}
```

缺 `unverified` 时 `status: invalid`，`analysis` 为 `null`。

### 4.10 GitHub 与链路

#### `github.read`

入参 `{"project_id", "repo": "owner/name", "ref": "main"}`；返回：

```json
{"repo": "owner/name", "branch": "main", "commit": "abc123", "prs": [], "checks": [], "deployment": null, "account": "zhangsan", "host": "github.com", "fetched_at": "..."}
```

#### `link.import`

```json
{"run_id": "run_...", "request_id": "req-1"}
```

返回 `{"nodes": [], "assertions": [], "gaps": [{"node_id": "log", "reason": "未找到该请求的日志"}]}`。

#### `link.assert`

入参 `{"run_id", "node_id", "expected", "actual"}`；返回 `{"result": "passed|failed|not_applicable"}`。

### 4.11 备份与诊断

#### `backup.create`

入参 `{"path": "/abs/backup.zip", "include_evidence": false}`；返回 `{"path", "sha256", "bytes", "includes_evidence"}`。

#### `backup.restore`

入参 `{"path": "/abs/backup.zip", "target": "/abs/new-workspace"}`；返回恢复清单与缺口；目标必须为空目录。

#### `doctor`

入参 `{}`；返回：

```json
{"python": {"ok": true, "version": "3.13.1"}, "package": {"ok": true, "version": "0.1.0"}, "model": {"ok": false, "source": "none"}, "github": {"ok": true, "account": "zhangsan"}, "workspace": {"ok": true, "path": "..."}, "proxy": {"ok": true}, "repairs": []}
```

`repairs` 为 AI 建议的修复项，含 `risk` 与 `command`；低风险项可带 `auto: true`。

## 5 数据文件

### 5.1 共同规则

- UTF-8、缩进 2、键排序；
- 每个文件一个对象，带 `schema_version`；
- 写入临时文件 + `os.replace`；
- 旧版本拒绝并提示转换或重建。

### 5.2 文件与字段

`workspace.json`：

```json
{"schema_version": 1, "workspace_id": "ws_...", "created_at": "..."}
```

`project.json`：

```json
{"schema_version": 1, "project_id": "proj_...", "name": "", "root": "/abs", "goal": "", "modules": [], "revision": 1}
```

`task.json`：

```json
{"schema_version": 1, "task_id": "task_...", "project_id": "proj_...", "title": "", "goal": "", "acceptance_items": [{"item_id": "ai_...", "text": "", "observable": true, "status": "active", "source": ""}], "revision": 1}
```

`plan.json`：

```json
{"schema_version": 1, "plan_id": "plan_...", "task_id": "task_...", "scope": {"preset": "selected", "module_ids": [], "acceptance_item_ids": []}, "rules_version_id": "rule_...", "cases": [{"case_id": "case_...", "acceptance_item_id": "ai_...", "layer": "L2", "preconditions": [], "steps": [], "expected": "", "expectation_source": "", "verify_method": "", "mock_allowed": true, "importance": "high", "depends_on": "service"}], "status": "published", "revision": 1}
```

`run.json`：

```json
{"schema_version": 1, "run_id": "run_...", "plan_id": "plan_...", "project_id": "proj_...", "scope": {"preset": "selected", "acceptance_item_ids": [], "frozen_at": "..."}, "confirmed_by": "", "confirmed_at": "...", "status": "running", "pid": 1234, "started_at": "...", "heartbeat_at": "...", "cases": [], "evidence_refs": [], "analysis": []}
```

`issue.json`：

```json
{"schema_version": 1, "issue_id": "issue_...", "project_id": "proj_...", "signature": "...", "title": "", "severity": "P1", "status": "pending", "disposition": null, "occurrences": [], "fix_version": null, "revision": 1}
```

`report.json`：

```json
{"schema_version": 1, "report_id": "rep_...", "run_id": "run_...", "outcome": "failed", "counts": {}, "coverage": {"selected": 2, "applicable": 8}, "changed_modules": [], "unmapped_files": [], "evidence_refs": [], "opinion": null, "revision": 1}
```

## 6 项目配置

`project.config.json` 与 `project.json` 同目录：

```json
{
  "schema_version": 1,
  "model": {"base_url": "https://api.deepseek.com", "name": "deepseek-chat", "timeout_seconds": 120},
  "services": {"start": "", "stop": "", "health": "", "timeout_seconds": 120, "keep_on_failure": false},
  "link": {"request_id_header": "X-Request-Id", "log_paths": [], "log_format": "jsonl", "log_fields": {}, "db_query": ""},
  "runners": {"pytest": {"parallel": false, "args": ""}, "playwright": {"parallel": false}},
  "evidence": {"file_cap_mb": 20, "media_budget_mb": 500, "video": false},
  "ai": {"max_files": 40, "max_depth": 3}
}
```

密钥不写入此文件。

## 7 版本与兼容

- 数据文件 `schema_version` 递增；旧版本只提示转换或重建，不自动迁移；
- 命令与字段变更需同步本册、`models.py`、扩展与契约测试；
- 新增命令不改变已有字段语义；废弃命令先标记，再删除。

## 8 契约测试清单

实现时至少覆盖：

1. 信封与退出码；
2. 每个命令的必填字段与错误码；
3. 枚举取值；
4. 文件 schema 版本与拒绝旧版本；
5. 幂等：同一 `request_id` 不重复执行；
6. 单项目单运行：第二个 `run.start` 返回 `RUN_ACTIVE`；
7. 未确认运行返回 `CONFIRMATION_REQUIRED`；
8. 计划范围为空返回 `SCOPE_EMPTY`；
9. 报告结论与计数不因 AI 摘要改变；
10. 旧格式备份恢复失败时给出 `SCHEMA_UNSUPPORTED`。
