# CLI 与数据契约（一期）

> 状态：2026-11-21 设计基线（D2）。实现依据：[轻量 Trae 插件修改计划](../../../修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md)；需求见[需求规格](../02-需求/02-需求规格.md)。
> 本册定义命令、字段、状态与错误码；扩展与 Python 都按本册实现，不另造第二套。当前注册 54 条命令（第 4 节；已移除 FR04 `rules.*`/`template.*` 8 条命令）。

## 1 通用约定

### 1.1 调用

```bash
python -m aitest.cli <command> --workspace <dir> [--input <json-file>] [--confirm]
```

- 输入：`--input` 文件或 stdin JSON，二者等价；
- 输出：stdout 一行 JSON；日志写 stderr；
- 退出码：`0` 成功；`1` 业务拒绝；`2` 参数或环境错误；
- 写操作（`MUTATING` 集合，含 `plan.generate`、`run.start`、`evidence.add`、`github.read` 等；`github.read` 会落盘提交快照）按 `request_id` 幂等：同一 `request_id` 重复提交返回缓存结果，不重复执行；未列入 `MUTATING` 的只读命令不缓存 `request_id`。`request_id` 为空时不做幂等缓存。

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
- ID 用前缀 + UUID/短哈希，如 `proj_`、`task_`、`plan_`、`run_`、`issue_`、`rep_`、`rule_`、`dlv_`、`conf_`、`ai_`、`case_`、`mod_`。报告 ID 前缀是 `rep_`，不是 `report_`。

## 2 错误码

| code | 含义 | next_step |
| --- | --- | --- |
| `INVALID_REQUEST` | payload 字段语义不合法（handler 校验失败，退出码 1） | 按字段提示修正 |
| `USAGE` | 参数、输入文件或信封 JSON/结构解析失败（退出码 2） | 修正参数后重试 |
| `INVALID_PATH` | 目录不存在或不可写，或项目 ID 含非法字符 | 换目录、修权限或检查项目 ID |
| `CONFIG_INVALID` | 其他命令读取到不是合法 JSON 对象的项目配置；`project.config` 自身读取仍返回 `INVALID_REQUEST` | 修复 `project.config.json` 后重试 |
| `WORKSPACE_LOCKED` | 业务文件被另一进程持有 | 等待或关闭另一实例 |
| `PROJECT_NOT_FOUND` | 项目不存在 | 先建项目或检查 ID |
| `TASK_NOT_FOUND` | 任务不存在 | 先建任务 |
| `PLAN_NOT_PUBLISHED` | 计划未发布 | 先 `plan.publish` |
| `PLAN_STALE` | 计划缺少来源签名，或项目修订、交付提交、任务契约、规则版本、`services`/`l1`/`runners` 配置、项目根目录任一变化 | 重新生成计划后重试 |
| `SCOPE_EMPTY` | 范围为空的 | 至少选一条验收项 |
| `CONFIRMATION_REQUIRED` | 既没有 `--confirm`，也没有同计划版本、同范围、同指纹的可复用确认记录 | 在面板确认或加 `--confirm` |
| `RUN_ACTIVE` | 同一项目已有运行 | 等待、取消或查询现有运行 |
| `RUN_NOT_FOUND` | 运行不存在 | 检查运行 ID |
| `RUN_STATE_CONFLICT` | 当前状态不允许该操作 | 先查询状态 |
| `RUN_START_FAILED` | worker 子进程启动失败 | 检查解释器与目录权限后重试 |
| `DEPENDENCY_INSTALL_FAILED` | `run.repair` 安装依赖失败 | 查看 repair-deps.log；无 pip 时需 uv 或 ensurepip，修正环境后重试 |
| `DEPENDENCY_ALREADY_INSTALLED` | `run.repair` 重复安装已记录的依赖 | 直接重跑未完成项，不要重复安装 |
| `MODEL_UNAVAILABLE` | 模型未配置或不可达 | 配置密钥或检查网络 |
| `MODEL_DISABLED` | 内部配置 `model.ai.mode=off` 显式关闭模型调用 | 如需启用 AI 辅助，改回 `model.ai.mode=assist` 后重试 |
| `MODEL_OUTPUT_INVALID` | 模型输出不合格 | 重试或改用人工 |
| `MODEL_TIMEOUT` | 计划生成超过时间预算 | 重试会复用已完成批次；也可缩小本次范围 |
| `TOKEN_BUDGET_EXCEEDED` | 单次运行或模型客户端 Token 消耗超过预算上限 | 调高 `model.max_token_budget` 或缩小本次范围后重试 |
| `GITHUB_UNAVAILABLE` | `gh` 缺失、未登录或读取失败 | 安装/登录后重试 |
| `LINK_CONFIG_MISSING` | 缺日志或查询配置 | 补项目配置 |
| `SCRIPT_REJECTED` | 生成脚本含危险操作 | 检查脚本或改用手写 |
| `EVIDENCE_BUDGET` | 证据预算已用完或视频未启用 | 调整 evidence 配置或只提交关键证据 |
| `EVIDENCE_WRITE_FAILED` | 证据写入失败 | 检查目录权限与磁盘 |
| `SCHEMA_UNSUPPORTED` | 旧数据格式 | 备份后重建或重新生成 |
| `BACKUP_INVALID` | 备份损坏或校验失败 | 换备份 |
| `CLEANUP_FAILED` | 清理命令无法执行或退出码非零；未配置清理段返回 `INVALID_REQUEST` | 检查项目配置与项目根目录 |
| `DISK_FULL` | 磁盘不足（仅 `ENOSPC` / Windows 磁盘满） | 清理磁盘后重试 |
| `PERMISSION_DENIED` | 写入目标权限不足 | 检查目录权限后重试 |
| `PATH_TOO_LONG` | 目标路径超过系统上限 | 缩短工作空间或文件名后重试 |
| `FILE_BUSY` | 目标文件持续被其他程序占用，原子替换重试耗尽 | 关闭占用程序后重试 |
| `WRITE_FAILED` | 其他写入失败（未归入以上类别） | 检查目标路径与可用空间 |
| `INTERNAL` | 未预期错误 | 附 request_id 反馈 |

## 3 枚举

语义、计数口径与判定以[功能清单](../02-需求/03-功能清单.md)第 2 节为准；本节只列 CLI/数据文件的传输值和拼写，供契约测试与外部调用比对。

### 3.1 运行状态

`running/completed/cancelled/interrupted/execution_error`

### 3.2 业务结论

`passed/failed/incomplete/not_applicable`

### 3.3 用例结果

`passed/failed/blocked/error/not_run/manual_pending`

### 3.4 Mock 真实性

`real/mock/mixed/unknown`

### 3.5 问题状态与处置

状态：`pending/in_progress/retest/closed`

处置：`fixed/non_defect/duplicate/deferred`

问题命令允许的具体流转见 4.7；关闭仍必须走 `issue.close` 并绑定新运行。

### 3.6 计划状态

`draft/published`

### 3.7 运行种类与后处理

运行种类：`task/project_check`（`Run.kind`）

后处理状态：`pending/running/done/error`（`Run.postprocess`）

### 3.8 任务意图与验收项状态

任务意图：`smoke/business`（`Task.intent`）

验收项状态：`active/pending`（`AcceptanceItem.status`）

### 3.9 用例重要度与人工步骤结果

用例重要度：`high/medium/low`（`Case.importance`）

人工步骤结果：`passed/failed/blocked`（`ManualStep.result`）

### 3.10 模块依赖类型

`calls/interface/shared_data`

### 3.11 分析状态与原因

分析状态：`generated/not_generated/invalid`（`Analysis.status`）

分析原因：`no_failures/execution_error/model_missing/model_invalid/model_error/`（`Analysis.reason`；最后一项是空字符串，表示生成成功）

### 3.12 报告摘要来源

`ai/fallback/none`

## 4 命令

除 `doctor`、`project.add`、`project.list`、`backup.create`、`backup.restore` 外，命令都必须带 `project_id` 定位项目；下文示例偶有省略时以本条为准。

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

入参 `{"project_id", "patch": {...}, "expected_revision": 3}`；`patch` 只允许 `name`、`goal`、`archived`（布尔）。返回新修订号。版本冲突返回 `RUN_STATE_CONFLICT`。

#### `project.config`

入参 `{"project_id": "proj_...", "action": "get|update|detect|add_convention|remove_convention", "patch": {"services": {...}}}`。`action` 省略时为 `get`，自动合并仓库根目录无密钥 `.aitest.json`（含多环境档案 `environments` 与共享错题本 `conventions`）与工作空间 `project.config.json` 后返回白名单段；`update` 按段合并写回并返回合并结果，`patch` 中某段为 `null` 时删除该段；`detect` 只读探测项目根目录的框架、测试命令与服务配置，返回 `{"draft": {...}, "detected": {"framework": [...], "test_commands": [...], "services": {...}}}`（不写入磁盘，脱敏且过滤凭据字段）；`add_convention` / `remove_convention` 用于维护项目错题本正常约定/预期豁免规则（`pattern` / `reason` / `match_type` / `scope`）。允许的段为 `model / services / link / runners / evidence / l1 / data / probes / environments / conventions`；未知段、非对象段或含密钥字段返回 `INVALID_REQUEST`。读取时也会剔除凭据样式的键，密钥不写入该文件（见第 6 节）。`l1` 段支持 `build`、`entry` 与 `smoke_command`；`smoke_command` 配置后优先用于冒烟计划；`probes.live_page.enabled` 可按需开启可选真页面巡检。

#### `module.add` / `module.update` / `module.list`

```json
{"project_id": "proj_...", "name": "订单", "responsibility": "下单与查询", "owner": "张三", "status": "开发中", "deps": [{"to": "payment", "type": "calls", "note": "支付"}]}
```

`type`：`calls / interface / shared_data`。

#### `module.suggest`

入参 `{"project_id": "proj_..."}`。读取项目根目录结构、README、清单与一个入口文件的只读摘要，调用模型给出模块建议，不写库。

返回：

```json
{"modules": [{"name": "order", "responsibility": "下单与查询", "owner": "", "deps": [{"to": "stock", "type": "calls", "note": "扣减库存"}]}], "registered": ["payment"]}
```

`deps.to` 必须指向本次建议中的模块或已登记模块，否则返回 `MODEL_OUTPUT_INVALID`；模型不可用返回 `MODEL_UNAVAILABLE`。用户确认后逐条调用 `module.add` 写入。

#### `project.snapshot`

入参 `{"project_id": "proj_..."}`。只读，在一个 CLI 进程内返回面板所需的完整读模型：`project`、`modules`、`map`、`map_stale`、`config`、`rules`、`tasks`（含 plans/delivery）、`runs`（`run.status` 详情）、`evidence`（按 run_id）、`reports`（各运行最新报告）与 `issues`。用于避免面板逐实体启动 CLI；不改变任何记录，字段仍以各实体契约为准。

#### `project.map` / `project.map.refresh`

`project.map` 入参 `{"project_id": "proj_..."}`，读取当前项目测试地图；没有地图时返回 `map: null`，同时始终附带实时静态全栈代码图 `graph`（4 层 `nodes`/`edges`/`conflicts`/`module_chunks`/`impact`）与只读 `local_git` 状态。

`project.map.refresh` 入参 `{"project_id": "proj_..."}`，AI 读取项目代码、README、路由、测试入口和配置，结合全栈代码图与局部模块切片生成或刷新测试地图，并同步返回最新 `graph` 与 `local_git`。返回：

```json
{"map": {"schema_version": 1, "project_id": "proj_...", "project_revision": 3, "source_commit": "abc123", "generated_at": "...", "business_functions": [{"name": "下单", "modules": ["order", "stock"], "entry_points": ["POST /orders"], "test_commands": ["pytest tests/order"], "testable_items": ["下单后订单出现"], "evidence": ["README.md", "routes.py:42"], "confidence": "high"}], "unknowns": [{"area": "支付回调", "reason": "没有入口或测试"}]}}
```

规则：

- 业务功能是第一层，模块是第二层；模块可以出现在多个业务功能下；
- `confidence` 只能是 `high / medium / low`；`low` 或无法判断的内容必须进入 `unknowns`；
- 地图按项目修订、源码提交和项目配置缓存；这些变化后 `project.map` 返回 `stale: true`；
- 地图是 AI 分析结果，不是项目全部业务功能的证明；用户可编辑、合并、忽略；
- AI 不可用返回 `MODEL_UNAVAILABLE`，不覆盖旧地图；项目信号不足返回空功能列表和原因；
- 地图不写入被测仓库。

### 4.2 目标与任务

#### `task.suggest`

入参 `{"project_id": "proj_..."}`。

返回：

```json
{"candidates": [{"title": "验证订单创建流程", "reason": "order 模块近三次提交", "source": "git", "intent": "business", "scope_preview": {"business_functions": ["下单"], "acceptance_items_estimate": 3}, "uncertainties": ["库存扣减是否在本次范围"]}], "fallback": "验证项目能否启动并跑通主流程", "intent": "smoke", "fallback_intent": "smoke"}
```

无信号时 `candidates` 为空、只给 `fallback`；AI 不可用时返回 `ok: true` 并标 `ai_generated: false`。通用目标（`fallback`）的 `intent` 为 `smoke`。候选必须带 `scope_preview` 和 `uncertainties`，让用户选目标前看到预计范围。

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
  "intent": "business",
  "origin": "goal",
  "source_map_revision": 3,
  "acceptance_items": [
    {"text": "下单后订单列表出现该订单", "observable": true, "source": "任务描述"},
    {"text": "系统要稳定", "observable": false, "source": "任务描述"}
  ]
}
```

返回：`{"task_id": "task_...", "revision": 1, "intent": "business", "acceptance_items": [{"item_id": "ai_...", "status": "active|pending", "observable": true}]}`。

`intent` 可选，取值 `smoke|business`，默认按 `goal` 推断：只含“能否启动/跑通主流程”等泛化目标且无具体业务词时为 `smoke`，否则 `business`。`origin` 可选，取值 `goal|range`（新建任务不接受 `manual`）：按目标测试传 `goal`，按范围测试传 `range`；`source_map_revision` 记录范围来自哪一版项目地图。旧任务记录里的 `origin=manual` 只为读取兼容保留，不表示新流程仍可写 `manual`。`observable=false` 的项标 `pending`，不进入范围。AI 提取出的泛化描述（如“项目能够启动”“主流程能够跑通”）自动标 `observable=false` 并转 `pending`；没有可观察项时任务照常保存，验收项列表为空。

#### `task.update` / `task.delete` / `task.list` / `task.show`

`update` 生成新修订；`delete` 彻底删除任务及其计划、运行、报告、关联问题和证据，不可恢复；`show` 返回任务、验收项与关联计划。

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

入参 `{"task_id"}`；无记录时返回 `{"delivery": null}`，有记录时返回 `{"delivery": {...}}`。

#### `delivery.draft`

入参 `{"project_id": "proj_...", "task_id": "task_...", "commit": "abc123"}`；`commit` 可省略。读取项目 README、`pyproject.toml` 或 `package.json`、`git show --stat`（失败不阻塞）与任务目标，调用模型生成草稿，**不落盘**。

返回：

```json
{"draft": {"done": ["下单接口"], "not_done": [], "changed_modules": ["order"], "interface_changes": [], "run_commands": [], "test_data": [], "dependencies": [], "mocks": [], "self_test_evidence": []}, "sources": {"readme": "README.md", "manifest": "pyproject.toml", "commit_stat": true}}
```

九个字段都是字符串数组；模型没给或格式不合法时为空数组，不编造运行命令。模型不可用时返回 `ok: true`、`draft: null`、`status: "not_generated"`、`reason: "model_missing"`。草稿由用户修改后再调用 `delivery.save`，CLI 不自动保存。`mocks` 只是开发自述，不参与运行后的 Mock 真实性判定，也不提供结果修改入口。

### 4.4 计划（FR04 规则已移除，保留小节编号不重排或顺延）

#### `plan.generate`

```json
{
  "project_id": "proj_...",
  "task_id": "task_...",
  "scope": {"preset": "all_applicable", "module_ids": [], "acceptance_item_ids": []}
}
```

`preset`：`all_applicable / selected`。`selected` 时必须给 `acceptance_item_ids`。

返回：

```json
{"plan_id": "plan_...", "plan_no": 2, "task_revision": 3, "created_at": "...", "revision": 1, "intent": "business", "source_signature": "sha256", "scope": {"preset": "selected", "acceptance_item_ids": ["ai_1", "ai_3"], "coverage": {"selected": 2, "applicable": 8}}, "coverage": {"selected": 2, "applicable": 8}, "case_count": 4, "cases": 4, "dependencies": ["pytest"], "status": "published", "ai_generated": true, "unverified_cases": ["case_..."]}
```

`unverified_cases` 列出 `expectation_source` 未匹配验收项原文的用例 ID。

规则：

- 每个选中验收项至少一条用例；生成后自动发布为 `published`。
- 选中项超过 6 个时按每批 6 个生成，批次之间相互独立并并发执行（默认 4 路，`AITEST_PLAN_CONCURRENCY` 可调；批大小用 `AITEST_PLAN_BATCH_SIZE` 可调）；单批输出不合法只重试并重新生成该批。
- 生成前先用一次映射调用把验收项对应到项目现有测试（中文验收项与英文测试名无法靠词面匹配）；映射命中的项默认使用该测试命令，模型若写成脚本或请求会被替换；只有映射为空时才保留生成的验证。
- 计划生成（含验收项映射与批次生成）共用整体时间预算（默认 150 秒，`AITEST_PLAN_BUDGET_SECONDS` 可调），超时返回 `MODEL_TIMEOUT` 并说明已完成批数；单次模型请求仍受模型自身的请求超时限制。已完成批次写入工作空间 `.cache/plan/`（落盘前脱敏），重试时复用，不重复调用模型。
- 校验失败只重新生成出错用例对应的验收项（定向修复），不整套重出；同一类“只写测试文件未定位测试函数/标题”的用例会一次性收集后合并修复。
- 同一测试节点被 3 个以上验收项绑定时，保留与该测试最匹配的验收项，其余定向重生成，避免一个测试冒充多项。
- 业务用例一项一案；模型输出组合用例时按验收项拆分，`expectation_source` 取对应验收项原文，并写“逐项断言仍需人工确认”警告。
- 业务用例禁止整包命令（不带具体文件或标题的 `bunx playwright test`、`cd <目录> && npm test`、`bun run test` 等）；模型重试后仍只能给出整包命令时转为 `manual` 并写计划警告，不计为自动通过。同一具体命令覆盖多个验收项时也写“文件级通过、逐项断言需人工确认”警告。
- 发布门禁只要求 `expected` 与 `preconditions` 非空；`expectation_source` 缺失或匹配不上不阻止发布，但该用例 `expectation_verified=false`，执行通过也按 `blocked` 计。
- 用例记录 `coverage_kind` 与 `test_nodes`：`direct` 指向真实存在的测试函数（`file::test_name`）或项目自己的 `junit:` 测试结果；`file` 只执行整个测试文件，`shared` 是同一测试节点被多个验收项共用，`manual` 为人工验证，`unverified` 无法定位。只有 `direct` 可以在执行通过后计为该项通过；`file`、`shared`、`unverified` 即使命令退出 0 也按 `blocked` 计。
- 模型生成的 `script:` 脚本只要包含真实断言，就与项目既有测试同样计为 `direct`：通过计通过，断言失败计失败（认证/前置条件类失败按执行规则记 `blocked`）。没有断言的脚本（只打印或退出 0）记为 `unverified`，通过也不计入。`http:`、`junit:` 视为直接证据；`sql:` 记为 `unverified`。模型生成的验证执行失败按结果记录并保留证据；401/403 且未带凭据按 `blocked`。只由弱证据覆盖的验收项按 `blocked` 展示，不当作未覆盖消失。
- `verify_method` 必须合法：
  - `command:` 后非空；
  - `script:` 需要 `script_name` 与 `script_content`；
  - `http:` 必须是非空 JSON 数组且每项有 `method/url`；默认不跟随重定向，只有 `follow_redirects: true` 才跟随；跨 host、HTTPS→HTTP 或其他 scheme 变化会被拒绝；未跟随的 3xx 在没有状态断言时按未完成处理；`expect.path` 从响应体根开始（如 `email`、`data.0.id`），写出的 `body.` 前缀会被去掉；`status` 保留给 HTTP 状态码，响应体字段恰好叫 `status` 时写 `body.status`（不会被去掉前缀）；
  - `playwright:` 路径必须是非空、项目内相对路径，且不得含 shell 元字符或危险命令；
  - `sql:`/`junit:`/`pytest:` 参数非空并经过危险命令扫描。
- `case_id` 必须唯一，只允许字母、数字、下划线和连字符；不合法返回 `INVALID_REQUEST` 并指出 `case_id`。

`task.intent=smoke` 且任务没有适用验收项时：

- 只生成 L1 用例，默认不跑全量测试；
- 按项目事实生成“构建/入口检查（`l1.build` 或可识别入口模块导入）”“pytest 测试发现（`python -m pytest --collect-only -q`）”“最小测试模块（`tests` 下最小的非空 `test_*.py`）”；
- 项目可用 `l1.smoke_command` 覆盖为单条命令；非 pytest 项目回退到 `runners`/`package.json` 声明的测试命令；没有可用检查时返回 `INVALID_REQUEST`；
- 不允许 `http:`、`playwright:` 或外部 URL。

`task.intent=business` 时保留分层逻辑，但 `http:` URL 必须指向 `services` 的 health host/port 或本机项目地址，不得使用 README 示例外网 URL；生成脚本不得访问外网示例域名。

`source_signature` 由 `project.revision`、交付提交（无则 `git HEAD`）、任务契约摘要、`services`/`l1`/`runners` 配置摘要与项目根目录计算。任务契约只包含验证意图与验收项的 ID/文本/状态/可观察性/模块；只改标题、目标描述、执行人或评审人不会使计划失效。

#### `plan.update`

入参 `{"plan_id", "patch": {"cases": [...]}, "expected_revision": 1}`；生成新修订并重新发布；旧运行仍引用旧计划。更新后重新计算并保存 `source_signature`。若当前任务契约与计划记录不一致，返回 `PLAN_STALE`，要求重新生成计划，不能沿用旧计划改用例。

#### `plan.show` / `plan.publish`

`show` 返回 `{"plan": {...}, "coverage": {"selected": N, "applicable": M}, "stale": false, "stale_reason": "", "task_updated": false}`；`stale` 只按任务契约判断（验收项、可观察性、模块或验证意图变化），`task_updated` 表示任务修订变了但契约未变。旧计划没有 `task_contract` 时 `stale: true`，提示重新生成，不做迁移。来源签名（项目、交付、服务/运行器、项目根目录）的变化不在 `show` 里判，由 `run.start` 返回 `PLAN_STALE`。`publish` 用于把 `draft` 发布，已发布计划返回 `RUN_STATE_CONFLICT`。

#### `plan.review`（只读无锁）

入参 `{"project_id", "plan_id", "use_cache": true, "refresh": false}`。**仅注册为只读命令（不入 `MUTATING`、不抢工作空间排他写锁）**，与 `plan.generate` 解耦且不阻塞 `run.start`。对当前计划执行双盲「需求/契约 vs 实现源码」冲突比对、权限矩阵补漏与弱断言体检，并按 `item_id + scenario_type + assertion_target + data_shape` 去重签名（`exact duplicate = 0`）；结果按 `input_hash` 写入独立只读缓存并直接返回 `{"plan_id", "plan_revision", "input_hash", "ai_used", "cached", "high_severity_count", "reviews": [...]}`。不直接修改 `plan.json`；用户勾选采纳时走 `plan.update` 持写锁生成新 `revision`。

#### `plan.repair_case`

入参 `{"project_id", "plan_id", "case_id", "run_id": "", "mode": "preview" | "apply", "repaired_script": "", "error_hint": ""}`。注册为写命令（`MUTATING`）：
- `mode="preview"`：只读预览，不要求 `--confirm`，不修改任何文件，返回经 `assertions_preserved` + `blocking_lint_errors` + `_mutates_seed_account` 守卫校验后的 `unified_diff`、`guard_blocked` 与 `repaired_script`；
- `mode="apply"`：必须带 `--confirm`，任一安全守卫拦截即返回 `INVALID_REQUEST` 拒绝落盘；校验通过后归档旧版本、更新 `Plan` 生成新 `revision` 持久化脚本，严禁原地覆写历史 `run` 记录。

> **模型增量可选字段（schema_version 维持 1，向后兼容旧记录）**：`Plan` 新增 `capabilities` / `recipes` / `coverage_ledger` / `ai_reviews`；`Case` 新增 `generation` / `assertion_strength`；`Run` 新增 `ai_hypotheses`；`RunCase` 新增 `script_repair`；`Report` 新增 `capabilities` / `coverage_ledger` / `source_summary` / `assertion_strength`。

### 4.6 执行

#### `run.quick`

```json
{"project_id": "proj_...", "confirmed_by": "cli"}
```

用于项目级快速检查：不建任务、不选验收项，只执行项目配置中的 L1 构建与入口导入检查。必须带 `--confirm`；当前不复用其他运行的确认记录。运行仍受同一项目单运行限制。

返回：

```json
{"run_id": "run_...", "status": "running", "kind": "project_check", "scope": {"selected": 0, "applicable": 0}}
```

规则：

- 运行的 `kind` 为 `project_check`；没有任务快照，`run.status` 的 `task` 为 `null`、`task_snapshot=false`；
- 计划快照不含业务用例，不生成验收项；报告显示 `0/0` 与 L1 结果，不把项目检查算成验收项通过；L1 中未配置构建或入口时对应项不执行，也不合成额外的最小测试命令；
- 快速检查失败仍可执行；结论只按 L1 与运行状态收敛；
- `run.quick` 不能指定验收项、不能附加业务用例；需要业务用例时走 `run.start`。

#### `run.start`

```json
{"project_id": "proj_...", "plan_id": "plan_...", "acceptance_item_ids": ["ai_1"]}
```

`acceptance_item_ids` 可省略；省略时使用计划范围。给出时必须为计划范围 `S` 的子集，超出返回 `INVALID_REQUEST`；`run.start` 只能收窄，不能扩大。返回：

```json
{"run_id": "run_...", "status": "running", "scope": {"selected": 2, "applicable": 8}, "confirmation": {"confirmation_id": "conf_...", "confirmed_by": "cli", "confirmed_at": "...", "reused": false, "fingerprint": "sha256", "plan_revision": 3, "side_effects": [], "dependencies": ["pytest"]}, "warnings": []}
```

规则：

- 必须有人确认：给出 `--confirm`，或存在与本次 `plan_id`、计划修订、范围和指纹一致的可复用确认记录（按项目最近一次运行的确认记录比对）；两者都没有返回 `CONFIRMATION_REQUIRED`。
- payload 里的 `confirmed_by` 不能代替 `--confirm`；收窄范围时指纹按收窄后的验收项集合重算。
- 新确认写入 `confirmations/<confirmation_id>.json`；`run.json` 只记录 `confirmation_id`、指纹与确认人/时间，不内嵌确认正文。
- 同一项目已有运行返回 `RUN_ACTIVE`，并在 message 中给出现有 run_id；范围为空返回 `SCOPE_EMPTY`；唯一例外是冒烟目标且任务没有适用验收项，此时只做项目级检查。
- 启动前重新计算完整 `source_signature`：签名为空，或与当前项目修订、交付提交、任务契约、规则版本、`services`/`l1`/`runners` 配置、项目根目录不一致时返回 `PLAN_STALE`；旧计划没有 `task_contract` 视为过期，不静默迁移。
- 确认复用绑定 `plan_id + 计划修订 + 范围 + 指纹`；环境、凭据或副作用变化都会改变指纹，需要重新确认。启动时同时写 `runs/<id>/plan.json` 与 `runs/<id>/task.json` 快照。

#### `run.status` / `run.list`

- `status` 入参 `{"project_id", "run_id", "include_tail": false}`。顶层字段：
  - `run`：完整运行记录，含 `kind`、`current_case_id`、`confirmation_id`、`latest_report_id`、`cases`、`evidence_refs`、`analysis`；
  - `plan`：运行目录里的计划快照；
  - `task`：运行启动时的任务快照；`task_snapshot` 在旧运行没有快照时为 `false`；
  - `outcome`、`counts`（`selected/applicable/covered/passed/failed/incomplete`）；
  - `item_outcomes`：每个所选验收项的最差结果与用例 ID；
  - `pending_items`、`manual_cases`、`l1`（项目级检查）、`uncovered_items`；
  - `link`：运行目录已有 `link.json` 时为首错节点、上下游、位置与 `assertions`（见 4.10），没有链路数据时为 `null`；
  - `log_tail`：仅当入参 `include_tail=true` 时返回运行目录最近日志文件末尾片段数组（`[{"name": "...", "text": "..."}]`，已脱敏）。

  报告 ID 不在顶层，读 `run.latest_report_id`；当前用例读 `run.current_case_id`。
- `list` 入参 `{"project_id", "limit": 20}`，按时间倒序；每条返回 `run_id/kind/status/started_at/heartbeat_at/elapsed_seconds/plan_id/plan_no/plan_revision/task_id/task_title/task_snapshot/outcome/counts/scope/latest_report_id`；`kind` 为 `task|project_check`。无法读取的旧运行不静默丢弃，放在 `skipped` 里并附 `run_id/code/message`。

#### `run.repair`

入参 `{"project_id", "run_id", "packages": ["factory-boy>=3.2"], "confirmed_by": "本机用户"}`，必须带 `--confirm`。

- `packages` 只能取本次运行 `dependency_suggestions` 中的包；每个值只允许包名 + extras + 版本范围，不接受选项、URL 或 shell 字符；
- 安装到项目解释器（优先根目录 `.venv`）。先尝试该解释器的 `python -m pip`；若解释器没有 pip（uv 创建的 venv 默认如此），依次回退到 `uv pip install --python <解释器>` 和 `python -m ensurepip --upgrade` 后重试 pip；`repair` 记录 `installer` 与实际尝试链，`repair-deps.log` 保留每次尝试的退出码与输出；
- 返回 `run_id`、`repair`（`packages` 实际版本、`confirmed_by`、`installer`、`attempts`、`evidence_ref`、`ok`）、`environment` 与 `dependencies`；
- 成功时写入 `run.dependency_repairs`、`run.dependencies`，追加 `run.environment_history`（安装前后指纹），并更新 `run.environment`；
- 安装失败返回 `DEPENDENCY_INSTALL_FAILED`，保留 `repair-deps.log`；重复安装已记录依赖返回 `DEPENDENCY_ALREADY_INSTALLED`；未带确认人或未确认返回 `CONFIRMATION_REQUIRED`；
- 不自动重跑；面板安装成功后按未完成项重新准备计划并运行。

#### `run.cancel`

入参 `{"run_id"}`；写取消标志，返回 `{"cancel_requested": true}`；不立即改状态。worker 会终止正在执行的命令进程树，当前用例按阻塞记录，剩余用例标 `not_run`，最终写 `cancelled`。

#### `run.manual`

```json
{"project_id": "proj_...", "run_id": "run_...", "case_id": "case_...", "step": "打开订单列表", "result": "passed", "note": "", "evidence": ["evidence/shot-1.png"]}
```

也支持可选非空数组 `steps`（`[{"step": "...", "result": "passed|failed|blocked", "note": "", "evidence": [...]}]`)，用于在单次调用内按序批量记录同一用例的多条人工步骤。只允许在运行结束后记录；运行中返回 `RUN_ACTIVE`。只允许对 `layer=L3` 且验证方式为空、`manual:` 或不具备自动验证方式的用例记录；对自动用例调用返回 `RUN_STATE_CONFLICT`。返回该用例当前状态；未记录完的步骤保持 `manual_pending`。

#### `data.cleanup`

入参 `{"project_id", "run_id", "dry_run": true}`；`dry_run=false` 时需 `--confirm`，配置的清理命令必须包含 `{run_id}` 或 `{data_prefix}` 占位符，且必须提供合法 `run_id`；`run_id` 只允许字母、数字、`-`、`_`，不接受 `.`、`..` 或路径分隔符，占位符替换后不得为空。返回 `{"dry_run": true, "executed": false, "run_id": "...", "command": "...", "exit_code": 0, "stdout": "...", "stderr": "...", "log_path": "actions/..."}`。

#### `evidence.add`

```json
{"project_id": "proj_...", "run_id": "run_...", "case_id": "case_...", "path": "/abs/shot.png", "note": "人工补证"}
```

仅运行结束后可用；运行中返回 `RUN_ACTIVE`。入参 `case_id` 可选，给出时把证据同时挂到该用例；`note` 可选，随证据记录。`media_type` 入参被忽略，媒体类型按证据文件名推断，由 `evidence.list` 返回。返回 `{"evidence_ref": "evidence/..."}`。

#### `evidence.list`

入参 `{"project_id", "run_id", "preview_ref": "evidence/...", "preview_bytes": 16384}`（`preview_ref`、`preview_bytes` 可选）；返回引用、sha256、media type、大小、是否存在、是否截断与备注。给出 `preview_ref` 时额外返回只读脱敏的 `preview`（`{"ref", "kind": "text|image|binary|missing", "text", "data_url", "truncated"}`）。

### 4.7 问题

#### `issue.add` / `issue.list` / `issue.show` / `issue.update` / `issue.close`

```json
{"project_id": "proj_...", "run_id": "run_...", "case_id": "case_...", "title": "下单生成两条订单", "severity": "P1", "error_type": "assertionerror", "stack_key": "assert 1 == 2", "expected": "只生成一条订单", "actual": "生成了两条订单", "steps": ["提交订单", "刷新列表"], "evidence_refs": ["evidence/run_1/case_1-0.log"]}
```

- 失败签名 = 用例 ID + 错误类型 + 关键堆栈/断言标识摘要；同一签名返回现有 `issue_id` 并追加带证据的发生记录；签名忽略 pytest 耗时汇总行（如 `1 failed in 0.10s`）；
- 问题记录 `file` / `line`（项目内源码坐标，可空）：自动从 pytest / traceback 证据解析；生成脚本位于运行目录时不记源码坐标；面板据此显示“跳源码/推送红线”；
- `list` 返回 `issue_id/title/status/severity/occurrences`，并附带 `last_at`、`last_result`、`unstable`、`disposition`、`reason`、`verification_run_id`、`last_case_id`、`last_run_id` 供列表展示；
- `show` 入参 `{"project_id", "issue_id"}`，返回完整问题（含 `occurrences`、证据引用、处置与状态）；问题保存发生时的 `item_no`/`item_text` 快照；
- `update` 入参 `{"project_id", "issue_id", "status"|"disposition", "severity", "fix_version", "reason"}`；允许的流转是待确认→处理中，处理中→待复测或退回待确认，待复测→处理中或退回待确认，已关闭→处理中（重开必须写原因）。`update` 不能直接把状态改为 `closed`；关闭必须走 `issue.close` 并绑定新运行。非缺陷/重复/延期必须写理由；
- `close` 入参 `{"project_id", "issue_id", "run_id", "reason"}`，`reason` 必填并写入问题记录：问题必须处于 `retest`，运行必须为 `completed`、开始时间晚于问题最后一次发生、包含该用例、有效结果（含预期核实降级）为 `passed` 且有证据，且不能复用问题发生时的运行；`cancelled`、`interrupted`、`execution_error` 或原始 `passed` 但应按 `blocked` 计的运行都不能关闭。否则返回 `RUN_STATE_CONFLICT`。

### 4.8 报告

#### `report.generate`

入参 `{"project_id", "run_id"}`。返回字段：

- `report_id`（前缀 `rep_`）、`generated_at`；
- `outcome`、`conclusion`、`counts`、`coverage`；
- `changed_modules`、`unmapped_files`、`uncovered_items`、`unverified_items`；
- `layers`、`item_details`（验收项主表）、`l1`（项目级检查）；计划还保存 `item_texts` 验收项文本快照；
- `issue_count`、`failure_occurrences`；
- `templates`：模板处置摘要（`template_id`、`name`、`summary`），只作说明，不参与结论与计数。

仅运行结束后可用；运行中返回 `RUN_ACTIVE`。每次调用生成新报告并把 `latest_report_id` 写回运行记录；显式重新生成会重新判定 Mock，并使用本次运行的有效用例结果与 plan/task/delivery 快照；旧报告保留。

#### `report.show`

入参 `{"project_id", "report_id"}`；返回完整 Report（结论、覆盖、验收项/用例明细、证据索引、问题、分析、模拟数据判断、验收意见、环境与 GitHub 快照，以及 `preflight` 环境安检结果、`probe_results` 免 AI 确定性硬探针、`confidence_summary` 三档可信度汇总与 `token_usage` 模型 Token 统计）。

#### `report.list`

入参 `{"project_id", "run_id", "include_body": false}`（`include_body` 可选）；返回该运行的报告版本摘要 `report_id/run_id/generated_at/outcome/revision/has_opinion`，按生成时间倒序；`include_body=true` 时每条附带完整报告正文 `report` 供列表批量加载。旧报告没有 `generated_at` 时用文件时间兜底。无法读取的旧报告不静默丢弃，放在 `skipped` 里并附 `code/message`。

#### `report.export`

入参 `{"project_id", "report_id", "path": "/abs/report.md"}`；返回导出路径、字节数、sha256 与行数。导出的 Markdown 含结论、范围与覆盖、验收项主表、未覆盖/未验证、分层计数、项目级检查、用例明细、失败证据、涉及模块/未映射文件、AI 分析、问题、Mock 判定、模板处置，以及源码提交、规则版本、环境与 GitHub 快照。导出格式只有 Markdown，不生成 ZIP，不打包证据文件；证据只写引用路径，导出前脱敏。

#### `report.opinion`

```json
{"project_id": "proj_...", "report_id": "rep_...", "by": "张三", "text": "A3 已复测通过，可以上线"}
```

返回 `{"opinion": {"by": "张三", "at": "...", "text": "..."}}`；不改变结论。

### 4.9 AI 分析

#### `analysis.run`

入参 `{"project_id", "run_id"}`；返回：

```json
{"status": "generated|not_generated|invalid", "reason": "no_failures|model_missing|model_invalid|model_error|", "analysis": {"observed": "...", "evidence_refs": [], "candidate_causes": [{"cause": "...", "confidence": "medium"}], "unverified": ["..."], "next_steps": ["..."]}}
```

缺 `unverified` 时 `status: invalid`，`analysis` 为 `null`。`reason` 说明未生成原因：无失败 `no_failures`、未配置模型 `model_missing`、模型输出不合格 `model_invalid`、模型调用异常 `model_error`；生成成功时为空字符串。

### 4.10 GitHub 与链路

#### `github.read`

入参 `{"project_id", "repo": "owner/name", "ref": "main"}`；返回：

```json
{
  "repo": "owner/name", "branch": "main", "commit": "abc123",
  "files": ["src/order.py"], "prs": [], "checks": [], "deployment": null,
  "account": "zhangsan", "host": "github.com", "fetched_at": "...",
  "change": {
    "repo": "owner/name", "branch": "main", "commit": "abc123",
    "files": ["src/order.py"], "prs": [], "checks": [], "deployment": null,
    "account": "zhangsan", "host": "github.com", "fetched_at": "..."
  },
  "path": "projects/<project_id>/github/abc123.json"
}
```

`files` 是本次提交涉及的文件名；`change` 与本次读取的记录相同，便于调用方直接取整条变更；`path` 是写入工作空间的相对路径（`projects/<project_id>/github/<commit>.json`）。

#### `link.import`

```json
{"run_id": "run_...", "request_id": "req-1"}
```

返回 `{"nodes": [], "assertions": [], "gaps": [{"node_id": "log", "reason": "未找到该请求的日志"}], "location": {"first_anomaly": null, "upstream": [], "downstream": [], "location": {"known": false, "reason": "没有可定位的异常节点"}}}`。`location.first_anomaly` 取首个非 `passed` 节点（或首个非 `passed` 断言所属节点）；`location.location` 从证据引用的 `file:line` 解析，解析不到时 `known: false` 并给出原因。已有 `nodes / assertions / gaps` 字段不变。

#### `link.assert`

入参 `{"run_id", "node_id", "expected", "actual", "operator"}`；读取该运行的 `link.json`，要求节点存在，写入/替换该节点的断言并返回 `{"result": "passed|failed"}`。`operator` 可选，默认 `equals`。

### 4.11 备份与诊断

#### `backup.create`

入参 `{"path": "/abs/backup.zip", "include_evidence": false}`；返回 `path`、`sha256`、`bytes`、`includes_evidence`、`estimated_evidence_bytes`、`estimated_evidence_mb`；预计证据大小由 CLI 返回，面板当前不展示。

#### `backup.restore`

入参 `{"path": "/abs/backup.zip", "target": "/abs/new-workspace"}`；返回恢复清单与缺口；目标不存在且父目录可写，或已存在且为空目录。恢复先校验 manifest `schema_version=1`、路径规范化、重复条目、符号链接、条目数（≤100000）与解压总大小（≤2 GiB），再解压到同目录临时目录并原子替换；中途失败不会留下半恢复目标。

#### `doctor`

入参 `{}`；返回：

```json
{"python": {"ok": true, "version": "3.13.1", "executable": "..."}, "package": {"ok": true, "version": "0.5.0"}, "model": {"ok": false, "source": "none", "error": "未配置模型密钥"}, "github": {"ok": true, "account": "zhangsan", "host": "github.com"}, "workspace": {"ok": true, "path": "...", "workspace_id": "ws_..."}, "proxy": {"ok": true, "configured": {}}, "project_config": {"ok": true, "problems": []}, "references": {"ok": true, "missing": []}, "repairs": [], "repairs_done": []}
```

`model.ok` 由真实连通性探测决定，不是“有密钥就算通过”；`github.ok` 要求 `gh auth status` 成功，配置 `repo` 时还要求可读该仓库；`proxy` 报告已配置项并校验 URL。`repairs` 按 `risk`（low/medium/high）与 `command` 给出建议，低风险项可带 `auto: true`；`repair=true` 只自动执行低风险项，并写入 `actions/doctor.jsonl`。

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
{"schema_version": 1, "project_id": "proj_...", "name": "", "root": "/abs", "goal": "", "modules": [], "revision": 1, "updated_at": "..."}
```

`task.json`：

```json
{"schema_version": 1, "task_id": "task_...", "project_id": "proj_...", "title": "", "goal": "", "intent": "business", "origin": "goal", "source_map_revision": 3, "scope": "", "inputs": "", "outputs": "", "preconditions": [], "executor": "", "reviewer": "", "acceptance_items": [{"item_id": "ai_...", "text": "", "observable": true, "status": "active", "source": "", "module_id": null}], "revision": 1}
```

示例是完整落盘字段；计划契约摘要只包含 `intent` 与 active 验收项的 `id/text/status/observable/module_id`，不包含 title/goal/executor/reviewer。

`project-map.json`：

```json
{"schema_version": 1, "project_id": "proj_...", "project_revision": 3, "source_commit": "abc123", "generated_at": "...", "model": "deepseek-chat", "business_functions": [{"name": "下单", "modules": ["order", "stock"], "entry_points": ["POST /orders"], "test_commands": ["pytest tests/order"], "testable_items": ["下单后订单出现"], "evidence": ["README.md", "routes.py:42"], "confidence": "high"}], "unknowns": [{"area": "支付回调", "reason": "没有入口或测试"}]}
```

`plan.json`：

```json
{"schema_version": 1, "plan_id": "plan_...", "task_id": "task_...", "plan_no": 2, "task_revision": 3, "task_contract": "sha256", "created_at": "...", "scope": {"preset": "selected", "module_ids": [], "acceptance_item_ids": []}, "applicable_count": 8, "cases": [{"case_id": "case_...", "acceptance_item_id": "ai_...", "layer": "L2", "preconditions": [], "steps": [], "expected": "", "expectation_source": "", "verify_method": "", "mock_allowed": true, "importance": "high", "depends_on": "service"}], "warnings": [], "unselected_by_module": [{"module_id": "mod_...", "module_name": "库存", "count": 2}], "source_signature": "sha256", "status": "published", "revision": 1}
```

`run.json`：

```json
{"schema_version": 1, "run_id": "run_...", "plan_id": "plan_...", "project_id": "proj_...", "kind": "task", "scope": {"preset": "selected", "acceptance_item_ids": [], "frozen_at": "..."}, "confirmed_by": "", "confirmed_at": "...", "confirmation_id": "conf_...", "confirmation_fingerprint": "sha256", "confirmation_reused": false, "status": "running", "current_case_id": "", "pid": 1234, "started_at": "...", "heartbeat_at": "...", "latest_report_id": "", "cases": [], "evidence_refs": [], "analysis": []}
```

`issue.json`：

```json
{"schema_version": 1, "issue_id": "issue_...", "project_id": "proj_...", "signature": "...", "title": "", "severity": "P1", "status": "pending", "disposition": null, "file": "tests/test_users.py", "line": 15, "occurrences": [], "fix_version": null, "evidence_refs": [], "revision": 1}
```

`confirmation.json`：

```json
{"schema_version": 1, "confirmation_id": "conf_...", "project_id": "proj_...", "plan_id": "plan_...", "plan_revision": 3, "acceptance_item_ids": ["ai_..."], "environment": {}, "credentials": {}, "side_effects": [], "fingerprint": "sha256", "confirmed_by": "cli", "confirmed_at": "...", "run_id": "run_..."}
```

`deliveries/<task_id>.json`：

```json
{"schema_version": 1, "delivery_id": "dlv_...", "task_id": "task_...", "done": [], "not_done": [], "changed_modules": [], "interface_changes": [], "run_commands": [], "test_data": "", "dependencies": [], "mocks": [], "self_test_evidence": [], "commit": "", "complete": false, "revision": 1}
```

`runs/<run_id>/link.json`：

```json
{"schema_version": 1, "imported_at": "...", "nodes": [{"node_id": "log:2", "type": "log", "status": "failed", "message": "", "parent_id": "log:1", "evidence_ref": "app.jsonl:2"}], "assertions": [{"node_id": "log:2", "expected": "order_1", "actual": "order_1", "result": "passed"}], "gaps": [], "location": {"first_anomaly": {}, "upstream": [], "downstream": [], "location": {"known": true, "path": "app.jsonl", "line": 2}}}
```

`report.json`：

```json
{"schema_version": 1, "report_id": "rep_...", "run_id": "run_...", "generated_at": "...", "outcome": "failed", "counts": {}, "coverage": {"selected": 2, "applicable": 8}, "changed_modules": [], "unmapped_files": [], "evidence_refs": [], "opinion": null, "revision": 1}
```

### 5.3 目录布局

```text
<workspace>/
├─ workspace.json
├─ .workspace.lock                     排他写锁
├─ requests/<request_id+command+payload 的短哈希>.json          写操作幂等缓存
├─ actions/doctor.jsonl                环境自检与修复动作
├─ actions/<YYYY-MM>.jsonl             worker 与运行动作
├─ .cache/mock/<key>.json              Mock 判定缓存
├─ .cache/plan/<project_id>/<key>.json 计划批次缓存（超时重试续跑）
└─ projects/<project_id>/
   ├─ project.json
   ├─ project.config.json
   ├─ project-map.json                 AI 项目测试地图缓存
   ├─ tasks/<task_id>.json
   ├─ tasks/revisions/<task_id>/<revision>.json     任务旧修订
   ├─ deliveries/<task_id>.json
   ├─ plans/<plan_id>.json
   ├─ plans/revisions/<plan_id>/<revision>.json     计划旧修订
   ├─ confirmations/<confirmation_id>.json          执行前确认记录
   ├─ runs/<run_id>/
   │  ├─ run.json
   │  ├─ plan.json                    启动时的计划快照（含收窄后的范围）
   │  ├─ task.json                    启动时的任务快照
   │  ├─ delivery.json                启动时的交付快照
   │  ├─ manual/<case_id>.json        人工检查记录
   │  ├─ link.json                    导入的调用链与定位
   │  ├─ evidence/                    证据文件
   │  └─ generated/                   运行前生成并校验的脚本；含 index.json
   ├─ issues/<issue_id>.json
   ├─ reports/<report_id>.json
   └─ github/<commit>.json            github.read 的只读快照
```

运行目录由本次 worker 独占；`run.json` 只引用确认记录，不内嵌确认正文。旧运行仍使用自己的 `plan.json` 与 `task.json` 快照。

## 6 项目配置

`project.config.json` 与 `project.json` 同目录：

```json
{
  "schema_version": 1,
  "model": {"base_url": "https://api.deepseek.com", "name": "deepseek-chat", "timeout_seconds": 120},
  "services": {"start": "", "stop": "", "health": "", "timeout_seconds": 120, "keep_on_failure": false, "reset": ""},
  "link": {"request_id_header": "X-Request-Id", "log_paths": [], "log_format": "jsonl", "log_fields": {}, "db_query": ""},
  "runners": {"pytest": {"command": "pytest"}, "playwright": {"command": "npx playwright test"}},
  "evidence": {"file_cap_mb": 20, "media_budget_mb": 500, "video": false, "mock": {"max_files": 240, "max_depth": 3, "per_component_files": 120}}
}
```

密钥不写入此文件。

## 7 版本与兼容

- 数据文件 `schema_version` 递增；旧版本只提示重建或重新生成，没有自动转换工具，不自动迁移；
- 命令与字段变更需同步本册、`models.py`、扩展与契约测试；
- 新增命令不改变已有字段语义；废弃命令先标记，再删除。

## 8 契约测试清单

实现时至少覆盖：

1. 信封与退出码；
2. 每个命令的必填字段与错误码；
3. 枚举取值；
4. 文件 schema 版本与拒绝旧版本；
5. 幂等：写操作同一 `request_id` 不重复执行；只读命令不缓存 `request_id`；
6. 单项目单运行：第二个 `run.start` 返回 `RUN_ACTIVE`；
7. 既无 `--confirm` 也无可用确认记录时返回 `CONFIRMATION_REQUIRED`；可复用确认必须同计划版本、同范围、同指纹；
8. 计划范围为空返回 `SCOPE_EMPTY`；
9. 报告结论与计数不因 AI 摘要改变；
10. 旧格式备份恢复失败时给出 `SCHEMA_UNSUPPORTED`；
11. `plan.show` 只按任务契约判过期，`run.start` 按完整来源签名判 `PLAN_STALE`，旧计划无 `task_contract` 判过期。
12. `run.status` 返回计划快照、任务快照与 `task_snapshot`；旧运行没有快照时明确为 `false`，不混用当前任务；
14. 报告生成写回 `latest_report_id`；`report.list` 按 `generated_at` 倒序；重新生成不覆盖旧报告；
15. `link.import` 缺配置返回 `LINK_CONFIG_MISSING`；`link.assert` 的节点不存在返回 `INVALID_REQUEST`，断言结果写回 `link.json`；
16. `data.cleanup` 的清理命令必须含 `{run_id}` 或 `{data_prefix}`，非法 `run_id` 拒绝；`dry_run=false` 需要确认；
17. `evidence.add` 只允许运行结束后补证；跨运行路径、重复文件名与超大文件按证据规则处理；
18. 扩展白名单与 CLI 注册表一致：面板只能调用 `PANEL_COMMANDS` 中的命令，新增命令先同步扩展、测试与文档。
19. `project.map` 无地图时返回 `null`；`project.map.refresh` 生成业务功能、模块、入口、未知项，不写被测仓库；项目修订/提交/配置变化后返回 `stale: true`；
20. `run.quick` 不建任务、不选验收项，运行 `kind=project_check`，报告 `0/0`，L1 失败不改写为验收项通过；
21. `task.suggest` 候选必须带 `scope_preview` 与 `uncertainties`；`task.add` 的 `origin` 只能是 `goal|range`；
22. `run.status`/`run.list` 返回 `kind`；项目检查运行的 `task=null`、`task_snapshot=false`；
23. `report.export` 只写 Markdown，不生成 ZIP、不打包证据；导出内容脱敏。
